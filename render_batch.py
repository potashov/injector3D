#!/usr/bin/env python3
"""Пакетный рендер матриц датасетов.

Запускает несколько серий `render_parallel.py` подряд: объём или доля
заполнения × угол наклона × тип жидкости × пресет камеры.  Каждая серия
получает собственный подкаталог `<output-dir>/<batch>/<series>/`, поэтому
master `.blend`, manifest и PNG разных наборов параметров не конфликтуют.
В корне пакета пишется `batch_manifest.json` со статусами и путями серий.

Примеры:
    python3 render_batch.py --liquids water,glycerol \\
        --volumes 2000,5000 --tilt-angles 10,20 --gpus 0,1

    python3 render_batch.py --config batch.json --output-dir /mnt/datasets

Формат JSON-конфига:
    {
      "mode": "ring",
      "liquids": ["water", "glycerol"],
      "volumes_mm3": [2000, 5000],
      "tilt_angles": [10, 20],
      "tilt_counts": [8],
      "camera_presets": ["reference"],
      "tag": "dataset",
      "extra_args": ["--no-postprocess"]
    }
"""

import argparse
import glob
import json
import os
import re
import shlex
import subprocess
import sys
import time

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_DIR)

from injector3d import config as cfg
from injector3d import output_manager as om
from injector3d import timing

LAUNCHER = os.path.join(PROJECT_DIR, 'render_parallel.py')
BATCH_MANIFEST = om.batch_manifest_filename()

CONFIG_KEYS = {
    'mode', 'liquids', 'volumes_mm3', 'fill_fractions', 'tilt_angles',
    'tilt_counts', 'camera_presets', 'cameras', 'tag', 'extra_args',
    'gpus', 'shards',
}

RESERVED_EXTRA_ARGS = {
    '--mode', '--output-dir', '--tilt-count', '--tilt-angle',
    '--fill-fraction', '--volume-mm3', '--gpu-index', '--shard-index',
    '--shard-count', '--timestamp', '--tag', '--liquid-type',
    '--liquid-ior', '--liquid-density', '--surface-tension',
    '--contact-angle', '--absorption-density', '--camera-preset',
    '--camera-distance', '--camera-z', '--camera-target-z',
    '--camera-lens', '--camera-roll', '--camera-distortion',
}


def _as_list(value, name, default=None):
    if value is None:
        return list(default) if default is not None else []
    if not isinstance(value, list):
        raise SystemExit(f'{name}: ожидался JSON-список')
    if not value:
        raise SystemExit(f'{name}: список не должен быть пустым')
    return value


def _as_number(value, name):
    try:
        return float(value)
    except (TypeError, ValueError):
        raise SystemExit(f'{name}: ожидалось число, получено {value!r}')


def _fmt_num(value):
    value = float(value)
    if value.is_integer():
        return str(int(value))
    return f'{value:g}'


def _slug_num(value):
    return _fmt_num(value).replace('-', 'm').replace('.', 'p')


def _slug_token(value):
    text = re.sub(r'[^A-Za-z0-9_.-]+', '-', str(value)).strip('-._')
    if not text:
        raise SystemExit(f'Недопустимый параметр для имени серии: {value!r}')
    return text


def _validate_tag(value):
    if value is None:
        return None
    tag = str(value).strip()
    if not tag:
        return None
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', tag):
        raise SystemExit(
            f'tag: допустимы буквы, цифры, точка, подчёркивание и дефис, '
            f'получено {tag!r}')
    return tag


def _normalize_config(raw):
    raw = dict(raw or {})
    unknown = sorted(set(raw) - CONFIG_KEYS)
    if unknown:
        raise SystemExit(f'Неизвестные ключи конфига: {", ".join(unknown)}')

    mode = str(raw.get('mode', 'ring')).strip().lower()
    if mode not in ('ring', 'tube', 'upright'):
        raise SystemExit("mode: ожидался 'ring', 'tube' или 'upright'")

    volumes_raw = raw.get('volumes_mm3')
    fills_raw = raw.get('fill_fractions')
    if volumes_raw is not None and fills_raw is not None:
        raise SystemExit(
            'volumes_mm3 и fill_fractions взаимоисключающие')
    if volumes_raw is not None:
        volumes = [_as_number(v, 'volumes_mm3')
                   for v in _as_list(volumes_raw, 'volumes_mm3')]
        if any(v < 0.0 for v in volumes):
            raise SystemExit('volumes_mm3: объёмы не могут быть отрицательными')
        fills = None
    elif fills_raw is not None:
        fills = [_as_number(v, 'fill_fractions')
                 for v in _as_list(fills_raw, 'fill_fractions')]
        if any(not (0.0 <= v <= 1.0) for v in fills):
            raise SystemExit('fill_fractions: нужны значения в [0, 1]')
        volumes = None
    else:
        volumes = None
        fills = [float(cfg.FILL_FRACTION)]

    liquids = [str(v).strip().lower()
               for v in _as_list(raw.get('liquids'), 'liquids',
                                 [cfg.DEFAULT_LIQUID_TYPE])]
    unknown_liquids = [v for v in liquids if v not in cfg.LIQUID_PRESETS]
    if unknown_liquids:
        available = ', '.join(sorted(cfg.LIQUID_PRESETS))
        raise SystemExit(
            f'liquids: неизвестные типы {", ".join(unknown_liquids)}; '
            f'доступны: {available}')

    cameras_raw = raw.get('camera_presets', raw.get('cameras'))
    cameras = [str(v).strip().lower()
               for v in _as_list(cameras_raw, 'camera_presets',
                                 [cfg.DEFAULT_CAMERA_PRESET])]
    unknown_cameras = [v for v in cameras if v not in cfg.CAMERA_PRESETS]
    if unknown_cameras:
        available = ', '.join(sorted(cfg.CAMERA_PRESETS))
        raise SystemExit(
            f'camera_presets: неизвестные пресеты '
            f'{", ".join(unknown_cameras)}; доступны: {available}')

    if mode == 'ring':
        counts = [int(_as_number(v, 'tilt_counts'))
                  for v in _as_list(raw.get('tilt_counts'), 'tilt_counts',
                                    [cfg.TILT_AZIMUTH_COUNT])]
        if any(v < 0 for v in counts):
            raise SystemExit('tilt_counts: N должен быть >= 0')
        angles = [_as_number(v, 'tilt_angles')
                  for v in _as_list(raw.get('tilt_angles'), 'tilt_angles',
                                    [cfg.TILT_ANGLE_DEG])]
        for count in counts:
            for angle in angles:
                try:
                    cfg.tilt_ring_variants(count, angle)
                except ValueError as exc:
                    raise SystemExit(f'tilt_angles: {exc}') from None
    elif mode == 'upright':
        counts = [0]
        angles = [None]
    else:
        counts = [None]
        angles = [None]

    tag = _validate_tag(raw.get('tag'))
    extra = raw.get('extra_args', [])
    if not isinstance(extra, list) or not all(
            isinstance(v, str) for v in extra):
        raise SystemExit('extra_args: ожидался список строк')
    for arg in extra:
        key = arg.split('=', 1)[0]
        if key in RESERVED_EXTRA_ARGS:
            raise SystemExit(
                f'extra_args: {key} задаётся матрицей и не может быть '
                f'переопределён')

    gpus = raw.get('gpus')
    if isinstance(gpus, (list, tuple)):
        gpus = ','.join(str(v) for v in gpus)
    if gpus is not None:
        gpus = str(gpus).strip() or None
    shards = raw.get('shards')
    if shards is not None:
        try:
            shards = int(shards)
        except (TypeError, ValueError):
            raise SystemExit('shards: ожидалось целое число')
        if shards < 1:
            raise SystemExit('shards: значение должно быть >= 1')

    return {
        'mode': mode,
        'volumes_mm3': volumes,
        'fill_fractions': fills,
        'liquids': liquids,
        'tilt_counts': counts,
        'tilt_angles': angles,
        'camera_presets': cameras,
        'tag': tag,
        'extra_args': list(extra),
        'gpus': gpus,
        'shards': shards,
    }


def build_series(data):
    """Строит список серий матрицы без запуска Blender."""
    if data['volumes_mm3'] is not None:
        fills = [('volume_mm3', v) for v in data['volumes_mm3']]
    else:
        fills = [('fill_fraction', v) for v in data['fill_fractions']]

    series = []
    seen = set()
    for liquid in data['liquids']:
        for fill_key, fill_value in fills:
            for angle in data['tilt_angles']:
                for count in data['tilt_counts']:
                    for camera in data['camera_presets']:
                        parts = [liquid]
                        if fill_key == 'volume_mm3':
                            parts.append(f'v{_slug_num(fill_value)}')
                        else:
                            parts.append(f'f{_slug_num(fill_value)}')
                        if data['mode'] == 'ring':
                            parts += [f'a{_slug_num(angle)}', f'n{count}']
                        elif data['mode'] == 'upright':
                            parts.append('upright')
                        else:
                            parts.append('tube')
                        parts.append(f'c_{_slug_token(camera)}')
                        series_id = '_'.join(parts)
                        if series_id in seen:
                            raise SystemExit(
                                f'Дублирующаяся серия: {series_id}')
                        seen.add(series_id)
                        if data['mode'] == 'ring':
                            expected = int(count) + 1
                        elif data['mode'] == 'upright':
                            expected = 1
                        else:
                            expected = len(cfg.TILT_VARIANTS)
                        series.append({
                            'id': series_id,
                            'mode': data['mode'],
                            'liquid_type': liquid,
                            'volume_mm3': (float(fill_value)
                                           if fill_key == 'volume_mm3'
                                           else None),
                            'fill_fraction': (float(fill_value)
                                              if fill_key == 'fill_fraction'
                                              else None),
                            'tilt_count': (int(count)
                                           if data['mode'] == 'ring'
                                           else (0 if data['mode'] == 'upright'
                                                 else None)),
                            'tilt_angle_deg': (float(angle)
                                               if data['mode'] == 'ring'
                                               else None),
                            'camera_preset': camera,
                            'tag': data['tag'],
                            'extra_args': list(data['extra_args']),
                            'expected_frames': expected,
                        })
    return series


def _limit_series(series, limit):
    if limit is None:
        return series
    if limit < 1:
        raise SystemExit('limit: значение должно быть >= 1')
    return series[:limit]


def _series_command(series, batch_root, gpus=None, shards=None,
                    blender=None, keep_shards=False):
    cmd = [
        sys.executable, LAUNCHER,
        '--mode', series['mode'],
        '--output-dir', batch_root,
    ]
    if gpus:
        cmd += ['--gpus', gpus]
    series_shards = shards
    if (series['mode'] in ('tube', 'upright')
            or series.get('tilt_count') == 0):
        series_shards = 1
    if series_shards:
        cmd += ['--shards', str(series_shards)]
    if blender:
        cmd += ['--blender', blender]
    if keep_shards:
        cmd += ['--keep-shards']

    forwarded = [
        '--timestamp', series['id'],
        '--liquid-type', series['liquid_type'],
    ]
    if series['volume_mm3'] is not None:
        forwarded += ['--volume-mm3', _fmt_num(series['volume_mm3'])]
    else:
        forwarded += ['--fill-fraction', _fmt_num(series['fill_fraction'])]
    if series['mode'] == 'ring':
        forwarded += [
            '--tilt-count', str(series['tilt_count']),
            '--tilt-angle', _fmt_num(series['tilt_angle_deg']),
        ]
    forwarded += ['--camera-preset', series['camera_preset']]
    if series['tag']:
        forwarded += ['--tag', series['tag']]
    forwarded += series['extra_args']
    return cmd + forwarded


def _find_final_manifest(series_dir):
    pattern = os.path.join(series_dir, '*_manifest*.json')
    paths = [path for path in glob.glob(pattern)
             if not re.search(r'_s\d+of\d+\.json$', path)]
    return sorted(paths)[-1] if paths else None


def _read_manifest(manifest_path):
    try:
        with open(manifest_path, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _frame_count(manifest_path):
    return len(_read_manifest(manifest_path).get('frames', []))


def _completed_manifest(series_dir, expected_frames):
    path = _find_final_manifest(series_dir)
    if path and _frame_count(path) >= expected_frames:
        return path
    return None


def _relative(path, batch_root):
    return os.path.relpath(path, batch_root)


def _record(series, command):
    record = {key: series[key] for key in (
        'id', 'mode', 'liquid_type', 'volume_mm3', 'fill_fraction',
        'tilt_count', 'tilt_angle_deg', 'camera_preset', 'tag',
        'expected_frames')}
    record.update({
        'directory': series['id'],
        'command': command,
        'status': 'pending',
        'returncode': None,
        'manifest': None,
        'frames': 0,
        'log': None,
        'started_at': None,
        'finished_at': None,
        'wall_ms': None,
        'timings': None,
    })
    return record


def _run_series(record, batch_root, resume=False):
    series_dir = om.render_dir(batch_root, record['id'])
    if resume:
        manifest = _completed_manifest(
            series_dir, record['expected_frames'])
        if manifest:
            record['status'] = 'skipped'
            record['returncode'] = 0
            record['manifest'] = _relative(manifest, batch_root)
            record['frames'] = _frame_count(manifest)
            record['timings'] = _read_manifest(manifest).get('timings')
            record['started_at'] = timing.local_now_iso()
            record['finished_at'] = record['started_at']
            record['wall_ms'] = 0.0
            print(f'  пропуск (уже готово): {record["manifest"]}')
            return record

    log_dir = os.path.join(batch_root, 'logs')
    om.ensure_dir(log_dir)
    log_path = os.path.join(log_dir, f'{record["id"]}.log')
    record['log'] = _relative(log_path, batch_root)
    print(f'  лог: {log_path}')
    record['started_at'] = timing.local_now_iso()
    started = timing.now_ms()
    try:
        with open(log_path, 'w', encoding='utf-8') as log:
            log.write('# ' + shlex.join(record['command']) + '\n\n')
            proc = subprocess.run(
                record['command'], stdout=log, stderr=subprocess.STDOUT,
                text=True)
        record['returncode'] = proc.returncode
    except OSError as exc:
        record['returncode'] = -1
        record['error'] = str(exc)
    finally:
        record['wall_ms'] = round(timing.now_ms() - started, 3)
        record['finished_at'] = timing.local_now_iso()

    manifest = _find_final_manifest(series_dir)
    if manifest:
        record['manifest'] = _relative(manifest, batch_root)
        record['frames'] = _frame_count(manifest)
        record['timings'] = _read_manifest(manifest).get('timings')
    record['status'] = ('ok' if record['returncode'] == 0
                        else 'failed')
    return record


def _batch_timings(records):
    summary = timing.aggregate_records(records)
    summary['by_liquid_type'] = timing.group_records(records, 'liquid_type')
    summary['by_volume_mm3'] = timing.group_records(records, 'volume_mm3')
    summary['by_tilt_angle_deg'] = timing.group_records(
        records, 'tilt_angle_deg')
    summary['by_tilt_count'] = timing.group_records(records, 'tilt_count')
    summary['by_camera_preset'] = timing.group_records(
        records, 'camera_preset')
    return summary


def _manifest_payload(batch_id, batch_root, data, records, created_at):
    return {
        'batch': batch_id,
        'created_at': created_at,
        'output_dir': batch_root,
        'mode': data['mode'],
        'matrix': {
            'liquids': data['liquids'],
            'volumes_mm3': data['volumes_mm3'],
            'fill_fractions': data['fill_fractions'],
            'tilt_angles': data['tilt_angles'],
            'tilt_counts': data['tilt_counts'],
            'camera_presets': data['camera_presets'],
            'tag': data['tag'],
            'extra_args': data['extra_args'],
        },
        'series_count': len(records),
        'timings': _batch_timings(records),
        'series': records,
    }


def _write_manifest(batch_root, payload):
    path = os.path.join(batch_root, BATCH_MANIFEST)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def _stage_total(record, name):
    timings = record.get('timings') or {}
    stage = (timings.get('stages') or {}).get(name) or {}
    return stage.get('total_ms')


def _print_series_timing(record):
    parts = []
    wall_ms = record.get('wall_ms')
    if wall_ms is not None:
        parts.append(f'wall={wall_ms / 1000.0:.2f} с')
    for name in ('render', 'postprocess', 'liquid_upright',
                 'liquid_tilted', 'liquid_build'):
        total = _stage_total(record, name)
        if total is not None:
            parts.append(f'{name}={total / 1000.0:.2f} с')
    if parts:
        print('  время: ' + ', '.join(parts))


def _split_list(value):
    if value is None:
        return None
    return [part.strip() for part in value.split(',') if part.strip()]


def _load_config(path):
    with open(path, 'r', encoding='utf-8') as fh:
        try:
            data = json.load(fh)
        except json.JSONDecodeError as exc:
            raise SystemExit(f'Ошибка JSON в {path}: {exc}')
    if not isinstance(data, dict):
        raise SystemExit(f'{path}: ожидался JSON-объект')
    return data


def main():
    parser = argparse.ArgumentParser(
        description='Пакетный рендер матриц датасетов injector3D')
    parser.add_argument('--config', help='JSON-файл матрицы параметров')
    parser.add_argument('--mode', choices=('ring', 'tube', 'upright'))
    parser.add_argument('--liquids', help='например: water,glycerol')
    parser.add_argument('--volumes', help='объёмы в мм³ через запятую')
    parser.add_argument('--fill-fractions', help='доли вместимости через запятую')
    parser.add_argument('--tilt-angles', help='углы наклона через запятую')
    parser.add_argument('--tilt-counts',
                        help='числа азимутов N через запятую; 0 — только '
                             'вертикальный кадр')
    parser.add_argument('--cameras', help='пресеты камеры через запятую')
    parser.add_argument('--tag', help='суффикс имён файлов')
    parser.add_argument('--extra-args', action='append', default=[],
                        metavar='ARG',
                        help='дополнительные аргументы create_scene.py; '
                             'например: --extra-args=--no-postprocess')
    parser.add_argument('--output-dir', default=cfg.OUTPUT_DIR,
                        help='база вывода; пакет ляжет в <output-dir>/<batch>')
    parser.add_argument('--timestamp', help='имя каталога пакета')
    parser.add_argument('--gpus', help='индексы GPU для render_parallel.py')
    parser.add_argument('--shards', type=int, help='число шардов на серию')
    parser.add_argument('--blender', help='путь к Blender')
    parser.add_argument('--limit', type=int, help='ограничить число серий')
    parser.add_argument('--resume', action='store_true',
                        help='пропускать серии с готовым manifest')
    parser.add_argument('--keep-going', action='store_true',
                        help='продолжать после ошибки серии')
    parser.add_argument('--keep-shards', action='store_true',
                        help='не удалять manifest шардов')
    parser.add_argument('--dry-run', action='store_true',
                        help='показать команды без запуска')
    args = parser.parse_args()

    matrix_cli = any((
        args.mode, args.liquids, args.volumes, args.fill_fractions,
        args.tilt_angles, args.tilt_counts, args.cameras, args.tag,
        args.extra_args,
    ))
    if not args.config and not matrix_cli:
        parser.error('нужен --config или хотя бы один параметр матрицы')

    raw = _load_config(args.config) if args.config else {}
    if args.mode is not None:
        raw['mode'] = args.mode
    if args.liquids is not None:
        raw['liquids'] = _split_list(args.liquids)
    if args.volumes is not None:
        raw['volumes_mm3'] = _split_list(args.volumes)
    if args.fill_fractions is not None:
        raw['fill_fractions'] = _split_list(args.fill_fractions)
    if args.tilt_angles is not None:
        raw['tilt_angles'] = _split_list(args.tilt_angles)
    if args.tilt_counts is not None:
        raw['tilt_counts'] = _split_list(args.tilt_counts)
    if args.cameras is not None:
        raw['camera_presets'] = _split_list(args.cameras)
    if args.tag is not None:
        raw['tag'] = args.tag
    if args.extra_args:
        extra = list(raw.get('extra_args', []))
        for chunk in args.extra_args:
            extra.extend(shlex.split(chunk))
        raw['extra_args'] = extra

    data = _normalize_config(raw)
    gpus = args.gpus if args.gpus is not None else data['gpus']
    shards = args.shards if args.shards is not None else data['shards']
    if data['mode'] in ('tube', 'upright') and shards not in (None, 1):
        parser.error(
            f'--mode {data["mode"]} поддерживает только --shards 1')
    if data['mode'] in ('tube', 'upright') and shards is None:
        shards = 1

    series = _limit_series(build_series(data), args.limit)
    batch_id = args.timestamp or om.new_timestamp()
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', batch_id):
        parser.error('--timestamp: допустимы буквы, цифры, точка, '
                     'подчёркивание и дефис')
    batch_root = om.render_dir(args.output_dir, batch_id)
    commands = [
        _series_command(
            item, batch_root, gpus=gpus, shards=shards,
            blender=args.blender, keep_shards=args.keep_shards)
        for item in series
    ]

    print(f'Пакет: {batch_id}')
    print(f'  каталог: {batch_root}')
    print(f'  серий: {len(series)}')
    print(f'  режим: {data["mode"]}')

    if args.dry_run:
        for index, (item, cmd) in enumerate(zip(series, commands), 1):
            print(f'\n[{index}/{len(series)}] {item["id"]}')
            print('  ' + shlex.join(cmd))
        return

    om.ensure_dir(batch_root)
    records = [_record(item, cmd)
               for item, cmd in zip(series, commands)]
    created_at = time.strftime('%Y-%m-%dT%H:%M:%S')
    payload = _manifest_payload(
        batch_id, batch_root, data, records, created_at)
    _write_manifest(batch_root, payload)

    failed = False
    for index, record in enumerate(records, 1):
        print(f'\n[{index}/{len(records)}] {record["id"]}')
        _run_series(record, batch_root, resume=args.resume)
        _print_series_timing(record)
        payload['timings'] = _batch_timings(records)
        _write_manifest(batch_root, payload)
        if record['status'] == 'failed':
            failed = True
            print(f'  ОШИБКА: код {record["returncode"]}')
            if not args.keep_going:
                break

    ok = sum(1 for r in records if r['status'] == 'ok')
    skipped = sum(1 for r in records if r['status'] == 'skipped')
    bad = sum(1 for r in records if r['status'] == 'failed')
    print(f'\nИтог: ok={ok}, skipped={skipped}, failed={bad}, '
          f'всего={len(records)}')
    batch_timings = _batch_timings(records)
    wall = batch_timings.get('wall_ms') or {}
    if wall.get('total_ms') is not None:
        print(f'Время пакета: wall={wall["total_ms"] / 1000.0:.2f} с')
    stages = batch_timings.get('stages') or {}
    render = stages.get('render') or {}
    postprocess = stages.get('postprocess') or {}
    if render.get('total_ms') is not None:
        print(f'  render={render["total_ms"] / 1000.0:.2f} с, '
              f'postprocess={postprocess.get("total_ms", 0.0) / 1000.0:.2f} с')
    print(f'Manifest пакета: {os.path.join(batch_root, BATCH_MANIFEST)}')
    if failed or bad:
        raise SystemExit(1)


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Параллельный рендер серии N+1 по нескольким GPU.

Запускает несколько процессов Blender, по одному на GPU. Каждый процесс
рендерит свою часть азимутов (`--shard-index/--shard-count`), после чего
manifest'ы шардов сливаются в один файл серии.

Все шарды пишут в один каталог прогона `<output-dir>/<timestamp>/` — его
создаёт launcher и передаёт через `--output-dir`/`--timestamp`, поэтому
kadры, master `.blend` и логи шардов не расходятся по разным папкам.

Пример:
    python3 render_parallel.py --tilt-count 8 --tilt-angle 10 --gpus 0,1
"""

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_DIR)

from injector3d import config as cfg
from injector3d import dataset as dataset_info
from injector3d import output_manager as om
from injector3d import timing

DEFAULT_BLENDER = os.path.join(
    PROJECT_DIR, 'blender-5.2.3-linux-x64', 'blender')


def _detect_gpus():
    try:
        out = subprocess.check_output(
            ['nvidia-smi', '--query-gpu=name', '--format=csv,noheader'],
            text=True, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        return []
    return list(range(len([line for line in out.splitlines() if line.strip()])))


def _blender_path(value):
    if value:
        return value
    if os.path.exists(DEFAULT_BLENDER):
        return DEFAULT_BLENDER
    found = shutil.which('blender')
    if found:
        return found
    raise SystemExit('Blender не найден: укажите --blender')


def _value_after(args, name):
    for i, arg in enumerate(args):
        if arg == name and i + 1 < len(args):
            return args[i + 1]
    return None


def _strip_options(args, names):
    out = []
    i = 0
    while i < len(args):
        if args[i] in names:
            i += 2
            continue
        out.append(args[i])
        i += 1
    return out


def _remove(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def _merge_manifests(run_dir, ts, shards, keep):
    """Сливает manifest'ы шардов в один общий manifest серии."""
    frames = []
    tag = None
    liquid = None
    seen = set()
    manifest_paths = []
    for shard in range(shards):
        pattern = os.path.join(
            run_dir, f'*_manifest_*_s{shard}of{shards}.json')
        matches = sorted(glob.glob(pattern))
        if not matches:
            raise SystemExit(f'Не найден manifest шарда {shard}: {pattern}')
        path = matches[-1]
        manifest_paths.append(path)
        with open(path, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
        if tag is None:
            tag = data.get('tag')
        if liquid is None:
            liquid = data.get('liquid_type')
        for frame in data.get('frames', []):
            final = frame.get('final')
            if final and final in seen:
                continue
            if final:
                seen.add(final)
            frames.append(frame)

    frames.sort(key=lambda frame: (
        frame.get('variant') != 'upright',
        -1 if frame.get('azimuth_deg') is None else frame['azimuth_deg'],
        frame.get('variant', ''),
    ))
    name = om.manifest_filename(liquid, tag)
    path = os.path.join(run_dir, name)
    timings = timing.summarize_frames(frames)
    timings['shards'] = shards
    payload = {
        'tag': tag,
        'timestamp': ts,
        'liquid_type': liquid,
        'shards': shards,
        'frames': frames,
        'timings': timings,
    }
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    if not keep:
        for shard_path in manifest_paths:
            _remove(shard_path)
    print(f'  Объединён manifest: {name} ({len(frames)} кадров)')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', default='ring',
                        choices=('ring', 'tube', 'upright'),
                        help='режим рендера (по умолчанию ring — серия N+1; '
                             'upright — один вертикальный кадр)')
    parser.add_argument('--output-dir', default=os.path.join(PROJECT_DIR, 'output'),
                        help='база вывода; прогон ляжет в '
                             '<output-dir>/<timestamp>, а с --dataset это '
                             'корень плоского датасета')
    parser.add_argument('--gpus', default=None,
                        help='индексы GPU через запятую, например 0,1')
    parser.add_argument('--shards', type=int, default=None,
                        help='число шардов; по умолчанию — число GPU')
    parser.add_argument('--blender', default=None)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--keep-shards', action='store_true',
                        help='не удалять manifest шардов после слияния')
    parser.add_argument('--dataset', action='store_true',
                        help='собрать плоский датасет в --output-dir')
    parser.add_argument('--dataset-append', action='store_true',
                        help='дополнить существующий датасет')
    parser.add_argument('--dataset-overwrite', action='store_true',
                        help='перезаписать существующий датасет')
    parser.add_argument('--dataset-split',
                        help='доли train,val,test; по умолчанию 0.8,0.1,0.1')
    parser.add_argument('--dataset-seed', type=int,
                        help='seed разбиения датасета')
    parser.add_argument('--keep-work', metavar='DIR',
                        help='сохранить work-dir рендера в DIR')
    args, forwarded = parser.parse_known_args()
    tilt_count = _value_after(forwarded, '--tilt-count')
    tilt_angle = _value_after(forwarded, '--tilt-angle')

    dataset_ratios = None
    if args.dataset:
        if args.dataset_append and args.dataset_overwrite:
            raise SystemExit(
                '--dataset-append и --dataset-overwrite взаимоисключающие')
        if '--no-postprocess' in forwarded:
            raise SystemExit('--dataset требует включённую постобработку')
        if '--overlay-labels' in forwarded:
            raise SystemExit('--overlay-labels нельзя сочетать с --dataset')
        volume = _value_after(forwarded, '--volume-mm3')
        if volume is None:
            raise SystemExit('--dataset требует --volume-mm3')
        if _value_after(forwarded, '--fill-fraction') is not None:
            raise SystemExit('--dataset несовместим с --fill-fraction')
        try:
            dataset_info.class_id_for_volume_mm3(float(volume))
            dataset_ratios = dataset_info.parse_split_ratios(
                args.dataset_split)
        except (dataset_info.DatasetError, ValueError) as exc:
            raise SystemExit(f'--dataset: {exc}') from None

    gpus = ([int(x) for x in args.gpus.split(',')]
            if args.gpus else _detect_gpus())
    if not gpus:
        print('GPU не найдены — один процесс на CPU')
        gpus = [None]
    shards = args.shards or len(gpus)
    if shards < 1:
        raise SystemExit('--shards должен быть >= 1')
    # Шардирование делит наклонные кадры серии N+1; в режиме 5 ракурсов
    # кадров всего пять и они не переставляются — один процесс.
    if args.mode == 'tube' and shards != 1:
        raise SystemExit('--mode tube рендерит один набор кадров: '
                         '--shards должен быть 1')
    single_frame = args.mode == 'upright'
    if tilt_count is not None:
        try:
            single_frame = single_frame or int(tilt_count) == 0
        except ValueError:
            pass
    if single_frame and shards != 1:
        raise SystemExit('--mode upright/--tilt-count 0 рендерит один кадр: '
                         '--shards должен быть 1')
    if args.mode == 'ring':
        try:
            n = (int(tilt_count) if tilt_count is not None
                 else cfg.TILT_AZIMUTH_COUNT)
            angle = (float(tilt_angle) if tilt_angle is not None
                     else cfg.TILT_ANGLE_DEG)
        except (TypeError, ValueError):
            raise SystemExit(
                '--tilt-count/--tilt-angle: ожидалось число') from None
        try:
            cfg.tilt_ring_variants(n, angle)
        except ValueError as exc:
            raise SystemExit(f'Ошибка: {exc}') from None

    # Timestamp читаем ДО _strip_options: он приходит в forwarded и будет
    # вырезан оттуда, а каталог прогона нужен уже сейчас.
    ts = _value_after(forwarded, '--timestamp') or om.new_timestamp()
    liquid = _value_after(forwarded, '--liquid-type')
    forwarded = _strip_options(
        forwarded, {'--gpu-index', '--shard-index', '--shard-count',
                    '--output-dir', '--timestamp', '--mode'})
    # Общий каталог прогона создаёт launcher: все шарды пишут в него.
    # В dataset-режиме `--output-dir` — корень датасета, а шарды пишут
    # во временный work-dir.
    dataset_root = None
    work_root = None
    if args.dataset:
        dataset_root = os.path.abspath(args.output_dir)
        if args.keep_work:
            work_root = os.path.abspath(args.keep_work)
            os.makedirs(work_root, exist_ok=True)
        else:
            work_root = tempfile.mkdtemp(prefix='injector3d_dataset_')
        run_dir = om.render_dir(work_root, ts)
        child_output_dir = work_root
    else:
        run_dir = om.render_dir(args.output_dir, ts)
        child_output_dir = args.output_dir
    forwarded += ['--timestamp', ts, '--output-dir', child_output_dir]

    blender = _blender_path(args.blender)
    script = os.path.join(PROJECT_DIR, 'injector3d', 'create_scene.py')
    commands = []
    for shard in range(shards):
        gpu = gpus[shard % len(gpus)]
        cmd = [
            blender, '--background', '--python', script, '--',
            '--mode', args.mode,
            *forwarded,
        ]
        # При одном шарде суффикс не нужен: manifest сразу финальный.
        if shards > 1:
            cmd += ['--shard-index', str(shard), '--shard-count', str(shards)]
        if gpu is not None:
            cmd += ['--gpu-index', str(gpu)]
        commands.append((shard, gpu, cmd))

    for shard, gpu, cmd in commands:
        target = f'GPU {gpu}' if gpu is not None else 'CPU'
        print(f'Шард {shard}/{shards} -> {target}')
        if args.dry_run:
            print('  ' + ' '.join(cmd))
    if args.dataset:
        print(f'  датасет: {dataset_root}')
        print(f'  work-dir: {work_root}')

    if args.dry_run:
        if args.dataset and not args.keep_work:
            shutil.rmtree(work_root, ignore_errors=True)
        return

    om.ensure_dir(run_dir)
    procs = []
    for shard, gpu, cmd in commands:
        log_path = os.path.join(run_dir, om.shard_log_filename(liquid, shard))
        log = open(log_path, 'w', encoding='utf-8')
        print(f'  лог: {log_path}')
        procs.append((shard, subprocess.Popen(
            cmd, stdout=log, stderr=subprocess.STDOUT, text=True), log))

    failed = []
    for shard, proc, log in procs:
        code = proc.wait()
        log.close()
        if code:
            failed.append((shard, code))
    if failed:
        for shard, code in failed:
            print(f'Шард {shard} завершился с кодом {code}')
        raise SystemExit(1)

    if shards > 1:
        _merge_manifests(run_dir, ts, shards, args.keep_shards)
    else:
        print('  manifest финальный (один шард, слияние не требуется)')
    if args.dataset:
        try:
            summary = dataset_info.export_run(
                dataset_root, run_dir, append=args.dataset_append,
                overwrite=args.dataset_overwrite,
                split_ratios=dataset_ratios, split_seed=args.dataset_seed)
        except dataset_info.DatasetError as exc:
            print(f'  ! Датасет не собран: {exc}')
            print(f'  work-dir сохранён: {work_root}')
            raise SystemExit(1) from None
        counts = summary['counts']
        print(f'  датасет: {dataset_root}')
        print(f'  кадров: {counts["images"]} '
              f'(train={counts["train"]}, val={counts["val"]}, '
              f'test={counts["test"]})')
        if args.keep_work:
            print(f'  work-dir сохранён: {work_root}')
        else:
            shutil.rmtree(work_root, ignore_errors=True)
        print(f'Готово: датасет {dataset_root}')
    else:
        print(f'Готово: {shards} шардов, каталог {run_dir}')


if __name__ == '__main__':
    main()

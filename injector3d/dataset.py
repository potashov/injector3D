"""Экспорт YOLO-pose датасета из готовых кадров и manifest.

Модуль работает без Blender и PIL: размер PNG читается из IHDR, метки
строятся из числовых полей manifest. Один кадр — одна строка:

    <class_id> <bbox_x> <bbox_y> <bbox_w> <bbox_h> <kpt_x1> <kpt_y1>

Координаты нормализованы по фактическому размеру итогового PNG:
X и ширина — по ширине, Y и высота — по высоте.
"""

import glob
import hashlib
import json
import math
import os
import re
import shutil
import struct
import time

from . import config as cfg


class DatasetError(ValueError):
    """Ошибка подготовки датасета."""


def dataset_params(overrides=None):
    params = dict(cfg.SCENE_DATASET)
    params.update(overrides or {})
    return params


def parse_split_ratios(text):
    """``0.8,0.1,0.1`` -> кортеж долей train/val/test."""
    if text is None:
        return tuple(cfg.SCENE_DATASET['split_ratios'])
    parts = [part.strip() for part in str(text).split(',') if part.strip()]
    if len(parts) != 3:
        raise DatasetError('--dataset-split: нужны три доли через запятую')
    try:
        ratios = tuple(float(part) for part in parts)
    except ValueError:
        raise DatasetError('--dataset-split: ожидались числа') from None
    if any(value < 0.0 for value in ratios):
        raise DatasetError('--dataset-split: доли не могут быть отрицательными')
    if abs(sum(ratios) - 1.0) > 1e-6:
        raise DatasetError('--dataset-split: сумма долей должна быть 1')
    return ratios


def class_id_for_volume_mm3(volume_mm3, params=None):
    """Класс уровня: 0.1 мл = 100 мм³, диапазон 0..200."""
    opts = params or cfg.SCENE_DATASET
    if volume_mm3 is None:
        raise DatasetError('не задан target_volume_mm3')
    step_mm3 = float(opts['class_step_ml']) * 1000.0
    level = float(volume_mm3) / step_mm3
    class_id = int(round(level))
    class_max = int(opts['class_max'])
    if class_id < 0 or class_id > class_max:
        raise DatasetError(
            f'объём {float(volume_mm3):g} мм³ вне классов 0..{class_max}')
    if (opts.get('require_volume_grid', True)
            and abs(level - class_id) > 1e-6):
        raise DatasetError(
            f'объём {float(volume_mm3):g} мм³ не кратен '
            f'{step_mm3:g} мм³ ({opts["class_step_ml"]:g} мл)')
    return class_id


def class_name(class_id, params=None):
    opts = params or cfg.SCENE_DATASET
    step_ml = float(opts['class_step_ml'])
    return f'{opts["class_prefix"]}_{class_id * step_ml:.1f}'


def png_size(path):
    """Ширина и высота PNG из IHDR без PIL."""
    try:
        with open(path, 'rb') as fh:
            head = fh.read(24)
    except OSError as exc:
        raise DatasetError(f'не читается PNG {path}: {exc}') from None
    if len(head) < 24 or head[:8] != b'\x89PNG\r\n\x1a\n':
        raise DatasetError(f'не PNG: {path}')
    if head[12:16] != b'IHDR':
        raise DatasetError(f'нет IHDR: {path}')
    width, height = struct.unpack('>II', head[16:24])
    if width <= 0 or height <= 0:
        raise DatasetError(f'некорректный размер PNG: {path}')
    return int(width), int(height)


def _target_volume(frame):
    value = frame.get('target_volume_mm3')
    if value is None:
        value = frame.get('volume_mm3')
    if value is None:
        raise DatasetError('в кадре нет target_volume_mm3/volume_mm3')
    return float(value)


def _border_edges_hit(meta, width, height, opts):
    if not meta.get('bbox_touches_border'):
        return []
    box = meta.get('bbox_final_px')
    if box is None:
        return ['unknown']
    try:
        x0, y0, x1, y1 = (float(value) for value in box)
    except (TypeError, ValueError):
        return ['unknown']
    final_w = float(meta.get('final_width_px') or width)
    final_h = float(meta.get('final_height_px') or height)
    edges = set(opts.get('reject_border_edges', ('left', 'right')))
    hit = []
    if 'left' in edges and x0 <= 0.0:
        hit.append('left')
    if 'right' in edges and x1 >= final_w - 1.0:
        hit.append('right')
    if 'top' in edges and y0 <= 0.0:
        hit.append('top')
    if 'bottom' in edges and y1 >= final_h - 1.0:
        hit.append('bottom')
    return hit


def label_line(meta, width, height, params=None, strict=True):
    """Строка разметки или ``None`` в нестрогом режиме."""
    opts = params or cfg.SCENE_DATASET
    try:
        class_id = class_id_for_volume_mm3(_target_volume(meta), opts)
        center = meta['bbox_center_px']
        box_w = float(meta['bbox_width_px'])
        box_h = float(meta['bbox_height_px'])
        keypoint = meta['ellipse_center_2d_px']
        if len(center) != 2 or len(keypoint) != 2:
            raise DatasetError('bbox_center_px/ellipse_center_2d_px: нужны 2 числа')
        values = (
            float(center[0]) / float(width),
            float(center[1]) / float(height),
            box_w / float(width),
            box_h / float(height),
            float(keypoint[0]) / float(width),
            float(keypoint[1]) / float(height),
        )
        if not all(math.isfinite(value) for value in values):
            raise DatasetError('в метке есть NaN/inf')
        if box_w <= 0.0 or box_h <= 0.0:
            raise DatasetError('bbox_width_px/bbox_height_px должны быть > 0')
        if any(value < -1e-6 or value > 1.0 + 1e-6 for value in values):
            raise DatasetError(f'координаты вне [0, 1]: {values}')
        if strict and opts.get('reject_border_bbox', True):
            edges = _border_edges_hit(meta, width, height, opts)
            if edges:
                raise DatasetError(
                    f'bbox касается запрещённой границы: {", ".join(edges)}')
            source = meta.get('bbox_source')
            if source != 'alpha':
                raise DatasetError(f'bbox_source={source!r}, ожидался alpha')
    except (KeyError, TypeError, ValueError, DatasetError) as exc:
        if strict:
            if isinstance(exc, DatasetError):
                raise
            raise DatasetError(f'неполные данные метки: {exc}') from None
        return None
    values = tuple(min(1.0, max(0.0, value)) for value in values)
    return f'{class_id} ' + ' '.join(f'{value:.6f}' for value in values)


def _safe_token(value, fallback='x'):
    if value is None:
        return fallback
    text = re.sub(r'[^A-Za-z0-9_.-]+', '-', str(value)).strip('-._')
    return text or fallback


def dataset_filename(index, class_id, target_volume_mm3, frame, manifest):
    volume = int(round(float(target_volume_mm3)))
    parts = [
        'ds',
        f'{int(index):07d}',
        f'{int(class_id):03d}',
        _safe_token(frame.get('liquid_type') or manifest.get('liquid_type')),
        f'v{volume:06d}',
        _safe_token(frame.get('camera_preset'), 'camera'),
        _safe_token(frame.get('variant'), 'frame'),
    ]
    tag = manifest.get('tag')
    if tag:
        parts.append(_safe_token(tag))
    return '_'.join(parts) + '.png'


def _frame_source_key(frame, manifest):
    payload = {
        'tag': manifest.get('tag'),
        'liquid_type': frame.get('liquid_type') or manifest.get('liquid_type'),
        'camera_preset': frame.get('camera_preset'),
        'variant': frame.get('variant'),
        'target_volume_mm3': _target_volume(frame),
        'azimuth_deg': frame.get('azimuth_deg'),
        'tilt_deg': frame.get('tilt_deg'),
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                     separators=(',', ':'))
    return hashlib.sha1(raw.encode('utf-8')).hexdigest()


def series_key(frame, manifest):
    volume = _target_volume(frame)
    parts = (
        manifest.get('tag') or '',
        frame.get('liquid_type') or manifest.get('liquid_type') or '',
        frame.get('camera_preset') or '',
        f'{volume:.6f}',
    )
    return '|'.join(str(part) for part in parts)


def split_for_series(key, ratios, seed=0):
    ratios = tuple(float(value) for value in ratios)
    if len(ratios) != 3 or abs(sum(ratios) - 1.0) > 1e-6:
        raise DatasetError('split_ratios: нужны три доли с суммой 1')
    digest = hashlib.sha1(f'{seed}:{key}'.encode('utf-8')).digest()
    value = int.from_bytes(digest[:8], 'big') / float(1 << 64)
    edge_train = ratios[0]
    edge_val = ratios[0] + ratios[1]
    if value < edge_train:
        return 'train'
    if value < edge_val:
        return 'val'
    return 'test'


def data_yaml_text(params=None):
    opts = params or cfg.SCENE_DATASET
    lines = ['kpt_shape:', '- 1', '- 2', 'names:']
    for class_id in range(int(opts['class_max']) + 1):
        lines.append(f'  {class_id}: {class_name(class_id, opts)}')
    lines += [
        'path: .',
        'train: ./train.txt',
        'val: ./val.txt',
        'test: ./test.txt',
    ]
    return '\n'.join(lines) + '\n'


def _read_manifest(path):
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise DatasetError(f'не читается manifest {path}: {exc}') from None
    if not isinstance(data, dict):
        raise DatasetError(f'{path}: ожидался JSON-объект')
    return data


def find_manifests(root, recursive=False):
    """Финальные manifest без шардовых ``_sNofM.json``."""
    pattern = (os.path.join(root, '**', '*_manifest*.json')
               if recursive else os.path.join(root, '*_manifest*.json'))
    paths = [
        path for path in glob.glob(pattern, recursive=recursive)
        if not re.search(r'_s\d+of\d+\.json$', path)
    ]
    return sorted(paths)


def find_final_manifest(run_dir):
    paths = find_manifests(run_dir)
    return paths[-1] if paths else None


def _atomic_write_text(path, text):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(text)
    os.replace(tmp, path)


def _atomic_copy(src, dst):
    tmp = dst + '.tmp'
    shutil.copyfile(src, tmp)
    os.replace(tmp, dst)


def _remove_dataset_files(root, opts):
    for name in (opts['images_dir'], opts['labels_dir']):
        path = os.path.join(root, name)
        if os.path.isdir(path):
            shutil.rmtree(path)
        elif os.path.exists(path):
            os.remove(path)
    for name in (opts['data_yaml'], opts['manifest'], *opts['split_files']):
        path = os.path.join(root, name)
        if os.path.exists(path):
            os.remove(path)


def _prepare_root(root, append, overwrite, opts):
    root = os.path.abspath(root)
    os.makedirs(root, exist_ok=True)
    allowed = {
        opts['images_dir'], opts['labels_dir'], opts['data_yaml'],
        opts['manifest'], *opts['split_files'],
    }
    entries = set(os.listdir(root))
    unexpected = entries - allowed
    if unexpected:
        raise DatasetError(
            f'{root}: посторонние файлы/каталоги: '
            f'{", ".join(sorted(unexpected))}')
    if overwrite:
        _remove_dataset_files(root, opts)
        entries = set()
    if entries and not append:
        raise DatasetError(
            f'{root}: датасет уже существует; нужен --dataset-append '
            f'или --dataset-overwrite')
    manifest_path = os.path.join(root, opts['manifest'])
    if append:
        if not os.path.exists(manifest_path):
            raise DatasetError(
                f'{root}: для --dataset-append нет {opts["manifest"]}')
        existing = _read_manifest(manifest_path)
    else:
        existing = None
    os.makedirs(os.path.join(root, opts['images_dir']), exist_ok=True)
    os.makedirs(os.path.join(root, opts['labels_dir']), exist_ok=True)
    return root, existing


def _check_compatible(existing, opts):
    if not existing:
        return
    for key, expected in (
            ('class_step_ml', float(opts['class_step_ml'])),
            ('class_max', int(opts['class_max'])),
            ('class_prefix', opts['class_prefix']),
            ('split_seed', int(opts['split_seed']))):
        if existing.get(key) != expected:
            raise DatasetError(
                f'{key} в датасете ({existing.get(key)!r}) не совпадает '
                f'с текущим ({expected!r})')
    ratios = [float(value) for value in opts['split_ratios']]
    if [float(value) for value in existing.get('split_ratios', [])] != ratios:
        raise DatasetError('split_ratios не совпадают с существующим датасетом')


def _existing_records(existing):
    records = list((existing or {}).get('frames') or [])
    by_source = {}
    for record in records:
        key = record.get('source_key')
        if key:
            by_source[key] = record
    next_index = max((int(record.get('index', 0)) for record in records),
                     default=0) + 1
    return records, by_source, next_index


def _collect_frames(manifest_paths, opts, ratios, seed):
    candidates = []
    seen_sources = set()
    for manifest_path in manifest_paths:
        manifest = _read_manifest(manifest_path)
        base = os.path.dirname(os.path.abspath(manifest_path))
        frames = manifest.get('frames') or []
        if not isinstance(frames, list):
            raise DatasetError(f'{manifest_path}: frames должен быть списком')
        for frame in frames:
            final = frame.get('final')
            if not final:
                raise DatasetError(f'{manifest_path}: у кадра нет final')
            image_path = os.path.join(base, final)
            if not os.path.exists(image_path):
                raise DatasetError(f'нет изображения: {image_path}')
            width, height = png_size(image_path)
            class_id = class_id_for_volume_mm3(_target_volume(frame), opts)
            line = label_line(frame, width, height, opts)
            source_key = _frame_source_key(frame, manifest)
            if source_key in seen_sources:
                continue
            seen_sources.add(source_key)
            split = split_for_series(
                series_key(frame, manifest), ratios, seed)
            candidates.append({
                'source_key': source_key,
                'source_manifest': os.path.basename(manifest_path),
                'source_image': final,
                'source_path': image_path,
                'frame': frame,
                'manifest': manifest,
                'class_id': class_id,
                'target_volume_mm3': _target_volume(frame),
                'label': line,
                'split': split,
                'width': width,
                'height': height,
            })
    candidates.sort(key=lambda item: (
        item['class_id'],
        str(item['frame'].get('liquid_type') or
            item['manifest'].get('liquid_type') or ''),
        str(item['frame'].get('camera_preset') or ''),
        item['target_volume_mm3'],
        float(item['frame'].get('tilt_deg') or 0.0),
        -1.0 if item['frame'].get('azimuth_deg') is None
        else float(item['frame'].get('azimuth_deg')),
        str(item['frame'].get('variant') or ''),
        item['source_key'],
    ))
    return candidates


def export_manifests(dataset_root, manifest_paths, append=False,
                     overwrite=False, split_ratios=None, split_seed=None,
                     params=None):
    """Собрать/дополнить плоский датасет из одного или нескольких manifest."""
    if append and overwrite:
        raise DatasetError('append и overwrite взаимоисключающие')
    opts = dataset_params(params)
    ratios = tuple(split_ratios or opts['split_ratios'])
    if abs(sum(ratios) - 1.0) > 1e-6 or any(v < 0 for v in ratios):
        raise DatasetError('split_ratios: нужны три неотрицательные доли с суммой 1')
    opts['split_ratios'] = ratios
    opts['split_seed'] = int(opts['split_seed'] if split_seed is None
                             else split_seed)
    if not manifest_paths:
        raise DatasetError('нет manifest для экспорта датасета')

    root, existing = _prepare_root(
        dataset_root, append, overwrite, opts)
    _check_compatible(existing, opts)
    records, by_source, next_index = _existing_records(existing)
    candidates = _collect_frames(manifest_paths, opts, ratios,
                                 opts['split_seed'])

    new_records = []
    for candidate in candidates:
        if candidate['source_key'] in by_source:
            continue
        index = next_index
        next_index += 1
        filename = dataset_filename(
            index, candidate['class_id'], candidate['target_volume_mm3'],
            candidate['frame'], candidate['manifest'])
        record = {
            'index': index,
            'source_key': candidate['source_key'],
            'source_manifest': candidate['source_manifest'],
            'source_image': candidate['source_image'],
            'image': filename,
            'label': os.path.splitext(filename)[0] + '.txt',
            'class_id': candidate['class_id'],
            'target_volume_mm3': candidate['target_volume_mm3'],
            'liquid_type': (candidate['frame'].get('liquid_type')
                            or candidate['manifest'].get('liquid_type')),
            'camera_preset': candidate['frame'].get('camera_preset'),
            'variant': candidate['frame'].get('variant'),
            'azimuth_deg': candidate['frame'].get('azimuth_deg'),
            'tilt_deg': candidate['frame'].get('tilt_deg'),
            'split': candidate['split'],
            'width_px': candidate['width'],
            'height_px': candidate['height'],
            'label_line': candidate['label'],
            'source_path': candidate['source_path'],
        }
        new_records.append(record)

    images_dir = os.path.join(root, opts['images_dir'])
    labels_dir = os.path.join(root, opts['labels_dir'])
    for record in new_records:
        dst_image = os.path.join(images_dir, record['image'])
        dst_label = os.path.join(labels_dir, record['label'])
        _atomic_copy(record.pop('source_path'), dst_image)
        _atomic_write_text(dst_label, record['label_line'] + '\n')

    all_records = sorted(records + new_records,
                         key=lambda item: int(item['index']))
    if not all_records:
        raise DatasetError('не найдено ни одного кадра для датасета')
    for split, filename in zip(
            ('train', 'val', 'test'), opts['split_files']):
        lines = [f"{opts['images_dir']}/{record['image']}"
                 for record in all_records if record.get('split') == split]
        _atomic_write_text(os.path.join(root, filename),
                           '\n'.join(lines) + ('\n' if lines else ''))

    _atomic_write_text(os.path.join(root, opts['data_yaml']),
                       data_yaml_text(opts))

    now = time.strftime('%Y-%m-%dT%H:%M:%S')
    created = (existing or {}).get('created_at', now)
    counts = {
        'images': len(all_records),
        'labels': len(all_records),
    }
    for split in ('train', 'val', 'test'):
        counts[split] = sum(1 for record in all_records
                            if record.get('split') == split)
    payload = {
        'version': 1,
        'created_at': created,
        'updated_at': now,
        'class_step_ml': float(opts['class_step_ml']),
        'class_max': int(opts['class_max']),
        'class_prefix': opts['class_prefix'],
        'split_ratios': list(ratios),
        'split_seed': int(opts['split_seed']),
        'counts': counts,
        'new_frames': len(new_records),
        'frames': all_records,
    }
    _atomic_write_text(os.path.join(root, opts['manifest']),
                       json.dumps(payload, ensure_ascii=False, indent=2) + '\n')
    return payload


def export_run(dataset_root, run_dir, recursive=False, **kwargs):
    manifests = find_manifests(run_dir, recursive=recursive)
    if not manifests:
        raise DatasetError(f'в {run_dir} нет финального manifest')
    return export_manifests(dataset_root, manifests, **kwargs)

"""Пути и имена файлов вывода.

Соглашение: один прогон = один каталог, названный по timestamp. Поэтому
timestamp больше не попадает в имена файлов, а номер пункта плана — ни в
пути, ни в имена: каталог уже отличает прогон от прогона, а тип жидкости
в начале имени отличает серию внутри прогона.

Каталог:
    <база>/<YYYYMMDD_HHMM>/, где база — --output-dir или config.OUTPUT_DIR
    <база>/<batch>/<series>/ — пакетный прогон render_batch.py

Имена:
    <жидкость>_<ракурс>[_<tag>][_v<объём>].png        — кадры
    <жидкость>_master[_<tag>][_s<k>of<M>].blend       — master серии
    <жидкость>_<ракурс>[_<tag>].blend                 — .blend на ракурс
    <жидкость>_manifest[_<tag>][_s<k>of<M>].json      — manifest серии
    <жидкость>_shard<k>.log                           — лог шарда
    <жидкость>[_<tag>].glb                            — экспорт GLB
    batch_manifest.json                               — сводка пакета
"""

import os
import time

from .config import DEFAULT_LIQUID_TYPE


def _stem(liquid_type):
    return liquid_type or DEFAULT_LIQUID_TYPE


def _join(*parts):
    # Каждая часть не должна добавлять своё подчёркивание: суффиксы вида
    # `_s0of2` пришли уже с разделителем, иначе имя получит `__`.
    return '_'.join(p.strip('_') for p in parts if p and p.strip('_'))


def new_timestamp():
    """Timestamp прогона: год, месяц, день, часы и минуты."""
    return time.strftime('%Y%m%d_%H%M')


def render_dir(base, timestamp):
    """Каталог прогона: `<база>/<timestamp>`."""
    return os.path.join(base, timestamp)


def render_filename(liquid_type, variant_name, tag=None, volume_mm3=None):
    """Имя PNG: `<жидкость>_<ракурс>[_<tag>][_v<объём>].png`."""
    volume = (f'v{int(round(float(volume_mm3)))}'
              if volume_mm3 is not None else None)
    return f'{_join(_stem(liquid_type), variant_name, tag, volume)}.png'


def master_blend_filename(liquid_type, tag=None, shard_suffix=''):
    """Имя master `.blend` серии."""
    return f'{_join(_stem(liquid_type), "master", tag, shard_suffix)}.blend'


def blend_filename(liquid_type, variant_name, tag=None):
    """Имя `.blend` одного ракурса (режим `render_tube`)."""
    return f'{_join(_stem(liquid_type), variant_name, tag)}.blend'


def manifest_filename(liquid_type, tag=None, shard_suffix=''):
    """Имя manifest серии."""
    return f'{_join(_stem(liquid_type), "manifest", tag, shard_suffix)}.json'


def shard_log_filename(liquid_type, shard_index):
    """Имя лога шарда для `render_parallel.py`."""
    return f'{_stem(liquid_type)}_shard{shard_index}.log'


def batch_manifest_filename():
    """Имя сводного manifest пакетного рендера."""
    return 'batch_manifest.json'


def glb_filename(liquid_type, tag=None):
    """Имя GLB-экспорта."""
    return f'{_join(_stem(liquid_type), tag)}.glb'


def ensure_dir(path):
    """Создаёт каталог, если его ещё нет, и возвращает путь."""
    os.makedirs(path, exist_ok=True)
    return path

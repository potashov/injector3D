#!/usr/bin/env python3
"""
injector3d — entry-point сборки сцены и рендера.

Собирает модули:
  - tube_geometry.create_tube()
  - materials.create_material_plastic() / create_material_liquid()
  - liquid.create_liquid()
  - scene.setup_scene()
  - render.render_tube() / render.render_ring()
  - валидация (габариты, замкнутость, объём)

Сцена всегда полная: жидкость и материалы подключаются в каждом прогоне.
Режим рендера выбирается явно (`--mode ring|tube|upright`), каталог
вывода — `--output-dir` (по умолчанию
`config.OUTPUT_DIR/<YYYYMMDD_HHMM>`).
"""

import sys
import os
import shutil
import tempfile

# Добавляем корень пакета в sys.path (при запуске `python create_scene.py`)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Импорт ПОСЛЕ изменения sys.path (bpy доступен только внутри Blender)
import bpy
import bmesh
import math
from mathutils import Matrix

from injector3d.tube_geometry import create_tube
from injector3d.materials import create_material_plastic, create_material_liquid
from injector3d.liquid import (
    create_liquid,
    build_liquid,
    capacity_mm3,
    meniscus_wall_rise_mm,
    target_volume_mm3,
)
from injector3d.scene import setup_scene, attach_camera_to_tube
from injector3d.render import render_tube, render_ring, tilt_matrix, export_scene_glb
from injector3d import config as cfg
from injector3d import dataset as dataset_info
from injector3d import output_manager as om
from injector3d import timing
from injector3d import graduation as grad
from injector3d.config import (
    INNER_DIAMETER,
    HEIGHT,
    WALL_THICKNESS,
    MM,
    RENDER_RESOLUTION,
    get_liquid_config,
    get_camera_config,
)

RENDER_MODES = ('ring', 'tube', 'upright')


def _parse_color(text):
    """``R,G,B[,A]`` (0..255) -> кортеж int."""
    parts = [int(x.strip()) for x in text.split(',')]
    if len(parts) not in (3, 4):
        raise ValueError('цвет: R,G,B или R,G,B,A')
    if any(v < 0 or v > 255 for v in parts):
        raise ValueError('компоненты цвета: 0..255')
    if len(parts) == 3:
        parts.append(255)
    return tuple(parts)


def parse_render_options(tail):
    """CLI-флаги постобработки и подписей (п. 5)."""
    postprocess = dict(cfg.SCENE_POSTPROCESS)
    overlay = dict(cfg.SCENE_OVERLAY)
    i = 0
    while i < len(tail):
        arg = tail[i]
        if arg == '--no-postprocess':
            postprocess['enabled'] = False
        elif arg == '--fill-background':
            postprocess['fill_background'] = True
        elif arg == '--no-fill-background':
            postprocess['fill_background'] = False
        elif arg == '--fill-color' and i + 1 < len(tail):
            postprocess['fill_color'] = _parse_color(tail[i + 1])
            i += 1
        elif arg == '--overlay-volume':
            overlay['volume'] = True
        elif arg == '--no-overlay-volume':
            overlay['volume'] = False
        elif arg == '--overlay-azimuth':
            overlay['azimuth'] = True
        elif arg == '--no-overlay-azimuth':
            overlay['azimuth'] = False
        elif arg == '--overlay-tilt':
            overlay['tilt_deg'] = True
        elif arg == '--no-overlay-tilt':
            overlay['tilt_deg'] = False
        elif arg in ('--overlay-ellipse', '--overlay-center'):
            overlay['ellipse_center'] = True
        elif arg in ('--no-overlay-ellipse', '--no-overlay-center'):
            overlay['ellipse_center'] = False
        elif arg == '--center-marker':
            overlay['center_marker'] = True
        elif arg == '--no-center-marker':
            overlay['center_marker'] = False
        elif arg == '--overlay-liquid':
            overlay['liquid'] = True
        elif arg == '--no-overlay-liquid':
            overlay['liquid'] = False
        elif arg in ('--overlay-bbox', '--bbox'):
            overlay['bbox'] = True
        elif arg in ('--no-overlay-bbox', '--no-bbox'):
            overlay['bbox'] = False
        elif arg == '--overlay-labels':
            overlay['labels'] = True
        elif arg == '--no-overlay-labels':
            overlay['labels'] = False
        i += 1
    return postprocess, overlay


def validate_tube(tube):
    """Валидация геометрии: габариты, замкнутость, объём, manifold.

    Все метрики считаются за один проход по bmesh: у `bpy.types.Mesh` в
    Blender 4.3 нет ни `link_faces` у рёбер, ни `bound_box`, поэтому
    работать приходится напрямую с вершинами/granями bmesh.

    Трубка имеет открытое отверстие сверху, поэтому рёбра верхней кромки
    легально имеют ровно 1 связанную грань (boundary).  Ошибкой считаются
    рёбра с 0 или 3+ гранями.
    """
    me = tube.data
    assert len(me.vertices) > 0, "Трубка: нет вершин"
    assert len(me.polygons) > 0, "Трубка: нет полигонов"
    me.calc_loop_triangles()

    bm = bmesh.new()
    bm.from_mesh(me)

    bad = [e for e in bm.edges if len(e.link_faces) not in (1, 2)]
    boundary = [e for e in bm.edges if len(e.link_faces) == 1]
    degenerate = [f for f in bm.faces if f.calc_area() <= 1e-15]

    # Верхняя кромка (z ≈ H) — единственное место с boundary-рёбрами
    z_top = HEIGHT * MM
    stray = [e for e in boundary
             if abs(e.verts[0].co.z - z_top) > 1e-6
             or abs(e.verts[1].co.z - z_top) > 1e-6]

    vol_mm3 = bm.calc_volume(signed=True) / (MM ** 3)
    normals_out = vol_mm3 > 0

    xs = [v.co.x for v in bm.verts]
    ys = [v.co.y for v in bm.verts]
    zs = [v.co.z for v in bm.verts]
    bm.free()

    diam_x = (max(xs) - min(xs)) / MM
    diam_y = (max(ys) - min(ys)) / MM
    z_mm = (max(zs) - min(zs)) / MM
    z_min = min(zs) / MM
    expected_diam = INNER_DIAMETER + WALL_THICKNESS * 2

    assert not bad, f"Трубка: {len(bad)} non-manifold рёбер"
    assert not stray, f"Трубка: {len(stray)} boundary-рёбер вне верхней кромки"
    assert not degenerate, f"Трубка: {len(degenerate)} вырожденных граней"
    assert normals_out, "Трубка: нормали направлены внутрь"
    assert abs(z_mm - HEIGHT) < 0.01, \
        f"Трубка: высота {z_mm:.3f}мм != {HEIGHT}мм"
    assert abs(z_min) < 0.01, f"Трубка: дно на z={z_min:.3f}мм, ожидалось 0"
    assert abs(diam_x - expected_diam) < 0.01, \
        f"Трубка: диаметр X {diam_x:.3f}мм != {expected_diam}мм"
    assert abs(diam_y - expected_diam) < 0.01, \
        f"Трубка: диаметр Y {diam_y:.3f}мм != {expected_diam}мм"

    # Ожидаемый объём материала — точная формула без учёта скруглений;
    # расхождение не должно превышать вклад самих галтелей.
    exp_mm3 = math.pi * (cfg.OUTER_RADIUS ** 2 * HEIGHT
                         - cfg.INNER_RADIUS ** 2
                         * (HEIGHT - cfg.BOTTOM_THICKNESS))
    drift = abs(vol_mm3 - exp_mm3) / exp_mm3
    assert drift < 0.01, \
        f"Трубка: объём {vol_mm3:.2f} мм³ отличается от расчётного " \
        f"{exp_mm3:.2f} мм³ на {drift * 100:.2f}%"

    print(f"  вершин={len(me.vertices)}  полигонов={len(me.polygons)}  "
          f"треугольников={len(me.loop_triangles)}")
    print(f"  рёбра: non-manifold={len(bad)}  "
          f"boundary(кромка)={len(boundary)}  вырожденных граней={len(degenerate)}")
    print(f"  габариты: ⌀{diam_x:.3f}×{diam_y:.3f}мм, высота {z_mm:.3f}мм")
    print(f"  объём материала: {vol_mm3:.2f} мм³ "
          f"(расчётно {exp_mm3:.2f}, отклонение {drift * 100:.2f}%)")
    return True


def _liquid_mesh_available(obj, info):
    """Есть ли реальный меш жидкости для рендера/проверки/GLB."""
    return bool(obj and obj.data and len(obj.data.vertices) > 0
                and info and not info.get('empty'))


def validate_liquid(liquid, target_mm3, info):
    """Валидация объёма жидкости: manifold, объём, положение поверхности.

    Свободная поверхность может быть плоской (fallback у дна/перелива),
    полной менисковой или смешанной (контакт на стенке и дне).  Для
    плоской и полной поверхности верхняя кромка горизонтальна; у
    смешанной верхний контур не горизонтален (сухая часть дна может быть
    выше кромки), поэтому там проверяется положение линии контакта.

    Пустой цилиндр (`target_mm3 <= 0`) и недоступный меш — допустимое
    состояние: проверка пропускается без исключения.
    """
    if not _liquid_mesh_available(liquid, info):
        print("  Жидкость: меша нет — проверка пропущена")
        return True

    me = liquid.data
    assert len(me.vertices) > 0, "Жидкость: пустой меш"
    assert len(me.polygons) > 0, "Жидкость: нет полигонов"

    bm = bmesh.new()
    bm.from_mesh(me)
    bad = [e for e in bm.edges if len(e.link_faces) != 2]
    degenerate = [f for f in bm.faces if f.calc_area() <= 1e-15]
    vol_mm3 = bm.calc_volume(signed=True) / (MM ** 3)

    mixed = bool(info.get('mixed'))
    if not mixed:
        zs = [v.co.z for v in bm.verts]
        z_top = max(zs)
        # Верхняя кромка (плоская крышка или кромка мениска) горизонтальна.
        top_zs = [z for z in zs if abs(z - z_top) < 1e-6]
        top_thickness_mm = (max(top_zs) - min(top_zs)) / MM
    bm.free()

    assert not bad, f"Жидкость: {len(bad)} non-manifold рёбер"
    assert not degenerate, f"Жидкость: {len(degenerate)} вырожденных граней"
    assert abs(info['volume_mm3'] - vol_mm3) < 0.05, \
        f"Жидкость: объём {vol_mm3:.2f} != посчитанный {info['volume_mm3']:.2f}"
    drift = abs(vol_mm3 - target_mm3) / target_mm3
    assert drift < 2e-4, \
        f"Жидкость: объём {vol_mm3:.2f} мм³ отличается от целевого " \
        f"{target_mm3:.2f} мм³ на {drift * 100:.3f}%"

    if mixed:
        contact_mm = info.get('contact_mm')
        bottom_max_mm = info.get('bottom_max_mm')
        bottom_min_mm = info.get('bottom_min_mm')
        assert contact_mm is not None and bottom_max_mm is not None, \
            "Жидкость: для смешанного мениска нет contact_mm/bottom_max_mm"
        assert not info.get('flat'), \
            "Жидкость: mixed-меш не должен быть плоским fallback"
        assert contact_mm <= bottom_max_mm + 1e-3, \
            f"Жидкость: кромка {contact_mm:.3f} мм выше дна " \
            f"{bottom_max_mm:.3f} мм — это не смешанный контакт"
        if bottom_min_mm is not None:
            assert contact_mm >= bottom_min_mm - 1e-3, \
                f"Жидкость: кромка {contact_mm:.3f} мм ниже дна " \
                f"{bottom_min_mm:.3f} мм"
    else:
        # Верх — либо плоскость на уровне (fallback), либо кромка мениска
        # на level + подъём у стенки.
        assert top_thickness_mm < 1e-4, \
            f"Жидкость: верхняя кромка не горизонтальна " \
            f"(толщина {top_thickness_mm} мм)"
        rise_mm = meniscus_wall_rise_mm(info.get('liquid_config'))
        offset_mm = z_top / MM - info['level_mm']
        assert min(abs(offset_mm), abs(offset_mm - rise_mm)) < 1e-3, \
            f"Жидкость: верх на {offset_mm:.3f} мм от уровня, " \
            f"ожидалось 0 или {rise_mm:.3f} мм (мениск)"

    print(f"  вершин={info['verts']}  полигонов={info['faces']}  "
          f"non-manifold={len(bad)}")
    print(f"  уровень (мировой z) = {info['level_mm']:.3f} мм, "
          f"объём = {vol_mm3:.2f} мм³ "
          f"(цель {target_mm3:.2f}, ошибка {info['error_mm3']:+.3f})")
    if mixed:
        print(f"  смешанный контакт: z = {contact_mm:.3f} мм "
              f"(дно {bottom_min_mm:.3f}…{bottom_max_mm:.3f} мм)")
    else:
        print(f"  верхняя кромка: z = {z_top / MM:.3f} мм "
              f"(подъём мениска {offset_mm:.3f} мм)")
    return True


def _dup_mesh_object(obj):
    """Дублирует объект с его мешем в новый bpy.data.object.

    `Mesh.copy()` переносит и слоты материалов, поэтому их не нужно
    копировать отдельно.
    """
    new_obj = bpy.data.objects.new(obj.name + "_dup", obj.data.copy())
    bpy.context.collection.objects.link(new_obj)
    new_obj.hide_render = True
    return new_obj


def _parse_cli(tail):
    """Разбирает хвост argv (после `--`) в параметры прогона.

    `argparse` здесь не подходит: Blender сам режет argv на `--`, а
    `--export-glb` принимает путь факультативно. Неизвестные флаги
    молча пропускаются — часть из них читает `parse_render_options()`
    из того же хвоста.

    Returns
    -------
    dict с ключами: mode, output_dir, n_tilt, angle_tilt, fill_fraction,
    volume_mm3, gpu_index, shard_index, shard_count, timestamp, tag,
    liquid_type, liquid_ior, liquid_density, surface_tension,
    contact_angle, absorption_density, camera_preset, camera_distance,
    camera_z, camera_target_z, camera_lens, camera_roll,
    camera_distortion, export_glb_path, timing.
    """
    mode = 'ring'
    output_dir = None

    n_tilt = None
    angle_tilt = None
    fill_fraction = None
    volume_mm3 = None
    gpu_index = None
    shard_index = 0
    shard_count = 1
    timestamp = None
    tag = None

    liquid_type = None
    liquid_ior = None
    liquid_density = None
    surface_tension = None
    contact_angle = None
    absorption_density = None

    camera_preset = None
    camera_distance = None
    camera_z = None
    camera_target_z = None
    camera_lens = None
    camera_roll = None
    camera_distortion = None

    export_glb_path = None
    timing_enabled = None
    graduation_enabled = None
    dataset_enabled = False
    dataset_append = False
    dataset_overwrite = False
    dataset_split = None
    dataset_seed = None
    keep_work = None

    i = 0
    while i < len(tail):
        arg = tail[i]
        if arg == '--no-timing':
            timing_enabled = False
            i += 1
            continue
        if arg == '--timing':
            timing_enabled = True
            i += 1
            continue
        if arg == '--no-graduation':
            graduation_enabled = False
            i += 1
            continue
        if arg == '--graduation':
            graduation_enabled = True
            i += 1
            continue
        if arg == '--dataset':
            dataset_enabled = True
            i += 1
            continue
        if arg == '--dataset-append':
            dataset_append = True
            i += 1
            continue
        if arg == '--dataset-overwrite':
            dataset_overwrite = True
            i += 1
            continue
        if arg == '--dataset-split' and i + 1 < len(tail):
            dataset_split = tail[i + 1]
            i += 2
            continue
        if arg == '--dataset-seed' and i + 1 < len(tail):
            dataset_seed = int(tail[i + 1])
            i += 2
            continue
        if arg == '--keep-work' and i + 1 < len(tail):
            keep_work = tail[i + 1]
            i += 2
            continue
        if arg == '--mode' and i + 1 < len(tail):
            mode = tail[i + 1].strip().lower()
            i += 2
            continue
        if arg == '--output-dir' and i + 1 < len(tail):
            output_dir = tail[i + 1]
            i += 2
            continue
        if arg == '--tilt-count' and i + 1 < len(tail):
            n_tilt = int(tail[i + 1])
            i += 2
            continue
        if arg == '--tilt-angle' and i + 1 < len(tail):
            angle_tilt = float(tail[i + 1])
            i += 2
            continue
        if arg == '--fill-fraction' and i + 1 < len(tail):
            fill_fraction = float(tail[i + 1])
            i += 2
            continue
        if arg == '--volume-mm3' and i + 1 < len(tail):
            volume_mm3 = float(tail[i + 1])
            i += 2
            continue
        if arg == '--gpu-index' and i + 1 < len(tail):
            gpu_index = int(tail[i + 1])
            i += 2
            continue
        if arg == '--shard-index' and i + 1 < len(tail):
            shard_index = int(tail[i + 1])
            i += 2
            continue
        if arg == '--shard-count' and i + 1 < len(tail):
            shard_count = int(tail[i + 1])
            i += 2
            continue
        if arg == '--timestamp' and i + 1 < len(tail):
            timestamp = tail[i + 1]
            i += 2
            continue
        if arg == '--tag' and i + 1 < len(tail):
            tag = tail[i + 1]
            i += 2
            continue
        if arg == '--liquid-type' and i + 1 < len(tail):
            liquid_type = tail[i + 1]
            i += 2
            continue
        if arg == '--liquid-ior' and i + 1 < len(tail):
            liquid_ior = float(tail[i + 1])
            i += 2
            continue
        if arg == '--liquid-density' and i + 1 < len(tail):
            liquid_density = float(tail[i + 1])
            i += 2
            continue
        if arg == '--surface-tension' and i + 1 < len(tail):
            surface_tension = float(tail[i + 1])
            i += 2
            continue
        if arg == '--contact-angle' and i + 1 < len(tail):
            contact_angle = float(tail[i + 1])
            i += 2
            continue
        if arg == '--absorption-density' and i + 1 < len(tail):
            absorption_density = float(tail[i + 1])
            i += 2
            continue
        if arg == '--camera-preset' and i + 1 < len(tail):
            camera_preset = tail[i + 1]
            i += 2
            continue
        if arg == '--camera-distance' and i + 1 < len(tail):
            camera_distance = float(tail[i + 1])
            i += 2
            continue
        if arg == '--camera-z' and i + 1 < len(tail):
            camera_z = float(tail[i + 1])
            i += 2
            continue
        if arg == '--camera-target-z' and i + 1 < len(tail):
            camera_target_z = float(tail[i + 1])
            i += 2
            continue
        if arg == '--camera-lens' and i + 1 < len(tail):
            camera_lens = float(tail[i + 1])
            i += 2
            continue
        if arg == '--camera-roll' and i + 1 < len(tail):
            camera_roll = float(tail[i + 1])
            i += 2
            continue
        if arg == '--camera-distortion' and i + 1 < len(tail):
            camera_distortion = float(tail[i + 1])
            i += 2
            continue
        if arg == '--export-glb':
            if i + 1 < len(tail) and not tail[i + 1].startswith('--'):
                export_glb_path = tail[i + 1]
                i += 2
            else:
                export_glb_path = True
                i += 1
            continue
        i += 1

    return {
        'mode': mode,
        'output_dir': output_dir,
        'n_tilt': n_tilt,
        'angle_tilt': angle_tilt,
        'fill_fraction': fill_fraction,
        'volume_mm3': volume_mm3,
        'gpu_index': gpu_index,
        'shard_index': shard_index,
        'shard_count': shard_count,
        'timestamp': timestamp,
        'tag': tag,
        'liquid_type': liquid_type,
        'liquid_ior': liquid_ior,
        'liquid_density': liquid_density,
        'surface_tension': surface_tension,
        'contact_angle': contact_angle,
        'absorption_density': absorption_density,
        'camera_preset': camera_preset,
        'camera_distance': camera_distance,
        'camera_z': camera_z,
        'camera_target_z': camera_target_z,
        'camera_lens': camera_lens,
        'camera_roll': camera_roll,
        'camera_distortion': camera_distortion,
        'export_glb_path': export_glb_path,
        'timing': timing_enabled,
        'graduation': graduation_enabled,
        'dataset': dataset_enabled,
        'dataset_append': dataset_append,
        'dataset_overwrite': dataset_overwrite,
        'dataset_split': dataset_split,
        'dataset_seed': dataset_seed,
        'keep_work': keep_work,
    }


def main():
    """Точка входа."""
    argv = sys.argv
    tail = []
    if "--" in argv:
        tail = argv[argv.index("--") + 1:]
    opts = _parse_cli(tail)
    mode = opts['mode']
    output_dir = opts['output_dir']
    n_tilt = opts['n_tilt']
    angle_tilt = opts['angle_tilt']
    fill_fraction = opts['fill_fraction']
    volume_mm3 = opts['volume_mm3']
    gpu_index = opts['gpu_index']
    shard_index = opts['shard_index']
    shard_count = opts['shard_count']
    timestamp = opts['timestamp']
    tag = opts['tag']
    liquid_type = opts['liquid_type']
    liquid_ior = opts['liquid_ior']
    liquid_density = opts['liquid_density']
    surface_tension = opts['surface_tension']
    contact_angle = opts['contact_angle']
    absorption_density = opts['absorption_density']
    camera_preset = opts['camera_preset']
    camera_distance = opts['camera_distance']
    camera_z = opts['camera_z']
    camera_target_z = opts['camera_target_z']
    camera_lens = opts['camera_lens']
    camera_roll = opts['camera_roll']
    camera_distortion = opts['camera_distortion']
    export_glb_path = opts['export_glb_path']
    timing_enabled = (cfg.TIMING_ENABLED if opts['timing'] is None
                      else opts['timing'])
    graduation_enabled = (bool(cfg.SCENE_GRADUATION['enabled'])
                          if opts['graduation'] is None
                          else opts['graduation'])
    dataset_enabled = opts['dataset']
    dataset_append = opts['dataset_append']
    dataset_overwrite = opts['dataset_overwrite']
    dataset_split = opts['dataset_split']
    dataset_seed = opts['dataset_seed']
    keep_work = opts['keep_work']

    postprocess, overlay = parse_render_options(tail)

    if mode not in RENDER_MODES:
        raise SystemExit(
            f'Ошибка: --mode должен быть одним из {", ".join(RENDER_MODES)}')

    dataset_ratios = None
    if dataset_enabled:
        if shard_count != 1:
            raise SystemExit(
                'Ошибка: --dataset требует --shard-count 1')
        if not postprocess.get('enabled', True):
            raise SystemExit(
                'Ошибка: --dataset требует включённую постобработку')
        if overlay.get('labels'):
            raise SystemExit(
                'Ошибка: --overlay-labels нельзя сочетать с --dataset')
        if dataset_append and dataset_overwrite:
            raise SystemExit(
                'Ошибка: --dataset-append и --dataset-overwrite '
                'взаимоисключающие')
        try:
            dataset_ratios = dataset_info.parse_split_ratios(dataset_split)
        except dataset_info.DatasetError as exc:
            raise SystemExit(f'Ошибка: {exc}') from None
        for key in ('volume', 'azimuth', 'tilt_deg', 'ellipse_center',
                    'center_marker', 'liquid', 'bbox', 'labels'):
            overlay[key] = False
        if graduation_enabled:
            print('  ! --dataset: градуировка отключена')
            graduation_enabled = False

    liquid_config = get_liquid_config(
        preset=liquid_type,
        ior=liquid_ior,
        density=liquid_density,
        surface_tension=surface_tension,
        contact_angle_deg=contact_angle,
        absorption_density=absorption_density,
    )
    camera_config = get_camera_config(
        preset=camera_preset,
        distance_mm=camera_distance,
        camera_z_mm=camera_z,
        target_z_mm=camera_target_z,
        lens_mm=camera_lens,
        roll_deg=camera_roll,
        distortion=camera_distortion,
    )

    if fill_fraction is not None and volume_mm3 is not None:
        raise SystemExit(
            'Ошибка: --fill-fraction и --volume-mm3 взаимоисключающие'
        )
    capacity = capacity_mm3()
    if volume_mm3 is not None:
        if volume_mm3 < 0.0:
            raise SystemExit('Ошибка: --volume-mm3 не может быть отрицательным')
        if volume_mm3 > capacity + 1e-9:
            raise SystemExit(
                f'Ошибка: объём {volume_mm3:.2f} мм³ больше вместимости '
                f'трубки {capacity:.2f} мм³'
            )
        target_mm3 = volume_mm3
        fill_fraction = volume_mm3 / capacity
    else:
        if fill_fraction is None:
            fill_fraction = cfg.FILL_FRACTION
        if not (0.0 <= fill_fraction <= 1.0):
            raise ValueError('fill-fraction должен быть в диапазоне [0, 1]')
        target_mm3 = target_volume_mm3(fill_fraction)
    if shard_count < 1 or not (0 <= shard_index < shard_count):
        raise ValueError('нужно 0 <= shard-index < shard-count')
    if dataset_enabled:
        try:
            dataset_info.class_id_for_volume_mm3(target_mm3)
        except dataset_info.DatasetError as exc:
            raise SystemExit(
                f'Ошибка: --dataset: {exc}; задайте объём, кратный '
                f'100 мм³ (0.1 мл), в пределах 0..20000 мм³') from None

    # Сцена всегда полная: жидкость и материалы собираются в каждом
    # прогоне. Режим выбирается явно — раньше его задавал номер пункта.
    # `upright` — алиас вырожденной ring-серии: только вертикальный кадр.
    is_upright = mode == 'upright'
    if is_upright and n_tilt not in (None, 0):
        raise SystemExit(
            'Ошибка: --mode upright несовместим с --tilt-count, '
            'отличным от 0')
    is_ring = mode in ('ring', 'upright')
    n = 0 if is_upright else (
        n_tilt if n_tilt is not None else cfg.TILT_AZIMUTH_COUNT)
    angle = angle_tilt if angle_tilt is not None else cfg.TILT_ANGLE_DEG
    if is_ring and n == 0 and shard_count > 1:
        raise SystemExit(
            'Ошибка: N=0 рендерит один кадр; --shard-count должен быть 1')
    if is_ring:
        try:
            cfg.tilt_ring_variants(n, angle)
        except ValueError as exc:
            raise SystemExit(f'Ошибка: {exc}') from None

    # Каталог прогона считается здесь один раз, чтобы кадры, master
    # `.blend`, manifest и GLB легли в него же (а не в разные папки).
    # `--output-dir` — это база: прогон всегда в подкаталоге по ts.
    # В dataset-режиме `--output-dir` — корень датасета, а рендер идёт
    # во временный work-dir, который после экспорта удаляется.
    ts = timestamp or om.new_timestamp()
    dataset_root = None
    work_root = None
    keep_dataset_work = False
    if dataset_enabled:
        dataset_root = os.path.abspath(output_dir or cfg.OUTPUT_DIR)
        if keep_work:
            work_root = os.path.abspath(keep_work)
            keep_dataset_work = True
            os.makedirs(work_root, exist_ok=True)
        else:
            work_root = tempfile.mkdtemp(prefix='injector3d_dataset_')
        run_dir = om.render_dir(work_root, ts)
    else:
        run_dir = om.render_dir(output_dir or cfg.OUTPUT_DIR, ts)
    timer = timing.Timer(enabled=timing_enabled, name=ts)

    if is_upright:
        mode_label = 'только вертикаль (upright)'
    elif is_ring:
        mode_label = 'N+1 (ring)' if n > 0 else 'N=0 (ring)'
    else:
        mode_label = '5 ракурсов (tube)'
    print(f"=== injector3D — {mode_label} + жидкость + материалы ===")
    print(f"    вывод: {run_dir}")
    if dataset_enabled:
        print(f"    датасет: {dataset_root}")
        print(f"    work-dir: {work_root}\n")
    else:
        print()

    # 1. Сцена
    print("1/5  Настройка сцены...")
    with timer.measure('scene_init'):
        camera = setup_scene(gpu_index=gpu_index,
                             camera_config=camera_config)
    print("   ✅ Сцена готова.\n")

    # 2. Геометрия
    print("2/5  Создание геометрии трубки...")
    with timer.measure('tube_build'):
        tube = create_tube("Tube")
        tube.data.name = "Tube_Mesh"
    print(f"   ✅ {tube.name}: ID={INNER_DIAMETER}мм, H={HEIGHT}мм, "
          f"W={WALL_THICKNESS}мм\n")

    # 3. Валидация
    print("3/5  Валидация геометрии...")
    with timer.measure('validate_tube'):
        validate_tube(tube)
    print("   ✅ Готово.\n")

    # 4. Материалы и жидкость
    print("4/5  Материалы и жидкость...")
    liquid = create_liquid("Liquid")
    on_tilt = None
    liquid_upright = None
    liquid_tilted = None
    info_up = None
    info_t = None
    if target_mm3 <= 0.0:
        print(f"   Вместимость полости: {capacity:.2f} мм³, "
              f"пустой цилиндр (V=0)")
    else:
        print(f"   Вместимость полости: {capacity:.2f} мм³, "
              f"цель {fill_fraction * 100:.0f}% = {target_mm3:.2f} мм³")

    with timer.measure('materials'):
        mat_plastic = create_material_plastic("Plastic")
        mat_liquid = create_material_liquid(
            "Liquid", liquid_config=liquid_config)
        tube.data.materials.append(mat_plastic)
        liquid.data.materials.append(mat_liquid)
    print(f"   ✅ Трубка: {tube.data.materials[0].name} "
          f"(IOR={cfg.IOR_PMMA}, transmission=1.0, roughness=0.0)")
    print(f"   ✅ Жидкость ({liquid_config.get('display_name', 'Liquid')}): "
          f"IOR={liquid_config['ior']}, "
          f"absorption density={liquid_config['absorption_density']}")

    # В ring-режиме строим два меша: upright и один наклонный.
    # Камера привязывается к трубке ЗДЕСЬ, на этапе вертикальной сцены,
    # поэтому в цикле рендера достаточно менять tube.matrix_world:
    # положение, наведение и ракурс камеры пересчитываются Blender'ом
    # сами, трубка и камера всегда остаются одной системой.
    if is_ring:
        if n > 0:
            print(f"   Серия наклонов: N={n}, θ={angle}°")
        else:
            print("   Вертикальный кадр: N=0, наклонный меш не строится")

        # 0) Трубка вертикальна -> привязка камеры. matrix_world трубки
        #    единична, поэтому matrix_parent_inverse тоже единичен и
        #    камера повторяет любой последующий поворот трубки.
        tube.matrix_world = Matrix.Identity(4)
        bpy.context.view_layer.update()
        with timer.measure('attach_camera'):
            attach_camera_to_tube(camera, tube)

        # 1) upright-меш: трубка вертикальна
        with timer.measure('liquid_upright'):
            info_up = build_liquid(liquid, tube, target_mm3, use_fast=True,
                                   liquid_config=liquid_config)
            if _liquid_mesh_available(liquid, info_up):
                liquid_upright = _dup_mesh_object(liquid)
                liquid_upright.name = "Liquid_upright"

        # 2) наклонный меш при φ = 0: наклон θ вокруг точки опоры.
        #    При N=0 наклонных кадров нет — меш не нужен.
        if n > 0:
            tube.matrix_world = tilt_matrix(math.radians(angle), 0.0)
            bpy.context.view_layer.update()
            with timer.measure('liquid_tilted'):
                info_t = build_liquid(liquid, tube, target_mm3, use_fast=True,
                                      liquid_config=liquid_config)
                if _liquid_mesh_available(liquid, info_t):
                    liquid_tilted = _dup_mesh_object(liquid)
                    liquid_tilted.name = "Liquid_tilted"

        # 3) Трубка обратно вертикально. Камера остаётся дочерней:
        #    в рендере меняем только tube.matrix_world.
        tube.matrix_world = Matrix.Identity(4)
        bpy.context.view_layer.update()

        # Исходный объект — промежуточная заготовка, в кадр не попадает
        liquid.hide_render = True
        liquid.hide_viewport = True

        if _liquid_mesh_available(liquid, info_up):
            print(f"   ✅ Upright: уровень {info_up['level_mm']:.3f} мм, "
                  f"V={info_up['volume_mm3']:.2f} мм³")
            if n > 0 and _liquid_mesh_available(liquid, info_t):
                print(f"   ✅ Tilted: уровень {info_t['level_mm']:.3f} мм, "
                      f"V={info_t['volume_mm3']:.2f} мм³")
        elif target_mm3 <= 0.0:
            label = "Upright/Tilted" if n > 0 else "Upright"
            print(f"   ✅ {label}: пустой цилиндр — меш жидкости "
                  "не создаётся")
        else:
            label = "Upright/Tilted" if n > 0 else "Upright"
            print(f"   ⚠  {label}: меш жидкости не создан — рендер "
                  "пойдёт без жидкости")
        with timer.measure('validate_liquid'):
            if _liquid_mesh_available(liquid, info_up):
                validate_liquid(liquid_upright, target_mm3, info_up)
            if n > 0 and _liquid_mesh_available(liquid, info_t):
                validate_liquid(liquid_tilted, target_mm3, info_t)
        if liquid_upright is not None and n > 0 and liquid_tilted is not None:
            print("   ✅ Жидкость: 2 меша (upright + наклонённый)\n")
        elif liquid_upright is not None:
            print("   ✅ Жидкость: 1 меш (upright)\n")
        else:
            print("   ✅ Жидкость: меша нет, сцена рендерится пустой\n")
    else:
        # Обычный рендер: перестраиваем жидкость на каждый наклон
        def on_tilt(variant_name, tilt_euler):
            info = build_liquid(liquid, tube, target_mm3,
                                liquid_config=liquid_config)
            if not _liquid_mesh_available(liquid, info):
                if target_mm3 <= 0.0:
                    print(f"     {variant_name:<9} наклон {tilt_euler} -> "
                          f"пустой цилиндр (V=0)")
                else:
                    print(f"     {variant_name:<9} наклон {tilt_euler} -> "
                          f"меш жидкости не создан")
                return info
            flag = "  ⚠ переливается" if info['spilled'] else ""
            print(f"     {variant_name:<9} наклон {tilt_euler} -> "
                  f"уровень {info['level_mm']:7.3f} мм, "
                  f"объём {info['volume_mm3']:9.2f} мм³ "
                  f"(Δ {info['error_mm3']:+.3f}){flag}")
            return info

        with timer.measure('liquid_initial'):
            info = on_tilt('upright', (0.0, 0.0, 0.0))
        if _liquid_mesh_available(liquid, info):
            if info['spilled']:
                print(f"   ⚠  Заполнение {fill_fraction * 100:.0f}% "
                      f"переливается через край: максимум "
                      f"{info['volume_mm3']:.2f} мм³")
            print(f"   ✅ Жидкость: уровень {info['level_mm']:.3f} мм, "
                  f"объём {info['volume_mm3']:.2f} мм³")
        elif target_mm3 <= 0.0:
            print("   ✅ Жидкость: пустой цилиндр — меш не создаётся")
        else:
            print("   ⚠  Жидкость: меш не создан — рендер пойдёт без неё")
        print(f"   ✅ Жидкость: {liquid.data.materials[0].name} "
              f"(IOR={liquid_config['ior']}, "
              f"absorption density={liquid_config['absorption_density']})")
        with timer.measure('validate_liquid'):
            if _liquid_mesh_available(liquid, info):
                validate_liquid(liquid, target_mm3, info)

    # 4b. Градуировка: риски и подписи объёма на стекле. Строится при
    #     вертикальной трубке (все будущие наклоны наследует через
    #     parent), поэтому вызов идёт после построения мешей жидкости.
    graduation_params = dict(cfg.SCENE_GRADUATION)
    graduation_params['enabled'] = graduation_enabled
    graduation_objects = []
    if graduation_params['enabled']:
        print("   Градуировка...")
        with timer.measure('graduation'):
            graduation_objects = grad.build_graduation(
                tube, params=graduation_params)
        print(f"   ✅ {grad.describe(params=graduation_params)}")
        print(f"   ✅ Разметка: {len(graduation_objects)} объект(а), "
              f"материал {graduation_objects[0].data.materials[0].name}")

    print("   ✅ Готово.\n")

    # 5. Экспорт GLB (если запрошен) — в каталог прогона
    if export_glb_path:
        if export_glb_path is True:
            om.ensure_dir(run_dir)
            export_glb_path = os.path.join(
                run_dir, om.glb_filename(liquid_type, tag))
        objs_to_export = [tube]
        if is_ring:
            if _liquid_mesh_available(liquid_upright, info_up):
                objs_to_export.append(liquid_upright)
        elif _liquid_mesh_available(liquid, info):
            objs_to_export.append(liquid)
        objs_to_export.extend(graduation_objects)
        with timer.measure('export_glb'):
            export_scene_glb(export_glb_path, objects=objs_to_export)

    # 6. Рендер
    if is_ring:
        if n > 0:
            print(f"5/5  Рендер N+1 ({n + 1} кадров, θ={angle}°)...")
        else:
            print("5/5  Рендер вертикального кадра...")
        render_ring(tube, liquid_upright, liquid_tilted,
                    output_dir=run_dir, n=n, angle=angle,
                    info_upright=info_up, info_tilted=info_t,
                    postprocess=postprocess, overlay=overlay,
                    shard=((shard_index, shard_count)
                           if shard_count > 1 else None),
                    tag=tag, liquid_type=liquid_config['name'],
                    timer=timer, target_volume_mm3=target_mm3)
    else:
        print("5/5  Рендер под 5 углами...")
        render_tube(tube, camera, output_dir=run_dir, on_tilt=on_tilt,
                    postprocess=postprocess, overlay=overlay,
                    tag=tag, liquid_type=liquid_config['name'],
                    timer=timer, target_volume_mm3=target_mm3)
    print("   ✅ Готово.\n")

    if dataset_enabled:
        try:
            summary = dataset_info.export_run(
                dataset_root, run_dir, append=dataset_append,
                overwrite=dataset_overwrite,
                split_ratios=dataset_ratios, split_seed=dataset_seed)
        except dataset_info.DatasetError as exc:
            print(f'  ! Датасет не собран: {exc}')
            print(f'  work-dir сохранён: {work_root}')
            raise SystemExit(1) from None
        counts = summary['counts']
        print(f"   Датасет: {dataset_root}")
        print(f"   Кадров: {counts['images']} "
              f"(train={counts['train']}, val={counts['val']}, "
              f"test={counts['test']})")
        if keep_dataset_work:
            print(f"   work-dir сохранён: {work_root}")
        else:
            shutil.rmtree(work_root, ignore_errors=True)
        print(f"=== Датасет готов: {dataset_root} ===")
    else:
        print(f"=== Прогон завершён: {run_dir} ===")


if __name__ == "__main__":
    main()

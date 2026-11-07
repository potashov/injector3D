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
Режим рендера выбирается явно (`--mode ring|tube`), каталог вывода —
`--output-dir` (по умолчанию `config.OUTPUT_DIR/<YYYYMMDD_HHMM>`).
"""

import sys
import os

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
from injector3d import output_manager as om
from injector3d.config import (
    INNER_DIAMETER,
    HEIGHT,
    WALL_THICKNESS,
    MM,
    RENDER_RESOLUTION,
    get_liquid_config,
    get_camera_config,
)

RENDER_MODES = ('ring', 'tube')


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


def validate_liquid(liquid, target_mm3, info):
    """Валидация объёма жидкости: manifold, объём, горизонтальность кромки.

    Свободная поверхность может быть плоской (fallback у дна/перелива)
    или менисковой, но её кромка/верх обязана лежать в горизонтальной
    мировой плоскости — поверхность не наклоняется вместе с трубкой.
    """
    me = liquid.data
    assert len(me.vertices) > 0, "Жидкость: пустой меш"
    assert len(me.polygons) > 0, "Жидкость: нет полигонов"

    bm = bmesh.new()
    bm.from_mesh(me)
    bad = [e for e in bm.edges if len(e.link_faces) != 2]
    degenerate = [f for f in bm.faces if f.calc_area() <= 1e-15]
    vol_mm3 = bm.calc_volume(signed=True) / (MM ** 3)

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
    assert top_thickness_mm < 1e-4, \
        f"Жидкость: верхняя кромка не горизонтальна (толщина {top_thickness_mm} мм)"

    # Верх — либо плоскость на уровне (fallback), либо кромка мениска
    # на level + подъём у стенки.
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


def main():
    """Точка входа."""
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

    argv = sys.argv
    tail = []
    if "--" in argv:
        tail = argv[argv.index("--") + 1:]
    i = 0
    while i < len(tail):
        arg = tail[i]
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
    postprocess, overlay = parse_render_options(tail)

    if mode not in RENDER_MODES:
        raise SystemExit(
            f'Ошибка: --mode должен быть одним из {", ".join(RENDER_MODES)}')

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
        if volume_mm3 <= 0.0:
            raise SystemExit('Ошибка: --volume-mm3 должен быть больше 0')
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
        if not (0.0 < fill_fraction <= 1.0):
            raise ValueError('fill-fraction должен быть в диапазоне (0, 1]')
        target_mm3 = target_volume_mm3(fill_fraction)
    if shard_count < 1 or not (0 <= shard_index < shard_count):
        raise ValueError('нужно 0 <= shard-index < shard-count')

    # Сцена всегда полная: жидкость и материалы собираются в каждом
    # прогоне. Режим выбирается явно — раньше его задавал номер пункта.
    is_ring = mode == 'ring'
    n = n_tilt if n_tilt is not None else cfg.TILT_AZIMUTH_COUNT
    angle = angle_tilt if angle_tilt is not None else cfg.TILT_ANGLE_DEG

    # Каталог прогона считается здесь один раз, чтобы кадры, master
    # `.blend`, manifest и GLB легли в него же (а не в разные папки).
    # `--output-dir` — это база: прогон всегда в подкаталоге по ts.
    ts = timestamp or om.new_timestamp()
    run_dir = om.render_dir(output_dir or cfg.OUTPUT_DIR, ts)

    print(f"=== injector3D — {'N+1 (ring)' if is_ring else '5 ракурсов (tube)'}"
          f" + жидкость + материалы ===")
    print(f"    вывод: {run_dir}\n")

    # 1. Сцена
    print("1/5  Настройка сцены...")
    camera = setup_scene(gpu_index=gpu_index, camera_config=camera_config)
    print("   ✅ Сцена готова.\n")

    # 2. Геометрия
    print("2/5  Создание геометрии трубки...")
    tube = create_tube("Tube")
    tube.data.name = "Tube_Mesh"
    print(f"   ✅ {tube.name}: ID={INNER_DIAMETER}мм, H={HEIGHT}мм, "
          f"W={WALL_THICKNESS}мм\n")

    # 3. Валидация
    print("3/5  Валидация геометрии...")
    validate_tube(tube)
    print("   ✅ Готово.\n")

    # 4. Материалы и жидкость
    print("4/5  Материалы и жидкость...")
    liquid = create_liquid("Liquid")
    on_tilt = None
    liquid_upright = None
    liquid_tilted = None
    print(f"   Вместимость полости: {capacity:.2f} мм³, "
          f"цель {fill_fraction * 100:.0f}% = {target_mm3:.2f} мм³")

    mat_plastic = create_material_plastic("Plastic")
    mat_liquid = create_material_liquid("Liquid", liquid_config=liquid_config)
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
        print(f"   Серия наклонов: N={n}, θ={angle}°")

        # 0) Трубка вертикальна -> привязка камеры. matrix_world трубки
        #    единична, поэтому matrix_parent_inverse тоже единичен и
        #    камера повторяет любой последующий поворот трубки.
        tube.matrix_world = Matrix.Identity(4)
        bpy.context.view_layer.update()
        attach_camera_to_tube(camera, tube)

        # 1) upright-меш: трубка вертикальна
        info_up = build_liquid(liquid, tube, target_mm3, use_fast=True,
                               liquid_config=liquid_config)
        liquid_upright = _dup_mesh_object(liquid)
        liquid_upright.name = "Liquid_upright"

        # 2) наклонный меш при φ = 0: наклон θ вокруг точки опоры
        tube.matrix_world = tilt_matrix(math.radians(angle), 0.0)
        bpy.context.view_layer.update()
        info_t = build_liquid(liquid, tube, target_mm3, use_fast=True,
                              liquid_config=liquid_config)
        liquid_tilted = _dup_mesh_object(liquid)
        liquid_tilted.name = "Liquid_tilted"

        # 3) Трубка обратно вертикально. Камера остаётся дочерней:
        #    в рендере меняем только tube.matrix_world.
        tube.matrix_world = Matrix.Identity(4)
        bpy.context.view_layer.update()

        # Исходный объект — промежуточная заготовка, в кадр не попадает
        liquid.hide_render = True
        liquid.hide_viewport = True

        print(f"   ✅ Upright: уровень {info_up['level_mm']:.3f} мм, "
              f"V={info_up['volume_mm3']:.2f} мм³")
        print(f"   ✅ Tilted: уровень {info_t['level_mm']:.3f} мм, "
              f"V={info_t['volume_mm3']:.2f} мм³")
        validate_liquid(liquid_upright, target_mm3, info_up)
        validate_liquid(liquid_tilted, target_mm3, info_t)
        print("   ✅ Жидкость: 2 меша (upright + наклонённый)\n")
    else:
        # Обычный рендер: перестраиваем жидкость на каждый наклон
        def on_tilt(variant_name, tilt_euler):
            info = build_liquid(liquid, tube, target_mm3,
                                liquid_config=liquid_config)
            flag = "  ⚠ переливается" if info['spilled'] else ""
            print(f"     {variant_name:<9} наклон {tilt_euler} -> "
                  f"уровень {info['level_mm']:7.3f} мм, "
                  f"объём {info['volume_mm3']:9.2f} мм³ "
                  f"(Δ {info['error_mm3']:+.3f}){flag}")
            return info

        info = on_tilt('upright', (0.0, 0.0, 0.0))
        if info['spilled']:
            print(f"   ⚠  Заполнение {fill_fraction * 100:.0f}% переливается "
                  f"через край: максимум {info['volume_mm3']:.2f} мм³")
        print(f"   ✅ Жидкость: уровень {info['level_mm']:.3f} мм, "
              f"объём {info['volume_mm3']:.2f} мм³")
        print(f"   ✅ Жидкость: {liquid.data.materials[0].name} "
              f"(IOR={liquid_config['ior']}, "
              f"absorption density={liquid_config['absorption_density']})")
        validate_liquid(liquid, target_mm3, info)
    print("   ✅ Готово.\n")

    # 5. Экспорт GLB (если запрошен) — в каталог прогона
    if export_glb_path:
        if export_glb_path is True:
            om.ensure_dir(run_dir)
            export_glb_path = os.path.join(
                run_dir, om.glb_filename(liquid_type, tag))
        objs_to_export = [tube]
        if is_ring:
            if liquid_upright:
                objs_to_export.append(liquid_upright)
        elif liquid:
            objs_to_export.append(liquid)
        export_scene_glb(export_glb_path, objects=objs_to_export)

    # 6. Рендер
    if is_ring:
        print(f"5/5  Рендер N+1 ({n + 1} кадров, θ={angle}°)...")
        render_ring(tube, liquid_upright, liquid_tilted,
                    output_dir=run_dir, n=n, angle=angle,
                    info_upright=info_up, info_tilted=info_t,
                    postprocess=postprocess, overlay=overlay,
                    shard=((shard_index, shard_count)
                           if shard_count > 1 else None),
                    tag=tag, liquid_type=liquid_config['name'])
    else:
        print("5/5  Рендер под 5 углами...")
        render_tube(tube, camera, output_dir=run_dir, on_tilt=on_tilt,
                    postprocess=postprocess, overlay=overlay,
                    tag=tag, liquid_type=liquid_config['name'])
    print("   ✅ Готово.\n")

    print(f"=== Прогон завершён: {run_dir} ===")


if __name__ == "__main__":
    main()

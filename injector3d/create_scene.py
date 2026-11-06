#!/usr/bin/env python3
"""
injector3d — entry-point для шага 1: геометрия трубки.

Собирает модули:
  - tube_geometry.create_tube()
  - materials.create_material_plastic() / create_material_liquid()
  - liquid.create_liquid()
  - scene.setup_scene()
  - render.render_tube()
  - валидация (габариты, замкнутость, объём)
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
from injector3d.render import render_tube, render_ring, tilt_matrix
from injector3d import config as cfg
from injector3d.config import (
    INNER_DIAMETER,
    HEIGHT,
    WALL_THICKNESS,
    MM,
    RENDER_RESOLUTION,
)


def parse_step(default="1"):
    """Номер пункта плана из `blender ... --python file.py -- <шаг>`.

    Номер попадает в имена файлов рендера, чтобы кадры разных пунктов
    плана не перезаписывали друг друга. Поддерживает целые и десятичные
    номера (например, ``4.5``).
    """
    argv = sys.argv
    if "--" in argv:
        tail = argv[argv.index("--") + 1:]
        if tail:
            s = tail[0].strip()
            if s.isdigit():
                return s
            parts = s.split('.')
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                return s
    return default


def _step_ge(step, n):
    """Сравнение шага с целым номером (``4.5 >= 4`` → True)."""
    try:
        return float(step) >= n
    except ValueError:
        return False


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
    rise_mm = meniscus_wall_rise_mm()
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


def main():
    """Точка входа."""
    step = parse_step()

    # Разбираем CLI-параметры пунктов 4.5 и 5
    n_tilt = None
    angle_tilt = None
    argv = sys.argv
    tail = []
    if "--" in argv:
        tail = argv[argv.index("--") + 1:]
    i = 0
    while i < len(tail):
        if tail[i] == '--tilt-count' and i + 1 < len(tail):
            n_tilt = int(tail[i + 1])
            i += 2
            continue
        if tail[i] == '--tilt-angle' and i + 1 < len(tail):
            angle_tilt = float(tail[i + 1])
            i += 2
            continue
        i += 1
    postprocess, overlay = parse_render_options(tail)

    # Жидкость с горизонтальным уровнем появляется с пункта 2, материалы —
    # с пункта 4: раньше рендер показывал бы то, чего на стадии ещё нет.
    with_liquid = _step_ge(step, 2)
    with_materials = _step_ge(step, 4)
    is_ring = step == '4.5'
    print(f"=== injector3D — Пункт {step}"
          f"{' + жидкость' if with_liquid else ''}"
          f"{' + материалы' if with_materials else ''}"
          f"{' N+1' if is_ring else ''} ===\n")

    # 1. Сцена
    print("1/5  Настройка сцены...")
    camera = setup_scene()
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
    liquid = None
    on_tilt = None
    liquid_upright = None
    liquid_tilted = None
    if not with_liquid:
        print("   ⏭  Пропущено: жидкость подключается с пункта 2")
    else:
        fill_fraction = cfg.FILL_FRACTION
        target_mm3 = target_volume_mm3(fill_fraction)
        liquid = create_liquid("Liquid")
        print(f"   Вместимость полости: {capacity_mm3():.2f} мм³, "
              f"цель {fill_fraction * 100:.0f}% = {target_mm3:.2f} мм³")

        if with_materials:
            mat_plastic = create_material_plastic("Plastic")
            mat_liquid = create_material_liquid("Liquid")
            tube.data.materials.append(mat_plastic)
            liquid.data.materials.append(mat_liquid)
            print(f"   ✅ Трубка: {tube.data.materials[0].name} "
                  f"(IOR={cfg.IOR_PMMA}, transmission=1.0, roughness=0.0)")
        else:
            print("   ⏭  Материалы: подключаются с пункта 4")

        # Для пункта 4.5 строим два меша: upright и один наклонный.
        # Камера привязывается к трубке ЗДЕСЬ, на этапе вертикальной сцены,
        # поэтому в цикле рендера достаточно менять tube.matrix_world:
        # положение, наведение и ракурс камеры пересчитываются Blender'ом
        # сами, трубка и камера всегда остаются одной системой.
        if is_ring:
            n = n_tilt if n_tilt is not None else cfg.TILT_AZIMUTH_COUNT
            angle = angle_tilt if angle_tilt is not None else cfg.TILT_ANGLE_DEG
            print(f"   Серия наклонов: N={n}, θ={angle}°")

            # 0) Трубка вертикальна -> привязка камеры. matrix_world трубки
            #    единична, поэтому matrix_parent_inverse тоже единичен и
            #    камера повторяет любой последующий поворот трубки.
            tube.matrix_world = Matrix.Identity(4)
            bpy.context.view_layer.update()
            attach_camera_to_tube(camera, tube)

            # 1) upright-меш: трубка вертикальна
            info_up = build_liquid(liquid, tube, target_mm3, use_fast=True)
            liquid_upright = _dup_mesh_object(liquid)
            liquid_upright.name = "Liquid_upright"

            # 2) наклонный меш при φ = 0: наклон θ вокруг точки опоры
            tube.matrix_world = tilt_matrix(math.radians(angle), 0.0)
            bpy.context.view_layer.update()
            info_t = build_liquid(liquid, tube, target_mm3, use_fast=True)
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
                info = build_liquid(liquid, tube, target_mm3)
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
            if with_materials:
                print(f"   ✅ Жидкость: {liquid.data.materials[0].name} "
                      "(IOR=1.33, absorption density=20.0)")
            validate_liquid(liquid, target_mm3, info)
    print("   ✅ Готово.\n")

    # 5. Рендер
    if is_ring:
        n = n_tilt if n_tilt is not None else cfg.TILT_AZIMUTH_COUNT
        angle = angle_tilt if angle_tilt is not None else cfg.TILT_ANGLE_DEG
        print(f"5/5  Рендер N+1 ({n + 1} кадров, θ={angle}°)...")
        render_ring(tube, liquid_upright, liquid_tilted,
                    step=step, n=n, angle=angle,
                    info_upright=info_up, info_tilted=info_t,
                    postprocess=postprocess, overlay=overlay)
    else:
        print(f"5/5  Рендер под 5 углами (пункт {step})...")
        render_tube(tube, camera, step=step, on_tilt=on_tilt,
                    postprocess=postprocess, overlay=overlay)
    print("   ✅ Готово.\n")

    print(f"=== Пункт {step} завершён ===")


def _dup_mesh_object(obj):
    """Дублирует объект с его мешем в новый bpy.data.object.

    `Mesh.copy()` переносит и слоты материалов, поэтому их не нужно
    копировать отдельно.
    """
    new_obj = bpy.data.objects.new(obj.name + "_dup", obj.data.copy())
    bpy.context.collection.objects.link(new_obj)
    new_obj.hide_render = True
    return new_obj


if __name__ == "__main__":
    main()

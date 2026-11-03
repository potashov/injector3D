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

from injector3d.tube_geometry import create_tube
from injector3d.materials import create_material_plastic, create_material_liquid
from injector3d.liquid import (
    create_liquid,
    build_liquid,
    capacity_mm3,
    target_volume_mm3,
)
from injector3d.scene import setup_scene
from injector3d.render import render_tube
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
    плана не перезаписывали друг друга.
    """
    argv = sys.argv
    if "--" in argv:
        tail = argv[argv.index("--") + 1:]
        if tail and tail[0].strip().isdigit():
            return tail[0].strip()
    return default


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
    """Валидация объёма жидкости: manifold, объём, горизонтальность верха.

    Свободная поверхность обязана быть одной плоской гранью с нормалью
    вдоль мировой +Z — это и есть физический горизонт.
    """
    me = liquid.data
    assert len(me.vertices) > 0, "Жидкость: пустой меш"
    assert len(me.polygons) > 0, "Жидкость: нет полигонов"

    bm = bmesh.new()
    bm.from_mesh(me)
    bad = [e for e in bm.edges if len(e.link_faces) != 2]
    degenerate = [f for f in bm.faces if f.calc_area() <= 1e-15]
    vol_mm3 = bm.calc_volume(signed=True) / (MM ** 3)

    top = max(bm.faces, key=lambda f: f.calc_center_median().z)
    top_normal_z = abs(top.normal.z)
    # Все вершины свободной поверхности обязаны лежать на одном уровне —
    # иначе это не горизонт, а наклонённый торец.
    zs = sorted(loop.vert.co.z for loop in top.loops)
    top_thickness_mm = (zs[-1] - zs[0]) / MM
    bm.free()

    assert not bad, f"Жидкость: {len(bad)} non-manifold рёбер"
    assert not degenerate, f"Жидкость: {len(degenerate)} вырожденных граней"
    assert top_normal_z > 0.999, \
        f"Жидкость: верхняя грань не горизонтальна (n.z={top_normal_z:.4f})"
    assert abs(info['volume_mm3'] - vol_mm3) < 0.05, \
        f"Жидкость: объём {vol_mm3:.2f} != посчитанный {info['volume_mm3']:.2f}"
    drift = abs(vol_mm3 - target_mm3) / target_mm3
    assert drift < 2e-4, \
        f"Жидкость: объём {vol_mm3:.2f} мм³ отличается от целевого " \
        f"{target_mm3:.2f} мм³ на {drift * 100:.3f}%"
    assert top_thickness_mm < 1e-6, \
        f"Жидкость: верхняя грань не плоская (толщина {top_thickness_mm} мм)"

    print(f"  вершин={info['verts']}  полигонов={info['faces']}  "
          f"non-manifold={len(bad)}")
    print(f"  уровень (мировой z) = {info['level_mm']:.3f} мм, "
          f"объём = {vol_mm3:.2f} мм³ "
          f"(цель {target_mm3:.2f}, ошибка {info['error_mm3']:+.3f})")
    return True


def main():
    """Точка входа."""
    step = parse_step()
    # Жидкость с горизонтальным уровнем появляется с пункта 2, материалы —
    # с пункта 4: раньше рендер показывал бы то, чего на стадии ещё нет.
    step_n = int(step)
    with_liquid = step_n >= 2
    with_materials = step_n >= 4
    print(f"=== injector3D — Пункт {step}"
          f"{' + жидкость' if with_liquid else ''}"
          f"{' + материалы' if with_materials else ''} ===\n")

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

        # Меш жидкости строится в мировых координатах от текущей ориентации
        # трубки, поэтому его нужно перестраивать после каждого наклона.
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
                  "(IOR=1.33, absorption density=0.5)")
        validate_liquid(liquid, target_mm3, info)
    print("   ✅ Готово.\n")

    # 5. Рендер
    print(f"5/5  Рендер под 5 углами (пункт {step})...")
    render_tube(tube, camera, step=step, on_tilt=on_tilt)
    print("   ✅ Готово.\n")

    print(f"=== Пункт {step} завершён ===")


if __name__ == "__main__":
    main()

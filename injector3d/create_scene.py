#!/usr/bin/env python3
"""
injector3d — entry-point для шага 1: геометрия трубки.

Собирает модули:
  - tube_geometry.create_tube()
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


def main():
    """Точка входа."""
    print("=== injector3D — Шаг 1: Геометрия трубки ===\n")

    # 1. Сцена
    print("1/4  Настройка сцены...")
    camera = setup_scene()
    print("   ✅ Сцена готова.\n")

    # 2. Геометрия
    print("2/4  Создание геометрии трубки...")
    tube = create_tube("Tube")
    tube.data.name = "Tube_Mesh"
    print(f"   ✅ {tube.name}: ID={INNER_DIAMETER}мм, H={HEIGHT}мм, "
          f"W={WALL_THICKNESS}мм\n")

    # 3. Валидация
    print("3/4  Валидация...")
    validate_tube(tube)
    print("   ✅ Готово.\n")

    # 4. Рендер
    print("4/4  Рендер под 5 углами...")
    render_tube(tube, camera, step='1')
    print("   ✅ Готово.\n")

    print("=== Шаг 1 завершён ===")


if __name__ == "__main__":
    main()

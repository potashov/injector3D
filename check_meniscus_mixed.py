"""Проверки прототипа смешанного мениска.

Запуск внутри Blender:
    blender --background --python check_meniscus_mixed.py

Проверяется clipped-прототип: осесимметричный мениск, обрезанный дном.
2D-PDE-солвер (`method='pde'`) здесь не проверяется — он оставлен
экспериментальным.
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mathutils import Euler, Vector

from injector3d.config import (
    CONTACT_ANGLE_DEG,
    INNER_RADIUS,
    LIQUID_DENSITY,
    SURFACE_TENSION,
)
from injector3d.meniscus_mixed import (
    LIQUID_RADIUS,
    ClippedMeniscusSolver,
    build_mixed_meniscus,
)
from injector3d.render import tilt_matrix

OK = True


def check(name, value, expected, tol):
    global OK
    good = abs(value - expected) <= tol
    OK &= good
    print(f"  {'OK ' if good else 'FAIL'} {name}: {value:.6f} "
          f"(ожидалось {expected:.6f} ± {tol:g})")


def check_true(name, value):
    global OK
    OK &= bool(value)
    print(f"  {'OK ' if value else 'FAIL'} {name}: {value}")


def _mw(angle, azimuth=270.0):
    return tilt_matrix(math.radians(angle), azimuth)


def _independent_volume(matrix_world, level_mm, nr=500, nt=512,
                        liquid_config=None):
    solver = ClippedMeniscusSolver(matrix_world, liquid_config,
                                  volume_nodes=nr, volume_angles=nt)
    return solver.volume_at_level(level_mm)


def test_2000_50():
    print("--- 2000 мм³ @ 50° ---")
    mw = _mw(50.0)
    res = build_mixed_meniscus(mw, 2000.0, return_bmesh=True)
    check("объём", res['volume_mm3'], 2000.0, 0.05)
    check_true("смешанный контакт", res['mixed'])
    check_true("не перелив", not res['spilled'])
    check_true("не плоский fallback", not res['flat'])
    check_true("contact < bottom_max",
               res['contact_mm'] < res['bottom_max_mm'] + 1e-9)

    vol_ind = _independent_volume(mw, res['level_mm'])
    check("независимый объём", vol_ind, 2000.0, 0.05)

    bm = res['bmesh']
    print(f"  меш: verts={len(bm.verts)} faces={len(bm.faces)} "
          f"edges={len(bm.edges)}")
    check_true("меш не пуст", len(bm.faces) > 0)
    manifold = sum(1 for e in bm.edges if len(e.link_faces) == 2)
    boundary = sum(1 for e in bm.edges if len(e.link_faces) == 1)
    loose = sum(1 for e in bm.edges if len(e.link_faces) == 0)
    check_true("нет висячих рёбер", loose == 0)
    check_true("manifold-рёбра", manifold == len(bm.edges) - boundary)
    check_true("граница только по кромке", boundary == 192)

    inv = mw.inverted()
    bad = 0
    for e in bm.edges:
        if len(e.link_faces) == 1:
            for v in e.verts:
                loc = inv @ v.co
                r = math.hypot(loc.x, loc.y) / 0.001
                if abs(r - LIQUID_RADIUS) > 1e-3:
                    bad += 1
    check_true("граничные вершины на r=R", bad == 0)
    nz = sum(f.normal.z for f in bm.faces)
    check_true("нормали крышки вверх", nz > 0)
    bm.free()


def test_closed_2000_50():
    print("--- замкнутый меш 2000 мм³ @ 50° ---")
    mw = _mw(50.0)
    res = build_mixed_meniscus(mw, 2000.0, return_bmesh=True, closed=True)
    check("объём меша", res['volume_mm3'], 2000.0, 0.05)
    check_true("смешанный контакт", res['mixed'])
    check_true("не перелив", not res['spilled'])
    check_true("не плоский fallback", not res['flat'])

    bm = res['bmesh']
    print(f"  меш: verts={len(bm.verts)} faces={len(bm.faces)} "
          f"edges={len(bm.edges)}")
    manifold = sum(1 for e in bm.edges if len(e.link_faces) == 2)
    boundary = sum(1 for e in bm.edges if len(e.link_faces) == 1)
    loose = sum(1 for e in bm.edges if len(e.link_faces) == 0)
    degenerate = sum(1 for f in bm.faces if f.calc_area() <= 1e-15)
    check_true("нет граничных рёбер", boundary == 0)
    check_true("нет висячих рёбер", loose == 0)
    check_true("все рёбра manifold", manifold == len(bm.edges))
    check_true("нет вырожденных граней", degenerate == 0)
    check("объём bmesh", bm.calc_volume(signed=True) / (0.001 ** 3),
          res['volume_mm3'], 0.05)
    bm.free()


def _mesh_points(res):
    bm = res['bmesh']
    pts = sorted((round(v.co.x / 0.001, 5), round(v.co.y / 0.001, 5),
                  round(v.co.z / 0.001, 5)) for v in bm.verts)
    bm.free()
    return pts


def test_azimuth_rotation():
    print("--- поворот mixed-меша по азимуту (2000 мм³ @ 50°) ---")
    base = build_mixed_meniscus(_mw(50.0, 0.0), 2000.0,
                                return_bmesh=True, closed=True)
    base_pts = _mesh_points(base)
    for phi in (90.0, 180.0, 270.0):
        res = build_mixed_meniscus(_mw(50.0, phi), 2000.0,
                                   return_bmesh=True, closed=True)
        got = _mesh_points(res)
        rot = Euler((0.0, 0.0, math.radians(phi))).to_matrix()
        rotated = sorted(tuple(round(c / 0.001, 5)
                               for c in (rot @ Vector((x * 0.001,
                                                       y * 0.001,
                                                       z * 0.001))))
                         for x, y, z in base_pts)
        same = len(rotated) == len(got) and all(
            all(abs(a - b) < 1e-4 for a, b in zip(p, q))
            for p, q in zip(rotated, got))
        check_true(f"Rz({phi:g}°)·меш(φ=0) == меш(φ={phi:g})", same)


def test_full_contact():
    print("--- 3000 мм³ @ 50° (полный контакт) ---")
    res = build_mixed_meniscus(_mw(50.0), 3000.0, return_bmesh=False)
    check("объём", res['volume_mm3'], 3000.0, 0.05)
    check_true("не смешанный", not res['mixed'])
    check_true("не перелив", not res['spilled'])


def test_sweep():
    print("--- sweep 1800..2300 @ 50° ---")
    prev_level = None
    for target in (1800.0, 1900.0, 2000.0, 2100.0, 2200.0, 2300.0):
        res = build_mixed_meniscus(_mw(50.0), target, return_bmesh=False)
        check(f"V({target:g})", res['volume_mm3'], target, 0.05)
        if prev_level is not None:
            check_true(f"L растёт ({target:g})", res['level_mm'] > prev_level)
            check_true(f"нет скачка L ({target:g})",
                       res['level_mm'] - prev_level < 0.5)
        prev_level = res['level_mm']


def test_transition():
    print("--- переход смешанного контакта при 2000 мм³ ---")
    expected = {30.0: False, 40.0: False, 50.0: True,
                60.0: True, 70.0: True}
    for angle, mixed in expected.items():
        res = build_mixed_meniscus(_mw(angle), 2000.0, return_bmesh=False)
        check(f"V({angle:g}°)", res['volume_mm3'], 2000.0, 0.05)
        check_true(f"mixed={mixed} при {angle:g}°",
                   res['mixed'] == mixed)


def test_presets():
    print("--- пресеты жидкостей @ 2000 мм³, 50° ---")
    presets = {
        'water': (SURFACE_TENSION, LIQUID_DENSITY, CONTACT_ANGLE_DEG),
        'glycerol': (0.0634, 1261.0, 45.0),
        'ethanol': (0.0223, 789.0, 5.0),
        'mineral_oil': (0.0300, 850.0, 35.0),
    }
    for name, props in presets.items():
        cfg = {
            'surface_tension': props[0],
            'density': props[1],
            'contact_angle_deg': props[2],
        }
        res = build_mixed_meniscus(_mw(50.0), 2000.0, liquid_config=cfg,
                                   return_bmesh=False)
        check(f"V({name})", res['volume_mm3'], 2000.0, 0.05)
        check_true(f"mixed({name})", res['mixed'])


def main():
    print("Проверка смешанного мениска (clipped-прототип)")
    print(f"R={INNER_RADIUS:.2f} мм, liquid R={LIQUID_RADIUS:.2f} мм")
    test_2000_50()
    test_closed_2000_50()
    test_azimuth_rotation()
    test_full_contact()
    test_sweep()
    test_transition()
    test_presets()
    print("\n" + ("ВСЕ ПРОВЕРКИ СМЕШАННОГО МЕНИСКА ПРОЙДЕНЫ"
                  if OK else "ЕСТЬ ОШИБКИ"))
    return 0 if OK else 1


if __name__ == '__main__':
    sys.exit(main())

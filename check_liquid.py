"""Числовая самопроверка пункта 2: объём жидкости при наклоне.

Запуск внутри Blender:
    blender --background --python check_liquid.py

Опорное аналитическое тождество.  Полость — цилиндр радиуса r; горизонт
в мировых координатах — это плоскость, наклонённая на угол θ к сечению
цилиндра и пересекающая его ось в точке h₀.  Высота плоскости над точкой
(x, y) сечения равна h₀ + x·tgθ, а ∫x dA по кругу = 0, поэтому

    V(θ) = ∮ (h₀ + x·tgθ) dA = π r² h₀

— объём от угла не зависит.  Отсюда два проверяемых следствия:
  1. бисекция возвращает один и тот же объём для всех θ (что и печатает
     create_scene.py), и одно и то же положение горизонта вдоль оси;
  2. мировой z этого горизонта = h₀·cos θ (ось наклонена на θ, начало
     трубки в мировой высоте 0).
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mathutils import Euler

from injector3d.liquid import (
    CLEARANCE,
    capacity_mm3,
    solve_level_for_volume,
    target_volume_mm3,
    volume_at_level,
)
from injector3d.config import (
    MM,
    INNER_RADIUS,
    HEIGHT,
    BOTTOM_THICKNESS,
    REVOLVE_SEGMENTS,
)


def matrix_for(tilt_deg_x=0.0, tilt_deg_y=0.0):
    """Мировая матрица трубки при наклоне вокруг её нижнего торца."""
    return Euler((math.radians(tilt_deg_x), math.radians(tilt_deg_y), 0.0)
                 ).to_matrix().to_4x4()


def axis_level_mm(level_mm, tilt_deg):
    """Мировой уровень -> положение того же горизонта вдоль оси трубки."""
    return level_mm / math.cos(math.radians(tilt_deg))


def main():
    ok = True

    def check(name, got, expected, tol):
        nonlocal ok
        drift = abs(got - expected)
        good = drift <= tol
        ok &= good
        print(f"  {'OK ' if good else 'FAIL'} {name}: "
              f"{got:.4f} (ожидалось {expected:.4f}, Δ {drift:.4f} ≤ {tol})")

    # 1. Вместимость: π r² (H - дно)
    print("1. Вместимость полости")
    check("V_max, мм³", capacity_mm3(),
          math.pi * INNER_RADIUS ** 2 * (HEIGHT - BOTTOM_THICKNESS), 1e-9)

    # 2. Объём как функция уровня монотонен и совпадает с формулой π r² h
    #    (пока горизонт не задел дно и не перелился через край).
    print("\n2. Вертикальная трубка: V = π r² h")
    r_eff = INNER_RADIUS - CLEARANCE
    for h in (10.0, 25.0, 50.0, 75.0):
        v = volume_at_level(matrix_for(), h)
        check(f"V(h={h:g})", v,
              math.pi * r_eff ** 2 * (h - BOTTOM_THICKNESS - CLEARANCE), 0.02)

    # 3. Наклон: объём не зависит от угла, мировой уровень = h₀·cos θ
    print("\n3. Наклон: V(θ) = const, z(θ) = h₀·cos θ")
    target = target_volume_mm3(0.5)
    h0 = None
    for tilt in (0.0, 10.0, 20.0, 30.0, 45.0):
        mw = matrix_for(tilt_deg_y=tilt)
        level, vol, spilled = solve_level_for_volume(mw, target)
        print(f"  θ={tilt:4.1f}°  уровень={level:8.4f} мм  "
              f"V={vol:10.4f} мм³  spilled={spilled}")
        check(f"V(θ={tilt:g})", vol, target, 0.02)
        if h0 is None:
            h0 = axis_level_mm(level, tilt)
        check(f"z(θ={tilt:g})", level, h0 * math.cos(math.radians(tilt)), 1e-3)

    # 4. Краевые случаи: малое заполнение под наклоном.  Горизонт может
    #    оказаться ниже самой высокой точки наклонённого дна — тогда меш
    #    превращается в клин, и срез обязан остаться manifold.
    print("\n4. Краевые случаи (малый/большой уровень под наклоном)")
    import bmesh
    from injector3d.liquid import _liquid_bmesh
    for tilt in (0.0, 30.0, 60.0):
        mw = matrix_for(tilt_deg_y=tilt)
        for frac in (0.02, 0.05, 0.5, 0.9):
            level, vol, spilled = solve_level_for_volume(
                mw, target_volume_mm3(frac))
            bm = _liquid_bmesh(mw, level, REVOLVE_SEGMENTS)
            if bm is None:
                print(f"  θ={tilt:4.1f}° {frac*100:4.0f}%: жидкости нет")
                ok = False
                continue
            bad = [e for e in bm.edges if len(e.link_faces) != 2]
            v = bm.calc_volume(signed=True) / (MM ** 3)
            bm.free()
            good = not bad and abs(v - vol) < 0.05
            ok &= good
            print(f"  {'OK ' if good else 'FAIL'} θ={tilt:4.1f}° "
                  f"{frac*100:4.0f}%: уровень {level:7.3f} мм, "
                  f"V={v:9.2f} (цель {target_volume_mm3(frac):8.2f}), "
                  f"non-manifold={len(bad)}, spilled={spilled}")

    # 5. Порог перелива: выше нижней точки кромки объём не растёт
    print("\n5. Перелив через край")
    mw = matrix_for(tilt_deg_y=30.0)
    _, v_rim, spilled = solve_level_for_volume(mw, target_volume_mm3(1.0))
    v_max = target_volume_mm3(1.0)
    print(f"  при 100% заполнения и наклоне 30° удержится "
          f"{v_rim:.2f} мм³ из {v_max:.2f} "
          f"({(1 - v_rim / v_max) * 100:.2f}% вылилось бы), spilled={spilled}")
    assert spilled and v_rim < v_max, \
        "наклон 30° не должен позволять заполнить трубку целиком"

    print("\n" + ("ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ" if ok else "ЕСТЬ НЕСОВПАДЕНИЯ"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

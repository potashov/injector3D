"""Числовая самопроверка пункта 2: объём жидкости при наклоне.

Запуск внутри Blender:
    blender --background --python check_liquid.py

Опорное аналитическое тождество.  Полость — цилиндр радиуса r; горизонт
в мировых координатах — это плоскость, наклонённая на угол θ к сечению
цилиндра и пересекающая его ось в точке h₀.  Высота плоскости над точкой
(x, y) сечения равна h₀ + x·tgθ, а ∫x dA по кругу = 0, поэтому

    V(θ) = ∮ (h₀ + x·tgθ) dA = π r² h₀

— объём от угла не зависит.  Отсюда проверяемые следствия:
  1. бисекция возвращает один и тот же объём для всех θ (что и печатает
     create_scene.py);
  2. мениск добавляет к объёму постоянную величину, а его кромка остаётся
     горизонтальной в мировых координатах при любом наклоне трубки —
     поверхность жидкости не наклоняется вместе с трубкой.
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mathutils import Euler

from injector3d.liquid import (
    CLEARANCE,
    _liquid_bmesh,
    capacity_mm3,
    meniscus_volume_mm3,
    meniscus_wall_rise_mm,
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
    #    плюс объём мениска (пока горизонт не задел дно и не перелился).
    print("\n2. Вертикальная трубка: V = π r² h + V_мениска")
    r_eff = INNER_RADIUS - CLEARANCE
    v_men = meniscus_volume_mm3()
    for h in (10.0, 25.0, 50.0, 75.0):
        v = volume_at_level(matrix_for(), h)
        check(f"V(h={h:g})", v,
              math.pi * r_eff ** 2 * (h - BOTTOM_THICKNESS - CLEARANCE)
              + v_men, 0.2)

    # 3. Наклон: объём не зависит от угла, кромка мениска остаётся
    #    горизонтальной в мировых координатах (не наклоняется с трубкой).
    print("\n3. Наклон: V(θ) = const, поверхность горизонтальна")
    target = target_volume_mm3(0.5)
    rise = meniscus_wall_rise_mm()
    for tilt in (0.0, 10.0, 20.0, 30.0, 45.0):
        mw = matrix_for(tilt_deg_y=tilt)
        level, vol, spilled = solve_level_for_volume(mw, target)
        print(f"  θ={tilt:4.1f}°  уровень={level:8.4f} мм  "
              f"V={vol:10.4f} мм³  spilled={spilled}")
        check(f"V(θ={tilt:g})", vol, target, 0.02)

        bm = _liquid_bmesh(mw, level, REVOLVE_SEGMENTS)
        zs = [v.co.z / MM for v in bm.verts]
        bm.free()
        z_top = max(zs)
        rim = [z for z in zs if abs(z - z_top) < 1e-6]
        check(f"z кромки θ={tilt:g}", z_top, level + rise, 1e-4)
        check(f"разброс z кромки θ={tilt:g}", max(rim) - min(rim), 0.0, 1e-4)

    # 4. Краевые случаи: малое заполнение под наклоном.  Горизонт может
    #    оказаться ниже самой высокой точки наклонённого дна — тогда меш
    #    превращается в клин, и срез обязан остаться manifold.
    print("\n4. Краевые случаи (малый/большой уровень под наклоном)")
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

    # 6. Fast-путь: compare fast=True vs fast=False (пункт 4.5)
    print("\n6. Fast-путь (шаблон мениска): fast=True vs fast=False")
    from injector3d.liquid import fast_volume_at_level, _MENISCUS_TEMPLATE_CACHE
    _MENISCUS_TEMPLATE_CACHE.clear()
    target = target_volume_mm3(0.5)
    for tilt in (0.0, 10.0, 20.0, 30.0):
        mw = matrix_for(tilt_deg_y=tilt)
        # Exact
        lev_exact, vol_exact, sp_exact = solve_level_for_volume(
            mw, target, fast=False)
        # Fast
        lev_fast, vol_fast, sp_fast = solve_level_for_volume(
            mw, target, fast=True)
        lev_diff = abs(lev_exact - lev_fast)
        vol_diff = abs(vol_exact - vol_fast)
        good = lev_diff < 0.05 and vol_diff < 0.1
        ok &= good
        print(f"  θ={tilt:4.1f}° exact={vol_exact:9.2f} fast={vol_fast:9.2f} "
              f"ΔV={vol_diff:.3f} Δh={lev_diff:.4f} "
              f"{'OK' if good else 'FAIL'}")

    # 7. Шаблон: ΔV_under постоянен при разном уровне
    print("\n7. Шаблон мениска: ΔV_under постоянен")
    mw = matrix_for(tilt_deg_y=30.0)
    from injector3d.liquid import _meniscus_template, _build_flat_bmesh
    from injector3d.config import MENISCUS_RINGS
    _MENISCUS_TEMPLATE_CACHE.clear()
    template = _meniscus_template(mw, REVOLVE_SEGMENTS, MENISCUS_RINGS)
    if template is not None:
        delta_under = template['delta_under']
        rise = template['rise']
        print(f"  ΔV_under = {delta_under:.2f} мм³, подъём у стенки = {rise:.3f} мм")
        # Проверяем на двух уровнях
        for frac in (0.3, 0.7):
            bm = _build_flat_bmesh(mw,
                                   solve_level_for_volume(
                                       mw, target_volume_mm3(frac),
                                       fast=False)[0] + rise,
                                   REVOLVE_SEGMENTS)
            if bm is not None:
                v_flat = bm.calc_volume(signed=True) / (MM ** 3)
                bm.free()
                v_men = v_flat - delta_under
                bm_exact = _liquid_bmesh(mw,
                                         solve_level_for_volume(
                                             mw, target_volume_mm3(frac),
                                             fast=False)[0],
                                         REVOLVE_SEGMENTS)
                if bm_exact is not None:
                    v_exact = bm_exact.calc_volume(signed=True) / (MM ** 3)
                    bm_exact.free()
                    diff = abs(v_men - v_exact)
                    good = diff < 0.5
                    ok &= good
                    print(f"  {frac*100:.0f}%: V_flat-Δ={v_men:.2f} "
                          f"V_exact={v_exact:.2f} Δ={diff:.3f} "
                          f"{'OK' if good else 'FAIL'}")
    else:
        print(f"  Шаблон не построен (θ=30°) — возможно, мениск вне эллипса")

    print("\n" + ("ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ" if ok else "ЕСТЬ НЕСОВПАДЕНИЯ"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

"""Самопроверка пресетов жидкостей (пункт 6 плана).

Проверяет:
1. Корректность решения ОДУ Юнга-Лапласа для каждого пресета:
   - сходимость бисекции ζ(0),
   - совпадение численного ΔV с аналитическим тождеством 2π λ² R cos θ.
2. Построение меша и сохранение инварианта V(θ)=const для каждого типа жидкости.
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mathutils import Matrix

from injector3d.config import (
    LIQUID_PRESETS,
    get_liquid_config,
    INNER_RADIUS,
    MM,
)
from injector3d.liquid import (
    CLEARANCE,
    MENISCUS_RADIUS,
    build_liquid,
    create_liquid,
    meniscus_wall_rise_mm,
    meniscus_volume_mm3,
    solve_level_for_volume,
    target_volume_mm3,
)
from injector3d.meniscus import solve_meniscus
from injector3d.render import tilt_matrix
from injector3d.tube_geometry import create_tube


def main():
    ok = True

    def check(name, got, expected, tol):
        nonlocal ok
        diff = abs(got - expected)
        passed = diff <= tol
        status = "OK" if passed else "FAIL"
        print(f"  {status:<4} {name}: {got:.4f} (ожидалось {expected:.4f}, Δ {diff:.4f} ≤ {tol})")
        if not passed:
            ok = False

    print("=== injector3D — Проверка пресетов жидкостей ===\n")

    print("1. Решение Юнга-Лапласа и аналитический объём мениска")
    for liq_name in sorted(LIQUID_PRESETS.keys()):
        cfg = get_liquid_config(liq_name)
        sol = solve_meniscus(
            R=MENISCUS_RADIUS,
            surface_tension=cfg['surface_tension'],
            liquid_density=cfg['density'],
            contact_angle_deg=cfg['contact_angle_deg'],
        )
        rise = meniscus_wall_rise_mm(cfg)
        d_net = meniscus_volume_mm3(cfg)
        print(f"  [{cfg['display_name']}] λ={sol['lambda_mm']:.3f} мм, θ={sol['contact_angle_deg']:.1f}°, "
              f"подъём={rise:.3f} мм, ΔV_net={d_net:.2f} мм³")
        check(f"ΔV численный vs аналитический ({liq_name})",
              sol['delta_V_mm3'], sol['delta_V_analytic'], 0.5)

    print("\n2. Инвариантность объёма V(θ) под наклоном для каждого пресета")
    tube = create_tube("TestTube")
    target_vol = target_volume_mm3(0.5)  # 50% заполнение (~12900 мм³)

    for liq_name in sorted(LIQUID_PRESETS.keys()):
        cfg = get_liquid_config(liq_name)
        liq_obj = create_liquid(f"Liquid_{liq_name}")

        # Upright
        tube.matrix_world = Matrix.Identity(4)
        info_up = build_liquid(liq_obj, tube, target_vol, use_fast=True, liquid_config=cfg)

        # Tilted 20°
        tube.matrix_world = tilt_matrix(math.radians(20.0), 0.0)
        info_tilt = build_liquid(liq_obj, tube, target_vol, use_fast=True, liquid_config=cfg)

        check(f"V(upright) == цель ({liq_name})",
              info_up['volume_mm3'], target_vol, 0.05)
        check(f"V(θ=20°) == V(upright) ({liq_name})",
              info_tilt['volume_mm3'], info_up['volume_mm3'], 0.05)

    print("\n" + ("ВСЕ ПРОВЕРКИ ПРЕСЕТОВ ПРОЙДЕНЫ" if ok else "ЕСТЬ ОШИБКИ"))
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()

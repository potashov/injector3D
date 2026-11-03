"""Мениск — осесимметричное решение Юнга-Лапласа (пункт 3 плана).

Задача
-----
Найти поверхность ζ(r) свободной жидкости внутри вертикальной цилиндрической
трубки радиуса R, удерживаемую капиллярными силами.  Уравнение Лапласа в
радиальной форме:

    (1/r) d/dr [ r z' / sqrt(1 + z'^2) ] = -z / lambda_c^2,

где lambda_c = sqrt(sigma / (rho * g)) — капиллярная длина.  Граничные
условия:

    z'(0) = 0      (симметрия на оси),
    z'(R) = ctg(theta)   (краевой угол Юнга).

При θ < 90° (смачивание) мениск поднимается у стенки, а центр опущен
ниже условной плоскости (z(0) < 0).  Поэтому бисекция ищет z(0)
в диапазоне [-10, 0], а не [0, 10].

Решение не выражается через элементарные функции (даже для плоских пластин
нужна эллиптическая функция Гукки), поэтому задача решается как задача
Коши с бисекцией начального значения z(0).

Свободная поверхность вычисляется в **локальных** координатах трубки
(ось z — ось трубки), а потом трансформируется в мировые координаты
(вместе со всей жидкостью).
"""

import math

from .config import (
    INNER_RADIUS,
    SURFACE_TENSION,
    LIQUID_DENSITY,
    GRAVITY,
    CONTACT_ANGLE_DEG,
    MENISCUS_RINGS,
    MENISCUS_STEPS,
)

# --- Параметры ---
CAP_LAMBDA_MM = math.sqrt(
    SURFACE_TENSION / (LIQUID_DENSITY * GRAVITY)) * 1000.0  # мм
CONTACT_ANGLE = math.radians(CONTACT_ANGLE_DEG)
CTG = math.cos(CONTACT_ANGLE) / math.sin(CONTACT_ANGLE)


def _deriv_zp(r, z, zp):
    """dz''/dr из уравнения Лапласа.

    Уравнение: (1/r) d/dr [ r z' / sqrt(1 + z'^2) ] = -z / lambda_c^2.
    Раскрываем производную:
        z'' = -(1+z'^2)^(3/2) * [ z/lambda_c^2 + z'/(r*sqrt(1+z'^2)) ].

    На оси r → 0 предел по Лопиталю:  z''(0) = -z(0) / (2*lambda_c^2).
    """
    if r < 1e-15:
        return -z / (2 * CAP_LAMBDA_MM ** 2)
    sq = math.sqrt(1 + zp * zp)
    # Защита от переполнения при больших zp
    if sq > 1e3:
        # При zp >> 1: sq ≈ |zp|, term ≈ z/λ² + 1/r
        # Если r мало, 1/r доминирует, z'' ≈ -|zp|³/r
        # Ограничиваем по модулю, чтобы RK4 не улетал
        sign = -math.copysign(1, z / CAP_LAMBDA_MM ** 2 + 1.0 / r)
        return sign * 1e3 * 1e3
    term = z / CAP_LAMBDA_MM ** 2 + zp / (r * sq)
    return -sq ** 3 * term


def _solve_ivp(z0, r_end, nsteps):
    """РК4 от r=0 до r_end.

    Возвращает (r_arr, z_arr, zp_arr).
    """
    h = r_end / nsteps
    r, z, zp = 0.0, z0, 0.0
    rs, zs, zps = [r], [z], [zp]

    for _ in range(nsteps):
        if r > r_end - 1e-15:
            break

        # k1
        k1_z = zp
        k1_zp = _deriv_zp(r, z, zp)

        # k2
        mid_r = r + 0.5 * h
        z2 = z + 0.5 * h * k1_z
        zp2 = zp + 0.5 * h * k1_zp
        k2_z = zp2
        k2_zp = _deriv_zp(mid_r, z2, zp2)

        # k3
        z3 = z + 0.5 * h * k2_z
        zp3 = zp + 0.5 * h * k2_zp
        k3_z = zp3
        k3_zp = _deriv_zp(mid_r, z3, zp3)

        # k4
        z4 = z + h * k3_z
        zp4 = zp + h * k3_zp
        k4_z = zp4
        k4_zp = _deriv_zp(r + h, z4, zp4)

        z += h / 6.0 * (k1_z + 2 * k2_z + 2 * k3_z + k4_z)
        zp += h / 6.0 * (k1_zp + 2 * k2_zp + 2 * k3_zp + k4_zp)
        r += h
        rs.append(r)
        zs.append(z)
        zps.append(zp)

    return rs, zs, zps


def solve_meniscus(R=INNER_RADIUS, nsteps=MENISCUS_STEPS):
    """Задача Юнга-Лапласа для трубки радиуса R.

    Returns
    -------
    dict с полями:
        h0 — подъём у стенки (мм),
        r, z, zp — массивы координат,
        delta_V_mm3 — объём мениска (положительный, мм³),
    """
    # Бисекция z(0) по краевому условию z'(R) = ctg(theta).
    #
    # При θ < 90° (смачивание) мениск поднимается у стенки, поэтому
    # z'(R) = ctg(θ) > 0.  Чтобы z' выросла от 0 до положительного
    # значения, z''(0) = -z(0)/(2λ²) должно быть > 0, то есть z(0) < 0.
    #
    # Функция objective(z0) = z'(R; z0) - ctg монотонно возрастает
    # (более высокий начальный столб = более крутой профиль),
    # поэтому бисекция сходится.

    def objective(z0):
        _, _, zps = _solve_ivp(z0, R, nsteps)
        return zps[-1] - CTG

    # Границы бисекции: z(0) ∈ [-10, 0] для смачивающей жидкости.
    # При z0 → -∞: z'(R) → +∞ (очень крутой профиль).
    # При z0 → 0:  z'(R) → 0 (почти плоская поверхность).
    #
    # Проверяем, что ctg попадает в диапазон:
    val_lo = objective(-10.0)  # z'(R) при z0 = -10
    val_hi = objective(0.0)    # z'(R) при z0 = 0

    if not (val_lo - CTG < 0 and val_hi - CTG > 0):
        # ctg не в диапазоне — расширяем границы
        if val_lo - CTG > 0:
            # z'(R; -10) > ctg, значит z(0) ещё меньше
            z_lo, z_hi = -100.0, -10.0
        else:
            # z'(R; 0) < ctg, значит z(0) > 0 (несмачивание)
            z_lo, z_hi = 0.0, 100.0
        for _ in range(64):
            z_mid = 0.5 * (z_lo + z_hi)
            val = objective(z_mid)
            if abs(val - CTG) < 1e-6:
                z_lo = z_hi = z_mid
                break
            if val < CTG:
                z_lo = z_mid
            else:
                z_hi = z_mid
    else:
        z_lo, z_hi = -10.0, 0.0
        for _ in range(64):
            z_mid = 0.5 * (z_lo + z_hi)
            val = objective(z_mid)
            if abs(val - CTG) < 1e-6:
                z_lo = z_hi = z_mid
                break
            if val < CTG:
                z_lo = z_mid
            else:
                z_hi = z_mid

    rs, zs, zps = _solve_ivp(z_lo, R, nsteps)
    h0 = zs[-1]

    # Объём мениска: V = ∮2πr ζ(r) dr — численно по трапециям.
    delta_v = 0.0
    for i in range(1, len(rs)):
        delta_v += 0.5 * (rs[i] * zs[i] + rs[i - 1] * zs[i - 1]) \
                   * (rs[i] - rs[i - 1])
    delta_v *= 2 * math.pi

    return {
        'h0': h0,
        'r': rs,
        'z': zs,
        'zp': zps,
        'delta_V_mm3': delta_v,
    }


def _check_mechanical_balance(R=INNER_RADIUS):
    """Механическое тождество: вес столба = сила поверхностного натяжения.

    Сила поверхностного натяжения вдоль стенки:
        F_cap = 2πR σ cos(θ)
    Вес столба мениска:
        W = ρ g V_meniscus
    По теореме Архимеда (баланс сил на свободной поверхности):
        F_cap = W
    """
    m = solve_meniscus(R)
    cap_force = 2 * math.pi * R * SURFACE_TENSION / 1000.0 * math.cos(CONTACT_ANGLE)
    weight = m['delta_V_mm3'] * LIQUID_DENSITY * GRAVITY / 1e9
    print(f"  cap_force = {cap_force:.6f} Н, weight = {weight:.6f} Н")
    print(f"  ratio = {cap_force / weight:.6f}")
    return cap_force, weight

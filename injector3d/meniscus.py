"""Мениск — осесимметричное решение Юнга-Лапласа (пункт 3 плана).

Консервативная формулировка через F(r) = r·ζ′/√(1+ζ′²).

Вместо неустойчивой стрельбы по ζ(0) решаем систему двух ОДУ
первого порядка:
    dF/dr = −r·ζ / λ²,
    dζ/dr = F / √(r² − F²),
с начальными условиями F(0) = 0, ζ(0) = ζ₀.  Параметр ζ₀ подбирается
бисекцией по краевому условию ζ′(R) = ctg(θ).

Преимущества:
  1. Нет сингулярности 1/r на оси — F(0) = 0, предел ζ′(0) = 0.
  2. Функция монотонна: при ζ₀ → −∞ ζ′(R) → +∞, при ζ₀ → 0 ζ′(R) → 0.
  3. Механическое тождество выполняется автоматически:
     F′ = −rζ/λ²  →  ζ = −λ²F′/r  →
     ΔV = ∮ 2πr ζ dr = −2πλ² ∮ F′ dr = −2πλ² F(R).
     Но F(R) = R·ζ′(R)/√(1+ζ′²(R)) = R·ctgθ/√(1+ctg²θ) = R·cosθ.
     Итого: ΔV = 2πλ²R·cosθ — проверять не нужно, это свойство решения.

Для σ = 0.0728 Н/м: λ = √(σ/(ρg)) = 2.724 мм.
Для θ = 25°: ctg = 2.1445, cos = 0.9063.
ΔV ≈ 2π·(2.724)²·9.5·0.9063 ≈ 401 мм³ ≈ 3.1 % от объёма заполнения.
"""

import math

from .config import (
    INNER_RADIUS,
    SURFACE_TENSION,
    LIQUID_DENSITY,
    GRAVITY,
    MENISCUS_STEPS,
)

# --- Параметры ---
CAP_LAMBDA_MM = math.sqrt(
    SURFACE_TENSION / (LIQUID_DENSITY * GRAVITY)) * 1000.0  # мм


def _deriv_y(r, y, lambda_mm=CAP_LAMBDA_MM):
    """Правая часть системы: y = [ζ, F],  F = r·ζ′/sqrt(1+ζ′²).

        dζ/dr = F / sqrt(r² − F²)
        dF/dr = +r·ζ / λ²

    Знак «плюс» во втором уравнении — следствие того, что уравнение
    Лапласа для смачивающей жидкости имеет вид

        (1/r) d/dr [ r ζ′ / sqrt(1 + ζ′²) ] = +ζ / λ².

    Вывод знака: в линейном приближении ζ″ + ζ′/r = ζ/λ², убывающее на
    бесконечности решение — модифицированная функция Бесселя K₀(r/λ);
    для неё K₀″ + K₀′/x = K₀, что и даёт знак «+».  Для трубки конечного
    радиуса это же уравнение даёт растущее решение I₀(r/λ): ζ минимален
    на оси и максимален у стенки — в точности форма вогнутого мениска.
    """
    zeta, F = y
    if r < 1e-12:
        # На оси F(0) = 0 и предел dζ/dr = F/r = F′(0) = 0·ζ/λ² = 0.
        return 0.0, 0.0
    sq = r * r - F * F
    if sq <= 0.0:
        # |F| → r означает ζ′ → ∞: решение «опрокинулось».
        return 0.0, r * zeta / lambda_mm ** 2
    return F / math.sqrt(sq), r * zeta / lambda_mm ** 2


def _solve_ivp(zeta0, r_end, nsteps, lambda_mm=CAP_LAMBDA_MM):
    """РК4 от r=0 до r_end.

    Возвращает (r_arr, zeta_arr, F_arr, tipped).
    tipped=True, если решение опрокинулось (|F| >= r) до границы.
    """
    h = r_end / nsteps
    r, zeta, F = 0.0, zeta0, 0.0
    rs, zs, Fs = [r], [zeta], [F]

    for _ in range(nsteps):
        if r > r_end - 1e-15:
            break
        if r > 1e-6 and abs(F) >= r:
            return rs, zs, Fs, True

        # k1
        k1 = _deriv_y(r, (zeta, F), lambda_mm)

        # k2
        y2 = (zeta + 0.5 * h * k1[0], F + 0.5 * h * k1[1])
        k2 = _deriv_y(r + 0.5 * h, y2, lambda_mm)

        # k3
        y3 = (zeta + 0.5 * h * k2[0], F + 0.5 * h * k2[1])
        k3 = _deriv_y(r + 0.5 * h, y3, lambda_mm)

        # k4
        y4 = (zeta + h * k3[0], F + h * k3[1])
        k4 = _deriv_y(r + h, y4, lambda_mm)

        zeta += h / 6.0 * (k1[0] + 2 * k2[0] + 2 * k3[0] + k4[0])
        F += h / 6.0 * (k1[1] + 2 * k2[1] + 2 * k3[1] + k4[1])
        r += h
        rs.append(r)
        zs.append(zeta)
        Fs.append(F)

    return rs, zs, Fs, False


def solve_meniscus(R=INNER_RADIUS, nsteps=MENISCUS_STEPS,
                   surface_tension=None, liquid_density=None,
                   contact_angle_deg=None):
    """Задача Юнга-Лапласа для трубки радиуса R и заданных свойств жидкости.

    Returns
    -------
    dict с полями:
        h0 — подъём у стенки (мм),
        r, z, F — массивы координат,
        delta_V_mm3 — объём мениска (должен совпадать с delta_V_analytic),
        delta_V_analytic — аналитический объём (2π λ² R cos θ),
        d_net — объём юбки относительно плоскости ζ(0),
        lambda_mm — капиллярная длина (мм),
        contact_angle_deg — краевой угол (град).
    """
    st = SURFACE_TENSION if surface_tension is None else float(surface_tension)
    rho = LIQUID_DENSITY if liquid_density is None else float(liquid_density)
    theta_deg = CONTACT_ANGLE_DEG if contact_angle_deg is None else float(contact_angle_deg)
    # Ограничиваем угол в физическом смачивающем диапазоне (0, 90)
    theta_deg = max(0.5, min(89.5, theta_deg))

    lambda_mm = math.sqrt(st / (rho * GRAVITY)) * 1000.0
    theta_rad = math.radians(theta_deg)
    ctg = math.cos(theta_rad) / math.sin(theta_rad)
    cos_val = math.cos(theta_rad)
    analytic_dV = 2 * math.pi * lambda_mm ** 2 * R * cos_val

    # Бисекция ζ(0) по краевому условию ζ′(R) = ctg(θ).
    #
    # objective(ζ₀) = ζ′(R) **возрастает** по ζ₀: при ζ₀ = 0 решение
    # тривиально (ζ ≡ 0, ζ′ ≡ 0), objective = 0.  При ζ₀ → +∞ решение
    # «опрокидывается» (F → r, наклон → ∞) и objective → +∞.
    # CTG > 0, поэтому корень лежит в (0, ∞).

    def objective(zeta0):
        rs, zs, Fs, tipped = _solve_ivp(zeta0, R, nsteps, lambda_mm)
        if tipped or Fs[-1] >= R:
            return 1e9  # F → R, ζ′ → ∞ или опрокидывание
        sq = R * R - Fs[-1] * Fs[-1]
        if sq <= 0:
            return 1e9
        return Fs[-1] / math.sqrt(sq)

    # Подбираем границы: z_lo = 0, z_hi = 2^k пока objective(z_hi) < ctg.
    z_lo, z_hi = 0.0, 0.5
    for _ in range(64):
        val = objective(z_hi)
        if val > ctg:
            break
        z_hi *= 2.0
    else:
        raise RuntimeError(
            f"не удалось заключить корень: objective({z_hi}) "
            f"= {objective(z_hi)} при ctg = {ctg}")

    for _ in range(64):
        z_mid = 0.5 * (z_lo + z_hi)
        val = objective(z_mid)
        if abs(val - ctg) < 1e-12:
            z_lo = z_hi = z_mid
            break
        if val < ctg:
            z_lo = z_mid
        else:
            z_hi = z_mid

    rs, zs, Fs, _ = _solve_ivp(z_lo, R, nsteps, lambda_mm)
    h0 = zs[-1]

    # Численный объём мениска (для сверки с аналитическим)
    delta_v = 0.0
    for i in range(1, len(rs)):
        delta_v += 0.5 * (rs[i] * zs[i] + rs[i - 1] * zs[i - 1]) \
                   * (rs[i] - rs[i - 1])
    delta_v *= 2 * math.pi

    # d_net — объём юбки относительно плоскости ζ(0):
    # V = πR²(H−ζ₀) + d_net при заполнении до уровня H на оси.
    z0 = zs[0]
    d_net = 0.0
    for i in range(1, len(rs)):
        d_net += 0.5 * (rs[i] * (zs[i] - z0) + rs[i - 1] * (zs[i - 1] - z0)) \
                 * (rs[i] - rs[i - 1])
    d_net *= 2 * math.pi

    return {
        'h0': h0,
        'r': rs,
        'z': zs,
        'F': Fs,
        'delta_V_mm3': delta_v,
        'delta_V_analytic': analytic_dV,
        'd_net': d_net,
        'lambda_mm': lambda_mm,
        'contact_angle_deg': theta_deg,
    }

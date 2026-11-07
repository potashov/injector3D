"""Смешанный мениск: свободная поверхность с контактом на стенке и дне.

Модуль не меняет существующий `build_liquid()` и может вызываться отдельно.
Постановка — в локальных цилиндрических координатах трубки: свободная
поверхность задаётся как функция `phi(x, y)` над диском `r <= R`, дно —
препятствие `b(x, y)`, стенка — граница `r = R`.

Уравнение Юнга–Лапласа в мировых горизонтальных координатах:

    G^{ij} d_i d_j phi = (phi - L) / lambda^2,

где `G = A^T A`, `A` — матрица перехода от локальных (x, y) к мировым
горизонтальным координатам.  Для прототипа используется линейная
аппроксимация; obstacle-проекция `phi >= b` даёт смешанную контактную
линию.  Уровень `L` подбирается бисекцией по целевому объёму.

Ограничения прототипа:

* краевой угол на стенке задаётся через Dirichlet-условие
  `phi(R, theta) = max(L + rise, b(R, theta))`, а не через Neumann;
* краевой угол на дне не задаётся явно — используется проекция на дно;
* меш по умолчанию — менисковая крышка (top surface), а не замкнутый
  объём жидкости.  Полный меш можно добавить отдельно.
"""

import math

import bmesh
import numpy as np
from mathutils import Vector

from .config import (
    BOTTOM_THICKNESS,
    CONTACT_ANGLE_DEG,
    GRAVITY,
    HEIGHT,
    INNER_RADIUS,
    LIQUID_DENSITY,
    MENISCUS_RINGS,
    MM,
    REVOLVE_SEGMENTS,
    SURFACE_TENSION,
)
from .meniscus import solve_meniscus

CLEARANCE = 0.02
LIQUID_RADIUS = INNER_RADIUS - CLEARANCE
LIQUID_Z0 = BOTTOM_THICKNESS + CLEARANCE


def _normalize_props(liquid_config):
    """Приводит dict/кортеж/None к (sigma, rho, theta_deg)."""
    if liquid_config is None:
        return (SURFACE_TENSION, LIQUID_DENSITY, CONTACT_ANGLE_DEG)
    if isinstance(liquid_config, dict):
        return (
            float(liquid_config.get('surface_tension', SURFACE_TENSION)),
            float(liquid_config.get('density', LIQUID_DENSITY)),
            float(liquid_config.get('contact_angle_deg', CONTACT_ANGLE_DEG)),
        )
    if isinstance(liquid_config, (tuple, list)) and len(liquid_config) >= 3:
        return tuple(float(v) for v in liquid_config[:3])
    return (SURFACE_TENSION, LIQUID_DENSITY, CONTACT_ANGLE_DEG)


def _world_columns(matrix_world):
    """Возвращает (ex, ey, axis, translation_mm) по matrix_world."""
    ex = np.array([matrix_world[0][0], matrix_world[1][0],
                   matrix_world[2][0]], dtype=float)
    ey = np.array([matrix_world[0][1], matrix_world[1][1],
                   matrix_world[2][1]], dtype=float)
    axis = np.array([matrix_world[0][2], matrix_world[1][2],
                     matrix_world[2][2]], dtype=float)
    t_mm = np.array([matrix_world[0][3], matrix_world[1][3],
                     matrix_world[2][3]], dtype=float) / MM
    return ex, ey, axis, t_mm


class MixedMeniscusSolver:
    """2D-солвер мениска в локальном диске трубки.

    Параметры
    ---------
    matrix_world : mathutils.Matrix
        Мировая матрица трубки.
    liquid_config : dict | tuple | None
        Свойства жидкости: surface_tension, density, contact_angle_deg.
    radius_mm : float
        Радиус меша жидкости (по умолчанию `INNER_RADIUS - CLEARANCE`).
    z0_mm : float
        Локальная высота дна (по умолчанию `BOTTOM_THICKNESS + CLEARANCE`).
    radial_nodes : int
        Число радиальных интервалов от `r0` до `R`.
    angular_nodes : int
        Число угловых узлов.
    inner_radius_mm : float
        Внутренний радиус, с которого начинается 2D-сетка.  Центральный
        диск меньше этого радиуса считается осесимметричным.
    """

    def __init__(self, matrix_world, liquid_config=None,
                 radius_mm=LIQUID_RADIUS, z0_mm=LIQUID_Z0,
                 radial_nodes=64, angular_nodes=128,
                 inner_radius_mm=1.0, omega=0.8, max_sweeps=1200,
                 tol_mm=2e-5):
        self.mw = matrix_world
        self.sigma, self.rho, self.theta_deg = _normalize_props(liquid_config)
        self.theta_deg = max(0.5, min(89.5, self.theta_deg))
        self.R = float(radius_mm)
        self.z0 = float(z0_mm)
        self.Nr = int(radial_nodes)
        self.Nt = int(angular_nodes)
        self.r0 = min(float(inner_radius_mm), 0.5 * self.R)
        self.omega = float(omega)
        self.max_sweeps = int(max_sweeps)
        self.tol_mm = float(tol_mm)

        self.lam_mm = math.sqrt(self.sigma / (self.rho * GRAVITY)) * 1000.0
        self.ex, self.ey, self.axis, self.t_mm = _world_columns(matrix_world)
        self.exz = float(self.ex[2])
        self.eyz = float(self.ey[2])
        self.az = float(self.axis[2])
        self.tz = float(self.t_mm[2])
        if self.az <= 1e-9:
            raise ValueError('ось трубки направлена вниз: a_z <= 0')

        A = np.array([[self.ex[0], self.ey[0]],
                      [self.ex[1], self.ey[1]]], dtype=float)
        G = A.T @ A
        H = np.linalg.inv(G)
        self.h11 = float(H[0, 0])
        self.h12 = float(H[0, 1])
        self.h22 = float(H[1, 1])

        prof = solve_meniscus(
            self.R,
            surface_tension=self.sigma,
            liquid_density=self.rho,
            contact_angle_deg=self.theta_deg,
        )
        self.prof = prof
        self.rise = float(prof['z'][-1] - prof['z'][0])
        self.cot_theta = (math.cos(math.radians(self.theta_deg))
                          / math.sin(math.radians(self.theta_deg)))
        self._r_prof = np.asarray(prof['r'], dtype=float)
        self._z_prof = np.asarray(prof['z'], dtype=float)
        self._z_prof0 = float(self._z_prof[0])

        self._build_grid()
        self._build_coefficients()

    # --- геометрия ---

    def _x_y(self, r, theta):
        return (np.asarray(r)[..., None] * np.cos(theta)[None, ...],
                np.asarray(r)[..., None] * np.sin(theta)[None, ...])

    def bottom_mm(self, x, y):
        return self.tz + self.exz * x + self.eyz * y + self.az * self.z0

    def wall_bottom_mm(self, theta):
        return (self.tz + self.R * (self.exz * np.cos(theta)
                                    + self.eyz * np.sin(theta))
                + self.az * self.z0)

    def wall_phi_mm(self, level_mm, theta):
        return np.maximum(level_mm + self.rise, self.wall_bottom_mm(theta))

    def axisymmetric_phi_mm(self, level_mm, r):
        zeta = np.interp(r, self._r_prof, self._z_prof)
        return level_mm + zeta - self._z_prof0

    def bottom_min_mm(self):
        return (self.tz + self.az * self.z0
                - self.R * math.hypot(self.exz, self.eyz))

    def bottom_max_mm(self):
        return (self.tz + self.az * self.z0
                + self.R * math.hypot(self.exz, self.eyz))

    def spill_level_mm(self):
        r = INNER_RADIUS
        theta = np.linspace(0.0, 2.0 * math.pi, 144, endpoint=False)
        z = (self.tz + r * (self.exz * np.cos(theta)
                            + self.eyz * np.sin(theta))
             + self.az * HEIGHT)
        return float(np.min(z))

    def _build_grid(self):
        self.h = (self.R - self.r0) / self.Nr
        self.r = np.linspace(self.r0, self.R, self.Nr + 1)
        self.dtheta = 2.0 * math.pi / self.Nt
        self.theta = np.arange(self.Nt, dtype=float) * self.dtheta
        self.X, self.Y = self._x_y(self.r, self.theta)
        self.B = self.bottom_mm(self.X, self.Y)
        self.wall_phi_theta = None

    def _build_coefficients(self):
        r = self.r[:, None]
        rs = np.maximum(r, 1e-9)
        c = np.cos(self.theta)[None, :]
        s = np.sin(self.theta)[None, :]
        c2, s2, cs = c * c, s * s, c * s

        A = self.h11 * c2 + 2.0 * self.h12 * cs + self.h22 * s2
        B = (2.0 * self.h12 * (c2 - s2)
             + 2.0 * cs * (self.h22 - self.h11)) / rs
        C = (self.h11 * s2 - 2.0 * self.h12 * cs
             + self.h22 * c2) / (rs * rs)
        D = (self.h11 * s2 + self.h22 * c2
             - 2.0 * self.h12 * cs) / rs
        E = (2.0 * cs * (self.h11 - self.h22)
             + 2.0 * self.h12 * (s2 - c2)) / (rs * rs)

        h2 = self.h * self.h
        dt = self.dtheta
        self.a_rp = A / h2 + D / (2.0 * self.h)
        self.a_rm = A / h2 - D / (2.0 * self.h)
        self.a_tp = C / (dt * dt) + E / (2.0 * dt)
        self.a_tm = C / (dt * dt) - E / (2.0 * dt)
        self.a_pp = B / (4.0 * self.h * dt)
        self.a_pm = -B / (4.0 * self.h * dt)
        self.a_mp = -B / (4.0 * self.h * dt)
        self.a_mm = B / (4.0 * self.h * dt)
        self.a_p = (-2.0 * A / h2 - 2.0 * C / (dt * dt)
                    - 1.0 / (self.lam_mm * self.lam_mm))

    # --- решение ---

    def wet_mask(self, level_mm):
        """Маска смоченной области по осесимметричному приближению.

        Вне маски жидкость отсутствует: `phi = b`.  Это приближённая
        контактная линия; 2D-решение уточняет форму поверхности внутри
        смоченной области.
        """
        axis = self.axisymmetric_phi_mm(level_mm, self.r)
        wet = axis[:, None] > self.B + 1e-6
        theta = self.theta
        inner_axis = self.axisymmetric_phi_mm(level_mm, self.r0)
        inner_b = self.bottom_mm(self.r0 * np.cos(theta),
                                 self.r0 * np.sin(theta))
        wet[0] = inner_axis > inner_b + 1e-6
        wet[-1] = (level_mm + self.rise
                   > self.wall_bottom_mm(theta) + 1e-6)
        return wet

    def _initial_phi(self, level_mm):
        wet = self.wet_mask(level_mm)
        axis = self.axisymmetric_phi_mm(level_mm, self.r)
        phi = np.where(wet, np.maximum(axis[:, None], self.B), self.B)
        theta = self.theta
        inner_axis = self.axisymmetric_phi_mm(level_mm, self.r0)
        inner_b = self.bottom_mm(self.r0 * np.cos(theta),
                                 self.r0 * np.sin(theta))
        phi[0] = np.where(wet[0], np.maximum(inner_axis, inner_b), inner_b)
        phi[-1] = np.where(wet[-1], self.wall_phi_mm(level_mm, theta),
                           self.B[-1])
        return phi

    def solve_pde(self, level_mm, phi0=None, wet=None):
        """Решает уравнение при заданном уровне `L` (мм).

        Вне смоченной маски поверхность совпадает с дном.  Внутри маски
        решается уравнение Юнга–Лапласа; на границе маски действует
        Dirichlet-условие `phi = b`.
        """
        if wet is None:
            wet = self.wet_mask(level_mm)
        if phi0 is None:
            phi = self._initial_phi(level_mm)
        else:
            phi = np.array(phi0, dtype=float, copy=True)

        theta = self.theta
        inner_axis = self.axisymmetric_phi_mm(level_mm, self.r0)
        inner_b = self.bottom_mm(self.r0 * np.cos(theta),
                                 self.r0 * np.sin(theta))
        inner = np.where(wet[0], np.maximum(inner_axis, inner_b), inner_b)
        wall_b = self.wall_bottom_mm(theta)
        wall = np.where(wet[-1], self.wall_phi_mm(level_mm, theta),
                        self.B[-1])
        phi[0] = inner
        phi[-1] = wall
        phi[~wet] = self.B[~wet]

        source = -level_mm / (self.lam_mm * self.lam_mm)
        omega = self.omega
        for sweep in range(self.max_sweeps):
            diff = 0.0
            for i in range(1, self.Nr):
                p = phi[i]
                p_prev = phi[i - 1]
                p_next = phi[i + 1]
                p_tp = np.roll(p, -1)
                p_tm = np.roll(p, 1)
                n_tp = np.roll(p_next, -1)
                n_tm = np.roll(p_next, 1)
                v_tp = np.roll(p_prev, -1)
                v_tm = np.roll(p_prev, 1)

                num = source - (
                    self.a_rp[i] * p_next + self.a_rm[i] * p_prev
                    + self.a_tp[i] * p_tp + self.a_tm[i] * p_tm
                    + self.a_pp[i] * n_tp + self.a_pm[i] * n_tm
                    + self.a_mp[i] * v_tp + self.a_mm[i] * v_tm
                )
                p_gs = num / self.a_p[i]
                p_new = (1.0 - omega) * p + omega * p_gs
                p_new = np.where(wet[i], np.maximum(p_new, self.B[i]),
                                 self.B[i])
                diff = max(diff, float(np.max(np.abs(p_new - p))))
                phi[i] = p_new

            phi[0] = inner
            # Внешние два кольца — граничный слой по осесимметричному
            # профилю.  Это устойчивее одной Neumann-аппроксимации и
            # сохраняет правильный подъём у стенки в полном режиме.
            outer1 = np.where(
                wet[-2],
                np.maximum(self.axisymmetric_phi_mm(level_mm, self.r[-2]),
                           self.B[-2]),
                self.B[-2],
            )
            phi[-2] = outer1
            wall_new = np.where(wet[-1], self.wall_phi_mm(level_mm, theta),
                                self.B[-1])
            diff = max(diff, float(np.max(np.abs(wall_new - phi[-1]))))
            phi[-1] = wall_new
            phi[~wet] = self.B[~wet]
            if diff < self.tol_mm:
                break
            if not np.all(np.isfinite(phi)):
                raise RuntimeError('PDE-солвер разошёлся')
        return phi

    def volume_mm3(self, phi, level_mm):
        """Объём под поверхностью `phi` (мм³), включая центральный диск."""
        hgt = np.maximum((phi - self.B) / self.az, 0.0)
        integrand = self.r[:, None] * hgt
        v_main = np.trapz(np.trapz(integrand, self.theta, axis=1),
                          self.r, axis=0)

        n_sub = 24
        r_sub = np.linspace(0.0, self.r0, n_sub)
        th_sub = np.linspace(0.0, 2.0 * math.pi, 96, endpoint=False)
        Xs, Ys = self._x_y(r_sub, th_sub)
        Bs = self.bottom_mm(Xs, Ys)
        zeta = np.interp(r_sub, self._r_prof, self._z_prof)
        phis = (level_mm + zeta - self._z_prof0)[:, None]
        hs = np.maximum((phis - Bs) / self.az, 0.0)
        v_inner = np.trapz(np.trapz(r_sub[:, None] * hs, th_sub, axis=1),
                           r_sub, axis=0)
        return float(v_main + v_inner)

    def solve_level(self, target_mm3, tol_mm3=0.02, max_iter=48):
        """Бисекция уровня `L` по целевому объёму."""
        lo = self.bottom_min_mm() - 2.0
        hi = self.spill_level_mm()
        phi = None
        v_hi = None
        for _ in range(max_iter):
            mid = 0.5 * (lo + hi)
            phi = self.solve_pde(mid, phi0=phi)
            v = self.volume_mm3(phi, mid)
            v_hi = v
            if abs(v - target_mm3) <= tol_mm3:
                return mid, v, False, phi
            if v < target_mm3:
                lo = mid
            else:
                hi = mid
        mid = 0.5 * (lo + hi)
        phi = self.solve_pde(mid, phi0=phi)
        v = self.volume_mm3(phi, mid)
        if target_mm3 >= v_hi:
            return hi, v_hi, True, phi
        return mid, v, False, phi

    # --- меш ---

    def _mesh_grid(self, phi, level_mm, inner_rings=8):
        """Сетка для крышки: центральный диск + 2D-решётка."""
        r_inner = np.linspace(0.0, self.r0, inner_rings + 1)[1:]
        r_mesh = np.concatenate([r_inner, self.r[1:]])
        th = self.theta
        z_inner = self.axisymmetric_phi_mm(level_mm, r_inner)
        X_inner, Y_inner = self._x_y(r_inner, th)
        B_inner = self.bottom_mm(X_inner, Y_inner)
        phi_inner = np.maximum(z_inner[:, None], B_inner)
        X_full = np.vstack([X_inner, self.X[1:]])
        Y_full = np.vstack([Y_inner, self.Y[1:]])
        B_full = np.vstack([B_inner, self.B[1:]])
        phi_full = np.vstack([phi_inner, phi[1:]])
        return r_mesh, X_full, Y_full, phi_full, B_full

    def build_cap_bmesh(self, phi, level_mm, inner_rings=8):
        """Строит верхнюю менисковую поверхность в мировых координатах.

        В сухих точках поверхность совпадает с дном.  Возвращается bmesh
        с открытой границей (менисковая крышка).
        """
        r_mesh, X, Y, phi_m, B = self._mesh_grid(phi, level_mm, inner_rings)
        top_z = np.maximum(phi_m, B)
        bm = bmesh.new()
        nr, nt = top_z.shape
        verts = [[bm.verts.new((X[i, j] * MM, Y[i, j] * MM,
                                top_z[i, j] * MM))
                  for j in range(nt)] for i in range(nr)]
        for i in range(nr - 1):
            for j in range(nt):
                jn = (j + 1) % nt
                try:
                    bm.faces.new((verts[i][j], verts[i][jn],
                                  verts[i + 1][jn], verts[i + 1][j]))
                except ValueError:
                    pass
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-9)
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        return bm

    def build_pde_liquid_bmesh(self, phi, level_mm, inner_rings=8):
        """Замкнутый меш жидкости для 2D-PDE-решения.

        Сухие ячейки имеют нулевую толщину; после сварки вершин и удаления
        вырожденных граней остаётся замкнутая смоченная область.  Это
        прототип; для точной геометрии контактной линии нужна отдельная
        экстракция контура.
        """
        r_mesh, X, Y, phi_m, B = self._mesh_grid(phi, level_mm, inner_rings)
        top_z = np.maximum(phi_m, B)
        bm = bmesh.new()
        nr, nt = top_z.shape
        top = [[bm.verts.new((X[i, j] * MM, Y[i, j] * MM,
                              top_z[i, j] * MM))
                for j in range(nt)] for i in range(nr)]
        bot = [[bm.verts.new((X[i, j] * MM, Y[i, j] * MM,
                              B[i, j] * MM))
                for j in range(nt)] for i in range(nr)]

        def face(vs):
            try:
                return bm.faces.new(vs)
            except ValueError:
                return None

        for i in range(nr - 1):
            for j in range(nt):
                jn = (j + 1) % nt
                if np.max(top_z[i:i + 2, j] - B[i:i + 2, j]) <= 1e-9 and \
                        np.max(top_z[i:i + 2, jn] - B[i:i + 2, jn]) <= 1e-9:
                    continue
                face((top[i][j], top[i][jn], top[i + 1][jn], top[i + 1][j]))
                face((bot[i][j], bot[i + 1][j], bot[i + 1][jn], bot[i][jn]))

        for i in range(nr - 1):
            j = 0
            face((top[i][j], top[i + 1][j], bot[i + 1][j], bot[i][j]))
            j = nt - 1
            face((top[i][j], bot[i][j], bot[i + 1][j], top[i + 1][j]))

        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-8)
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        bm.normal_update()
        return bm


    def build_liquid_bmesh(self, level_mm, radial_rings=120,
                           angular_segments=REVOLVE_SEGMENTS):
        """Замкнутый меш жидкости по обрезанному осесимметричному профилю.

        Строятся верх (свободная поверхность или дно в сухих точках), низ
        (дно) и боковые стенки по границе смоченной области.  Граница
        определяется по ячейкам: ребро, которое принадлежит ровно одной
        смоченной ячейке, замыкается боковой гранью.  Так автоматически
        закрываются и внешняя стенка `r = R`, и линия контакта на дне.
        """
        nr = int(radial_rings)
        nt = int(angular_segments)
        r = np.linspace(0.0, self.R, nr + 1)
        th = np.linspace(0.0, 2.0 * math.pi, nt, endpoint=False)
        X, Y = self._x_y(r, th)
        phi = self.axisymmetric_phi_mm(level_mm, r)
        B = self.bottom_mm(X, Y)
        top = np.maximum(phi[:, None], B)
        thick = top - B
        eps = 1e-9

        bm = bmesh.new()
        ctop = bm.verts.new(self._world_vertex(0.0, 0.0, top[0, 0]))
        cbot = bm.verts.new(self._world_vertex(0.0, 0.0, B[0, 0]))
        top_rings, bot_rings = [], []
        for i in range(1, nr + 1):
            tr, br = [], []
            for j in range(nt):
                tr.append(bm.verts.new(self._world_vertex(
                    X[i, j], Y[i, j], top[i, j])))
                br.append(bm.verts.new(self._world_vertex(
                    X[i, j], Y[i, j], B[i, j])))
            top_rings.append(tr)
            bot_rings.append(br)

        def node_id(i, j):
            return i * nt + j

        def node_ij(node):
            if node < 0:
                return 0, 0
            return divmod(node, nt)

        def vtop(node):
            if node < 0:
                return ctop
            i, j = node_ij(node)
            return top_rings[i - 1][j]

        def vbot(node):
            if node < 0:
                return cbot
            i, j = node_ij(node)
            return bot_rings[i - 1][j]

        def node_thick(node):
            i, j = node_ij(node)
            return thick[i, j]

        def face(vs):
            try:
                return bm.faces.new(vs)
            except ValueError:
                return None

        cells = []
        for j in range(nt):
            jn = (j + 1) % nt
            cells.append((-1, node_id(1, j), node_id(1, jn)))
        for i in range(1, nr):
            for j in range(nt):
                jn = (j + 1) % nt
                cells.append((node_id(i, j), node_id(i, jn),
                              node_id(i + 1, jn), node_id(i + 1, j)))

        edge_count = {}
        edge_rep = {}
        for nodes in cells:
            if not any(node_thick(n) > eps for n in nodes):
                continue
            face([vtop(n) for n in nodes])
            face([vbot(n) for n in nodes])

            m = len(nodes)
            for k in range(m):
                a = nodes[k]
                b = nodes[(k + 1) % m]
                key = (a, b) if a < b else (b, a)
                edge_count[key] = edge_count.get(key, 0) + 1
                edge_rep.setdefault(key, (a, b))

        for key, count in edge_count.items():
            if count != 1:
                continue
            a, b = edge_rep[key]
            if node_thick(a) <= eps and node_thick(b) <= eps:
                continue
            face((vtop(a), vtop(b), vbot(b), vbot(a)))

        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-9)
        degenerate = [f for f in bm.faces if f.calc_area() < 1e-14]
        if degenerate:
            bmesh.ops.delete(bm, geom=degenerate, context='FACES')
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        if bm.faces and bm.calc_volume(signed=True) < 0:
            bmesh.ops.reverse_faces(bm, faces=bm.faces)
        return bm

    def build_liquid_for_target(self, target_mm3, *,
                                radial_rings=120,
                                angular_segments=REVOLVE_SEGMENTS,
                                tol_mm3=0.02, mesh_tol_mm3=0.03,
                                max_corr=6, level_mm=None,
                                spilled=None):
        """Строит замкнутый меш и корректирует уровень по его объёму.

        Точный 1D-объём и объём треугольной сетки немного расходятся.
        После построения меша уровень уточняется методом Ньютона по
        фактическому `bm.calc_volume()`, чтобы `validate_liquid()` и
        целевой объём сходились на одной и той же геометрии.
        """
        if level_mm is None:
            level, _vol1d, spilled = self.solve_level(
                target_mm3, tol_mm3=tol_mm3)
            spilled = bool(spilled)
        else:
            level = float(level_mm)
            spilled = bool(spilled)
        bm = self.build_liquid_bmesh(
            level, radial_rings=radial_rings,
            angular_segments=angular_segments)
        vol = bm.calc_volume(signed=True) / (MM ** 3)

        if not spilled:
            for _ in range(max_corr):
                err = vol - target_mm3
                if abs(err) <= mesh_tol_mm3:
                    break
                dl = 0.05
                v_lo = self.volume_at_level(level - dl)
                v_hi = self.volume_at_level(level + dl)
                deriv = (v_hi - v_lo) / (2.0 * dl)
                if abs(deriv) < 1e-12:
                    break
                new_level = level - err / deriv
                new_level = min(max(new_level,
                                    self.bottom_min_mm() - 2.0),
                                self.spill_level_mm())
                if abs(new_level - level) < 1e-7:
                    break
                bm.free()
                level = new_level
                bm = self.build_liquid_bmesh(
                    level, radial_rings=radial_rings,
                    angular_segments=angular_segments)
                vol = bm.calc_volume(signed=True) / (MM ** 3)

        mixed = bool(level + self.rise <= self.bottom_max_mm() + 1e-9)
        flat = bool(level + self.rise <= self.bottom_min_mm() + 1e-9)
        return bm, float(level), float(vol), bool(spilled), mixed, flat


def build_mixed_meniscus(matrix_world, target_volume_mm3,
                         liquid_config=None, *, return_bmesh=True,
                         closed=False, method='clipped',
                         radial_nodes=64, angular_nodes=128,
                         inner_radius_mm=1.0, tol_mm3=0.02,
                         max_iter=32, radial_rings=120,
                         angular_segments=REVOLVE_SEGMENTS):
    """Строит смешанный мениск под целевой объём.

    `method='clipped'` — быстрый и устойчивый прототип: осесимметричный
    мениск обрезается дном.  `method='pde'` — экспериментальный 2D-солвер;
    он медленнее и требует отдельной проверки сходимости.

    Возвращает dict с полями:
        bmesh, level_mm, volume_mm3, error_mm3, spilled, flat, mixed,
        wall_contact, bottom_contact, info.
    """
    if method == 'pde':
        solver = MixedMeniscusSolver(
            matrix_world, liquid_config,
            radial_nodes=radial_nodes, angular_nodes=angular_nodes,
            inner_radius_mm=inner_radius_mm,
        )
        level, vol, spilled, phi = solver.solve_level(
            target_volume_mm3, tol_mm3=tol_mm3, max_iter=max_iter)
        mixed = bool(level + solver.rise <= solver.bottom_max_mm())
        flat = bool(level + solver.rise <= solver.bottom_min_mm() + 1e-9)
        bm = None
        if return_bmesh:
            bm = (solver.build_pde_liquid_bmesh(phi, level) if closed
                  else solver.build_cap_bmesh(phi, level))
    else:
        solver = ClippedMeniscusSolver(matrix_world, liquid_config)
        if closed:
            bm, level, vol, spilled, mixed, flat =                 solver.build_liquid_for_target(
                    target_volume_mm3, radial_rings=radial_rings,
                    angular_segments=angular_segments, tol_mm3=tol_mm3)
            if not return_bmesh:
                bm.free()
                bm = None
        else:
            level, vol, spilled = solver.solve_level(
                target_volume_mm3, tol_mm3=tol_mm3)
            mixed = bool(level + solver.rise <= solver.bottom_max_mm())
            flat = bool(level + solver.rise <= solver.bottom_min_mm() + 1e-9)
            bm = solver.build_cap_bmesh(level) if return_bmesh else None

    return {
        'bmesh': bm,
        'level_mm': float(level),
        'volume_mm3': float(vol),
        'error_mm3': float(vol - target_volume_mm3),
        'spilled': bool(spilled),
        'flat': flat,
        'mixed': mixed,
        'rise_mm': float(solver.rise),
        'contact_mm': float(level + solver.rise),
        'bottom_max_mm': float(solver.bottom_max_mm()),
        'bottom_min_mm': float(solver.bottom_min_mm()),
        'lambda_mm': float(solver.lam_mm),
        'wall_contact': None,
        'bottom_contact': None,
        'info': {
            'method': method,
            'closed': closed,
            'radial_nodes': solver.Nr,
            'angular_nodes': solver.Nt,
            'inner_radius_mm': solver.r0,
            'lambda_mm': solver.lam_mm,
            'rise_mm': solver.rise,
            'radial_rings': radial_rings if closed else None,
            'angular_segments': angular_segments if closed else None,
        },
    }


def _validate_2000():
    """Быстрая самопроверка: 2000 мм³ при 50°."""
    from .render import tilt_matrix

    mw = tilt_matrix(math.radians(50.0), 270.0)
    res = build_mixed_meniscus(mw, 2000.0, return_bmesh=False,
                               method='clipped')
    print('--- mixed meniscus 2000 mm3 @ 50 deg ---')
    print(f"level     = {res['level_mm']:.6f} мм")
    print(f"volume    = {res['volume_mm3']:.6f} мм³")
    print(f"error     = {res['error_mm3']:.6f} мм³")
    print(f"rise      = {res['rise_mm']:.6f} мм")
    print(f"mixed     = {res['mixed']}")
    print(f"spilled   = {res['spilled']}")

    # Предельные режимы: полный мениск при большом объёме.
    res_full = build_mixed_meniscus(mw, 3000.0, return_bmesh=False,
                                    method='clipped')
    print('--- 3000 mm3 (full contact) ---')
    print(f"level     = {res_full['level_mm']:.6f} мм")
    print(f"volume    = {res_full['volume_mm3']:.6f} мм³")
    print(f"mixed     = {res_full['mixed']}")

    # Непрерывность по объёму.
    print('--- sweep ---')
    prev = None
    for target in (1800.0, 1900.0, 2000.0, 2100.0, 2200.0, 2300.0):
        r = build_mixed_meniscus(mw, target, return_bmesh=False,
                                 method='clipped')
        step = '' if prev is None else f" ΔL={r['level_mm'] - prev:+.6f}"
        print(f"{target:7.0f} -> L={r['level_mm']:.6f} "
              f"V={r['volume_mm3']:.4f} mixed={r['mixed']}{step}")
        prev = r['level_mm']


class ClippedMeniscusSolver(MixedMeniscusSolver):
    """Быстрый прототип: осесимметричный мениск, обрезанный дном.

    Поверхность `phi(r) = L + zeta(r) - zeta(0)` берётся из
    `meniscus.solve_meniscus()`, а смешанный контакт получается
    геометрически: в точках, где `phi < b(r, theta)`, жидкость
    отсутствует.  Это не решает 2D-уравнение, но даёт непрерывную
    поверхность, корректный объём и приемлемую первую форму мениска.

    Класс использует геометрию `MixedMeniscusSolver`, но не запускает
    его PDE-солвер.
    """

    def __init__(self, matrix_world, liquid_config=None,
                 radius_mm=LIQUID_RADIUS, z0_mm=LIQUID_Z0,
                 volume_nodes=320, volume_angles=384):
        super().__init__(
            matrix_world, liquid_config,
            radius_mm=radius_mm, z0_mm=z0_mm,
            radial_nodes=2, angular_nodes=8,
            inner_radius_mm=0.5, max_sweeps=1,
        )
        self.Nv = int(volume_nodes)
        self.Nvt = int(volume_angles)
        self.r_vol = np.linspace(0.0, self.R, self.Nv + 1)
        self.theta_vol = np.linspace(0.0, 2.0 * math.pi, self.Nvt,
                                     endpoint=False)
        self.zeta_vol = (np.interp(self.r_vol, self._r_prof,
                                   self._z_prof) - self._z_prof0)
        self.b0 = self.tz + self.az * self.z0
        self._nq = 200

    def _volume_theta(self, level_mm, theta):
        """Объём для одного азимута: точное пересечение с дном по r."""
        b1 = self.exz * math.cos(theta) + self.eyz * math.sin(theta)
        D = (level_mm + self.zeta_vol) - (self.b0 + b1 * self.r_vol)
        pos = D > 0.0
        if not np.any(pos):
            return 0.0
        cuts = np.where(np.diff(np.r_[False, pos, False].astype(np.int8)))[0]
        total = 0.0
        n = len(self.r_vol)

        def root(a, b, da, db):
            for _ in range(60):
                m = 0.5 * (a + b)
                dm = (level_mm + np.interp(m, self._r_prof,
                                           self._z_prof) - self._z_prof0
                      - (self.b0 + b1 * m))
                if (da > 0.0) == (dm > 0.0):
                    a, da = m, dm
                else:
                    b, db = m, dm
            return 0.5 * (a + b)

        for k in range(0, len(cuts), 2):
            i0, i1 = int(cuts[k]), int(cuts[k + 1] - 1)
            ra = (0.0 if i0 == 0
                  else root(self.r_vol[i0 - 1], self.r_vol[i0],
                            D[i0 - 1], D[i0]))
            rb = (self.R if i1 == n - 1
                  else root(self.r_vol[i1], self.r_vol[i1 + 1],
                            D[i1], D[i1 + 1]))
            if rb <= ra:
                continue
            rq = np.linspace(ra, rb, self._nq)
            ph = (level_mm + np.interp(rq, self._r_prof, self._z_prof)
                  - self._z_prof0)
            f = rq * (ph - (self.b0 + b1 * rq)) / self.az
            total += float(np.trapz(f, rq))
        return total

    def volume_at_level(self, level_mm):
        return float(2.0 * math.pi / self.Nvt
                     * sum(self._volume_theta(level_mm, t)
                           for t in self.theta_vol))

    def solve_level(self, target_mm3, tol_mm3=0.02, max_iter=80):
        lo = self.bottom_min_mm() - 2.0
        hi = self.spill_level_mm()
        v_hi = self.volume_at_level(hi)
        if target_mm3 >= v_hi:
            return hi, v_hi, True
        for _ in range(max_iter):
            mid = 0.5 * (lo + hi)
            v = self.volume_at_level(mid)
            if abs(v - target_mm3) <= tol_mm3:
                return mid, v, False
            if v < target_mm3:
                lo = mid
            else:
                hi = mid
        mid = 0.5 * (lo + hi)
        return mid, self.volume_at_level(mid), False

    def _world_vertex(self, x_mm, y_mm, top_mm):
        z_local = (top_mm - self.tz - self.exz * x_mm
                   - self.eyz * y_mm) / self.az
        p = self.mw @ Vector((x_mm * MM, y_mm * MM, z_local * MM))
        return p

    def build_cap_bmesh(self, level_mm, radial_rings=120,
                        angular_segments=REVOLVE_SEGMENTS):
        """Верхняя менисковая поверхность (крышка) в мировых координатах."""
        nr = int(radial_rings)
        nt = int(angular_segments)
        r = np.linspace(0.0, self.R, nr + 1)
        th = np.linspace(0.0, 2.0 * math.pi, nt, endpoint=False)
        X, Y = self._x_y(r, th)
        phi = self.axisymmetric_phi_mm(level_mm, r)
        B = self.bottom_mm(X, Y)
        top = np.maximum(phi[:, None], B)
        zl = (top - self.tz - self.exz * X - self.eyz * Y) / self.az

        bm = bmesh.new()
        center_p = self._world_vertex(0.0, 0.0, top[0, 0])
        center = bm.verts.new(center_p)
        rings = []
        for i in range(1, nr + 1):
            ring = []
            for j in range(nt):
                p = self._world_vertex(X[i, j], Y[i, j], top[i, j])
                ring.append(bm.verts.new(p))
            rings.append(ring)

        for j in range(nt):
            jn = (j + 1) % nt
            try:
                bm.faces.new((center, rings[0][j], rings[0][jn]))
            except ValueError:
                pass
        for i in range(len(rings) - 1):
            for j in range(nt):
                jn = (j + 1) % nt
                try:
                    bm.faces.new((rings[i][j], rings[i][jn],
                                  rings[i + 1][jn], rings[i + 1][j]))
                except ValueError:
                    pass

        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-9)
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        if bm.faces:
            nz = sum(f.normal.z for f in bm.faces)
            if nz < 0:
                bmesh.ops.reverse_faces(bm, faces=bm.faces)
        return bm


def build_mixed_liquid(matrix_world, target_volume_mm3, liquid_config=None, *,
                       radial_rings=120,
                       angular_segments=REVOLVE_SEGMENTS,
                       tol_mm3=0.02, mesh_tol_mm3=0.03, max_corr=6,
                       volume_nodes=320, volume_angles=384):
    """Замкнутый меш только для режима «стенка + дно».

    Возвращает `None`, если целевой объём не попадает в смешанный режим
    (полный мениск, плоский клин у дна или перелив): в этих случаях
    `liquid.build_liquid()` использует старый путь.  Для смешанного
    режима возвращает bmesh и метаданные, совместимые с `build_liquid()`.
    """
    solver = ClippedMeniscusSolver(
        matrix_world, liquid_config,
        volume_nodes=volume_nodes, volume_angles=volume_angles)
    level, _vol1d, spilled = solver.solve_level(
        target_volume_mm3, tol_mm3=tol_mm3)
    mixed = bool(level + solver.rise <= solver.bottom_max_mm() + 1e-9)
    flat = bool(level + solver.rise <= solver.bottom_min_mm() + 1e-9)
    if spilled or not mixed or flat:
        return None

    bm, level, vol, spilled, mixed, flat = solver.build_liquid_for_target(
        target_volume_mm3, radial_rings=radial_rings,
        angular_segments=angular_segments, tol_mm3=tol_mm3,
        mesh_tol_mm3=mesh_tol_mm3, max_corr=max_corr,
        level_mm=level, spilled=spilled)
    if spilled or not mixed or flat:
        bm.free()
        return None

    return {
        'bmesh': bm,
        'level_mm': float(level),
        'volume_mm3': float(vol),
        'error_mm3': float(vol - target_volume_mm3),
        'spilled': False,
        'flat': False,
        'mixed': True,
        'rise_mm': float(solver.rise),
        'contact_mm': float(level + solver.rise),
        'bottom_max_mm': float(solver.bottom_max_mm()),
        'bottom_min_mm': float(solver.bottom_min_mm()),
        'lambda_mm': float(solver.lam_mm),
        'wall_contact': None,
        'bottom_contact': None,
        'info': {
            'method': 'clipped',
            'closed': True,
            'radial_nodes': solver.Nv,
            'angular_nodes': solver.Nvt,
            'inner_radius_mm': solver.r0,
            'lambda_mm': solver.lam_mm,
            'rise_mm': solver.rise,
            'radial_rings': radial_rings,
            'angular_segments': angular_segments,
        },
    }


if __name__ == '__main__':
    _validate_2000()

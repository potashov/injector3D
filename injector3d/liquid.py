"""Объём жидкости внутри трубки (пункты 2 и 3 плана).

Жидкость — это полость трубки, пересечённая с полупространством
`z_world <= level_mm`, где `z_world` — мировая z-координата.  Благодаря
этому свободная поверхность остаётся горизонтальной при любом наклоне
трубки, а на стенке получается эллиптическая смоченная кромка, как у
реальной пробирки.

Свободная поверхность не плоская, а загнута вверх у стенки — мениск
(пункт 3).  Мениск добавляет объём, поэтому уровень подбирается бисекцией
по объёму **вместе с мениском**, иначе заполнение систематически
завышалось бы на ~2.8 %.

Меш строится в мировых координатах и перестраивается под каждый ракурс
(`render.render_tube()` меняет поворот трубки и вызывает перестройку).
Объект жидкости сознательно **не** делается дочерним трубке: тогда уровень
наклонялся бы вместе с ней.
"""

import bpy
import bmesh
import math

from mathutils import Matrix, Vector

from .meniscus import solve_meniscus
from .config import (
    MM,
    INNER_RADIUS,
    HEIGHT,
    BOTTOM_THICKNESS,
    REVOLVE_SEGMENTS,
    MENISCUS_RINGS,
)

# Зазор отделяет поверхность жидкости от внутренней стенки трубки: в Cycles
# строго совпадающие поверхности дают z-fighting и артефакты рефракции.
# 20 мкм неразличимы при масштабе сцены.
CLEARANCE = 0.02


def capacity_mm3():
    """Номинальная вместимость полости, мм³."""
    return math.pi * INNER_RADIUS ** 2 * (HEIGHT - BOTTOM_THICKNESS)


def target_volume_mm3(fill_fraction):
    """Целевой объём жидкости по доле вместимости, мм³."""
    return capacity_mm3() * min(max(fill_fraction, 0.0), 1.0)


def _equal_area_radius(r_mm, segments=REVOLVE_SEGMENTS):
    """Радиус n-угольника, площадь которого равна площади круга радиуса r."""
    return r_mm * math.sqrt(2 * math.pi
                            / (segments * math.sin(2 * math.pi / segments)))


# --- Кэш решения мениска ---
_MENISCUS_CACHE = {}


def _meniscus_profile(R_mm):
    """Кэшированное решение Юнга-Лапласа: dict с r, z, delta_V_mm3."""
    key = round(R_mm, 9)
    if key not in _MENISCUS_CACHE:
        _MENISCUS_CACHE[key] = solve_meniscus(R_mm)
    return _MENISCUS_CACHE[key]


def _zeta_at(prof, r_mm):
    """Линейная интерполяция ζ(r) по решению ОДУ."""
    rs, zs = prof['r'], prof['z']
    if r_mm <= rs[0]:
        return zs[0]
    if r_mm >= rs[-1]:
        return zs[-1]
    i = min(int(r_mm / rs[-1] * (len(rs) - 1)), len(rs) - 2)
    t = (r_mm - rs[i]) / (rs[i + 1] - rs[i])
    return zs[i] + t * (zs[i + 1] - zs[i])


# --- Построение меша ---

def _build_flat_bmesh(mw, level_mm, segments):
    """Плоский горизонт: цилиндр ∩ {z_world <= level_mm}."""
    bm = bmesh.new()
    r = _equal_area_radius(INNER_RADIUS - CLEARANCE, segments) * MM
    z0 = (BOTTOM_THICKNESS + CLEARANCE) * MM
    z1 = HEIGHT * MM

    bmesh.ops.create_cone(
        bm, cap_ends=True, cap_tris=False, segments=segments,
        radius1=r, radius2=r, depth=z1 - z0,
    )
    for v in bm.verts:
        v.co.z += (z0 + z1) / 2.0
    bmesh.ops.transform(bm, matrix=mw, verts=bm.verts)

    zs = [v.co.z for v in bm.verts]
    level = level_mm * MM
    if level <= min(zs) + 1e-9:
        bm.free()
        return None
    if level >= max(zs) - 1e-9:
        return bm

    bmesh.ops.bisect_plane(
        bm, geom=bm.verts[:] + bm.edges[:] + bm.faces[:],
        plane_co=(0.0, 0.0, level), plane_no=(0.0, 0.0, 1.0),
        clear_outer=True,
    )
    bmesh.ops.holes_fill(bm, edges=bm.edges[:], sides=0)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    if bm.calc_volume(signed=True) < 0:
        bmesh.ops.reverse_faces(bm, faces=bm.faces)
    return bm


def _build_meniscus_bmesh(mw, level_mm, segments):
    """Параметрический меш с мениском.

    level_mm — мировой уровень горизонта на оси трубки (мм).

    Алгоритм:
    1. Строим цилиндр в локальных координатах трубки (ось Z совпадает с осью трубки).
    2. Срезаем по плоскости z_local = h_level — это горизонт в системе трубки.
    3. Вместо плоской крышки строим менисковую поверхность.
    4. Трансформируем весь меш через mw.
    """
    z0_m = (BOTTOM_THICKNESS + CLEARANCE) * MM
    level_m = level_mm * MM
    h_level_m = (level_m - mw[2][3]) / mw[2][2]

    if h_level_m <= z0_m + 1e-9:
        return None

    r = (INNER_RADIUS - CLEARANCE) * MM

    bm = bmesh.new()

    # 1. Плоский цилиндр в локальных координатах (ось Z вдоль трубки)
    bmesh.ops.create_cone(
        bm, cap_ends=True, cap_tris=False, segments=segments,
        radius1=r, radius2=r, depth=h_level_m - z0_m,
    )
    for v in bm.verts:
        v.co.z += z0_m

    # 2. Срезаем по горизонту (локальная координата)
    zs = [v.co.z for v in bm.verts]
    h = h_level_m
    if h <= min(zs) + 1e-9:
        bm.free()
        return None
    if h >= max(zs) - 1e-9:
        # Горизонт выше трубки
        return bm

    bmesh.ops.bisect_plane(
        bm, geom=bm.verts[:] + bm.edges[:] + bm.faces[:],
        plane_co=(0.0, 0.0, h), plane_no=(0.0, 0.0, 1.0),
        clear_outer=True,
    )
    bmesh.ops.holes_fill(bm, edges=bm.edges[:], sides=0)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    if bm.calc_volume(signed=True) < 0:
        bmesh.ops.reverse_faces(bm, faces=bm.faces)

    # 3. Заменяем верхнюю крышку мениском
    prof = _meniscus_profile(INNER_RADIUS)
    zeta0 = prof['z'][0]
    zeta_vals = [prof['z'][i] for i in range(len(prof['r']))]

    # Находим вершины верхней кромки
    top_verts = [v for v in bm.verts if abs(v.co.z - h) < 1e-9]
    if len(top_verts) < 3:
        return bm

    # Строим менисковые вершины
    men_verts = []
    for v in top_verts:
        x, y = v.co.x, v.co.y
        rr = math.sqrt(x * x + y * y)
        if rr < 1e-9:
            men_verts.append(v)
            continue
        zeta_r = _zeta_at(prof, rr / MM)
        dz = (zeta_r - zeta0) * MM
        v.co.z += dz
        men_verts.append(v)

    # 4. Трансформируем через mw
    bmesh.ops.transform(bm, matrix=mw, verts=bm.verts)

    return bm


def _is_vertical(mw):
    """Трубка вертикальна, если её ось совпадает с мировой Z."""
    return abs(mw[2][0]) < 1e-9 and abs(mw[2][1]) < 1e-9


def _liquid_bmesh(mw, level_mm, segments):
    """Построение меша жидкости (плоский или менисковый)."""
    if level_mm <= BOTTOM_THICKNESS + CLEARANCE:
        return None
    if level_mm >= HEIGHT - CLEARANCE:
        return _build_flat_bmesh(mw, level_mm, segments)
    # Для вертикальной трубки мениск осесимметричен и не влияет на форму
    # свободной поверхности в плоском сечении — используем плоский меш.
    if _is_vertical(mw):
        return _build_flat_bmesh(mw, level_mm, segments)
    return _build_meniscus_bmesh(mw, level_mm, segments)


# --- Объём ---

def volume_at_level(mw, level_mm, segments=REVOLVE_SEGMENTS):
    """Объём жидкости (мм³) при мировом уровне level_mm."""
    bm = _liquid_bmesh(mw, level_mm, segments)
    if bm is None:
        return 0.0
    vol = bm.calc_volume(signed=True) / (MM ** 3)
    bm.free()
    return vol


def spill_level_mm(mw):
    """Мировой z нижней точки верхней кромки."""
    r = INNER_RADIUS * MM
    z = HEIGHT * MM
    samples = 72
    zs = [(mw @ Vector((r * math.cos(2 * math.pi * i / samples),
                        r * math.sin(2 * math.pi * i / samples),
                        z))).z
          for i in range(samples)]
    return min(zs) / MM


def solve_level_for_volume(mw, target_mm3,
                           tol_mm3=0.02, max_iter=48,
                           segments=REVOLVE_SEGMENTS):
    """Бисекция мирового уровня по целевому объёму.

    Объём считается полностью через меш (с мениском, если включён),
    поэтому двойного учёта нет: бисекция просто ищет level_mm, при
    котором volume_at_level(mw, level_mm) ≈ target_mm3.

    Returns
    -------
    (level_mm, volume_mm3, spilled)
        spilled=True — даже у самой кромки объёма не хватает.
    """
    lo = BOTTOM_THICKNESS + CLEARANCE
    hi = spill_level_mm(mw)

    v_hi = volume_at_level(mw, hi, segments)
    if target_mm3 >= v_hi:
        return hi, v_hi, True

    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        v = volume_at_level(mw, mid, segments)
        if abs(v - target_mm3) <= tol_mm3:
            return mid, v, False
        if v < target_mm3:
            lo = mid
        else:
            hi = mid
    mid = 0.5 * (lo + hi)
    return mid, volume_at_level(mw, mid, segments), False


# --- API ---

def create_liquid(name="Liquid"):
    """Пустой объект жидкости; меш заполняет build_liquid()."""
    me = bpy.data.meshes.new(name + "_Mesh")
    obj = bpy.data.objects.new(name, me)
    bpy.context.collection.objects.link(obj)
    return obj


def build_liquid(obj, tube, target_mm3, segments=REVOLVE_SEGMENTS):
    """Перестраивает меш жидкости под текущую ориентацию трубки.

    Меш записывается в мировых координатах, поэтому объект жидкости
    остаётся в единичной трансформации и не зависит от иерархии.

    Returns
    -------
    dict с полями level_mm, volume_mm3, error_mm3, spilled, verts, faces.
    """
    mw = tube.matrix_world.copy()
    level, vol, spilled = solve_level_for_volume(
        mw, target_mm3, tol_mm3=0.02, segments=segments)

    bm = _liquid_bmesh(mw, level, segments)
    me = obj.data
    if bm is None:
        me.clear_geometry()
    else:
        bm.to_mesh(me)
        bm.free()
    me.update()
    obj.matrix_world = Matrix.Identity(4)

    return {
        'level_mm': level,
        'volume_mm3': vol,
        'error_mm3': vol - target_mm3,
        'spilled': spilled,
        'verts': len(me.vertices),
        'faces': len(me.polygons),
    }

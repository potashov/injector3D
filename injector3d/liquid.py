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

# Мениск решаем для фактического радиуса меша жидкости, а не номинального
# внутреннего радиуса трубки: иначе объём мениска систематически завышался бы.
MENISCUS_RADIUS = INNER_RADIUS - CLEARANCE


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

# Кэш шаблона менисковой крышки: для заданной ориентации трубки форма
# горизонтального сечения не зависит от уровня (пока срез идёт по стенке).
# Шаблон хранит постоянную объёмную поправку и позволяет искать уровень
# по дешёвому плоскому телу, не пересобирая мениск на каждой итерации.
_MENISCUS_TEMPLATE_CACHE = {}


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


def meniscus_wall_rise_mm():
    """Подъём жидкости у стенки относительно уровня на оси, мм."""
    prof = _meniscus_profile(MENISCUS_RADIUS)
    return prof['z'][-1] - prof['z'][0]


def meniscus_volume_mm3():
    """Объём жидкости между плоскостью уровня на оси и мениском, мм³."""
    return _meniscus_profile(MENISCUS_RADIUS)['d_net']


def _meniscus_rise_mm(distance_mm):
    """Подъём свободной поверхности на расстоянии от стенки, мм.

    distance_mm = 0 у стенки (максимальный подъём), растёт к оси.
    """
    prof = _meniscus_profile(MENISCUS_RADIUS)
    r_mm = MENISCUS_RADIUS - max(0.0, distance_mm)
    return _zeta_at(prof, r_mm) - prof['z'][0]


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


def _build_meniscus_bmesh(mw, level_mm, segments, rings=MENISCUS_RINGS):
    """Меш жидкости с мениском, сохраняющим мировой горизонт.

    level_mm — мировой уровень жидкости на оси (мм).

    Поверхность строится в мировых координатах: сначала полость режется
    горизонтальной плоскостью на уровне кромки контакта
    (level + подъём у стенки), затем плоская крышка заменяется радиальной
    сеткой.  Высота каждой точки зависит только от расстояния до стенки,
    поэтому при наклоне трубки поверхность не наклоняется вместе с ней,
    а мениск подстраивается под эллиптическую кромку.
    """
    rise_mm = meniscus_wall_rise_mm()
    contact_mm = level_mm + rise_mm
    contact_m = contact_mm * MM

    # Плоскость кромки контакта должна целиком пересекать боковую стенку.
    # Иначе (малое заполнение / сильный наклон / перелив) мениск не
    # помещается — используем плоский горизонт.
    z0_m = (BOTTOM_THICKNESS + CLEARANCE) * MM
    r_liq_m = (INNER_RADIUS - CLEARANCE) * MM
    slope = math.hypot(mw[2][0], mw[2][1])
    max_bottom_m = mw[2][3] + mw[2][2] * z0_m + r_liq_m * slope
    if contact_m <= max_bottom_m + 1e-9:
        return _build_flat_bmesh(mw, level_mm, segments)
    if contact_m >= spill_level_mm(mw) * MM - 1e-9:
        return _build_flat_bmesh(mw, level_mm, segments)

    bm = _build_flat_bmesh(mw, contact_mm, segments)
    if bm is None:
        return None

    # Верхняя крышка среза — плоская, на уровне кромки контакта.
    cap_faces = [f for f in bm.faces
                 if abs(f.calc_center_median().z - contact_m) < 1e-6
                 and f.normal.z > 0.5]
    boundary = list({v for f in cap_faces for v in f.verts})
    if not cap_faces or len(boundary) < 3:
        return bm

    # Центр эллипса среза — пересечение оси трубки с плоскостью контакта.
    center_local_z = (contact_m - mw[2][3]) / mw[2][2]
    center = mw @ Vector((0.0, 0.0, center_local_z))
    cx, cy = center.x, center.y
    boundary.sort(key=lambda v: math.atan2(v.co.y - cy, v.co.x - cx))

    bmesh.ops.delete(bm, geom=cap_faces, context='FACES_ONLY')

    n = len(boundary)
    dist_mm = [math.hypot(v.co.x - cx, v.co.y - cy) / MM
               for v in boundary]
    center_v = bm.verts.new((cx, cy, level_mm * MM))

    ring_prev = None
    for k in range(1, rings):
        s = k / rings
        ring = []
        for j, bv in enumerate(boundary):
            px = cx + s * (bv.co.x - cx)
            py = cy + s * (bv.co.y - cy)
            d_mm = (1.0 - s) * dist_mm[j]
            pz = (level_mm + _meniscus_rise_mm(d_mm)) * MM
            ring.append(bm.verts.new((px, py, pz)))

        if ring_prev is None:
            for j in range(n):
                bm.faces.new((center_v, ring[j], ring[(j + 1) % n]))
        else:
            for j in range(n):
                jn = (j + 1) % n
                bm.faces.new((ring_prev[j], ring_prev[jn],
                              ring[jn], ring[j]))
        ring_prev = ring

    if ring_prev is None:
        for j in range(n):
            bm.faces.new((center_v, boundary[j], boundary[(j + 1) % n]))
    else:
        for j in range(n):
            jn = (j + 1) % n
            bm.faces.new((ring_prev[j], ring_prev[jn],
                          boundary[jn], boundary[j]))

    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    if bm.calc_volume(signed=True) < 0:
        bmesh.ops.reverse_faces(bm, faces=bm.faces)

    return bm


def _liquid_bmesh(mw, level_mm, segments):
    """Построение меша жидкости (плоский или менисковый)."""
    if level_mm <= bottom_level_min_mm(mw) + 1e-9:
        return None
    if level_mm >= HEIGHT - CLEARANCE:
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


def bottom_level_min_mm(mw):
    """Мировой z самой низкой точки дна полости.

    Для наклонённой трубки горизонт может опускаться ниже
    BOTTOM_THICKNESS + CLEARANCE: жидкость занимает клин у нижнего края.
    """
    z0_m = (BOTTOM_THICKNESS + CLEARANCE) * MM
    r_liq_m = (INNER_RADIUS - CLEARANCE) * MM
    slope = math.hypot(mw[2][0], mw[2][1])
    return (mw[2][3] + mw[2][2] * z0_m - r_liq_m * slope) / MM


def _meniscus_valid(mw, level_mm):
    """Полный эллиптический срез: кромка не у дна и не у перелива."""
    contact_m = (level_mm + meniscus_wall_rise_mm()) * MM
    z0_m = (BOTTOM_THICKNESS + CLEARANCE) * MM
    r_liq_m = (INNER_RADIUS - CLEARANCE) * MM
    slope = math.hypot(mw[2][0], mw[2][1])
    max_bottom_m = mw[2][3] + mw[2][2] * z0_m + r_liq_m * slope
    return (contact_m > max_bottom_m + 1e-9
            and contact_m < spill_level_mm(mw) * MM - 1e-9)


def _template_key(mw, segments, rings):
    """Ключ шаблона: ориентация + дискретизация."""
    return (
        tuple(round(float(mw[i][j]), 9) for i in range(4) for j in range(4)),
        int(segments),
        int(rings),
    )


def _meniscus_template(mw, segments, rings):
    """Шаблон мениска для данной ориентации трубки.

    Форма эллиптической кромки не зависит от уровня, пока срез идёт по
    цилиндрической стенке. Поэтому `ΔV_under` (объём между плоскостью
    кромки и мениском) постоянен, а бисекцию уровня можно вести по
    дешёвому плоскому телу.

    Returns
    -------
    dict | None
        {'rise', 'delta_under', 'level0'} или None, если мениск на этой
        ориентации не помещается (у дна/перелива).
    """
    key = _template_key(mw, segments, rings)
    if key in _MENISCUS_TEMPLATE_CACHE:
        return _MENISCUS_TEMPLATE_CACHE[key]

    lo = bottom_level_min_mm(mw)
    hi = spill_level_mm(mw)
    level = 0.5 * (lo + hi)
    if not _meniscus_valid(mw, level):
        level = hi - meniscus_wall_rise_mm() - 1e-3
    if not _meniscus_valid(mw, level):
        _MENISCUS_TEMPLATE_CACHE[key] = None
        return None

    rise = meniscus_wall_rise_mm()
    contact = level + rise

    bm_flat = _build_flat_bmesh(mw, contact, segments)
    if bm_flat is None:
        _MENISCUS_TEMPLATE_CACHE[key] = None
        return None
    v_flat = bm_flat.calc_volume(signed=True) / (MM ** 3)
    bm_flat.free()

    bm_men = _build_meniscus_bmesh(mw, level, segments, rings)
    if bm_men is None:
        _MENISCUS_TEMPLATE_CACHE[key] = None
        return None
    v_men = bm_men.calc_volume(signed=True) / (MM ** 3)
    bm_men.free()

    delta = v_flat - v_men
    template = {
        'rise': rise,
        'delta_under': delta,
        'level0': level,
    }
    _MENISCUS_TEMPLATE_CACHE[key] = template
    return template


def fast_volume_at_level(mw, level_mm, segments=REVOLVE_SEGMENTS,
                         rings=MENISCUS_RINGS):
    """Объём через плоское тело и постоянную поправку мениска.

    Использует шаблон мениска; вне полного эллиптического среза
    возвращается к точному `volume_at_level()`.
    """
    template = _meniscus_template(mw, segments, rings)
    if template is None or not _meniscus_valid(mw, level_mm):
        return volume_at_level(mw, level_mm, segments)

    contact = level_mm + template['rise']
    bm = _build_flat_bmesh(mw, contact, segments)
    if bm is None:
        return 0.0
    v_flat = bm.calc_volume(signed=True) / (MM ** 3)
    bm.free()
    return v_flat - template['delta_under']


def solve_level_for_volume(mw, target_mm3,
                           tol_mm3=0.02, max_iter=48,
                           segments=REVOLVE_SEGMENTS,
                           fast=False,
                           rings=MENISCUS_RINGS):
    """Бисекция мирового уровня по целевому объёму.

    При `fast=True` используется кэшированный шаблон мениска: объём
    считается как плоское тело до кромки минус постоянная `ΔV_under`.
    Это убирает пересборку мениска на каждой итерации бисекции.
    Вне полного эллиптического среза автоматически используется точный
    `volume_at_level()`.

    Returns
    -------
    (level_mm, volume_mm3, spilled)
        spilled=True — даже у самой кромки объёма не хватает.
    """
    lo = bottom_level_min_mm(mw)
    hi = spill_level_mm(mw)

    def volume_fn(level):
        if fast:
            return fast_volume_at_level(mw, level, segments, rings)
        return volume_at_level(mw, level, segments)

    v_hi = volume_fn(hi)
    if target_mm3 >= v_hi:
        return hi, v_hi, True

    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        v = volume_fn(mid)
        if abs(v - target_mm3) <= tol_mm3:
            return mid, v, False
        if v < target_mm3:
            lo = mid
        else:
            hi = mid
    mid = 0.5 * (lo + hi)
    return mid, volume_fn(mid), False


# --- API ---

def create_liquid(name="Liquid"):
    """Пустой объект жидкости; меш заполняет build_liquid()."""
    me = bpy.data.meshes.new(name + "_Mesh")
    obj = bpy.data.objects.new(name, me)
    bpy.context.collection.objects.link(obj)
    return obj


def build_liquid(obj, tube, target_mm3, segments=REVOLVE_SEGMENTS,
                 use_fast=True):
    """Перестраивает меш жидкости под текущую ориентацию трубки.

    Меш записывается в мировых координатах, поэтому объект жидкости
    остаётся в единичной трансформации и не зависит от иерархии.

    При `use_fast=True` уровень ищется по кэшированному шаблону мениска
    (плоское тело + постоянная ΔV_under), а полный меш собирается один
    раз. Финальный объём возвращается по фактическому мешу.

    Returns
    -------
    dict с полями level_mm, volume_mm3, error_mm3, spilled, verts, faces.
    """
    mw = tube.matrix_world.copy()
    level, _solver_vol, spilled = solve_level_for_volume(
        mw, target_mm3, tol_mm3=0.02, segments=segments, fast=use_fast)

    bm = _liquid_bmesh(mw, level, segments)
    me = obj.data
    if bm is None:
        me.clear_geometry()
        vol = 0.0
    else:
        vol = bm.calc_volume(signed=True) / (MM ** 3)
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

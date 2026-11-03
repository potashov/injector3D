"""Объём жидкости внутри трубки (пункт 2 плана).

Жидкость — это полость трубки, пересечённая с полупространством
`z <= level`, где `z` — **мировые** координаты.  Благодаря этому свободная
поверхность остаётся горизонтальной при любом наклоне трубки, а на стенке
получается эллиптическая смоченная кромка, как у реальной пробирки.

Меш строится в мировых координатах и перестраивается под каждый ракурс
(`render.render_tube()` меняет поворот трубки и вызывает перестройку).
Объект жидкости сознательно **не** делается дочерним трубке: тогда уровень
наклонялся бы вместе с ней.

Уровень подбирается бисекцией по целевому объёму.  Объём от уровня монотонно
возрастает, а наклон массы не меняет — то есть один и тот же объём жидкости
даёт разный мировой уровень в зависимости от угла.
"""

import bpy
import bmesh
import math

from mathutils import Matrix, Vector

from .config import (
    MM,
    INNER_RADIUS,
    HEIGHT,
    BOTTOM_THICKNESS,
    REVOLVE_SEGMENTS,
)

# Зазор отделяет поверхность жидкости от внутренней стенки трубки: в Cycles
# строго совпадающие поверхности дают z-fighting и артефакты рефракции.
# 20 мкм неразличимы при масштабе сцены.
CLEARANCE = 0.02

EPS_MM = 1e-6


def capacity_mm3():
    """Номинальная вместимость полости, мм³.

    Скруглениями внутреннего дна и верхней кромки (суммарно < 6 мм³ из
    ~29500 мм³, < 0.02%) здесь пренебрегаем.
    """
    return math.pi * INNER_RADIUS ** 2 * (HEIGHT - BOTTOM_THICKNESS)


def target_volume_mm3(fill_fraction):
    """Целевой объём жидкости по доле вместимости, мм³."""
    return capacity_mm3() * min(max(fill_fraction, 0.0), 1.0)


def _equal_area_radius(r_mm, segments=REVOLVE_SEGMENTS):
    """Радиус n-угольника, площадь которого равна площади круга радиуса r.

    Меш — 192-угольник, его площадь меньше площади круга на ~0.085%, и без
    компенсации объём жидкости систематически недобором уходил бы от
    номинальных 19.5 мм внутреннего диаметра.
    """
    return r_mm * math.sqrt(2 * math.pi
                            / (segments * math.sin(2 * math.pi / segments)))


def _circle_z_range(matrix_world, radius_mm, z_local_mm, samples=72):
    """Диапазон мировых z окружности радиуса radius на высоте z_local.

    Нужен, чтобы по наклону найти нижнюю/верхнюю точку дна и кромки.
    """
    r = radius_mm * MM
    z = z_local_mm * MM
    zs = [(matrix_world @ Vector((r * math.cos(2 * math.pi * i / samples),
                                  r * math.sin(2 * math.pi * i / samples),
                                  z))).z
          for i in range(samples)]
    return min(zs), max(zs)


def _liquid_bmesh(matrix_world, level_mm, segments=REVOLVE_SEGMENTS):
    """bmesh жидкости: цилиндр полости ∩ {z <= level_mm}, в мировых координатах.

    Возвращает None, если горизонт ниже самой низкой точки дна (жидкости нет).
    """
    r = _equal_area_radius(INNER_RADIUS - CLEARANCE, segments) * MM
    z0 = (BOTTOM_THICKNESS + CLEARANCE) * MM
    z1 = HEIGHT * MM

    bm = bmesh.new()
    # create_cone даёт цилиндр с закрытыми торцами вдоль локальной оси Z
    bmesh.ops.create_cone(
        bm, cap_ends=True, cap_tris=False, segments=segments,
        radius1=r, radius2=r, depth=z1 - z0,
    )
    for v in bm.verts:
        v.co.z += (z0 + z1) / 2.0
    bmesh.ops.transform(bm, matrix=matrix_world, verts=bm.verts)

    zs = [v.co.z for v in bm.verts]
    level = level_mm * MM
    if level <= min(zs) + EPS_MM * MM:
        bm.free()
        return None
    if level >= max(zs) - EPS_MM * MM:
        # Горизонт выше полости — жидкость заполняет её целиком.
        return bm

    bmesh.ops.bisect_plane(
        bm,
        geom=bm.verts[:] + bm.edges[:] + bm.faces[:],
        plane_co=(0.0, 0.0, level),
        plane_no=(0.0, 0.0, 1.0),
        clear_outer=True,          # отбрасываем всё, что выше горизонта
    )
    # Срез оставляет открытую границу (эллипс) — стягиваем её одной гранью:
    # она плоская, поэтому для Cycles этого достаточно.
    bmesh.ops.holes_fill(bm, edges=bm.edges[:], sides=0)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    if bm.calc_volume(signed=True) < 0:
        bmesh.ops.reverse_faces(bm, faces=bm.faces)
    return bm


def volume_at_level(matrix_world, level_mm, segments=REVOLVE_SEGMENTS):
    """Объём жидкости (мм³) при мировом уровне горизонта level_mm."""
    bm = _liquid_bmesh(matrix_world, level_mm, segments)
    if bm is None:
        return 0.0
    vol = bm.calc_volume(signed=True) / (MM ** 3)
    bm.free()
    return vol


def spill_level_mm(matrix_world):
    """Мировой z нижней точки верхней кромки.

    Выше этого уровня жидкость начинает переливаться через край: при наклоне
    край с одной стороны поднимается, с другой опускается, и удержать
    столб выше самой низкой точки кромки физически невозможно.
    """
    lo, _ = _circle_z_range(matrix_world, INNER_RADIUS, HEIGHT)
    return lo / MM


def solve_level_for_volume(matrix_world, target_mm3,
                           tol_mm3=0.02, max_iter=48,
                           segments=REVOLVE_SEGMENTS):
    """Бисекция уровня по целевому объёму.

    Returns
    -------
    (level_mm, volume_mm3, spilled)
        spilled=True — даже у самой кромки объёма не хватает, то есть
        заданное заполнение для этого угла переливается через край.
    """
    lo, _ = _circle_z_range(matrix_world, INNER_RADIUS, BOTTOM_THICKNESS)
    lo /= MM
    hi = spill_level_mm(matrix_world)

    v_hi = volume_at_level(matrix_world, hi, segments)
    if target_mm3 >= v_hi:
        return hi, v_hi, True

    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        v = volume_at_level(matrix_world, mid, segments)
        if abs(v - target_mm3) <= tol_mm3:
            return mid, v, False
        if v < target_mm3:
            lo = mid
        else:
            hi = mid
    mid = 0.5 * (lo + hi)
    return mid, volume_at_level(matrix_world, mid, segments), False


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

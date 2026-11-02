"""Геометрия трубки.

Создаёт полую цилиндрическую трубку с точным профилем поперечного
сечения и скруглёнными кромками (fillets).  Профиль задаётся в
локальной системе координат (X = радиаль, Y = вдоль оси трубки) и
потом revolves вокруг оси Y.
"""

import bpy
import math
import bmesh
from mathutils import Vector, Matrix

from .config import (
    MM,
    INNER_RADIUS,
    OUTER_RADIUS,
    HEIGHT,
    BOTTOM_THICKNESS,
    FILLET_OUTER_BOTTOM,
    FILLET_INNER_BOTTOM,
    FILLET_OUTER_TOP,
    FILLET_INNER_TOP,
    ARC_SEGMENTS,
    REVOLVE_SEGMENTS,
)


# ------------------------------------------------------------------
# Утилиты
# ------------------------------------------------------------------

def _arc_points(
    cx: float,
    cy: float,
    radius: float,
    start_angle: float,
    end_angle: float,
    segments: int,
) -> list[Vector]:
    """Возвращает точки дуги (CW если end < start, CCW если end > start)."""
    pts: list[Vector] = []
    for i in range(segments + 1):
        t = i / segments
        angle = start_angle + t * (end_angle - start_angle)
        pts.append(Vector((cx + radius * math.cos(angle),
                           cy + radius * math.sin(angle))))
    return pts


# ------------------------------------------------------------------
# 2D-профиль трубки
# ------------------------------------------------------------------

def _build_profile() -> list[Vector]:
    """Строит замкнутый 2D-профиль трубки в координатах X-Y.

    X — радиальное направление (от центра трубки),
    Y — вдоль оси трубки (от дна к верху).

    Профиль обходается по часовой стрелке (CW), начиная от точки
    начала внешнего нижнего скругления.  При revolve вокруг оси Y
    этот контур создаёт объём трубки.

    Порядок (CW):
      1. Скругление внешнего нижнего угла
      2. Дно (внешнее)
      3. Центральная ось
      4. Внутреннее дно
      5. Скругление внутреннего нижнего угла
      6. Внутренняя стенка (вверх)
      7. Скругление внутреннего верхнего угла
      8. Верхняя кромка
      9. Скругление внешнего верхнего угла
      10. Внешняя стенка (вниз)
    """
    pts: list[Vector] = []

    # --- 1. Скругление внешнего нижнего угла ---
    # Центр скругления: (R_o - f_o_b, f_o_b)
    # Дуга от (R_o, f_o_b) до (R_o - f_o_b, 0)
    cx = OUTER_RADIUS - FILLET_OUTER_BOTTOM
    cy = FILLET_OUTER_BOTTOM
    pts.extend(_arc_points(cx, cy, FILLET_OUTER_BOTTOM,
                           0, -math.pi / 2, ARC_SEGMENTS))

    # --- 2. Дно (внешнее): от скругления к центру ---
    pts.append(Vector((cx, 0.0)))  # (R_o - f_o_b, 0)
    pts.append(Vector((0.0, 0.0)))

    # --- 3. Центральная ось: от дна к внутреннему дну ---
    pts.append(Vector((0.0, BOTTOM_THICKNESS)))

    # --- 4. Внутреннее дно: от центра к скруглению ---
    pts.append(Vector((INNER_RADIUS + FILLET_INNER_BOTTOM, BOTTOM_THICKNESS)))

    # --- 5. Скругление внутреннего нижнего угла ---
    # Центр: (R_i + f_i_b, t_b - f_i_b)
    # Дуга от (R_i + f_i_b, t_b) до (R_i, t_b - f_i_b)
    cx = INNER_RADIUS + FILLET_INNER_BOTTOM
    cy = BOTTOM_THICKNESS - FILLET_INNER_BOTTOM
    pts.extend(_arc_points(cx, cy, FILLET_INNER_BOTTOM,
                           math.pi / 2, math.pi, ARC_SEGMENTS))

    # --- 6. Внутренняя стенка (вверх) ---
    pts.append(Vector((INNER_RADIUS, cy)))  # (R_i, t_b - f_i_b)
    pts.append(Vector((INNER_RADIUS, HEIGHT - FILLET_INNER_TOP)))

    # --- 7. Скругление внутреннего верхнего угла ---
    # Центр: (R_i + f_i_t, H - f_i_t)
    # Дуга от (R_i, H - f_i_t) до (R_i + f_i_t, H)
    cx = INNER_RADIUS + FILLET_INNER_TOP
    cy = HEIGHT - FILLET_INNER_TOP
    pts.extend(_arc_points(cx, cy, FILLET_INNER_TOP,
                           math.pi, math.pi / 2, ARC_SEGMENTS))

    # --- 8. Верхняя кромка ---
    pts.append(Vector((cx, HEIGHT)))  # (R_i + f_i_t, H)
    pts.append(Vector((OUTER_RADIUS - FILLET_OUTER_TOP, HEIGHT)))

    # --- 9. Скругление внешнего верхнего угла ---
    # Центр: (R_o - f_o_t, H - f_o_t)
    # Дуга от (R_o - f_o_t, H) до (R_o, H - f_o_t)
    cx = OUTER_RADIUS - FILLET_OUTER_TOP
    cy = HEIGHT - FILLET_OUTER_TOP
    pts.extend(_arc_points(cx, cy, FILLET_OUTER_TOP,
                           -math.pi / 2, 0, ARC_SEGMENTS))

    # --- 10. Внешняя стенка (вниз) ---
    pts.append(Vector((OUTER_RADIUS, cy)))  # (R_o, H - f_o_t)
    pts.append(Vector((OUTER_RADIUS, FILLET_OUTER_BOTTOM)))

    return pts


# ------------------------------------------------------------------
# Revolve + создание объекта
# ------------------------------------------------------------------

def _revolve_profile(profile: list[Vector]) -> bmesh.types.BMesh:
    """Вращает профиль вокруг оси Y и возвращает готовый bmesh."""
    bm = bmesh.new()
    angle_step = math.tau / REVOLVE_SEGMENTS

    # Создаём вершины: для каждой точки профиля и каждого угла поворота
    verts: list[bmesh.types.BMVert] = []
    for j in range(REVOLVE_SEGMENTS):
        angle = j * angle_step
        rot = Matrix.Rotation(angle, 4, 'Y')
        for p in profile:
            p_rot = rot @ Vector((p.x, p.y, 0.0))
            verts.append(bm.verts.new(p_rot))

    bm.verts.ensure_lookup_table()
    n = len(profile)

    # Рёбра по профилю (для каждого j)
    for j in range(REVOLVE_SEGMENTS):
        for i in range(n - 1):
            idx = j * n + i
            bm.edges.new(verts[idx], verts[idx + 1])

    # Рёбра по вращению (для каждой точки профиля)
    for i in range(n):
        for j in range(REVOLVE_SEGMENTS - 1):
            idx1 = j * n + i
            idx2 = (j + 1) * n + i
            bm.edges.new(verts[idx1], verts[idx2])

    # Полигоны между рёбрами профиля
    for j in range(REVOLVE_SEGMENTS - 1):
        for i in range(n - 1):
            v1 = verts[j * n + i]
            v2 = verts[j * n + i + 1]
            v3 = verts[(j + 1) * n + i + 1]
            v4 = verts[(j + 1) * n + i]
            try:
                bm.faces.new([v1, v2, v3, v4])
            except ValueError:
                pass  # самопересекающийся полигон, пропускаем

    # Нижний торец (j = 0)
    bottom_ring = [verts[j * n + i] for i in range(n)]
    try:
        bm.faces.new(bottom_ring)
    except ValueError:
        pass

    # Верхний торец (j = REVOLVE_SEGMENTS - 1)
    top_ring = [verts[(REVOLVE_SEGMENTS - 1) * n + i] for i in range(n)]
    try:
        bm.faces.new(top_ring)
    except ValueError:
        pass

    return bm


# ------------------------------------------------------------------
# Публичный API
# ------------------------------------------------------------------

def create_tube(name: str = "Tube") -> bpy.types.Object:
    """Создаёт и возвращает объект трубки.

    Parameters
    ----------
    name : str
        Имя создаваемого объекта.

    Returns
    -------
    bpy.types.Object
        Созданный меш-объект трубки.
    """
    profile = _build_profile()
    bm = _revolve_profile(profile)

    me = bpy.data.meshes.new(name + "_Mesh")
    bm.to_mesh(me)
    bm.free()
    me.update()
    # Нормали
    me.calc_normals_split()

    obj = bpy.data.objects.new(name, me)
    bpy.context.collection.objects.link(obj)
    return obj

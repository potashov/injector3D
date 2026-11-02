"""Геометрия трубки.

Создаёт полую цилиндрическую трубку с точным профилем поперечного
сечения и скруглёнными кромками (fillets).  Профиль задаётся в
локальной системе координат (X = радиаль, Z = вдоль оси трубки) и
потом revolves вокруг оси Z (согласно ТЗ: ось трубки — +Z).

Алгоритм: сетка «профиль × угол» со схлопыванием вершин на оси —
даёт 100% manifold-меш по построению.
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


def _arc_points(cx, cy, radius, start_angle, end_angle, segments):
    """Возвращает точки дуги."""
    pts = []
    for i in range(segments + 1):
        t = i / segments
        angle = start_angle + t * (end_angle - start_angle)
        pts.append(Vector((cx + radius * math.cos(angle),
                           cy + radius * math.sin(angle))))
    return pts


def _build_profile():
    """Строит замкнутый 2D-профиль трубки в координатах X-Z.

    Порядок:
      1. Внешнее нижнее скругление
      2. Дно (внешнее)
      3. Центральная ось
      4. Внутреннее дно
      5. Внутреннее нижнее скругление
      6. Внутренняя стенка (вверх)
      7. Внутреннее верхнее скругление
      8. Верхняя кромка
      9. Внешнее верхнее скругление
      10. Внешняя стенка (вниз)
    """
    pts = []

    # 1. Внешнее нижнее скругление
    cx = OUTER_RADIUS - FILLET_OUTER_BOTTOM
    cy = FILLET_OUTER_BOTTOM
    pts.extend(_arc_points(cx, cy, FILLET_OUTER_BOTTOM,
                           0, -math.pi / 2, ARC_SEGMENTS))

    # 2. Дно (внешнее): от скругления к центру
    pts.append(Vector((cx, 0.0)))
    pts.append(Vector((0.0, 0.0)))

    # 3. Центральная ось
    pts.append(Vector((0.0, BOTTOM_THICKNESS)))

    # 4. Внутреннее дно
    pts.append(Vector((INNER_RADIUS + FILLET_INNER_BOTTOM, BOTTOM_THICKNESS)))

    # 5. Внутреннее нижнее скругление
    cx = INNER_RADIUS + FILLET_INNER_BOTTOM
    cy = BOTTOM_THICKNESS - FILLET_INNER_BOTTOM
    pts.extend(_arc_points(cx, cy, FILLET_INNER_BOTTOM,
                           math.pi / 2, math.pi, ARC_SEGMENTS))

    # 6. Внутренняя стенка (вверх)
    pts.append(Vector((INNER_RADIUS, cy)))
    pts.append(Vector((INNER_RADIUS, HEIGHT - FILLET_INNER_TOP)))

    # 7. Внутреннее верхнее скругление
    cx = INNER_RADIUS + FILLET_INNER_TOP
    cy = HEIGHT - FILLET_INNER_TOP
    pts.extend(_arc_points(cx, cy, FILLET_INNER_TOP,
                           math.pi, math.pi / 2, ARC_SEGMENTS))

    # 8. Верхняя кромка
    pts.append(Vector((cx, HEIGHT)))
    pts.append(Vector((OUTER_RADIUS - FILLET_OUTER_TOP, HEIGHT)))

    # 9. Внешнее верхнее скругление
    cx = OUTER_RADIUS - FILLET_OUTER_TOP
    cy = HEIGHT - FILLET_OUTER_TOP
    pts.extend(_arc_points(cx, cy, FILLET_OUTER_TOP,
                           -math.pi / 2, 0, ARC_SEGMENTS))

    # 10. Внешняя стенка (вниз)
    pts.append(Vector((OUTER_RADIUS, cy)))
    pts.append(Vector((OUTER_RADIUS, FILLET_OUTER_BOTTOM)))

    # Убираем подряд идущие дубликаты
    prof = []
    for q in pts:
        if prof and (q - prof[-1]).length < 1e-9:
            continue
        prof.append(q)
    # Убираем замыкающий дубликат первой точки
    if prof and (prof[0] - prof[-1]).length < 1e-9:
        prof.pop()

    return prof


def create_tube(name="Tube"):
    """Создаёт и возвращает объект трубки.

    Использует сетку «профиль × угол» со схлопыванием вершин на оси
    вращения — даёт 100% manifold-меш по построению.
    """
    prof = _build_profile()
    N = len(prof)

    # Находим точки профиля, лежащие на оси вращения (x ≈ 0)
    axis_idx = {i for i, q in enumerate(prof) if abs(q.x) < 1e-12}

    bm = bmesh.new()

    # Осевые вершины: по одной на каждую ось-точку (общие для всех углов)
    axis_v = {}
    for i in axis_idx:
        axis_v[i] = bm.verts.new(Vector((0.0, 0.0, prof[i].y * MM)))

    # Строим сетку: для каждого угла поворачиваем профиль вокруг Z
    grid = []
    for j in range(REVOLVE_SEGMENTS):
        a = j * 2 * math.pi / REVOLVE_SEGMENTS
        ca, sa = math.cos(a), math.sin(a)
        col = []
        for i, q in enumerate(prof):
            if i in axis_v:
                col.append(axis_v[i])
            else:
                col.append(bm.verts.new(
                    Vector((q.x * MM * ca, q.x * MM * sa, q.y * MM))))
        grid.append(col)

    def _face(a, b, c, d):
        """Создаёт полигон, объединяя дубликаты вершин."""
        vs = []
        for v in (a, b, c, d):
            if v not in vs:
                vs.append(v)
        if len(vs) < 3:
            return None
        try:
            return bm.faces.new(vs)
        except ValueError:
            return None

    # Создаём полигоны
    for j in range(REVOLVE_SEGMENTS):
        jn = (j + 1) % REVOLVE_SEGMENTS
        for i in range(N):
            inext = (i + 1) % N
            _face(grid[j][i], grid[j][inext],
                  grid[jn][inext], grid[jn][i])

    # Чистка
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-9)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)

    # Создаём меш
    me = bpy.data.meshes.new(name + "_Mesh")
    bm.to_mesh(me)
    bm.free()
    me.update()

    obj = bpy.data.objects.new(name, me)
    bpy.context.collection.objects.link(obj)
    return obj

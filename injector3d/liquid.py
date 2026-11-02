"""Объём жидкости внутри трубки.

На этом шаге жидкость — простой цилиндр, соосный трубке.  Горизонтальный
срез в мировых координатах (чтобы уровень оставался горизонтальным при
наклоне трубки) добавляется на шаге 2.

Зазор `CLEARANCE` отделяет поверхность жидкости от внутренней стенки
трубки: в Cycles строго совпадающие поверхности дают z-fighting и
артефакты рефракции.  20 мкм неразличимы при масштабе сцены.
"""

import bpy
import bmesh
import math

from .config import (
    MM,
    INNER_RADIUS,
    HEIGHT,
    BOTTOM_THICKNESS,
)

CLEARANCE = 0.02  # зазор до стенки и дна, мм


def create_liquid(name="Liquid", fill_level=0.5):
    """Создаёт цилиндр жидкости внутри трубки.

    Parameters
    ----------
    name : str
        Имя объекта.
    fill_level : float
        Доля заполненной внутренней высоты, от 0.0 до 1.0.

    Returns
    -------
    bpy.types.Object
        manifold-цилиндр жидкости, дно которого стоит на внутреннем дне.
    """
    r = (INNER_RADIUS - CLEARANCE) * MM
    z0 = (BOTTOM_THICKNESS + CLEARANCE) * MM
    inner_height = (HEIGHT - BOTTOM_THICKNESS) * MM
    z1 = z0 + inner_height * max(0.0, min(1.0, fill_level))

    bm = bmesh.new()
    # create_cone даёт цилиндр с закрытыми торцами вдоль локальной оси Z
    bmesh.ops.create_cone(
        bm,
        cap_ends=True,
        cap_tris=False,
        segments=192,
        radius1=r,
        radius2=r,
        depth=(z1 - z0),
    )
    # create_cone центрирован в начале координат — поднимаем на нужную высоту
    for v in bm.verts:
        v.co.z += (z0 + z1) / 2.0

    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)

    me = bpy.data.meshes.new(name + "_Mesh")
    bm.to_mesh(me)
    bm.free()
    me.update()

    obj = bpy.data.objects.new(name, me)
    bpy.context.collection.objects.link(obj)
    return obj


def liquid_volume_mm3(fill_level):
    """Расчётный объём жидкости, мм³ (для отчётности и подбора уровня)."""
    r = INNER_RADIUS - CLEARANCE
    return math.pi * r * r * (HEIGHT - BOTTOM_THICKNESS) * fill_level

"""Материалы трубки и жидкости.

Создаёт два материала:
  - mat_plastic: прозрачный пластик (PMMA/акрил)
  - mat_liquid: жидкость с объёмным поглощением (вода)

Оба используют Principled BSDF + Volume Absorption через Material Output.
"""

import bpy

from .config import IOR_PMMA


def create_material_plastic(name="Plastic"):
    """Создаёт материал прозрачного пластика (PMMA).

    Principled BSDF:
      - Transmission Weight: 1.0 (полностью прозрачный)
      - IOR: 1.49 (PMMA/акрил)
      - Roughness: 0.0 (идеально гладкий)
      - Base Color: 0.95, 0.95, 0.95 (слегка тёплый белый)
    """
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree

    # Очищаем стандартные ноды
    for n in nt.nodes:
        nt.nodes.remove(n)

    # Principled BSDF
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.name = "Plastic_BSDF"
    bsdf.inputs["Transmission Weight"].default_value = 1.0
    bsdf.inputs["IOR"].default_value = IOR_PMMA
    bsdf.inputs["Roughness"].default_value = 0.0
    bsdf.inputs["Base Color"].default_value = (0.95, 0.95, 0.95, 1.0)

    # Material Output
    mout = nt.nodes.new("ShaderNodeOutputMaterial")
    mout.name = "Material_Output"

    # Связь: BSDF → Surface
    nt.links.new(bsdf.outputs["BSDF"], mout.inputs["Surface"])

    return mat


def create_material_liquid(name="Liquid"):
    """Создаёт материал жидкости с объёмным поглощением.

    Principled BSDF:
      - Transmission Weight: 1.0 (жидкость прозрачна)
      - IOR: 1.33 (вода)
      - Roughness: 0.0 (идеально гладкая поверхность)
      - Base Color: 1.0, 1.0, 0.794 (слабо жёлтый)

    Volume Absorption:
      - Color: 1.0, 1.0, 0.42 (тот же тон, но синий занижен сильнее)
      - Density: 20.0

    Оттенок (RGB 73,73,58) задан как *тон*, а не как абсолютный цвет:
    B/R = 58/73 = 0.794 при R = G = 1.0. Если подставить 73,73,58 как
    есть, в линейном пространстве это (0.067, 0.067, 0.042) — жидкость
    пропустит ~7 % света и станет почти чёрной.

    Base Color задаёт тон преломлённого света и не зависит от длины пути,
    поэтому он и виден в первую очередь. Volume Absorption добавляет тот
    же тон зависящий от толщины слоя (у донышка желтизна сильнее).
    Density в Cycles считается на метр, а слой жидкости — 19 мм, поэтому
    рабочий порядок — десятки: при 0.2 оптическая толщина 0.004 и тон не
    проявился бы вовсе.

    В объёме синий занижен до 0.42 (сильнее, чем 0.794 из замера):
    отражения и преломления в стенке трубки подмешивают к жидкости
    белый свет и вытягивают B/R кадра вверх. Подобрано по медиане
    пикселей тела жидкости: 0.794 -> B/R 0.902, 0.62 -> 0.863,
    0.50 -> 0.843, 0.42 -> 0.824 при цели 0.794.
    """
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree

    # Очищаем стандартные ноды
    for n in nt.nodes:
        nt.nodes.remove(n)

    # Тон жидкости: целевой sRGB (73, 73, 58) даёт B/R = 0.794, но белые
    # блики и отражения вытягивают B/R в кадре вверх, поэтому синий в
    # объёме занижен сильнее, чем в замере.
    tint = (1.0, 1.0, 58.0 / 73.0, 1.0)
    tint_vol = (1.0, 1.0, 0.42, 1.0)

    # Principled BSDF
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.name = "Liquid_BSDF"
    bsdf.inputs["Transmission Weight"].default_value = 1.0
    bsdf.inputs["IOR"].default_value = 1.33  # вода
    bsdf.inputs["Roughness"].default_value = 0.0
    bsdf.inputs["Base Color"].default_value = tint

    # Volume Absorption
    vol = nt.nodes.new("ShaderNodeVolumeAbsorption")
    vol.name = "VolAbsorb"
    vol.inputs["Color"].default_value = tint_vol
    vol.inputs["Density"].default_value = 20.0

    # Material Output
    mout = nt.nodes.new("ShaderNodeOutputMaterial")
    mout.name = "Material_Output"

    # Связи: BSDF → Surface, Volume → Volume
    nt.links.new(bsdf.outputs["BSDF"], mout.inputs["Surface"])
    nt.links.new(vol.outputs["Volume"], mout.inputs["Volume"])

    return mat

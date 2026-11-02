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
      - Base Color: 0.8, 0.9, 1.0 (голубоватый оттенок)

    Volume Absorption:
      - Color: 0.8, 0.9, 1.0 (голубоватый)
      - Density: 0.5 (умеренное поглощение)
    """
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree

    # Очищаем стандартные ноды
    for n in nt.nodes:
        nt.nodes.remove(n)

    # Principled BSDF
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.name = "Liquid_BSDF"
    bsdf.inputs["Transmission Weight"].default_value = 1.0
    bsdf.inputs["IOR"].default_value = 1.33  # вода
    bsdf.inputs["Roughness"].default_value = 0.0
    bsdf.inputs["Base Color"].default_value = (0.8, 0.9, 1.0, 1.0)

    # Volume Absorption
    vol = nt.nodes.new("ShaderNodeVolumeAbsorption")
    vol.name = "VolAbsorb"
    vol.inputs["Color"].default_value = (0.8, 0.9, 1.0, 1.0)
    vol.inputs["Density"].default_value = 0.5

    # Material Output
    mout = nt.nodes.new("ShaderNodeOutputMaterial")
    mout.name = "Material_Output"

    # Связи: BSDF → Surface, Volume → Volume
    nt.links.new(bsdf.outputs["BSDF"], mout.inputs["Surface"])
    nt.links.new(vol.outputs["Volume"], mout.inputs["Volume"])

    return mat

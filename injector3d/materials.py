"""Материалы трубки и жидкости.

Создаёт два материала:
  - mat_plastic: прозрачный пластик (PMMA/акрил)
  - mat_liquid: жидкость с объёмным поглощением (вода)

Оба используют Principled BSDF + Volume Absorption через Material Output.
"""

import bpy

from .config import IOR_PMMA, get_liquid_config


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


def create_material_liquid(name="Liquid", liquid_config=None):
    """Создаёт материал жидкости с объёмным поглощением.

    Principled BSDF + Volume Absorption.
    Параметры берутся из liquid_config (по умолчанию пресет 'water').
    """
    cfg = liquid_config or get_liquid_config('water')

    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree

    # Очищаем стандартные ноды
    for n in nt.nodes:
        nt.nodes.remove(n)

    tint = tuple(cfg['base_color'])
    tint_vol = tuple(cfg['absorption_color'])
    density = float(cfg['absorption_density'])
    ior = float(cfg['ior'])

    # Principled BSDF
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.name = "Liquid_BSDF"
    bsdf.inputs["Transmission Weight"].default_value = 1.0
    bsdf.inputs["IOR"].default_value = ior
    bsdf.inputs["Roughness"].default_value = 0.0
    bsdf.inputs["Base Color"].default_value = tint

    # Volume Absorption
    vol = nt.nodes.new("ShaderNodeVolumeAbsorption")
    vol.name = "VolAbsorb"
    vol.inputs["Color"].default_value = tint_vol
    vol.inputs["Density"].default_value = density

    # Material Output
    mout = nt.nodes.new("ShaderNodeOutputMaterial")
    mout.name = "Material_Output"

    # Связи: BSDF → Surface, Volume → Volume
    nt.links.new(bsdf.outputs["BSDF"], mout.inputs["Surface"])
    nt.links.new(vol.outputs["Volume"], mout.inputs["Volume"])

    return mat

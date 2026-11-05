"""Калибровка WORLD_STRENGTH по яркости и оттенку тела жидкости.

Воспроизводит реальный конвейер шага 4 (тот же композитор с Lensdist и та же
постобработка), поэтому метрики напрямую сравнимы с кадрами output/4/.
Каждый прогон — отдельная сцена: WORLD_STRENGTH ставится в узел Background
после setup_scene(), который читает значение из config.
"""

import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bpy

from injector3d import config as cfg
from injector3d.create_scene import validate_liquid
from injector3d.liquid import build_liquid, create_liquid, target_volume_mm3
from injector3d.materials import create_material_liquid, create_material_plastic
from injector3d.render import _configure_compositor, _postprocess
from injector3d.scene import setup_scene
from injector3d.tube_geometry import create_tube

# 0.06 ≈ световой тон исходного серого мира (0.16,0.17,0.19)×0.35 = 0.056,
# но нейтральный по цвету — так видно вклад именно цвета, а не силы.
# Дальше — вверх до заведомого пересвета (3.0), чтобы увидеть кривую.
# Уточнённая сетка: между 0.06 и 0.30 был перелом от 53 до 145 яркости,
# цель ~73 — нужно 0.10–0.16.
STRENGTHS = (0.06, 0.10, 0.12, 0.14, 0.16, 0.20, 0.25, 0.30, 0.60, 1.20, 2.50)
OUT_ROOT = '/tmp/world_calib'
TARGET = cfg.WORLD_COLOR

os.makedirs(OUT_ROOT, exist_ok=True)

for s in STRENGTHS:
    camera = setup_scene()

    world = bpy.context.scene.world
    bg = world.node_tree.nodes.get('Background')
    bg.inputs['Color'].default_value = TARGET
    bg.inputs['Strength'].default_value = s

    tube = create_tube('Tube')
    tube.data.materials.append(create_material_plastic('Plastic'))

    liquid = create_liquid('Liquid')
    liquid.data.materials.append(create_material_liquid('Liquid'))
    target_mm3 = target_volume_mm3(cfg.FILL_FRACTION)
    info = build_liquid(liquid, tube, target_mm3)
    validate_liquid(liquid, target_mm3, info)

    scene = bpy.context.scene
    _configure_compositor(scene)
    scene.render.resolution_x, scene.render.resolution_y = cfg.RENDER_RESOLUTION
    scene.render.resolution_percentage = 100
    scene.cycles.samples = cfg.SCENE_CAMERA['samples']
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = 'PNG'
    scene.render.image_settings.color_mode = 'RGBA'

    tag = f'{s:.2f}'
    base = os.path.join(OUT_ROOT, tag)
    os.makedirs(base, exist_ok=True)
    raw = os.path.join(base, 'raw.png')
    final = os.path.join(base, 'upright.png')
    scene.render.filepath = raw
    bpy.ops.render.render(write_still=True)
    _postprocess(raw, final)
    os.remove(raw)
    print(f'WORLD_CALIB strength={tag} -> {final}', flush=True)

print('=== DONE ===', flush=True)

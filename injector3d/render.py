"""Рендер трубки под разными углами наклона.

Имена файлов уникальны: <step>_<ракурс>_<timestamp>.png/.blend
"""

import bpy
import math
import os
import shutil
import subprocess
import tempfile
import time

from .config import (
    OUTPUT_DIR,
    PROJECT_DIR,
    RENDER_RESOLUTION,
    SCENE_CAMERA,
    SCENE_POSTPROCESS,
    TILT_VARIANTS,
)


def _configure_compositor(scene):
    """Включает barrel-дисторсию без обрезки кадра под неё."""
    tree = scene.node_tree
    tree.nodes.clear()

    layers = tree.nodes.new('CompositorNodeRLayers')
    lens = tree.nodes.new('CompositorNodeLensdist')
    lens.use_fit = False
    lens.inputs['Dispersion'].default_value = 0.0
    lens.inputs['Distortion'].default_value = SCENE_CAMERA['distortion']
    composite = tree.nodes.new('CompositorNodeComposite')
    tree.links.new(layers.outputs['Image'], lens.inputs['Image'])
    tree.links.new(lens.outputs['Image'], composite.inputs['Image'])


def _postprocess(raw_path, final_path):
    """Заливает чёрные полосы Lensdist белым через системный python3."""
    script = os.path.join(PROJECT_DIR, 'postprocess_reference.py')
    python = shutil.which('python3') or 'python3'
    subprocess.run([
        python, script, raw_path, final_path,
        '--shift', str(SCENE_POSTPROCESS['shift_x_px']),
        '--height', str(SCENE_POSTPROCESS['target_height_px']),
    ], check=True)


def render_tube(tube, camera, step='1', on_tilt=None):
    """Рендер трубки под каждым из TILT_VARIANTS.

    Parameters
    ----------
    tube : bpy.types.Object
        Объект трубки.
    camera : bpy.types.Object
        Объект камеры.
    step : str
        Номер шага для именования файлов.
    on_tilt : callable | None
        Вызывается после установки поворота трубки — перестроить жидкость
        под новую ориентацию (её уровень горизонтален в мировых координатах).
    """
    ts = time.strftime('%Y%m%d_%H%M%S')
    base = os.path.join(OUTPUT_DIR, step)
    os.makedirs(base, exist_ok=True)

    scene = bpy.context.scene
    scene.use_nodes = True
    _configure_compositor(scene)
    scene.render.resolution_x = RENDER_RESOLUTION[0]
    scene.render.resolution_y = RENDER_RESOLUTION[1]
    scene.render.resolution_percentage = 100
    scene.cycles.samples = SCENE_CAMERA['samples']
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = 'PNG'
    scene.render.image_settings.color_mode = 'RGBA'

    for variant_name, tilt_euler in TILT_VARIANTS:
        # Устанавливаем наклон
        tube.rotation_euler = (
            math.radians(tilt_euler[0]),
            math.radians(tilt_euler[1]),
            math.radians(tilt_euler[2]),
        )
        bpy.context.view_layer.update()
        if on_tilt is not None:
            on_tilt(variant_name, tilt_euler)

        # Рендер во временный RGBA, затем белый фон и дисторсия в постобработке
        render_fname = f'{step}_{variant_name}_{ts}.png'
        final_path = os.path.join(base, render_fname)
        raw_path = os.path.join(
            tempfile.gettempdir(),
            f'injector3d_{step}_{variant_name}_{ts}_raw.png',
        )
        scene.render.filepath = raw_path

        # Сохраняем .blend
        blend_fname = f'{step}_{variant_name}_{ts}.blend'
        blend_path = os.path.join(base, blend_fname)
        bpy.ops.wm.save_as_mainfile(filepath=blend_path)
        print(f'  Сохранён: {blend_fname}')

        bpy.ops.render.render(write_still=True)
        _postprocess(raw_path, final_path)
        os.remove(raw_path)
        print(f'  Рендер: {variant_name} -> {render_fname}')

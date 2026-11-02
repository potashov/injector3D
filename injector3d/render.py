"""Рендер трубки под разными углами наклона.

Имена файлов уникальны: <step>_<ракурс>_<timestamp>.png/.blend
"""

import bpy
import math
import os
import time

from .config import (
    OUTPUT_DIR,
    RENDER_RESOLUTION,
    TILT_VARIANTS,
)


def render_tube(tube, camera, step='1'):
    """Рендер трубки под каждым из TILT_VARIANTS.

    Parameters
    ----------
    tube : bpy.types.Object
        Объект трубки.
    camera : bpy.types.Object
        Объект камеры.
    step : str
        Номер шага для именования файлов.
    """
    ts = time.strftime('%Y%m%d_%H%M%S')
    base = os.path.join(OUTPUT_DIR, step)
    os.makedirs(base, exist_ok=True)

    for variant_name, tilt_euler in TILT_VARIANTS:
        # Устанавливаем наклон
        tube.rotation_euler = (
            math.radians(tilt_euler[0]),
            math.radians(tilt_euler[1]),
            math.radians(tilt_euler[2]),
        )
        bpy.context.view_layer.update()

        # Сохраняем .blend
        blend_fname = f'{step}_{variant_name}_{ts}.blend'
        blend_path = os.path.join(base, blend_fname)
        bpy.ops.wm.save_as_mainfile(filepath=blend_path)
        print(f'  Сохранён: {blend_fname}')

        # Рендер
        render_fname = f'{step}_{variant_name}_{ts}.png'
        bpy.context.scene.render.filepath = os.path.join(base, render_fname)
        bpy.context.scene.render.image_settings.file_format = 'PNG'
        bpy.context.scene.render.image_settings.color_mode = 'RGBA'
        bpy.context.scene.render.resolution_x = RENDER_RESOLUTION[0]
        bpy.context.scene.render.resolution_y = RENDER_RESOLUTION[1]

        bpy.ops.render.render(write_still=True)
        print(f'  Рендер: {variant_name} -> {render_fname}')

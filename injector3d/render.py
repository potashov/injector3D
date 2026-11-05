"""Рендер трубки под разными углами наклона.

Камера жёстко привязана к трубке, поэтому кадр не зависит от наклона.
Свет остаётся в мировых координатах, жидкость — тоже (уровень горизонтален).
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
    tilt_ring_variants,
)
from .scene import attach_camera_to_tube


def _configure_compositor(scene, distortion=None, fit=False):
    """Включает barrel-дисторсию без обрезки кадра под неё.

    В Blender 5.x композитор переехал из `scene.node_tree` в
    `scene.compositing_node_group`, а `CompositorNodeComposite` заменён на
    `NodeGroupOutput`. Для 4.3 сохранена ветка со старым API.
    """
    if distortion is None:
        distortion = SCENE_CAMERA['distortion']

    if hasattr(scene, 'compositing_node_group'):
        tree = bpy.data.node_groups.new(
            'injector3d_compositor', 'CompositorNodeTree')
        tree.interface.new_socket(
            name='Image', in_out='OUTPUT', socket_type='NodeSocketColor')
        scene.compositing_node_group = tree
        scene.render.use_compositing = True

        layers = tree.nodes.new('CompositorNodeRLayers')
        lens = tree.nodes.new('CompositorNodeLensdist')
        lens.inputs['Fit'].default_value = fit
        lens.inputs['Jitter'].default_value = False
        lens.inputs['Dispersion'].default_value = 0.0
        lens.inputs['Distortion'].default_value = distortion
        composite = tree.nodes.new('NodeGroupOutput')
    else:
        scene.use_nodes = True
        tree = scene.node_tree
        tree.nodes.clear()

        layers = tree.nodes.new('CompositorNodeRLayers')
        lens = tree.nodes.new('CompositorNodeLensdist')
        lens.use_fit = fit
        if hasattr(lens, 'use_jitter'):
            lens.use_jitter = False
        lens.inputs['Dispersion'].default_value = 0.0
        lens.inputs['Distortion'].default_value = distortion
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


def _prepare_render_scene():
    """Общие настройки кадра: разрешение, сэмплы, RGBA, композитор."""
    scene = bpy.context.scene
    _configure_compositor(scene)
    scene.render.resolution_x = RENDER_RESOLUTION[0]
    scene.render.resolution_y = RENDER_RESOLUTION[1]
    scene.render.resolution_percentage = 100
    scene.cycles.samples = SCENE_CAMERA['samples']
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = 'PNG'
    scene.render.image_settings.color_mode = 'RGBA'
    return scene


def _render_one(raw_path, final_path, variant_name):
    """Рендер одного кадра и внешняя постобработка."""
    bpy.ops.render.render(write_still=True)
    _postprocess(raw_path, final_path)
    os.remove(raw_path)
    print(f'  Рендер: {variant_name} -> {os.path.basename(final_path)}')


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

    attach_camera_to_tube(camera, tube)

    scene = _prepare_render_scene()

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

        _render_one(raw_path, final_path, variant_name)


def render_ring(tube, camera, rig, liquid_upright, liquid_tilted,
                step='4.5', n=4, angle=10.0):
    """Рендер N+1 кадров: вертикаль + N азимутов при угле `angle`.

    Иерархия:
      rig (вращение по Z = азимут)
      ├─ tube (локальный наклон θ по Y)
      │  └─ camera (parented в create_scene)
      ├─ liquid_upright (parented к rig, hide_render для наклонных)
      └─ liquid_tilted (parented к rig, hide_render для upright)

    Наклон θ задаётся в цикле через tube.rotation_euler.y — это
    Ry(θ) внутри Rz(φ) рига, итоговый угол от вертикали одинаков.
    Свет остаётся в мировых координатах.
    """
    variants = tilt_ring_variants(n, angle)
    tag = f'a{angle:g}_n{n}'
    ts = time.strftime('%Y%m%d_%H%M%S')
    base = os.path.join(OUTPUT_DIR, step)
    os.makedirs(base, exist_ok=True)

    scene = _prepare_render_scene()

    # Один master на серию; в кадр не сохраняем .blend по каждому ракурсу.
    rig.rotation_euler = (0.0, 0.0, 0.0)
    liquid_upright.hide_render = False
    liquid_tilted.hide_render = True
    bpy.context.view_layer.update()

    master_fname = f'{step}_master_{tag}_{ts}.blend'
    master_path = os.path.join(base, master_fname)
    bpy.ops.wm.save_as_mainfile(filepath=master_path)
    print(f'  Сохранён master: {master_fname}')

    angle_rad = math.radians(angle)
    for variant_name, azimuth_deg in variants:
        is_upright = azimuth_deg is None
        if is_upright:
            rig.rotation_euler = (0.0, 0.0, 0.0)
            tube.rotation_euler = (0.0, 0.0, 0.0)
            liquid_upright.hide_render = False
            liquid_tilted.hide_render = True
        else:
            rig.rotation_euler = (0.0, 0.0, math.radians(azimuth_deg))
            tube.rotation_euler = (0.0, angle_rad, 0.0)
            liquid_upright.hide_render = True
            liquid_tilted.hide_render = False
        bpy.context.view_layer.update()

        render_fname = f'{step}_{variant_name}_{tag}_{ts}.png'
        final_path = os.path.join(base, render_fname)
        raw_path = os.path.join(
            tempfile.gettempdir(),
            f'injector3d_{step}_{variant_name}_{tag}_{ts}_raw.png',
        )
        scene.render.filepath = raw_path
        _render_one(raw_path, final_path, variant_name)

    # Оставляем сцену в вертикальном положении для сохранения/отладки.
    rig.rotation_euler = (0.0, 0.0, 0.0)
    tube.rotation_euler = (0.0, 0.0, 0.0)
    liquid_upright.hide_render = False
    liquid_tilted.hide_render = True
    bpy.context.view_layer.update()

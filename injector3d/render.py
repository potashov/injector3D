"""Рендер трубки под разными углами наклона.

Камера во всех режимах жёстко привязана к трубке (`attach_camera_to_tube`
на этапе вертикальной сцены): трубка в кадре не меняется, наклоняется
только жидкость. Отличаются режимы типом поворота трубки:

- `render_tube()` (п. 1–4): наклон вокруг дна по историческим Эйлеровым
  `config.TILT_VARIANTS`;
- `render_ring()` (п. 4.5): наклон на θ в плоскости азимута φ вокруг точки
  опоры — `tilt_matrix(θ, φ) = Rz(φ)·Ry(θ)·Rz(φ)⁻¹` в точке опоры. Именно
  этот тип поворота даёт разные кадры: нормаль горизонта жидкости в системе
  координат трубки равна `Rz(φ)·Ry(θ)⁻¹·e_z` и обходит конус с полууглом θ.
  Вращение всей связки вокруг Z (`Rz(φ)·Ry(θ)`, старый AzimuthRig) такой
  нормали φ не даёт — все азимуты были бы идентичны.

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

from mathutils import Matrix, Vector

from .config import (
    MM,
    OUTPUT_DIR,
    PROJECT_DIR,
    RENDER_RESOLUTION,
    SCENE_CAMERA,
    SCENE_POSTPROCESS,
    TILT_PIVOT_Z_MM,
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


def tilt_matrix(theta_rad, azimuth_deg):
    """Мировая матрица трубки: наклон θ в плоскости азимута φ вокруг опоры.

    Поворот `R(φ) = Rz(φ)·Ry(θ)·Rz(φ)⁻¹` — это наклон на θ вокруг
    горизонтальной оси `a(φ) = (-sin φ, cos φ, 0)`. Ось трубки уходит в
    `(sin θ·cos φ, sin θ·sin φ, cos θ)`: угол θ от мировой Z одинаков при
    любом φ, азимут выбирает только плоскость наклона.

    Важно отличие от `Rz(φ)·Ry(θ)` (старый AzimuthRig): здесь нормаль
    горизонта жидкости в системе координат трубки `R(φ)⁻¹·e_z =
    Rz(φ)·Ry(θ)⁻¹·e_z` зависит от φ и обходит конус с полууглом θ, поэтому
    при жёстко привязанной камере кадры получаются разными.

    Поворот выполняется вокруг `P = (0, 0, TILT_PIVOT_Z_MM)` — середина
    трубки. При наклоне вокруг дна (z = 0) верхний край на θ = 30° уезжал
    бы вбок на ~46 мм.
    """
    phi = math.radians(azimuth_deg)
    pivot = Vector((0.0, 0.0, TILT_PIVOT_Z_MM * MM))
    axis = Vector((-math.sin(phi), math.cos(phi), 0.0))
    rot = Matrix.Rotation(theta_rad, 4, axis)
    return Matrix.Translation(pivot) @ rot @ Matrix.Translation(-pivot)


def render_ring(tube, liquid_upright, liquid_tilted,
                step='4.5', n=4, angle=10.0):
    """Рендер N+1 кадров: вертикаль + N плоскостей наклона при угле `angle`.

    Камера — дочерняя трубке (привязка сделана в `create_scene` на этапе
    вертикальной сцены) и в цикле не трогается вовсе: меняя единственный
    `tube.matrix_world`, мы перемещаем и наклоняем всю систему «трубка +
    камера» как единое целое.

    Жидкость — независимый меш в мировых координатах: её уровень
    горизонтален в мире и не должен наклоняться вместе с трубкой. Наклонный
    меш строится один раз при φ = 0 и для остальных φ поворачивается вокруг
    мировой Z. Это корректно, потому что `Rz(φ)` не меняет `z_world`, а
    полость и точка опоры лежат на оси Z, поэтому
        Rz(φ) · [M(0)(cavity) ∩ {z <= h}] = M(φ)(cavity) ∩ {z <= h}.
    Тождество закреплено в `check_liquid.py`, раздел 8.
    """
    variants = tilt_ring_variants(n, angle)
    tag = f'a{angle:g}_n{n}'
    ts = time.strftime('%Y%m%d_%H%M%S')
    base = os.path.join(OUTPUT_DIR, step)
    os.makedirs(base, exist_ok=True)

    scene = _prepare_render_scene()

    theta_rad = math.radians(angle)
    tube.matrix_world = Matrix.Identity(4)
    liquid_tilted.rotation_euler = (0.0, 0.0, 0.0)
    liquid_upright.hide_render = False
    liquid_tilted.hide_render = True
    bpy.context.view_layer.update()

    master_fname = f'{step}_master_{tag}_{ts}.blend'
    master_path = os.path.join(base, master_fname)
    bpy.ops.wm.save_as_mainfile(filepath=master_path)
    print(f'  Сохранён master: {master_fname}')

    for variant_name, azimuth_deg in variants:
        if azimuth_deg is None:
            tube.matrix_world = Matrix.Identity(4)
            liquid_upright.hide_render = False
            liquid_tilted.hide_render = True
        else:
            tube.matrix_world = tilt_matrix(theta_rad, azimuth_deg)
            liquid_tilted.rotation_euler = (0.0, 0.0, math.radians(azimuth_deg))
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
    tube.matrix_world = Matrix.Identity(4)
    liquid_tilted.rotation_euler = (0.0, 0.0, 0.0)
    liquid_upright.hide_render = False
    liquid_tilted.hide_render = True
    bpy.context.view_layer.update()

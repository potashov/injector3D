#!/usr/bin/env python3
"""Рендер кадра, повторяющего референс 06120.

Запуск:
    ./blender-4.3.2-linux-x64/blender --background --python render_reference.py

Результат: output/06120/06120_match_<timestamp>.png
Кадр 1200×1200, белый фон, трубка сдвинута вправо на 105 px.
"""

import math
import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bpy
from mathutils import Quaternion, Vector

from injector3d import config as cfg
from injector3d.liquid import (
    build_liquid,
    create_liquid,
    target_volume_mm3,
)
from injector3d.materials import create_material_liquid, create_material_plastic
from injector3d.scene import setup_scene
from injector3d.tube_geometry import create_tube


def _configure_camera(camera):
    p = cfg.REFERENCE_CAMERA
    for constraint in list(camera.constraints):
        camera.constraints.remove(constraint)

    target = Vector((0.0, 0.0, p['target_z_mm'] * cfg.MM))
    location = Vector((0.0, -p['distance_mm'] * cfg.MM,
                       p['camera_z_mm'] * cfg.MM))
    camera.location = location
    direction = target - location
    base = direction.to_track_quat('-Z', 'Y')
    roll = Quaternion((0.0, 0.0, 1.0), math.radians(p['roll_deg']))
    camera.rotation_mode = 'QUATERNION'
    camera.rotation_quaternion = base @ roll

    camera.data.lens = p['lens_mm']
    camera.data.sensor_width = p['sensor_width_mm']
    camera.data.sensor_height = p['sensor_height_mm']
    camera.data.sensor_fit = 'HORIZONTAL'
    camera.data.clip_start = 0.0001
    camera.data.dof.use_dof = True
    camera.data.dof.aperture_fstop = p['fstop']
    camera.data.dof.focus_distance = p['distance_mm'] * cfg.MM


def _configure_compositor(scene):
    p = cfg.REFERENCE_CAMERA
    scene.use_nodes = True
    tree = scene.node_tree
    tree.nodes.clear()

    layers = tree.nodes.new('CompositorNodeRLayers')
    lens = tree.nodes.new('CompositorNodeLensdist')
    lens.use_fit = False
    lens.inputs['Dispersion'].default_value = 0.0
    lens.inputs['Distortion'].default_value = p['distortion']
    composite = tree.nodes.new('CompositorNodeComposite')
    tree.links.new(layers.outputs['Image'], lens.inputs['Image'])
    tree.links.new(lens.outputs['Image'], composite.inputs['Image'])


def main():
    p = cfg.REFERENCE_CAMERA
    camera = setup_scene()

    tube = create_tube('Tube')
    tube.data.materials.append(create_material_plastic('Plastic'))
    liquid = create_liquid('Liquid')
    liquid.data.materials.append(create_material_liquid('Liquid'))
    build_liquid(liquid, tube, target_volume_mm3(cfg.FILL_FRACTION))

    _configure_camera(camera)
    _configure_compositor(bpy.context.scene)

    scene = bpy.context.scene
    scene.render.engine = 'CYCLES'
    scene.render.resolution_x, scene.render.resolution_y = cfg.REFERENCE_RESOLUTION
    scene.render.resolution_percentage = 100
    scene.cycles.samples = p['samples']
    scene.cycles.use_denoising = False
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = 'PNG'
    scene.render.image_settings.color_mode = 'RGBA'

    timestamp = time.strftime('%Y%m%d_%H%M%S')
    raw_path = os.path.join(tempfile.gettempdir(),
                            f'injector3d_ref_{timestamp}.png')
    final_path = os.path.join(cfg.REFERENCE_OUTPUT_DIR,
                              f'06120_match_{timestamp}.png')
    scene.render.filepath = raw_path
    bpy.ops.render.render(write_still=True)

    postprocess = os.path.join(cfg.PROJECT_DIR, 'postprocess_reference.py')
    python = shutil.which('python3') or 'python3'
    subprocess.run([
        python, postprocess, raw_path, final_path,
        '--shift', str(p['shift_x_px']),
        '--height', str(p['target_height_px']),
    ], check=True)
    os.remove(raw_path)
    print(f'  Готово: {final_path}')


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""
injector3d — генерация 3D-сцен трубок для Blender.

Шаг 1: Создание геометрии трубки с точным профилем и скруглёнными кромками.
"""

import bpy
import math
import os
import sys

# Добавляем корень проекта в путь
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from injector3d.config import (
    MM,
    INNER_DIAMETER,
    HEIGHT,
    WALL_THICKNESS,
    OUTPUT_DIR,
    RENDER_RESOLUTION,
    RENDER_SAMPLES,
    DENOISER,
    TILT_VARIANTS,
    CAMERA_POSITION,
    CAMERA_LENS,
    CAMERA_FSTOP,
    CAMERA_SENSOR,
    CAMERA_TARGET,
)
from injector3d.tube_geometry import create_tube


def setup_scene():
    """Настройка сцены и материалов."""
    # Очистка
    bpy.ops.wm.read_factory_settings(use_empty=True)

    # Cycles
    bpy.context.scene.render.engine = 'CYCLES'
    bpy.context.scene.cycles.device = 'CPU'
    bpy.context.scene.cycles.samples = RENDER_SAMPLES
    bpy.context.scene.cycles.preview_samples = 32
    bpy.context.scene.cycles.caustics_reflective = True
    bpy.context.scene.cycles.caustics_refractive = True
    bpy.context.scene.cycles.denoiser = DENOISER

    # Разрешаем все CPU-ядра
    prefs = bpy.context.preferences
    addon = prefs.addons.get('cycles')
    if addon:
        prefs.cycles.compute_device_type = 'CPU'
        prefs.cycles.devices[0].use = True

    # Камера
    bpy.ops.object.camera_add(location=CAMERA_POSITION)
    cam = bpy.context.active_object
    cam.name = "Camera"
    cam.data.lens = CAMERA_LENS
    cam.data.sensor_width = CAMERA_SENSOR
    cam.data.dof.use_dof = True
    cam.data.dof.aperture_fstop = CAMERA_FSTOP
    cam.data.dof.focus_distance = math.dist(CAMERA_POSITION, CAMERA_TARGET)

    # Следит за трубкой
    track = cam.constraints.new('TRACK_TO')
    track.target = None  # будет установлен после создания трубки
    track.track_axis = 'TRACK_NEGATIVE_Z'
    track.up_axis = 'UP_Y'

    # Освещение
    _setup_lighting()

    # Фон
    _setup_background()

    return cam


def _setup_lighting():
    """Студийное освещение."""
    lights = [
        ("Key", (0.8, -0.5, 0.6), 5000, 0.7),
        ("Fill", (-0.6, -0.4, 0.3), 2000, 0.4),
        ("Rim", (0.0, 0.8, 0.5), 3000, 0.5),
    ]
    for name, loc, energy, angle in lights:
        bpy.ops.object.light_add(type='AREA', location=loc)
        light = bpy.context.active_object
        light.name = name + "_Light"
        light.data.energy = energy
        light.data.size = 1.0
        light.data.angle_x = math.radians(angle)
        light.data.angle_y = math.radians(angle)


def _setup_background():
    """Серый градиентный фон."""
    world = bpy.data.worlds['World']
    world.use_nodes = True
    bg = world.node_tree.nodes['Background']
    bg.inputs['Color'].default_value = (0.15, 0.15, 0.15, 1.0)
    bg.inputs['Strength'].default_value = 1.0


def render_tube(tube, camera):
    """Рендер трубки под разными углами наклона."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    for variant_name, tilt_euler in TILT_VARIANTS:
        # Устанавливаем наклон
        tube.rotation_euler = (
            math.radians(tilt_euler[0]),
            math.radians(tilt_euler[1]),
            math.radians(tilt_euler[2]),
        )
        bpy.context.view_layer.update()

        # Сохраняем .blend
        blend_path = os.path.join(OUTPUT_DIR, "1_" + variant_name + ".blend")
        bpy.ops.wm.save_as_mainfile(filepath=blend_path)
        print(f"Saved: {blend_path}")

        # Рендер
        bpy.context.scene.render.filepath = os.path.join(
            OUTPUT_DIR, "1_" + variant_name + ".png"
        )
        bpy.context.scene.render.image_settings.file_format = 'PNG'
        bpy.context.scene.render.image_settings.color_mode = 'RGBA'
        bpy.context.scene.render.resolution_x = RENDER_RESOLUTION[0]
        bpy.context.scene.render.resolution_y = RENDER_RESOLUTION[1]

        bpy.ops.render.render(write_standalone=True)
        print(f"Rendered: {variant_name} (tilt={tilt_euler})")


def main():
    """Точка входа."""
    print("Setting up scene...")
    camera = setup_scene()
    print("Scene ready.")

    print("Creating tube geometry...")
    tube = create_tube("Tube")
    tube.data.name = "Tube_Mesh"
    print(f"Tube created: {tube.name} "
          f"(ID={INNER_DIAMETER}mm, H={HEIGHT}mm, "
          f"W={WALL_THICKNESS}mm)")

    # Устанавливаем камеру на трубку
    camera.constraints[0].target = tube

    print("Rendering...")
    render_tube(tube, camera)
    print("Done!")


if __name__ == "__main__":
    main()

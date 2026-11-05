"""Настройка сцены, камеры, освещения и Cycles.

Все линейные размеры — в метрах (Blender-юниты).
Камера на -Y, смотрит на трубку.
"""

import bpy
import addon_utils
import math

from mathutils import Quaternion, Vector

from .config import (
    MM,
    SCENE_CAMERA,
    MAX_BOUNCES,
    CLAMP_INDIRECT,
    RENDER_SAMPLES,
    RENDER_TIME_LIMIT_S,
    DENOISER,
    VIEW_TRANSFORM,
    VIEW_LOOK,
    GPU_DEVICE_FILTER,
    LIGHTS,
    WORLD_COLOR,
    WORLD_STRENGTH,
)


def _safe_set(owner, attr, value):
    """Ставит значение с try/except.

    В headless-сборке Blender 4.3 из Debian enum_items пуст, но
    прямое присваивание работает. Поэтому проверяем только
    наличие атрибута, а не список допустимых значений.
    """
    try:
        setattr(owner, attr, value)
    except TypeError:
        print(f"    ! {attr}={value!r} недоступен — пропущено")


def setup_render_devices():
    """Включает CUDA-устройства Cycles. Возвращает их количество.

    У Blender 4.3 нет `preferences.cycles`, доступ к настройкам Cycles
    идёт через словарь аддонов. get_devices_for_type('CUDA') возвращает
    и CPU-устройство Cycles, и iGPU — их отключаем, чтобы в рендере
    участвовали только дискретные карты.
    """
    prefs = bpy.context.preferences.addons['cycles'].preferences
    prefs.refresh_devices()
    prefs.compute_device_type = 'CUDA'

    gpus = 0
    for device in prefs.get_devices_for_type('CUDA'):
        if any(vendor in device.name for vendor in GPU_DEVICE_FILTER):
            device.use = True
            gpus += 1
            print(f"    Включён GPU: {device.name}")
        else:
            device.use = False
    if not gpus:
        print("    ! CUDA-устройства не найдены — рендер на CPU")
    return gpus


def setup_scene():
    """Создаёт чистую сцену, настраивает Cycles, камеру, свет, фон."""
    bpy.ops.wm.read_factory_settings(use_empty=True)

    # ---- Включаем аддон Cycles ----
    addon_utils.enable('cycles', default_set=True)

    # ---- Cycles ----
    sc = bpy.context.scene
    sc.render.engine = 'CYCLES'
    # Без активных GPU-устройств оставляем CPU: с device='GPU' и пустым
    # списком устройств Cycles отвечает «no compatible GPU device found».
    sc.cycles.device = 'GPU' if setup_render_devices() else 'CPU'
    sc.cycles.samples = RENDER_SAMPLES
    sc.cycles.preview_samples = 32
    sc.cycles.caustics_reflective = True
    sc.cycles.caustics_refractive = True
    sc.cycles.time_limit = RENDER_TIME_LIMIT_S
    sc.cycles.max_bounces = MAX_BOUNCES['total']
    sc.cycles.transmission_bounces = MAX_BOUNCES['transmission']
    sc.cycles.transparent_max_bounces = MAX_BOUNCES['transparent']
    sc.cycles.glossy_bounces = MAX_BOUNCES['glossy']
    sc.cycles.diffuse_bounces = MAX_BOUNCES['diffuse']
    sc.cycles.volume_bounces = MAX_BOUNCES['volume']
    if hasattr(sc.cycles, 'sample_clamp_indirect'):
        sc.cycles.sample_clamp_indirect = CLAMP_INDIRECT
    else:
        sc.cycles.clamp_indirect = CLAMP_INDIRECT

    # Все доступные CPU-ядра (режим AUTO)
    sc.render.threads_mode = 'AUTO'
    # BVH и текстуры переиспользуются между кадрами серии из 5 ракурсов —
    # на GPU это снимает перестройку сцены перед каждым кадром.
    sc.render.use_persistent_data = True

    # Денойзер и tone-mapping (могут быть недоступны в headless-сборке).
    # Если OIDN недоступен, денуазинг обязательно выключаем: иначе
    # bpy.ops.render.render() падает с «Failed to denoise».
    denoiser_ok = _safe_set(sc.cycles, 'denoiser', DENOISER)
    sc.cycles.use_denoising = bool(denoiser_ok)

    _safe_set(sc.view_settings, 'view_transform', VIEW_TRANSFORM)
    _safe_set(sc.view_settings, 'look', VIEW_LOOK)

    # Камера: та же оптика и ракурс, что у референса 06120
    p = SCENE_CAMERA
    target = Vector((0.0, 0.0, p['target_z_mm'] * MM))
    location = Vector((0.0, -p['distance_mm'] * MM,
                       p['camera_z_mm'] * MM))
    bpy.ops.object.camera_add(location=location)
    cam = bpy.context.active_object
    cam.name = 'Camera'
    cam.data.lens = p['lens_mm']
    cam.data.sensor_width = p['sensor_width_mm']
    cam.data.sensor_height = p['sensor_height_mm']
    cam.data.sensor_fit = 'HORIZONTAL'
    cam.data.clip_start = 0.0001
    cam.data.dof.use_dof = True
    cam.data.dof.aperture_fstop = p['fstop']
    cam.data.dof.focus_distance = p['distance_mm'] * MM

    base = (target - location).to_track_quat('-Z', 'Y')
    roll = Quaternion((0.0, 0.0, 1.0), math.radians(p['roll_deg']))
    cam.rotation_mode = 'QUATERNION'
    cam.rotation_quaternion = base @ roll

    _setup_lighting()
    _setup_background()

    # Камера в сцене
    sc.camera = cam
    return cam


def attach_camera_to_tube(camera, tube):
    """Жёстко привязывает камеру к трубке.

    Камера сохраняет положение и ориентацию относительно трубки, поэтому
    при наклоне трубка в кадре не меняется. Свет остаётся в мировых
    координатах; жидкость тоже строится в мире с горизонтальным уровнем,
    поэтому её наклон относительно трубки виден.
    """
    tube.rotation_euler = (0.0, 0.0, 0.0)
    bpy.context.view_layer.update()
    camera.parent = tube
    camera.matrix_parent_inverse = tube.matrix_world.inverted()


def _setup_lighting():
    """Студийное освещение: 3 площади из config.LIGHTS.

    В Blender 4.3 AreaLight не имеет angle_x/angle_y — только size
    и size_y. Углы облучения задаются через размер площади.
    """
    for name, loc, energy, size, _angle in LIGHTS:
        bpy.ops.object.light_add(type='AREA', location=loc)
        light = bpy.context.active_object
        light.name = name + '_Light'
        light.data.energy = energy
        light.data.size = size
        light.data.size_y = size  # квадратная площадь

    # Дополнительный круглый свет сверху для бликов на трубке
    bpy.ops.object.light_add(type='AREA', location=(0.0, 0.0, 0.8))
    light = bpy.context.active_object
    light.name = 'Top_Light'
    light.data.energy = 30.0
    light.data.size = 0.5
    light.data.size_y = 0.5


def _setup_background():
    """Серый фон из config.

    read_factory_settings(use_empty=True) не создаёт World, поэтому мир
    создаётся явно и привязывается к сцене.
    """
    sc = bpy.context.scene
    world = bpy.data.worlds.get('World') or bpy.data.worlds.new('World')
    sc.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get('Background')
    if bg is None:
        bg = world.node_tree.nodes.new('ShaderNodeBackground')
    bg.inputs['Color'].default_value = WORLD_COLOR
    bg.inputs['Strength'].default_value = WORLD_STRENGTH

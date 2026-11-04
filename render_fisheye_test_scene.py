import bpy
import math
import datetime
import os

# 1. Очистка сцены
bpy.ops.wm.read_factory_settings(use_empty=True)

# Метрическая система (миллиметры)
scene = bpy.context.scene
scene.unit_settings.system = 'METRIC'
scene.unit_settings.length_unit = 'MILLIMETERS'
scene.unit_settings.scale_length = 0.001

# 2. Трубка (диаметр 19 мм, высота 92 мм)
bpy.ops.mesh.primitive_cylinder_add(vertices=64, radius=9.5, depth=92, location=(0, 0, 46))
tube = bpy.context.active_object
tube.name = "Tube_19x92mm"

# Материал с желтизной
mat = bpy.data.materials.new(name="Tube_Glass_Yellowish")
mat.use_nodes = True
bsdf = mat.node_tree.nodes.get("Principled BSDF")
bsdf.inputs['Base Color'].default_value = (0.95, 0.9, 0.65, 1.0)
bsdf.inputs['Roughness'].default_value = 0.1
bsdf.inputs['Transmission Weight'].default_value = 0.8
tube.data.materials.append(mat)

# 3. Вертикальные направляющие для проверки дисторсии
#bpy.ops.mesh.primitive_cube_add(size=1, location=(-15, 0, 46))
#cube1 = bpy.context.active_object
#cube1.scale = (1, 10, 100)
#bpy.ops.mesh.primitive_cube_add(size=1, location=(15, 0, 46))
#cube2 = bpy.context.active_object
#cube2.scale = (1, 10, 100)

# 4. Камера с панорамной проекцией Fisheye Equisolid
bpy.ops.object.camera_add(location=(0, -80, 46))
cam = bpy.context.active_object
cam.name = "Fisheye_Camera_OV5693"
scene.camera = cam
cam.rotation_euler = (math.radians(90), 0, 0)

# Параметры для рыбьего глаза. Но бочкообразность не меняется
#cam.data.type = 'PANO'
#cam.data.panorama_type = 'FISHEYE_EQUISOLID'
#cam.data.fisheye_fov = math.radians(120)

# Параметры для OV5693 + 1.3mm Lens
cam.data.type = 'PERSP'
cam.data.lens = 1.3
cam.data.sensor_width = 3.6736

cam.data.sensor_fit = 'HORIZONTAL'
cam.data.dof.use_dof = False

# 5. Свет и фон
bpy.ops.object.light_add(type='AREA', location=(-50, -100, 100))
light = bpy.context.active_object
light.data.energy = 500000

world = bpy.data.worlds.new("World")
scene.world = world
world.use_nodes = True
bg = world.node_tree.nodes["Background"]
bg.inputs[0].default_value = (0.5, 0.5, 0.5, 1.0)
bg.inputs[1].default_value = 1.0

# =======================================================
# 6. ГЕНЕРАЦИЯ ПУТЕЙ (ЕДИНОЕ ИМЯ ДЛЯ СЦЕНЫ И РЕНДЕРА)
# =======================================================
timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
base_dir = "/home/nikolay/opencv/injector3D/output"
os.makedirs(base_dir, exist_ok=True)

# Базовое имя файла без расширения
base_filename = f"fisheye_ov5693_{timestamp}"

# Путь для сохранения файла сцены .blend
blend_filepath = os.path.join(base_dir, f"{base_filename}.blend")

# Путь для сохранения картинки .png
png_filepath = os.path.join(base_dir, f"{base_filename}.png")

# =======================================================
# 7. НАСТРОЙКИ РЕНДЕРА (ЗАПЕКАЕМ В ФАЙЛ)
# =======================================================
scene.render.engine = 'CYCLES'
scene.cycles.device = 'GPU'
scene.cycles.samples = 64

# Квадратное разрешение 1200x1200
scene.render.resolution_x = 1200
scene.render.resolution_y = 1200
scene.render.resolution_percentage = 100

# Формат файла - PNG
scene.render.image_settings.file_format = 'PNG'

# Указываем путь для сохранения картинки (такой же, как у сцены, но .png)
scene.render.filepath = png_filepath

# =======================================================
# ВКЛЮЧАЕМ ДИСТОРСИЮ ЧЕРЕЗ COMPOSITOR
# =======================================================
scene.use_nodes = True
tree = scene.node_tree
tree.nodes.clear()

rl = tree.nodes.new('CompositorNodeRLayers')
lens = tree.nodes.new('CompositorNodeLensdist')
comp = tree.nodes.new('CompositorNodeComposite')

# Настройка ноды (без distortion_type)
lens.use_fit = True            # Обрезает черные углы
lens.use_jitter = False        # Отключает шум (для чистоты)
# lens.use_projector = False   # Оставляем False для радиальной (бочка/подушка)

# Устанавливаем степень искажения (положительное значение = бочка)
lens.inputs['Distortion'].default_value = 5.6  # <-- Параметр К1. Регулируйте здесь (0.1 ... 0.5)

# Соединяем
tree.links.new(rl.outputs['Image'], lens.inputs['Image'])
tree.links.new(lens.outputs['Image'], comp.inputs['Image'])

# =======================================================
# 8. СОХРАНЕНИЕ ФАЙЛА СЦЕНЫ
# =======================================================
bpy.ops.wm.save_as_mainfile(filepath=blend_filepath)
print(f"✅ Сцена сохранена: {blend_filepath}")
print(f"✅ При рендере картинка сохранится как: {png_filepath}")

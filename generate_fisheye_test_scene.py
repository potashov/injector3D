import bpy
import math
import datetime
import os

# 1. Очистка сцены
bpy.ops.wm.read_factory_settings(use_empty=True)

# Метрическая система
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
bpy.ops.mesh.primitive_cube_add(size=1, location=(-15, 0, 46))
cube1 = bpy.context.active_object
cube1.scale = (1, 10, 100)
bpy.ops.mesh.primitive_cube_add(size=1, location=(15, 0, 46))
cube2 = bpy.context.active_object
cube2.scale = (1, 10, 100)

# 4. Камера с панорамной проекцией Fisheye Equisolid
bpy.ops.object.camera_add(location=(0, -25, 46))
cam = bpy.context.active_object
cam.name = "Fisheye_Camera_OV5693"
scene.camera = cam
cam.rotation_euler = (math.radians(90), 0, 0)

# --- ПАРАМЕТРЫ ДЛЯ OV5693 + 1.3mm LENS ---
cam.data.type = 'PANO'
cam.data.panorama_type = 'FISHEYE_EQUISOLID'
cam.data.lens = 1.3          # Фокусное расстояние 1.3 мм
cam.data.sensor_width = 3.6736 # Ширина сенсора OV5693 в мм
cam.data.sensor_fit = 'HORIZONTAL'

# Глубина резкости (опционально)
cam.data.dof.use_dof = False  # Для фиксированной линзы можно отключить

# 5. Настройки рендера
scene.render.engine = 'CYCLES'
scene.cycles.device = 'GPU'
scene.cycles.samples = 64

# Соотношение сторон 4:3, как у сенсора
scene.render.resolution_x = 2592
scene.render.resolution_y = 1944
scene.render.resolution_percentage = 100

# 6. Свет и фон
bpy.ops.object.light_add(type='AREA', location=(-50, -100, 100))
light = bpy.context.active_object
light.data.energy = 500000

world = bpy.data.worlds.new("World")
scene.world = world
world.use_nodes = True
bg = world.node_tree.nodes["Background"]
bg.inputs[0].default_value = (0.5, 0.5, 0.5, 1.0)
bg.inputs[1].default_value = 1.0

# 7. Сохранение с временной меткой
timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
base_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
os.makedirs(base_dir, exist_ok=True)
filepath = os.path.join(base_dir, f"fisheye_ov5693_{timestamp}.blend")

bpy.ops.wm.save_as_mainfile(filepath=filepath)
print(f"✅ Сцена сохранена: {filepath}")

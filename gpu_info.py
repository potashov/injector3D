import bpy
import gpu
import sys

# Рендеринг Workbench для инициализации GPU-бэкенда в фоновом режиме
scene = bpy.context.scene
scene.render.resolution_x = 1
scene.render.resolution_y = 1
scene.render.engine = "BLENDER_WORKBENCH"
bpy.ops.render.render(animation=False, write_still=False)

print('GPU_VENDOR:' + gpu.platform.vendor_get())
print('GPU_RENDERER:' + gpu.platform.renderer_get())
print('GPU_VERSION:' + gpu.platform.version_get())
print('GPU_DEVICE_TYPE:' + gpu.platform.device_type_get())
sys.exit(0)

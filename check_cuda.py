import bpy

# 1. Получаем доступ к настройкам Cycles
prefs = bpy.context.preferences.addons['cycles'].preferences

# 2. Принудительно обновляем список устройств
prefs.refresh_devices()

# 3. Устанавливаем тип вычислительного устройства CUDA
# Это важный шаг, который часто пропускают
prefs.compute_device_type = 'CUDA'

# 4. Получаем список устройств CUDA и включаем их
print("--- Поиск устройств CUDA ---")
cuda_devices = prefs.get_devices_for_type('CUDA')
if cuda_devices:
    for device in cuda_devices:
        device.use = True  # Включаем устройство
        print(f"Найдено и включено: {device.name} (Тип: {device.type})")
else:
    print("Устройства CUDA не найдены.")

# 5. (Опционально) Устанавливаем устройство рендеринга в GPU
# bpy.context.scene.cycles.device = 'GPU'


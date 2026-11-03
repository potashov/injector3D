import bpy

prefs = bpy.context.preferences.addons['cycles'].preferences
# Важно: принудительно обновляем список устройств
prefs.refresh_devices()

try:
    cuda_devices = prefs.get_devices_for_type('CUDA')
    if cuda_devices:
        print("✅ Сборка Blender включает поддержку CUDA.")
        for dev in cuda_devices:
            print(f"   Найдено CUDA-устройство: {dev.name}")
    else:
        print("❌ Устройства CUDA не найдены. Сборка может быть без поддержки CUDA, либо библиотеки CUDA не загружаются.")
except Exception as e:
    print(f"❌ Ошибка при запросе устройств CUDA: {e}")
    print("   Это также может указывать на отсутствие поддержки CUDA в сборке.")

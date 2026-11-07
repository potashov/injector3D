"""Конфигурация проекта injector3D.

Все линейные размеры задаются в миллиметрах и переводятся в метры Blender
через MM (1 мм = 0.001 сценической единицы). Метрический масштаб важен для
корректной работы DOF, объёмного поглощения и фотометрии источников света.

Оси: ось трубки — +Z локальных координат, дно на z = 0.
"""

import os

MM = 0.001

# --- Геометрия трубки (ТЗ) ---
INNER_DIAMETER = 19.0      # внутренний диаметр, мм
HEIGHT = 92.0              # высота, мм
WALL_THICKNESS = 1.0       # толщина стенки, мм
BOTTOM_THICKNESS = 1.0     # толщина дна, мм

INNER_RADIUS = INNER_DIAMETER / 2.0            # 9.5 мм
OUTER_RADIUS = INNER_RADIUS + WALL_THICKNESS   # 10.5 мм

# --- Скругления кромок, мм ---
# Подбираются с ограничениями геометрии:
#   верхний буртик:  FILLET_INNER_TOP + FILLET_OUTER_TOP <= WALL_THICKNESS
#   внутреннее дно:  FILLET_INNER_BOTTOM <= BOTTOM_THICKNESS
FILLET_OUTER_BOTTOM = 0.50    # внешний нижний угол (выпуклый)
FILLET_INNER_BOTTOM = 0.60    # галтель внутреннего угла дна (вогнутая)
FILLET_OUTER_TOP = 0.25       # внешний верхний угол (выпуклый)
FILLET_INNER_TOP = 0.25       # внутренний верхний угол (выпуклый)

# --- Дискретизация ---
REVOLVE_SEGMENTS = 192   # сегментов вращения профиля
ARC_SEGMENTS = 8         # сегментов на каждое скругление

# --- Оптика (используется на шаге 4) ---
IOR_PMMA = 1.49          # оргстекло/акрил

# --- Жидкость (пункт 2 и 6) ---
# Доля вместимости полости. Уровень подбирается бисекцией так, чтобы объём
# совпал при любом наклоне; 0.5 оставлена и выше spill-порога с запасом:
# при наклоне 30° кромка с ближней стороны опускается до ~75 мм.
FILL_FRACTION = 0.5

# Пресеты жидкостей (пункт 6): физические параметры для мениска и
# оптические параметры для Cycles Principled BSDF + Volume Absorption.
LIQUID_PRESETS = {
    'water': {
        'name': 'water',
        'display_name': 'Water',
        'ior': 1.333,
        'density': 1000.0,            # кг/м³
        'surface_tension': 0.0728,     # Н/м, 20 °C
        'contact_angle_deg': 25.0,     # град на PMMA
        'base_color': (1.0, 1.0, 58.0 / 73.0, 1.0),
        'absorption_color': (1.0, 1.0, 0.42, 1.0),
        'absorption_density': 20.0,
    },
    'glycerol': {
        'name': 'glycerol',
        'display_name': 'Glycerol',
        'ior': 1.473,
        'density': 1261.0,
        'surface_tension': 0.0634,
        'contact_angle_deg': 45.0,
        'base_color': (1.0, 1.0, 0.95, 1.0),
        'absorption_color': (1.0, 1.0, 0.85, 1.0),
        'absorption_density': 8.0,
    },
    'ethanol': {
        'name': 'ethanol',
        'display_name': 'Ethanol',
        'ior': 1.361,
        'density': 789.0,
        'surface_tension': 0.0223,
        'contact_angle_deg': 5.0,
        'base_color': (1.0, 1.0, 1.0, 1.0),
        'absorption_color': (1.0, 1.0, 1.0, 1.0),
        'absorption_density': 2.0,
    },
    'mineral_oil': {
        'name': 'mineral_oil',
        'display_name': 'Mineral Oil',
        'ior': 1.467,
        'density': 850.0,
        'surface_tension': 0.0300,
        'contact_angle_deg': 35.0,
        'base_color': (1.0, 0.95, 0.70, 1.0),
        'absorption_color': (1.0, 0.90, 0.35, 1.0),
        'absorption_density': 35.0,
    },
}
DEFAULT_LIQUID_TYPE = 'water'


def get_liquid_config(preset=None, **overrides):
    """Возвращает конфигурацию жидкости с учётом возможных переопределений."""
    preset_name = (preset or DEFAULT_LIQUID_TYPE).lower()
    if preset_name not in LIQUID_PRESETS:
        available = ', '.join(sorted(LIQUID_PRESETS.keys()))
        raise ValueError(
            f"Неизвестный тип жидкости '{preset_name}'. Доступны: {available}")
    cfg = dict(LIQUID_PRESETS[preset_name])
    for k, v in overrides.items():
        if v is not None:
            cfg[k] = v
    return cfg


# --- Мениск (пункт 3) ---
# Осесимметричное решение Юнга-Лапласа (см. meniscus.py):
#   (1/r) d/dr [ r z' / sqrt(1 + z'^2) ] = z / lambda_c^2,
#   z'(0) = 0,   z'(R) = ctg(theta).
# Нелинейная задача решается численно (РК4 + бисекция по z(0)), поэтому
# форма мениска получается физической, а не подобранной кривой.
# Для sigma = 0.0728 Н/м: lambda_c = sqrt(sigma/(rho*g)) = 2.724 мм.
SURFACE_TENSION = LIQUID_PRESETS['water']['surface_tension']
LIQUID_DENSITY = LIQUID_PRESETS['water']['density']
GRAVITY = 9.81              # м/с²
CONTACT_ANGLE_DEG = LIQUID_PRESETS['water']['contact_angle_deg']
MENISCUS_RINGS = 80         # радиальных узлов свободной поверхности
MENISCUS_STEPS = 2000        # шагов интегрирования ОДУ

# --- Капли на внутренней поверхности (пункт 8, ВНЕ РАБОТЫ) ---
# Параметры заготовлены, реализация не выполнена.  Капли — внутри трубки,
# 0–2 штуки (не 45 на внешней поверхности, как было в черновом варианте).
DROPLETS_COUNT_RANGE = (0, 2)
DROPLETS_SEED = 20261103
DROPLETS_RADIUS_RANGE = (0.5, 2.0)     # мм
DROPLETS_HEIGHT_RANGE = (3.0, HEIGHT - 6.0)  # мм, вдоль оси трубки
DROPLETS_SPHERES = (16, 10)            # сегментов × колец UV-сферы

# --- Сцена ---
# Камера-референс 06120 (OV5693, 1.3 мм, barrel). Та же камера используется
# для основной серии: близкая дистанция, широкий угол, roll
# и дисторсия. Кадр 1200×1200 на белом фоне, верх трубки обрезан.
REFERENCE_RESOLUTION = (1200, 1200)
REFERENCE_CAMERA = {
    'lens_mm': 1.3,
    'sensor_width_mm': 3.6288,
    'sensor_height_mm': 2.7216,
    'distance_mm': 46.0,
    'target_z_mm': 35.0,
    'camera_z_mm': 40.0,
    'roll_deg': 1.5,
    'fstop': 2.8,
    'distortion': 0.66,
    'shift_x_px': 105,
    'target_height_px': 1124,
    'samples': 128,
}

# Основная сцена использует ту же оптику/расположение, что и референс.
SCENE_CAMERA = REFERENCE_CAMERA
RENDER_RESOLUTION = REFERENCE_RESOLUTION

# Пресеты камер (пункт 6):
#   - reference: близкая широкоугольная камера с дисторсией (OV5693 1.3 мм);
#   - studio: длиннофокусная студийная камера (85 мм) без дисторсии.
CAMERA_PRESETS = {
    'reference': dict(REFERENCE_CAMERA),
    'studio': {
        'lens_mm': 85.0,
        'sensor_width_mm': 36.0,
        'sensor_height_mm': 24.0,
        'distance_mm': 350.0,
        'target_z_mm': 46.0,
        'camera_z_mm': 46.0,
        'roll_deg': 0.0,
        'fstop': 5.6,
        'distortion': 0.0,
        'shift_x_px': 0,
        'target_height_px': 0,
        'samples': 128,
    },
}
DEFAULT_CAMERA_PRESET = 'reference'


def get_camera_config(preset=None, **overrides):
    """Возвращает конфигурацию камеры с учётом возможных переопределений."""
    preset_name = (preset or DEFAULT_CAMERA_PRESET).lower()
    if preset_name not in CAMERA_PRESETS:
        available = ', '.join(sorted(CAMERA_PRESETS.keys()))
        raise ValueError(
            f"Неизвестный пресет камеры '{preset_name}'. Доступны: {available}")
    cfg = dict(CAMERA_PRESETS[preset_name])
    for k, v in overrides.items():
        if v is not None:
            cfg[k] = v
    return cfg


# Для серии из 5 наклонов трубка смещена вправо на 105 px — это безопасно,
# так как камера привязана к трубке и не уходит за границу кадра.
SCENE_POSTPROCESS = {
    'enabled': True,
    'fill_background': False,
    'fill_color': (255, 255, 255, 255),
    'shift_x_px': 105,
    'target_height_px': 0,
    'compression': 3,
    'diagnostics': False,
}

# Подписи и маркер на кадре. Каждый элемент включается отдельно; по
# умолчанию не добавляется ничего.
SCENE_OVERLAY = {
    'volume': False,
    'azimuth': False,
    'tilt_deg': False,
    'ellipse_center': False,
    'center_marker': False,
    'liquid': False,
    'font_path': '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
    'font_size_px': 18,
    'margin_px': 12,
    'text_color': (0, 0, 0, 255),
    'stroke_color': (255, 255, 255, 255),
    'stroke_width_px': 1,
    'marker_color': (255, 0, 0, 255),
    'marker_size_px': 3,
}

# Камера на -Y смотрит в +Y (up = +Z), поэтому в кадре +X — вправо:
#   поворот вокруг мировой X на +угол  -> верх трубки тянется к камере;
#   поворот вокруг мировой Y на +угол  -> верх трубки тянется вправо.
TILT_DEG = 30.0
TILT_VARIANTS = (
    ("upright",    (0.0, 0.0, 0.0)),          # вертикально
    ("left30",     (0.0, -TILT_DEG, 0.0)),    # отклонена влево
    ("away30",     (-TILT_DEG, 0.0, 0.0)),    # отклонена вдаль от камеры
    ("right30",    (0.0, TILT_DEG, 0.0)),     # отклонена вправо
    ("toward30",   (TILT_DEG, 0.0, 0.0)),     # наклонена в сторону камеры
)

# --- Пункт 4.5: серия N+1 по окружности ---
# N — число наклонных положений; всего кадров N+1 (вертикаль + N).
# Азимут φ задаёт ПЛОСКОСТЬ наклона: ось трубки уходит в мировое направление
# (sin θ·cos φ, sin θ·sin φ, cos θ), угол θ от вертикали одинаков для всех φ.
# Порядок по умолчанию совпадает с примером: -YZ, -XZ, +YZ, +XZ при N=4,
# то есть старт 270° и шаг -360°/N.
TILT_AZIMUTH_COUNT = 4
TILT_ANGLE_DEG = 10.0
TILT_AZIMUTH_START_DEG = 270.0
# Точка опоры наклона — середина трубки: вокруг неё поворачивается трубка и
# на неё наводится камера. При наклоне вокруг дна (z = 0) верхний край на
# θ = 30° уезжал бы вбок на ~46 мм и выходил бы за кадр.
TILT_PIVOT_Z_MM = HEIGHT / 2.0


def tilt_ring_variants(n=None, angle=None):
    """N+1 ракурсов: вертикаль + N азимутов при фиксированном угле.

    Возвращает кортеж `(имя, азимут_град | None)`. У вертикали азимут
    `None`; у наклонных кадров имя `azNNN` (азимут по модулю 360°).
    """
    n = TILT_AZIMUTH_COUNT if n is None else int(n)
    if n < 1:
        raise ValueError("TILT_AZIMUTH_COUNT должен быть >= 1")
    if angle is not None and not (0.0 < float(angle) < 90.0):
        raise ValueError("угол наклона должен быть в диапазоне (0, 90)")

    step = -360.0 / n
    variants = [("upright", None)]
    for k in range(n):
        phi = (TILT_AZIMUTH_START_DEG + k * step) % 360.0
        variants.append((f"az{int(round(phi)):03d}", phi))
    return tuple(variants)

# --- Освещение ---
# Объект ~10 см, камера в 46 мм от оси: площади 2000–5000 Вт изначального
# каркаса давали гарантированный пересвет. Порядки подобраны под
# метрическую сцену и короткую дистанцию референсной камеры.
# Формат: (имя, позиция, мощность Вт, размер квадратной области м).
# Угол облучения здесь не задаётся: у Area Light в Blender его нет,
# направленность задаётся размером площади.
LIGHTS = (
    ("Key",  (0.35, -0.30, 0.40),  60.0, 0.30),
    ("Fill", (-0.40, -0.35, 0.15),  18.0, 0.45),
    ("Rim",  (-0.15,  0.45, 0.45),  45.0, 0.25),
)
# Мир — это не фон кадра: film_transparent=True выносит его из PNG, а
# postprocess_reference.py заливает фон белым. Здесь World работает как
# сфера-отражатель: свет, прошедший сквозь стенку и жидкость, возвращается
# обратно. Тёмно-серый мир (0.16,0.17,0.19 × 0.35 ≈ 0.056) возвращать было
# нечему, и жидкость выглядела серой и «плотной» при том, что фон в кадре
# белый.
#
# Сила подобрана по сетке прогонов (метрика — медиана пикселей тела
# жидкости в нижней половине трубки против воздуха над ней):
#   0.35 серый  RGB (51,52,41)  контраст +6.0   — жидкость тонет в фоне
#   0.14        RGB (96,93,72)  контраст +9.0
#   0.30        RGB (145,143,119) контраст +10.0 — максимум контраста
#   0.60        RGB (183,181,162) контраст +7.7  — начинается пересвет
#   3.00        RGB (238,237,229) — жидкость выбита в белый
# Контраст «воздух минус жидкость» пика доходит до 0.30 и дальше падает:
# при бо́льших значениях мир засвечивает и воздух, и жидкость одинаково,
# граница уровня размывается. 0.30 — правая граница этого пика.
WORLD_COLOR = (1.0, 1.0, 1.0, 1.0)
WORLD_STRENGTH = 0.30

# --- Рендер (Cycles, GPU: 2× Quadro P1000 / CUDA 12.4, драйвер 550.163.01) ---
# Число сэмплов задаёт пресет камеры ('samples'), а не глобальная константа:
# оптика и разрешение кадра определяют нужную чистоту картинки вместе.
RENDER_TIME_LIMIT_S = 75   # страховка от зависания пакетного рендера
GPU_DEVICE_FILTER = ("Quadro", "GeForce", "Tesla")  # NVIDIA-устройства CUDA

# Денуазер задаётся, но use_denoising выключен (см. scene.py): OIDN
# размывает линию горизонта мениска и стоит +56 % времени кадра.
# tone-mapping может быть недоступен в старых headless-сборках.
DENOISER = "OPENIMAGEDENOISE"
VIEW_TRANSFORM = "AgX"
VIEW_LOOK = "AgX - Medium High Contrast"

# Тайл трассировки: 2048 пикселей по меньшей стороне вместо авторасчёта
# Blender. Выигрыш зависит от кадра: на az180 (самом дорогом) +3.2 %,
# на az90 — 0 %, в отдельном прогоне замерено до +7.6 %. Картинка при
# этом побитово идентична авто-тайлу (MAE = 0.0000 при фиксированном
# seed), поэтому правка безопасна: это чистая экономия без риска.
TILE_SIZE = 2048

# Максимумы трассировки: стекло требует глубоких траекторий с рефракцией.
MAX_BOUNCES = {
    "total": 16,
    "transmission": 16,
    "transparent": 16,
    "glossy": 6,
    "diffuse": 4,
    "volume": 0,
}
CLAMP_INDIRECT = 10.0   # гасит «снежинки» каустик

# Выход — рядом с пакетом, чтобы не зависеть от текущей папки при запуске
# `blender --background --python ...`.
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(PROJECT_DIR, "output")
REFERENCE_OUTPUT_DIR = os.path.join(OUTPUT_DIR, '06120')

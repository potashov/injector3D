"""Конфигурация проекта injector3D.

Все линейные размеры задаются в миллиметрах и переводятся в метры Blender
через MM (1 мм = 0.001 сценической единицы). Метрический масштаб важен для
корректной работы DOF, объёмного поглощения и фотометрии источников света.

Оси: ось трубки — +Z локальных координат, дно на z = 0.
"""

MM = 0.001

# --- Геометрия трубки (ТЗ) ---
INNER_DIAMETER = 19.5      # внутренний диаметр, мм
HEIGHT = 100.0             # высота, мм
WALL_THICKNESS = 1.0       # толщина стенки, мм
BOTTOM_THICKNESS = 1.0     # толщина дна, мм

INNER_RADIUS = INNER_DIAMETER / 2.0            # 9.75 мм
OUTER_RADIUS = INNER_RADIUS + WALL_THICKNESS   # 10.75 мм

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

# --- Сцена ---
CAMERA_TARGET = (0.0, 0.0, 0.05)
CAMERA_POSITION = (0.0, -0.35, 0.055)
CAMERA_LENS = 85.0
CAMERA_SENSOR = 36.0
CAMERA_FSTOP = 8.0

# Камера стоит на -Y и смотрит в сторону +Y, поэтому:
#   наклон вокруг мировой X на +угол -> верх трубки тянется к камере;
#   наклон вокруг мировой Y на +угол -> верх трубки тянется вправо по кадру.
TILT_DEG = 30.0
TILT_VARIANTS = (
    ("upright",   (0.0, 0.0, 0.0)),
    ("left30",    (0.0, -TILT_DEG, 0.0)),
    ("away30",    (-TILT_DEG, 0.0, 0.0)),
    ("right30",   (0.0, TILT_DEG, 0.0)),
    ("toward30",  (TILT_DEG, 0.0, 0.0)),
)

# --- Рендер (Cycles, CPU: CUDA 12 в Blender 4.3 не поддерживает Pascal/CC6.1) ---
RENDER_RESOLUTION = (1024, 1280)
RENDER_SAMPLES = 128
RENDER_TIME_LIMIT_S = 75   # страховка от зависания пакетного рендера на CPU
DENOISER = "OPENIMAGEDENOISE"
VIEW_TRANSFORM = "AgX"
VIEW_LOOK = "AgX - Medium High Contrast"

# Максимумы трассировки: стекло требует глубоких траекторий с рефракцией.
MAX_BOUNCES = {
    "total": 16,
    "transmission": 16,
    "transparent": 16,
    "glossy": 6,
    "diffuse": 4,
    "volume": 0,
}
CLAMP_INDIRECT = 10.0   # гасит «снежинки» каустик на CPU

OUTPUT_DIR = "output"

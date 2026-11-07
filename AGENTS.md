# AGENTS.md — injector3D

Headless-Blender проект: генерирует 3D-сцену трубки с жидкостью и рендерит
её под заданным числом ракурсов (N+1: вертикаль + N плоскостей наклона,
Это **необычный Python-пакет**: модули импортируют `bpy`, поэтому запускать их
нужно только внутри Blender, а не через `python`.

## Команды

```bash
# Локальная сборка Blender 5.2.3 LTS (папка в .gitignore)
BL=./blender-5.2.3-linux-x64/blender

# Рендер серии N+1 (вертикаль + N азимутов наклона) — режим по умолчанию
$BL --background --python injector3d/create_scene.py -- \
    --mode ring --tilt-count 8 --tilt-angle 10

# Рендер 5 фиксированных ракурсов
$BL --background --python injector3d/create_scene.py -- --mode tube

# Кадр под референс 06120 (1200×1200, белый фон, сдвиг вправо 105 px)
$BL --background --python render_reference.py

# Числовая самопроверка жидкости
$BL --background --python check_liquid.py

# Проверка физики пресетов жидкостей
$BL --background --python check_liquid_presets.py

# Проверки постобработки (системный python3, без Blender)
python3 check_postprocess.py

# Параллельный рендер серии по GPU (системный python3)
python3 render_parallel.py --tilt-count 8 --tilt-angle 10 --gpus 0,1

# Диагностика CUDA/GPU
$BL --background --python check_cuda.py
$BL --background --python check_build_cuda.py
$BL --background --python gpu_info.py
```

Тестового фреймворка, линтера и typecheck нет — проверка = успешные
прогоны `check_liquid.py` и `check_postprocess.py` (assert'ы + код
возврата) и рендер без исключений.

## Версия Blender — проверить первой

Проект проверен под **Blender 5.2.3 LTS** (имена сокетов
`Transmission Weight`, AgX и т.п.). Локальная сборка установлена в
`./blender-5.2.3-linux-x64/blender` (папка в `.gitignore`), используйте её
для всех запусков. Код сохраняет совместимость с 4.3.2 через ветки API для
композитора и `sample_clamp_indirect`. Перед запуском сверяйте
`$BL --version`.

## Архитектура

- `injector3d/create_scene.py` — entry-point: сцена → трубка → валидация →
  жидкость/материалы → рендер.
- `injector3d/config.py` — все параметры и *все* размеры в мм; в метры
  переводятся через `MM = 0.001`. Не смешивайте мм и Blender-юниты.
- `injector3d/liquid.py` — меш жидкости = полость ∩ `z_world <= level`.
  Меш строится **в мировых координатах** и пересобирается на каждый наклон
  через callback `on_tilt` из `render.py`; объект жидкости намеренно **не**
  дочерний трубке, иначе уровень наклонялся бы вместе с ней.
  Свободная поверхность — мениск: срез на уровне кромки контакта и
  радиальная сетка (`MENISCUS_RINGS`); при наклоне кромка эллиптическая,
  но сама поверхность остаётся горизонтальной. Для клина у дна/перелива —
  плоский fallback. Если целевой объём попадает в разрыв между плоским
  fallback и полным мениском, `build_liquid()` строит плоскую поверхность
  с точным объёмом (`flat=True` в info).
- `injector3d/meniscus.py` — солвер Юнга-Лапласа (РК4 + бисекция по ζ(0)),
  профиль считается для радиуса меша `INNER_RADIUS - CLEARANCE` и
  кэшируется в `liquid._MENISCUS_CACHE`.
- `check_liquid.py` проверяет V(θ)=const, объём с мениском, горизонтальность
  кромки, manifold на краевых случаях и плоский fallback в разрыве
  объёма у дна (2000 мм³ при 50°/60°).
- `check_postprocess.py` — автономные проверки постобработки: alpha bbox,
  финальный 2D-центр, fill/height, маркер и result JSON.
- `injector3d/scene.py` — камера из `config.SCENE_CAMERA` (та же, что
  `REFERENCE_CAMERA`): ручной track + roll +1.5°, 1.3 мм, f/2.8.
  `attach_camera_to_tube()` жёстко родит камеру к трубке — вызывается для
  обоих режимов рендера; свет всегда остаётся в мировых координатах.
- `injector3d/render.py` — два режима, оба с привязанной камерой:
  - `render_tube()` по фиксированным `config.TILT_VARIANTS` (5 ракурсов):
    наклон вокруг дна, Эйлеровы углы.
  - `render_ring()` — N+1 кадров (п. 4.5): наклон на θ в
    плоскости азимута φ через точку опоры `TILT_PIVOT_Z_MM`
    (`tilt_matrix(θ, φ) = Rz(φ)·Ry(θ)·Rz(φ)⁻¹`). Камера в цикле не
    трогается — меняется единственный `tube.matrix_world`, и Blender
    сам переносит/наклоняет всю систему «трубка + камера».
    Кадры различаются именно из-за типа поворота: нормаль горизонта
    жидкости в системе трубки равна `R(φ)⁻¹·e_z = Rz(φ)·Ry(θ)⁻¹·e_z` и
    обходит конус с полууглом θ. Старый `AzimuthRig` давал
    `Rz(φ)·Ry(θ)` — там φ сокращался, и все кадры были идентичны.
  Рендер 1200×1200 в RGBA с `film_transparent=True`, Lensdist
  `use_fit=False`, затем опциональная постобработка.
- `injector3d/output_manager.py` — единый источник путей и имён вывода.
  Каталог прогона: `<база>/<YYYYMMDD_HHMM>/`, база — `--output-dir`
  (по умолчанию `config.OUTPUT_DIR`). Имена: `<жидкость>_<ракурс>[_<tag>]
  [_v<объём>].png`, `<жидкость>_master[_<tag>][_s<k>of<M>].blend`,
  `<жидкость>_manifest[_<tag>][_s<k>of<M>].json`,
  `<жидкость>_shard<k>.log`, `<жидкость>[_<tag>].glb`. Ни номера шага, ни
  timestamp в имени файла нет — каталог уже отличает прогон от прогона.
- `render_reference.py` — отдельный кадр под `06120.png`:
  `config.REFERENCE_CAMERA`, дополнительно сдвиг +105 px и высота 1124 px.
- `postprocess_reference.py` — системный `python3` (PIL/numpy): по
  умолчанию сохраняет RGBA без заливки, `--fill` композитит `--fill-color`;
  `--height 0` отключает масштаб, `--shift` задаёт сдвиг; `--metadata`
  добавляет подписи/маркер. Для основной серии — `--shift 105 --height 0`.
- `reports/reference_camera_06120.md` — параметры камеры и метрики.
- `reports/step*.md` — отчёты по пунктам плана; `reports/step4.5.md` —
  подробный план промежуточного пункта 4.5 (N+1, кэш мениска, наклон по
  азимуту); `reports/step5_postprocessing.md` — план постобработки,
  опциональной заливки, подписей, объёма в имени файла и 2D-центра
  эллипса (п. 5).
  `PLAN.md`, `CHANGELOG.md` — актуальный контекст и известные проблемы.

## Серия N+1 (ring) — режим рендера

Запуск:

```bash
$BL --background --python injector3d/create_scene.py -- --mode ring --tilt-count 8 --tilt-angle 10
```

- N+1 кадров: вертикаль + N наклонов на угол θ в плоскостях
  `φ_k = φ_start + k·360°/N`.
- `N` — `config.TILT_AZIMUTH_COUNT` или `--tilt-count`; `θ` —
  `config.TILT_ANGLE_DEG` или `--tilt-angle`.
- Наклон: `render.tilt_matrix(θ, φ) = Rz(φ)·Ry(θ)·Rz(φ)⁻¹` — поворот трубки
  вокруг горизонтальной оси `(-sin φ, cos φ, 0)` через точку опоры
  `config.TILT_PIVOT_Z_MM` (середина трубки).
- Камера привязана к трубке на этапе вертикальной сцены
  (`create_scene` → `attach_camera_to_tube`) и в цикле рендера **не
  трогается**: меняется единственный `tube.matrix_world`, и вся система
  «трубка + камера» едет и наклоняется как единое целое. Кадры при этом
  разные, потому что нормаль горизонта жидкости в системе трубки
  `R(φ)⁻¹·e_z = Rz(φ)·Ry(θ)⁻¹·e_z` зависит от φ. Старый `AzimuthRig`
  давал `Rz(φ)·Ry(θ)`, где φ сокращался, — все кадры были идентичны.
- Два меша жидкости: `liquid_upright` и `liquid_tilted` — независимые
  объекты в мировых координатах (не дочерние трубке, иначе уровень
  наклонялся бы с ней); наклонный меш строится при φ = 0 и для
  остальных φ только поворачивается вокруг Z (`Rz(φ)` не меняет
  `z_world`, тождество проверено в `check_liquid.py`, раздел 8).
- Один master `.blend` на серию.
- Fast-путь: `_MENISCUS_TEMPLATE_CACHE` с `ΔV_under`;
  `build_liquid(use_fast=True)` ускоряет построение в 4.6–6.4 раза
  (замер; оптимистичная оценка «8–10×» из плана не подтвердилась).
- `check_liquid.py` проверяет fast-путь: ΔV ≤ 0.001 мм³.
- Подробный план, замеры и фик наклона — `reports/step4.5.md` (§15–16);
  замеры производительности и расклад прогона по стадиям —
  `reports/performance.md`.

## Пункт 5 — постобработка и подписи — ОСНОВНОЕ РЕАЛИЗОВАНО

- `config.SCENE_POSTPROCESS`: `enabled`, `fill_background`, `fill_color`,
  `shift_x_px`, `target_height_px`, `compression`, `diagnostics`.
- `config.SCENE_OVERLAY`: `volume`, `azimuth`, `tilt_deg`,
  `ellipse_center`, `center_marker`, шрифт/цвета/размер.
- CLI: `--no-postprocess`, `--fill-background/--no-fill-background`,
  `--fill-color R,G,B[,A]`, `--overlay-volume/azimuth/tilt/ellipse/center`,
  `--center-marker` и их `--no-*`-версии.
- `render.py` пишет `v<round(volume_mm3)>` в имя PNG, передаёт metadata
  (3D-центр, raw 2D-центр) и рисует подписи/маркер через
  `postprocess_reference.py`; при `enabled=False` PNG сохраняется сразу.
- `postprocess_reference.py --worker` — один процесс на серию; raw
  отправляется в stdin сразу после рендера, обработка идёт параллельно
  рендеру следующего кадра. В конце серии пишется
  `<жидкость>_manifest[_<tag>][_s<k>of<M>].json`.
- `liquid.contact_level_mm()` / `contact_center_mm()` — кромка контакта
  (`level + rise` для мениска) и её 3D-центр.
- Lensdist для 2D-центра: `q = ((x-W/2)/W, (y-H/2)/H)`,
  `factor = (1+d)/(1+4d|q|²)`, `p' = center + (p-center)·factor`;
  проверено на калибровочной сетке, расхождение ≤ 1.2 px.
- `postprocess_reference.py` пересчитывает `x+shift`, `y·scale` и рисует
  маркер/блок; fill по умолчанию выключен, RGBA сохраняется.
- Manifest содержит raw- и финальный 2D-центр; `check_postprocess.py`
  проверяет пересчёт и overlay. Композиторный путь без overlay отложен:
  async-worker скрывает Python за рендером следующего кадра.

## Пункт 6 — CLI, пресеты жидкостей, камера, GLB и параллельный рендер — ОСНОВНОЕ РЕАЛИЗОВАНО

- `create_scene.py` принимает `--fill-fraction` или взаимоисключающий
  `--volume-mm3` (абсолютный объём, проверяется по `capacity_mm3()`),
  `--liquid-type` (`water`, `glycerol`, `ethanol`, `mineral_oil`) с
  переопределениями физики (`--liquid-ior`, `--liquid-density`,
  `--surface-tension`, `--contact-angle`, `--absorption-density`),
  `--camera-preset` (`reference`, `studio`) и параметры камеры
  (`--camera-distance`, `--camera-z`, `--camera-target-z`, `--camera-lens`,
  `--camera-roll`, `--camera-distortion`), `--export-glb [PATH]`,
  `--gpu-index`, `--shard-index/--shard-count`, `--timestamp`, `--tag`.
- Мениск Юнга-Лапласа и Cycles-материалы полностью параметризованы;
  `check_liquid_presets.py` подтверждает физическую точность (расхождение
  численного и аналитического ΔV ≤ 0.0013 мм³).
- `export_scene_glb()` экспортирует геометрию трубки и жидкости с
  PBR-материалами в формат GLB (glTF 2.0).
- `render.py` всегда ставит тип жидкости в начало имени кадра
  (`water_…`, `glycerol_…`) и записывает `liquid_type`, `liquid_ior`,
  `camera_distortion` в JSON manifest.
- `render_parallel.py` (системный `python3`) делит N наклонных кадров
  между GPU, запускает по процессу Blender на GPU и сливает manifest
  шардов; вертикаль рендерит только шард 0. Все шарды пишут в один
  каталог прогона: launcher считает `<output-dir>/<timestamp>` и передаёт
  его шардам через `--output-dir`/`--timestamp`. Логи —
  `<жидкость>_shard<k>.log`, master `.blend` получает суффикс `_s<k>of<M>`.
- Замер на 2× P1000: N=4 ×1.10, N=8 ×1.08, а не ~×1.8. Оба GPU уже
  работают внутри одного кадра; launcher — для изоляции шардов/пакетных
  запусков, не основной рычаг скорости. См. `reports/performance.md` §9.5.
- `scene.setup_scene(gpu_index=...)` оставляет одно CUDA-устройство;
  без индекса включает все, без CUDA — CPU-fallback.
- В очереди остаётся пакетный рендер матриц параметров датасетов одним запуском.
  План и замеры — `reports/step6_cli.md`.

## Вывод и артефакты

- Стадий сцены нет: каждый прогон собирает полную сцену (жидкость +
  материалы). Режим рендера — `--mode ring` (N+1, по умолчанию) или
  `--mode tube` (5 ракурсов); номер пункта плана в запуске не участвует.
- Каталог прогона — `<база>/<YYYYMMDD_HHMM>/`, база — `--output-dir`
  (по умолчанию `config.OUTPUT_DIR`, т.е. `output/`). `--timestamp`
  фиксирует имя каталога и синхронизирует шарды.
- `output/<YYYYMMDD_HHMM>/*.png` **отслеживаются git** как результат
  работы; `.blend`/`.blend1`/`.json`/`.log` игнорируются. Не удаляйте
  чужие рендеры и не считайте `output/` временным каталогом.
- `output/06120/` — отдельный результат под референс; основной
  `create_scene.py` его не перезаписывает.
- `output/1/` … `output/4.5/` — исторические прогоны старой схемы
  `output/<пункт>/`; новые прогоны в них не пишутся.
- Один прогон `render_ring()` = N+1 кадров 1200×1200 @ 128 сэмплов
  (GPU ~9.8 с/кадр в среднем, 5.7–13.4 с в зависимости от азимута; есть
  `RENDER_TIME_LIMIT_S = 75` — страховка, штатно не срабатывает). Серия
  масштабируется по N: N=4 → 5 кадров, N=8 → 9. Полная серия небесплатна:
  Cycles — ~93 % времени, жидкость при 2 постройках на серию — <1 %.
- `config.SCENE_POSTPROCESS` для основной серии: `shift_x_px=105`,
  `target_height_px=0`, `fill_background=False`. Сдвиг совпадает с
  референсом; фон по умолчанию прозрачный. В обоих режимах
  камера привязана к трубке, поэтому трубка целиком в кадре при любом
  наклоне и не уходит за его границу.
  Вертикальная растяжка отключена — она привязана к одиночной
  вертикальной трубке.

## Особенности Blender / окружения

- Настройки Cycles только через
  `bpy.context.preferences.addons['cycles'].preferences`, не `preferences.cycles`.
- `MeshEdge.link_faces` и `Mesh.bound_box` в 4.3 недоступны — работать через
  `bmesh` (см. `validate_tube`).
- В локальной сборке 5.2.3 OpenImageDenoise доступен, но **денуазинг
  выключен явно** (`use_denoising = False`): замер на az180 против эталона
  512 spp показал, что OIDN размывает линию горизонта мениска в 6 раз
  сильнее (MAE в полосе мениска 0.158 → 0.963) и стоит +56 % времени кадра.
  Прежняя запись `bool(_safe_set(...))` была багом — денуазинг не включался
  никогда; теперь это осознанный выбор, `denoiser` задаётся на случай
  выборочного включения в п. 5. AgX и `AgX - Medium High Contrast` работают.
- Тайл трассировки фиксирован: `use_auto_tile = False` + `config.TILE_SIZE
  = 2048`. Авторасчёт Blender на 2× P1000 медленнее на 3–7 %, картинка при
  этом побитово идентична. Разбор рычагов Cycles — `reports/performance.md` §9.
- CUDA включается для дискретных NVIDIA (`config.GPU_DEVICE_FILTER`); если
  GPU нет — обязателен fallback `cycles.device = 'CPU'`.
- Стартовые `WARNING OptiX initialization failed (7804)` и
  `HIPEW initialization failed` безвредны: OptiX — это BVH-ускорение,
  HIP — AMD. Рендер идёт на CUDA.
- `gpu_info.py` в headless показывает `llvmpipe` и `EGL_BAD_MATCH`: это
  GLES-контекст viewports для Workbench, к Cycles отношения не имеет.
  Наличие GPU для рендера проверяет `check_cuda.py`, а не `gpu_info.py`.
- Верх трубки открыт: boundary-рёбра допустимы только на верхней кромке.

## Конвенции

- Документация, комментарии, отчёты и сообщения коммитов — по-русски;
  коммиты в стиле `Пункт N: ...`.
- Порядок работы: сначала обновить/проверить код и `check_liquid.py`,
  затем перерендерить затронутые серии в новый каталог прогона.
- Не менять геометрию/материалы «на ходу» без повторного рендера: PNG в
  `output/` должны соответствовать текущему коду.
- Пункт 3 завершён: мениск работает для вертикали и наклона, отчёт —
  `reports/step3.md`, контрольные рендеры — `output/3/`. `output/1`,
  `output/2`, `output/4` пересняты под ⌀19×92 мм и мениск. Пункты 8–10
  (капли, пузырьки, солвер смешанного контакта) отложены; контекст по
  мениску — `reports/step3.md`, по смешанному контакту —
  `reports/meniscus_mixed_contact.md`.

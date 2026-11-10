# Changelog

Формат основан на [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/).
Проект использует смысловые версии. Подробные обоснования, замеры и
старые записи — в `reports/README.md` и
`reports/archive/changelog_archive.md`.

## [Unreleased]

Планируемая версия: `1.1.0`.

### Добавлено

- **Один вертикальный кадр.** `--tilt-count 0` разрешён и означает
  вырожденную серию N+1: рендерится только вертикаль, наклонный меш не
  строится. `--mode upright` — алиас этого поведения для
  `create_scene.py`; `render_parallel.py` и `render_batch.py` понимают
  режим `upright` и один кадр без шардирования. Проверки —
  `check_batch.py`; CLI — `reports/step6_cli.md`.
- **Градуировка на стекле (шкала объёма).** `injector3d/graduation.py`
  строит риски и подписи как 3D-меши на внешней стенке трубки: короткие
  через 2 мл, длинные на 0, 10 и 20 мл, подписи «10» и «20» повёрнуты на
  180°. Шаг шкалы считается из геометрии трубки
  (`h = V/(πR²)`, для ⌀19 мм — 7.054 мм), капиллярный подъём не
  учитывается; нуль — внутренняя плоскость дна. Цвет, шрифт, кегль и
  размеры рисок — в `config.SCENE_GRADUATION`; по умолчанию разметка
  выключена, включается флагом `--graduation`. Проверки —
  `check_graduation.py`; отчёт — `reports/step11_graduation.md`.
- **Рендер пустого цилиндра (V=0).** `--volume-mm3 0` и
  `--fill-fraction 0` разрешены: меш жидкости и мениск не строятся,
  проверки объёма пропускаются, но PNG, manifest и GLB без жидкости
  создаются штатно. То же поведение — при недоступном меше для
  положительного объёма, без исключения. Проверки — `check_liquid.py`,
  `check_batch.py`; CLI — `reports/step6_cli.md`.
- **bbox в metadata/manifest и overlay.** `injector3d/bbox.py` считает
  alpha-bbox и пересчитывает raw → final; manifest кадра содержит
  `bbox_raw_px`, `bbox_final_px`, `bbox_center_px`, `bbox_width_px`,
  `bbox_height_px`, `bbox_alpha_threshold`, `bbox_touches_border`,
  `bbox_source`. Добавлены `--overlay-bbox/--bbox` и зелёная рамка/точка.
  Коммит `6d2f83f`; отчёт — `reports/step5_postprocessing.md`.
- **Сквозные тайминги.** `injector3d/timing.py`, тайминги этапов кадра,
  серии и пакета, `summarize_timings.py`, `check_timing.py`.
  Коммит `14b1b47`; отчёт — `reports/timing_instrumentation.md`.
- **Пакетный рендер матриц.** `render_batch.py`: жидкости × объёмы ×
  углы × камеры, `batch_manifest.json`, `--resume`, `--limit`,
  `--dry-run`, `check_batch.py`. Коммит `6f07494`; отчёт —
  `reports/step6_cli.md`.
- **Смешанный мениск «стенка + дно».** `meniscus_mixed.py` с clipped-
  солвером, автоматический выбор в `build_liquid()`, проверки и серии
  N=8. Коммиты `8843cd2`, `76e696e`; отчёты —
  `reports/meniscus_mixed_contact.md`, `reports/meniscus_mixed_prototype.md`.
- **Пресеты жидкостей, камеры и GLB.** `LIQUID_PRESETS`, `CAMERA_PRESETS`,
  `--liquid-type`, `--camera-preset`, `--export-glb`. Коммит `b2ba965`;
  отчёт — `reports/step6_cli.md`.
- **Параллельный рендер серии и финальный 2D-центр.** Шардирование по
  GPU, слияние manifest, `render_parallel.py`, result JSON постобработки.
  Отчёты — `reports/step5_postprocessing.md`, `reports/step6_cli.md`.

### Изменено

- **Документация консолидирована.** Содержимое `AGENTS.md` заменено:
  теперь это каноническая инструкция; `KODA.md` удалён; подробный
  changelog перенесён в `reports/archive/changelog_archive.md`,
  завершённые этапы — в
  `reports/archive/plan_archive.md`. `README.md` содержит актуальную
  схему без раздела статуса, `PLAN.md` — только текущие и будущие
  работы, `reports/README.md` — индекс отчётов, `docs/adr/` — ключевые
  архитектурные решения.
- **Вывод по каталогам прогона.** Один прогон — один каталог
  `<база>/<YYYYMMDD_HHMM>/`; имена файлов формирует
  `injector3d/output_manager.py`; номер пункта плана убран из путей.
  Коммит `1e0f83c`.
- **Камера привязана к трубке, наклон идёт по азимуту.** Кадры серии
  различаются типом поворота `Rz(φ)·Ry(θ)·Rz(φ)⁻¹`, а не вращением
  связки вокруг Z. Подробности — `reports/step4.5.md`.
- **Основная серия переведена на камеру 06120 и сдвиг +105 px.** Мир
  сделан белым (`WORLD_STRENGTH = 0.30`), жидкость получила желтоватый
  оттенок по замеру. Отчёты — `reports/reference_camera_06120.md`,
  `reports/performance.md`.

### Исправлено

- **Малый объём при большом наклоне.** Добавлен детектор разрыва и
  плоский fallback с точным объёмом (`flat=True`). Подробности —
  `reports/step3.md`, `reports/meniscus_mixed_contact.md`.
- **Идентичные кадры серии N+1.** Удалён `AzimuthRig`; наклон выполняется
  в плоскости азимута. Подробности — `reports/step4.5.md`,
  `docs/adr/0002-camera-attached-and-azimuth-tilt.md`.
- **Устаревшие оценки производительности.** README приведён к замерам;
  разбор — `reports/performance.md`.

### Удалено

- `KODA.md`; KodaCode читает `AGENTS.md`, отдельный файл не нужен.
- Черновые fisheye-скрипты, не входившие в план.
- `AzimuthRig` и промежуточная схема с неподвижной камерой.

## [1.0.0] — 2026-11-02

### Добавлено

- Manifold-геометрия трубки ⌀19×92 мм, студийная сцена, 5 пробных
  рендеров.
- Единый entry-point `injector3d/create_scene.py` и вынос модулей
  `scene.py`, `render.py`, `config.py`.

### Известные ограничения

- Денуазинг выключен сознательно: OIDN размывает мениск.
- Настройки Cycles-устройств доступны через
  `preferences.addons['cycles'].preferences`.

## Ссылки

- Полная история до 2026-11-08:
  `reports/archive/changelog_archive.md`.
- Завершённые этапы плана: `reports/archive/plan_archive.md`.
- Индекс отчётов: `reports/README.md`.
- Текущий план: `PLAN.md`.
- Архитектура: `README.md#архитектура`.
- Решения: `docs/adr/`.

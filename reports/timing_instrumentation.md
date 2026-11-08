# Учёт времени по этапам: текущее состояние и предложения

**Дата:** 8 ноября 2026
**Статус:** реализовано; проверки и smoke-прогоны пройдены.

## 1. Текущее состояние

Структурированного замера времени по этапам **нет**.

- В `injector3d/render.py`, `create_scene.py`, `liquid.py`,
  `meniscus_mixed.py` нет `time.perf_counter()` / `monotonic()`; длительности
  этапов нигде не накапливаются.
- `injector3d/scene.py` задаёт `scene.cycles.time_limit = 75` — это только
  страховочный лимит рендера, а не измерение.
- В manifest серии (`injector3d/render.py`) и кадра (`_frame_metadata`) есть
  объём, азимут, наклон и центр, но **нет длительностей**.
- В `batch_manifest.json` (`render_batch.py`) есть `created_at`, команды,
  статусы, `returncode`, число кадров и пути — **нет времени выполнения
  серий**.
- `render_parallel.py` пишет shard-логи, но не собирает и не сливает
  тайминги.
- `postprocess_reference.py` возвращает результат обработки, но не её
  длительность.
- `reports/performance.md` содержит только ручные разовые замеры
  (~9.8 с/кадр, жидкость ~0.9 %, Cycles ~93 %); они не обновляются
  автоматически.

## 2. Что предлагается измерять

### Одиночный запуск / серия

- `scene_init` — `setup_scene()`, сброс сцены, камера, свет.
- `tube_build` — геометрия трубки.
- `validate_tube` — валидация геометрии.
- `liquid_upright` — построение вертикальной жидкости.
- `liquid_tilted` — построение наклонной жидкости.
- `materials` — материалы.
- `render` — весь `render_ring()` / `render_tube()`.
- `manifest` — запись manifest.
- `export_glb` — если включён GLB.
- `wall_ms` — общее время процесса сцены.

### Кадр

- `tilt_apply` — установка матрицы/Эйлера и `view_layer.update()`.
- `liquid_build` — построение жидкости для `render_tube()`.
- `save_blend` — `save_as_mainfile`, где он есть.
- `render` — вокруг `bpy.ops.render.render()`.
- `postprocess` — чистая обработка кадра в `postprocess_reference.py`.
- `frame_total` — суммарно по кадру на стороне рендера.

### Пакет

- `started_at`, `finished_at`, `wall_ms` на серию.
- `render`, `postprocess`, `liquid_upright`, `liquid_tilted`, `mesh` и
  остальные этапы из серии.
- Количество кадров, статусы `ok/failed/skipped`, `returncode`.
- Агрегаты по жидкости, объёму, углу, числу наклонов и камере:
  `count`, `total`, `mean`, `median`, `p95`, `min`, `max`.
- Самые медленные серии и кадры.

## 3. Где хранить

- **Кадр:** в `frames[]` manifest добавить `timings_ms`:

  ```json
  "timings_ms": {
    "tilt_apply": 0.2,
    "render": 9800.0,
    "postprocess": 540.0,
    "frame_total": 10350.0
  }
  ```

- **Серия:** в manifest верхнего уровня добавить `timings` с суммами и
  статистикой этапов, `wall_ms`, `started_at`, `finished_at`.
- **Пакет:** в `batch_manifest.json` добавить `timings` в каждую запись
  серии и общий блок агрегатов. Это основной источник истины.
- **Дополнительно:** `summarize_timings.py` умеет отдавать table/CSV/JSON/MD
  из manifest; отдельные `timings.jsonl`/`timings.csv` не обязательны.
- **Логи:** строки вида `TIMING ...` в shard/batch-логах — для отладки, не
  как основной источник.

## 4. Как показывать

- После серии — короткая таблица этапов: total, mean, p95.
- После пакета — сводка по сериям, агрегаты по объёму/углу/жидкости и
  топ медленных серий.
- CLI `summarize_timings.py`: `--batch`, `--run`, `--by`, `--format`.
- Автоматически обновляемый раздел в `reports/performance.md` из manifest,
  чтобы ручные замеры не расходились с кодом.

## 5. Важные нюансы

- `render` — это wall-time вокруг `bpy.ops.render.render()`, включая
  синхронизацию и запись PNG. Для «чистого GPU» нужен отдельный
  профилировщик; в первую версию это не тащим.
- Постобработка асинхронная: нужно разделять `postprocess` (чистая работа
  worker) и время рендера, иначе они смешаются.
- В multi-shard агрегаты серии пересчитываются из **слитых кадров**, а не
  суммируются из агрегатов шардов — иначе upright может посчитаться дважды.
- В каждом тайминг-словаре полезно хранить `shard` и `gpu_index`.
- Время запуска Blender-процесса измеряется в `render_parallel.py` /
  `render_batch.py` отдельно от времени внутри сцены.

## 6. Порядок реализации

1. `injector3d/timing.py` — таймер и агрегаты.
2. Тайминги кадра и серии в `render.py`.
3. Тайминги этапов сцены в `create_scene.py`.
4. Длительность в результате `postprocess_reference.py`.
5. Агрегаты в `render_batch.py`, слияние в `render_parallel.py`.
6. `summarize_timings.py` + проверки в `check_timing.py`,
   `check_batch.py`, `check_postprocess.py`.
7. Обновить `reports/performance.md` и `AGENTS.md`.

## 7. Реализация

- `injector3d/timing.py` — `Timer`, `timed()`, `stats()`,
  `summarize_frames()`, `aggregate_records()`, `group_records()`.
- `create_scene.py` — этапы `scene_init`, `tube_build`, `validate_tube`,
  `materials`, `attach_camera`, `liquid_upright`, `liquid_tilted`,
  `liquid_initial`, `validate_liquid`, `export_glb`; флаги `--timing` /
  `--no-timing`.
- `render.py` — в кадре `tilt_apply`, `liquid_build`, `save_blend`, `render`,
  `postprocess_submit`, `frame_total`; в серии `master_blend`. В manifest
  серии пишется `timings`, в кадр — `timings_ms`.
- `postprocess_reference.py` — возвращает `timings_ms.postprocess` в
  result JSON; `render.py` переносит его в кадр.
- `render_parallel.py` — при слиянии шардов пересчитывает `timings` из
  кадров, чтобы upright не учитывался дважды.
- `render_batch.py` — `started_at`, `finished_at`, `wall_ms`, `timings`
  каждой серии и общий блок `timings` с группами по жидкости, объёму,
  углу, числу наклонов и камере.
- `summarize_timings.py` — table/CSV/JSON/MD по manifest прогона или
  пакета.
- `check_timing.py` — проверки таймера, кадров, batch-агрегатов и слияния
  шардов без Blender.

### Результаты smoke-прогонов

Ring, N=1, θ=10°, 2000 мм³, GPU 0, с постобработкой:

| Этап | total | mean | p95 |
|---|---:|---:|---:|
| `render` (2 кадра) | 23.18 с | 11.59 с | 13.24 с |
| `postprocess` (2 кадра) | 0.28 с | 0.14 с | 0.15 с |
| `liquid_upright` | 2.05 с | 2.05 с | 2.05 с |
| `liquid_tilted` | 1.62 с | 1.62 с | 1.62 с |
| `wall` процесса | 27.19 с | — | — |

Tube, 5 ракурсов, 2000 мм³, GPU 0, без постобработки:

| Этап | total | mean | p95 |
|---|---:|---:|---:|
| `render` (5 кадров) | 96.44 с | 19.29 с | 25.05 с |
| `liquid_build` (5 кадров) | 7.42 с | 1.48 с | 1.50 с |
| `frame_total` | 103.94 с | 20.79 с | 26.58 с |

Пакетный smoke из одной серии записал `wall_ms`, `timings` серии и блок
`timings` с `by_liquid_type`/`by_volume_mm3` и т.п. в
`batch_manifest.json`.

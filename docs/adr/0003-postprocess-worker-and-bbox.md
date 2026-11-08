# ADR 0003: Worker-постобработка и bbox до overlay

- **Статус:** принято
- **Дата:** 2026-11-08
- **Связанные файлы:** `postprocess_reference.py`, `injector3d/bbox.py`,
  `injector3d/render.py`

## Контекст

Постобработка должна успевать за рендером и не удлинять серию настолько,
чтобы это стало основным узким местом. Bbox нужен для метрик объекта и
не должен включать служебный overlay.

## Решение

- `postprocess_reference.py --worker` — один долгоживущий процесс на
  серию; raw PNG отправляется в stdin сразу после рендера.
- Bbox считается по alpha-маске raw до нанесения overlay.
- Final-координаты bbox пересчитываются как `x+shift`, `y·scale` с
  обрезкой по границам.
- Overlay bbox включается отдельно (`--overlay-bbox/--bbox`) и не входит
  в alpha-bbox.
- Manifest кадра хранит `bbox_raw_px`, `bbox_final_px`, `bbox_center_px`,
  `bbox_width_px`, `bbox_height_px`, `bbox_alpha_threshold`,
  `bbox_touches_border`, `bbox_source`.

## Последствия

- Постобработка идёт параллельно рендеру следующего кадра.
- Метрики bbox воспроизводимы независимо от того, включён overlay или
  нет.
- При `--no-postprocess` bbox считается через Blender image API, а
  overlay не рисуется.

## Ссылки

- `reports/step5_postprocessing.md`
- `check_postprocess.py`
- `AGENTS.md`, раздел «Постобработка и вывод»

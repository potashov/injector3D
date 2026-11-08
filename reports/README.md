# Отчёты injector3D

> Индекс подробных отчётов. Актуальные документы лежат в этом каталоге,
> исторические — в `reports/archive/`. Краткая история изменений —
> `CHANGELOG.md`; текущий план — `PLAN.md`.

## Актуальные отчёты

| Файл | О чём |
|---|---|
| `step1.md` | геометрия трубки, manifold-сетка, студийная сцена |
| `step2.md` | объём жидкости, мировой горизонт, бисекция уровня |
| `step3.md` | мениск Юнга-Лапласа, подъём у стенки, плоский fallback |
| `step4.md` | материалы PMMA и жидкости, поглощение |
| `step4.5.md` | серия N+1, наклон по азимуту, два меша, fast-path |
| `step5_postprocessing.md` | постобработка, overlay, 2D-центр, bbox, manifest |
| `step6_cli.md` | CLI, пресеты жидкостей и камеры, GLB, шардирование, batch |
| `timing_instrumentation.md` | тайминги этапов, кадров, серий и пакетов |
| `performance.md` | замеры Cycles, fast-path, GPU и постобработки |
| `reference_camera_06120.md` | камера OV5693, дисторсия и метрики референса |
| `meniscus_mixed_contact.md` | постановка смешанного контакта «стенка + дно» |
| `meniscus_mixed_prototype.md` | реализация clipped-солвера и проверки |
| `meniscus_mixed_solver_design.md` | расчётная схема и численные детали солвера |

## Архив

| Файл | Почему в архиве |
|---|---|
| `archive/changelog_archive.md` | подробный changelog до 2026-11-08 |
| `archive/plan_archive.md` | снимок старого `PLAN.md` с завершёнными этапами |
| `archive/plan_status_and_next_steps.md` | исторический аудит статуса и roadmap |
| `archive/step6_options_plan.md` | выполненный план пункта 6 |
| `archive/code_review_20261107.md` | снимок ревизии кода на 2026-11-07 |

## Правила

- Новые отчёты по этапам и замерам добавляются в `reports/`.
- Завершённые планы, аудиты и снимки переносятся в `reports/archive/`.
- `reports/archive/` — не источник текущего статуса; не читать без
  необходимости.
- Краткие факты изменений — в `CHANGELOG.md`, а не в отчётах.
- Ссылки на отчёты из `PLAN.md` и `CHANGELOG.md` должны указывать на
  актуальный путь.

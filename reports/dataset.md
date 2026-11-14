# YOLO-pose датасет

## Назначение

`--dataset` превращает обычный рендер injector3D в плоский датасет для
YOLO-pose: изображение трубки с жидкостью, bbox объекта и одна
keypoint — 2D-центр контактной кромки. Во время рендера `.txt` не
создаются: сначала пишется manifest с числовыми полями, затем
`injector3d/dataset.py` собирает датасет без Blender и без PIL.

## Классы

Шаг класса — 0.1 мл:

```text
class_id = round(target_volume_mm3 / 100)
```

Диапазон `0..200`, имена `level_vol_0.0 … level_vol_20.0`. Объём должен
быть кратен 100 мм³; `V=0` даёт класс 0. Класс считается по номиналу
`target_volume_mm3`, а не по фактическому `volume_mm3` после подгонки
уровня.

## Структура

```text
<output-dir>/
  images/ds_<index>_<class>_<liquid>_v<volume>_<camera>_<variant>[_<tag>].png
  labels/<то же имя>.txt
  train.txt
  val.txt
  test.txt
  data.yaml
  dataset_manifest.json
```

`data.yaml` содержит `kpt_shape: [1, 2]`, 201 имя класса, `path: .` и
пути к split-файлам. Split по умолчанию `0.8/0.1/0.1`; серия целиком
попадает в один split, выбор детерминирован `seed=0`.

## Метка

```text
<class_id> <bbox_x> <bbox_y> <bbox_w> <bbox_h> <kpt_x> <kpt_y>
```

`bbox_*` — центр и размеры финального alpha-bbox; `kpt` —
`ellipse_center_2d_px`. Нормировка идёт по фактическому размеру
финального PNG: X/ширина — по ширине, Y/высота — по высоте, без
Y-flip. При `V=0` keypoint — проекция центра внутреннего дна
(`z = BOTTOM_THICKNESS`), потому что поверхности жидкости нет.

## Политика границ

Исходно планировалось отклонять любой bbox, касающийся границы кадра.
Контрольный рендер `V=0` показал, что alpha-bbox высокой трубки штатно
касается верхнего края (`bbox_final_px = [538, 0, 876, 1104]` при
кадре 1200×1200). Строгий запрет отклонил бы все кадры, включая `V=0`.
Принято компромиссное правило: отклонять касание левой/правой границы,
а верх/низ разрешать. Настройка —
`SCENE_DATASET['reject_border_edges']`; по умолчанию
`('left', 'right')`.

Дополнительно требуется `bbox_source == "alpha"`: bbox по RGB/фону
может включать блики и не совпадать с объектом.

## Режимы запуска

- `create_scene.py --dataset` — только при `--shard-count 1`.
- `render_parallel.py --dataset` — экспорт после слияния manifest
  шардов.
- `render_batch.py --dataset` — экспорт после всех серий, manifest
  ищутся рекурсивно по подкаталогам.

Датасетный режим требует включённую постобработку, запрещает
`--overlay-labels` и `fill_fractions`, отключает градуировку. Рендер
идёт во временный work-dir; после успешного экспорта он удаляется,
`--keep-work DIR` сохраняет его. `--dataset-append` дополняет датасет,
`--dataset-overwrite` пересобирает; без них существующий каталог не
перезаписывается.

## Проверки

- `python3 check_dataset.py` — классы, метки, `V=0`, границы, split,
  append, рекурсивный batch-экспорт.
- `python3 check_postprocess.py` — final size и overlay-labels.
- `python3 check_batch.py` — матрица и CLI batch без Blender.

Контрольные прогоны 2026-11-14:

| Запуск | Результат |
|---|---|
| `create_scene.py --dataset --mode upright --volume-mm3 0` | 1 кадр, class 0, `train=1` |
| `create_scene.py --dataset --mode upright --volume-mm3 5000` | 1 кадр, class 50 |
| `render_parallel.py --dataset ... --volume-mm3 5000` | 1 кадр, work-dir удалён |
| `render_batch.py --dataset --volumes 0,5000` | 2 кадра, `train=2`, split `1,0,0` |

## Ограничения

- Bbox — по alpha всего объекта (трубка + жидкость), а не отдельно по
  жидкости.
- `--overlay-labels` — только preview; с `--dataset` несовместим.
- В `render_batch.py --dataset` work-dir по умолчанию временный,
  поэтому `--resume` имеет смысл только с повторным `--keep-work DIR`.
- Если камера обрежет трубку слева/справа, кадр будет отклонён.

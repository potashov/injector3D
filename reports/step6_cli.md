# Пункт 6: CLI-обвязка, пресеты жидкостей, камера, GLB и параллельный рендер

**Дата:** 7 ноября 2026 (batch runner — 8 ноября 2026)
**Статус:** пункт 6 реализован. Есть режимы `--mode ring|tube`,
`--output-dir` с каталогом прогона по timestamp, параметры объёма, выбор
типа жидкости (`water`, `glycerol`, `ethanol`, `mineral_oil`) и их
физических свойств, пресеты и параметры камеры (`reference`, `studio`),
экспорт в `.glb`, шардирование серии, выбор GPU, launcher, который
запускает несколько Blender-процессов и сливает manifest, и пакетный
`render_batch.py` для матриц параметров. Номер пункта плана из путей,
имён файлов и стадий сцены убран.

## 1. CLI `create_scene.py`

Кроме флагов постобработки (п. 5) поддерживаются:

| Флаг | Смысл |
|------|-------|
| `--mode ring\|tube` | режим рендера: `ring` — N+1 (по умолчанию), `tube` — 5 фиксированных ракурсов |
| `--output-dir PATH` | база вывода; прогон кладётся в `PATH/<YYYYMMDD_HHMM>/` (по умолчанию `config.OUTPUT_DIR`) |
| `--fill-fraction F` | доля вместимости, `0 <= F <= 1`; `0` — пустой цилиндр; по умолчанию `FILL_FRACTION` |
| `--volume-mm3 V` | абсолютный объём в мм³, `0 <= V <= capacity_mm3()`; `0` — пустой цилиндр; взаимоисключающий с `--fill-fraction` |
| `--liquid-type TYPE` | пресет жидкости: `water`, `glycerol`, `ethanol`, `mineral_oil` |
| `--liquid-ior IOR` | переопределение IOR жидкости |
| `--liquid-density RHO` | переопределение плотности жидкости (кг/м³) |
| `--surface-tension S` | переопределение поверхностного натяжения (Н/м) |
| `--contact-angle DEG` | переопределение краевого угла на PMMA (град) |
| `--absorption-density D` | переопределение плотности поглощения Volume Absorption |
| `--camera-preset PRESET` | пресет камеры: `reference` (OV5693 1.3 мм, дисторсия 0.66) или `studio` (85 мм) |
| `--camera-distance MM` | дистанция от оси трубки (мм) |
| `--camera-z MM` | высота камеры (мм) |
| `--camera-target-z MM` | точка наведения камеры по Z (мм) |
| `--camera-lens MM` | фокусное расстояние объектива (мм) |
| `--camera-roll DEG` | угол крена roll (град) |
| `--camera-distortion D` | коэффициент радиальной дисторсии Lensdist (0.0 для прямолинейной) |
| `--export-glb [PATH]` | экспорт геометрии трубки и жидкости с материалами в формат GLB (glTF 2.0) |
| `--gpu-index I` | включить только I-й дискретный CUDA-GPU (0-based) |
| `--shard-index K` | индекс шарда, `0 <= K < M` |
| `--shard-count M` | число шардов; вертикаль достаётся шарду 0, наклонные распределяются по `index % M` |
| `--timestamp TS` | фиксирует имя каталога прогона `<база>/TS/` и синхронизирует шарды |
| `--tag TAG` | дополнительный суффикс к `a<угол>_n<N>` в именах файлов |
| `--graduation` | нанести шкалу объёма на стекло (по умолчанию выключена) |
| `--no-graduation` | не наносить шкалу, даже если включена в конфиге |

Примеры:

```bash
BL=./blender-5.2.3-linux-x64/blender

# Глицерин, экспорт в GLB, 10° наклон
$BL --background --python injector3d/create_scene.py -- \
    --mode ring --liquid-type glycerol --tilt-count 4 --tilt-angle 10 \
    --export-glb
# → output/<YYYYMMDD_HHMM>/glycerol_*.png + glycerol_*.glb

# Студийная камера 85 мм без дисторсии, этанол, своя база вывода
$BL --background --python injector3d/create_scene.py -- \
    --mode ring --camera-preset studio --liquid-type ethanol \
    --tilt-count 8 --tilt-angle 15 --output-dir /mnt/datasets/injector
```

`--volume-mm3` проверяется по `capacity_mm3()` из параметров трубки в
`config.py`; при превышении вместимости выводится ошибка и запуск
прерывается. `--fill-fraction` и `--volume-mm3` нельзя задавать вместе.

При `V=0` или `F=0` рендер не запрещается: это пустой цилиндр, в котором
освещение и рефлексы всё равно зависят от ракурса. Меш жидкости и мениск
в этом случае не строятся, проверки объёма пропускаются, а PNG, manifest
и GLB (без жидкости) создаются как обычно. То же поведение действует,
если положительный объём не удалось превратить в меш: кадр рендерится
без жидкости, без исключения.

Тип жидкости всегда стоит в начале имени кадра (`water_…`,
`glycerol_…`), а в JSON manifest записываются `liquid_type`, `liquid_ior`
и `camera_distortion`. Номера пункта плана в путях и именах нет:
каталог прогона называет timestamp, а префикс-жидкость отличает серию
внутри прогона (`injector3d/output_manager.py`).

## 2. Параллельный запуск

`render_parallel.py` — системный `python3`, не внутри Blender:

```bash
python3 render_parallel.py --tilt-count 8 --tilt-angle 10 \
    --liquid-type glycerol --gpus 0,1 --output-dir /mnt/datasets/injector
```

Он:

1. Определяет GPU (или берёт `--gpus 0,1`); без GPU запускает один
   процесс на CPU.
2. Считает каталог прогона `<output-dir>/<timestamp>` и создаёт его —
   все шарды пишут в один каталог.
3. Делит N наклонных кадров между `M` шардами; вертикаль рендерит
   только шард 0.
4. Запускает `M` процессов Blender; каждый получает
   `--shard-index/--shard-count`, свой `--gpu-index`, общий
   `--output-dir` и `--timestamp`.
5. Пишет логи `<жидкость>_shard<k>.log` в каталоге прогона.
6. После успешного завершения сливает manifest шардов в один
   `<жидкость>_manifest[_<tag>][_s<k>of<M>].json`, сортируя кадры: сначала
   upright, затем по азимуту; shard-manifest'ы удаляются (`--keep-shards`
   оставляет их).

`--dry-run` печатает команды без запуска. `--shards` позволяет задать
число процессов независимо от числа GPU; при `M > числа GPU` шарды
делят GPU по кругу. Имена master `.blend` содержат суффикс
`_s<k>of<M>`, финальные PNG — нет, поэтому конфликтов нет.

## 3. Пакетный рендер матриц

`render_batch.py` — системный `python3`, запускает несколько серий через
`render_parallel.py`:

```bash
python3 render_batch.py --liquids water,glycerol \
    --volumes 2000,5000 --tilt-angles 10,20 --gpus 0,1

python3 render_batch.py --config batch.json \
    --output-dir /mnt/datasets/injector --gpus 0,1
```

Конфиг — JSON-объект:

```json
{
  "mode": "ring",
  "liquids": ["water", "glycerol"],
  "volumes_mm3": [2000, 5000],
  "tilt_angles": [10, 20],
  "tilt_counts": [8],
  "camera_presets": ["reference"],
  "tag": "dataset",
  "extra_args": ["--no-postprocess"]
}
```

Ключи `volumes_mm3` и `fill_fractions` взаимоисключающие; если не задан
ни один, берётся `config.FILL_FRACTION`. Матрица разворачивается в серии
`<жидкость>_v<объём>_a<угол>_n<N>_c_<камера>` (или
`<жидкость>_f<доля>_tube_c_<камера>` для `--mode tube`). Каждая серия
лежит в `<output-dir>/<batch>/<series>/`: master, manifest, PNG и
shard-логи не конфликтуют между объёмами. В корне пакета пишутся
`batch_manifest.json` (матрица, команды, статусы, пути) и `logs/`.

Флаги: `--limit` (первые серии), `--resume` (пропустить серии с готовым
manifest нужной длины), `--keep-going` (продолжать после ошибки),
`--keep-shards`, `--dry-run` (только команды). `--mode tube` принудительно
использует один шард. `check_batch.py` проверяет матрицу, имена серий,
команды, лимит, resume, `batch_manifest.json` и dry-run без Blender.

Smoke-прогон (ring, N=1, 2000 мм³, `--no-postprocess`): 2 кадра,
manifest серии и `batch_manifest.json`; повтор с `--resume` пропускает
готовую серию.

## 4. Manifest и финальный центр

После п. 5 worker возвращает не только готовый PNG, но и итоговый
`ellipse_center_2d_px`; он попадает в manifest рядом с raw-центром.
Поэтому объединённый manifest пригоден для проверки сдвига/масштаба без
повторного чтения PNG. В manifest также есть `shards` — число шардов,
которыми собрана серия.

## 5. Проверки

- `check_postprocess.py` (системный `python3`): alpha bbox, пересчёт
  raw-центра в финальный, RGBA/shift, fill/height, маркер, batch
  namespace и result JSON. Все проверки проходят.
- `check_batch.py` (системный `python3`): матрица, имена серий, команды,
  `--limit`, resume по manifest, атомарная запись `batch_manifest.json`,
  CLI `--dry-run` без создания каталога. Все проверки проходят.
- `--gpu-index 0` и `--gpu-index 1` в headless включают
  `Quadro P1000` с индексами `[0]` и `[1]` соответственно.
- Два шарда, N=2, θ=10°: 3 кадра, в объединённом manifest
  `upright`, `az090`, `az270`; оба master и оба shard-лога созданы,
  shard-manifest'ы слиты.
- Два шарда, N=1, θ=10°, с `--tag`: 2 кадра, имена файлов содержат
  `a10_n1_<tag>`, manifest объединён.
- `--dry-run` показывает по одной команде на GPU.

Проверено после перехода на новую схему вывода (7 ноября 2026):

- `--mode ring`, N=2, глицерин, 12900 мм³, `--output-dir /tmp/...`:
  каталог `<база>/<ts>/`, внутри `glycerol_upright_a10_n2_smoke_v12900.png`,
  `glycerol_master_a10_n2_smoke.blend`, `glycerol_manifest_a10_n2_smoke.json`,
  `glycerol_smoke.glb` — номера шага и timestamp в именах нет.
- `--mode tube`, `--no-postprocess`: 5 кадров `water_<ракурс>_v12901.png`
  и `water_manifest.json` в том же каталоге прогона.
- Launcher, 2 шарда, N=4: один каталог, 5 кадров, объединённый
  `water_manifest_a10_n4.json` (`shards: 2`), `water_shard0.log`/
  `water_shard1.log`, master `_s0of2`/`_s1of2`. При `--shards 1` слияние
  не выполняется — manifest сразу финальный; `--mode tube` с
  `--shards > 1` отклоняется.

## 6. Замер ускорения

1200×1200, 128 сэмплов, θ=10°, два шарда по одному GPU:

| Серия | Один процесс, 2 GPU | Два шарда, по 1 GPU | Ускорение |
|---|---:|---:|---:|
| N=4, 5 кадров, с постобработкой | 49.18 с | 44.70 с | ×1.10 |
| N=8, 9 кадров, без постобработки | 111.37 с | 102.85 с | ×1.08 |

Ожидание ~×1.8 из `reports/performance.md` §9.5 **не подтвердилось**:
один GPU тратит на кадр ~23–25 с, а один процесс с двумя GPU — ~12 с.
Обе P1000 уже работают внутри одного кадра, поэтому деление кадров между
процессами почти не помогает. Launcher оставлен для изоляции шардов и
пакетных запусков, а не как основной рычаг скорости.

## 7. Что осталось

- Пункты 8 и 9 плана: капли на внутренних стенках трубки и пузырьки
  воздуха у мениска.
- Пункт 10 реализован и интегрирован в `build_liquid()`; см.
  `reports/meniscus_mixed_prototype.md`.

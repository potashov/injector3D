"""Рендер трубки под разными углами наклона.

Камера во всех режимах жёстко привязана к трубке (`attach_camera_to_tube`
на этапе вертикальной сцены): трубка в кадре не меняется, наклоняется
только жидкость. Отличаются режимы типом поворота трубки:

- `render_tube()`: наклон вокруг дна по историческим Эйлеровым
  `config.TILT_VARIANTS`;
- `render_ring()`: наклон на θ в плоскости азимута φ вокруг точки
  опоры — `tilt_matrix(θ, φ) = Rz(φ)·Ry(θ)·Rz(φ)⁻¹` в точке опоры.

Свет остаётся в мировых координатах, жидкость — тоже (уровень горизонтален).
Имена файлов: `<жидкость>_<ракурс>[_<tag>][_v<объём>].png`.
Каталог вывода: `<output_dir>/<YYYYMMDD_HHMM>/`.
"""

import atexit
import bpy
import json
import math
import os
import shutil
import subprocess
import tempfile

import numpy as np

from mathutils import Matrix, Vector
from bpy_extras.object_utils import world_to_camera_view

from .config import (
    BOTTOM_THICKNESS,
    DEFAULT_LIQUID_TYPE,
    MM,
    OUTPUT_DIR,
    PROJECT_DIR,
    RENDER_RESOLUTION,
    SCENE_CAMERA,
    SCENE_OVERLAY,
    SCENE_POSTPROCESS,
    TILT_PIVOT_Z_MM,
    TILT_VARIANTS,
    tilt_ring_variants,
)
from . import bbox as bbox_info
from . import timing
from .liquid import contact_center_mm
from .output_manager import (
    blend_filename,
    ensure_dir,
    manifest_filename,
    master_blend_filename,
    new_timestamp,
    render_dir,
    render_filename,
)
from .scene import attach_camera_to_tube


# Временные файлы серии (JSON метаданных, JSON результатов, raw-кадры).
# Штатно каждый удаляется по месту создания, но при прерывании прогона
# (Ctrl+C, ошибка рендера) они оставались бы в /tmp до конца жизни ОС.
# atexit чистит всё, что не было снято с учёта.
_TEMP_FILES = set()


def _register_temp(path):
    _TEMP_FILES.add(path)
    return path


def _unregister_temp(path):
    _TEMP_FILES.discard(path)


def _remove_temp(path):
    """Удаляет временный файл и снимает его с учёта (идемпотентно)."""
    _unregister_temp(path)
    try:
        os.remove(path)
    except OSError:
        pass


def _cleanup_temps():
    """atexit-колбэк: удаляет всё, что не было удалено по месту создания."""
    for path in sorted(_TEMP_FILES):
        try:
            os.remove(path)
        except OSError:
            pass
    _TEMP_FILES.clear()


atexit.register(_cleanup_temps)


def _configure_compositor(scene, distortion=None, fit=False):
    """Включает barrel-дисторсию без обрезки кадра под неё.

    В Blender 5.x композитор переехал из `scene.node_tree` в
    `scene.compositing_node_group`, а `CompositorNodeComposite` заменён на
    `NodeGroupOutput`. Для 4.3 сохранена ветка со старым API.
    """
    if distortion is None:
        cam = scene.camera
        if cam and 'config' in cam and 'distortion' in cam['config']:
            distortion = float(cam['config']['distortion'])
        else:
            distortion = SCENE_CAMERA['distortion']

    if abs(distortion) < 1e-6:
        if hasattr(scene, 'compositing_node_group'):
            scene.compositing_node_group = None
            scene.render.use_compositing = False
        else:
            scene.use_nodes = False
        return

    if hasattr(scene, 'compositing_node_group'):
        tree = bpy.data.node_groups.new(
            'injector3d_compositor', 'CompositorNodeTree')
        tree.interface.new_socket(
            name='Image', in_out='OUTPUT', socket_type='NodeSocketColor')
        scene.compositing_node_group = tree
        scene.render.use_compositing = True

        layers = tree.nodes.new('CompositorNodeRLayers')
        lens = tree.nodes.new('CompositorNodeLensdist')
        lens.inputs['Fit'].default_value = fit
        lens.inputs['Jitter'].default_value = False
        lens.inputs['Dispersion'].default_value = 0.0
        lens.inputs['Distortion'].default_value = distortion
        composite = tree.nodes.new('NodeGroupOutput')
    else:
        scene.use_nodes = True
        tree = scene.node_tree
        tree.nodes.clear()

        layers = tree.nodes.new('CompositorNodeRLayers')
        lens = tree.nodes.new('CompositorNodeLensdist')
        lens.use_fit = fit
        if hasattr(lens, 'use_jitter'):
            lens.use_jitter = False
        lens.inputs['Dispersion'].default_value = 0.0
        lens.inputs['Distortion'].default_value = distortion
        composite = tree.nodes.new('CompositorNodeComposite')

    tree.links.new(layers.outputs['Image'], lens.inputs['Image'])
    tree.links.new(lens.outputs['Image'], composite.inputs['Image'])


def _metadata_with_overlay(metadata, overlay):
    meta = dict(metadata or {})
    meta['overlay'] = dict(overlay or SCENE_OVERLAY)
    return meta


def _write_metadata(metadata, overlay):
    """Пишет временный JSON для одиночной постобработки."""
    meta = _metadata_with_overlay(metadata, overlay)
    fd, path = tempfile.mkstemp(prefix='injector3d_meta_', suffix='.json')
    _register_temp(path)
    with os.fdopen(fd, 'w', encoding='utf-8') as fh:
        json.dump(meta, fh, ensure_ascii=False)
    return path


def _format_color(color):
    return ','.join(str(int(c)) for c in color)


def _postprocess(raw_path, final_path, metadata=None, postprocess=None,
                 overlay=None):
    """Внешняя постобработка: заливка, сдвиг, масштаб, подписи."""
    opts = postprocess or SCENE_POSTPROCESS
    script = os.path.join(PROJECT_DIR, 'postprocess_reference.py')
    python = shutil.which('python3') or 'python3'
    meta_path = _write_metadata(metadata, overlay)
    cmd = [
        python, script, raw_path, final_path,
        '--shift', str(opts.get('shift_x_px', 0)),
        '--height', str(opts.get('target_height_px', 0)),
        '--compression', str(opts.get('compression', 3)),
        '--metadata', meta_path,
    ]
    if opts.get('fill_background', False):
        cmd.append('--fill')
        cmd += ['--fill-color',
                _format_color(opts.get('fill_color', (255, 255, 255, 255)))]
    if opts.get('diagnostics', False):
        cmd.append('--verbose')
    try:
        subprocess.run(cmd, check=True)
    finally:
        _remove_temp(meta_path)


def _start_postprocess_worker(opts):
    """Один долгоживущий процесс постобработки на серию."""
    script = os.path.join(PROJECT_DIR, 'postprocess_reference.py')
    python = shutil.which('python3') or 'python3'
    return subprocess.Popen(
        [python, script, '--worker'],
        stdin=subprocess.PIPE, text=True, bufsize=1)


def _submit_postprocess(worker, job, opts):
    fd, result_path = tempfile.mkstemp(
        prefix='injector3d_result_', suffix='.json')
    os.close(fd)
    _register_temp(result_path)
    job['result'] = result_path
    payload = {'postprocess': opts, 'job': job}
    worker.stdin.write(json.dumps(payload, ensure_ascii=False) + '\n')
    worker.stdin.flush()


def _bbox_from_png(path):
    """Bbox и размер готового PNG через Blender image API (без PIL)."""
    try:
        image = bpy.data.images.load(path, check_existing=False)
    except RuntimeError:
        return None, None
    try:
        width, height = image.size
        if width <= 0 or height <= 0:
            return None, None
        pixels = np.empty(width * height * 4, dtype=np.float32)
        image.pixels.foreach_get(pixels)
        rgba = pixels.reshape((height, width, 4))[::-1]
        raw, source = bbox_info.alpha_bbox(rgba)
        if raw is None:
            return None, (width, height)
        return (bbox_info.bbox_record(raw, (width, height), source=source),
                (width, height))
    finally:
        bpy.data.images.remove(image)


def _finish_postprocess_worker(worker, jobs):
    worker.stdin.close()
    code = worker.wait()
    if code:
        raise subprocess.CalledProcessError(
            code, 'postprocess_reference.py --worker')
    for job in jobs:
        result_path = job.get('result')
        if result_path:
            try:
                with open(result_path, 'r', encoding='utf-8') as fh:
                    result = json.load(fh)
                center = result.get('ellipse_center_2d_px')
                if center is not None:
                    job['metadata']['ellipse_center_2d_px'] = center
                for key in (
                        'bbox_raw_px', 'bbox_final_px', 'bbox_center_px',
                        'bbox_width_px', 'bbox_height_px',
                        'bbox_alpha_threshold', 'bbox_touches_border',
                        'bbox_source', 'final_width_px', 'final_height_px'):
                    if result.get(key) is not None:
                        job['metadata'][key] = result[key]
                process_ms = (result.get('timings_ms') or {}).get(
                    'postprocess')
                if (process_ms is not None
                        and 'timings_ms' in job['metadata']):
                    job['metadata']['timings_ms']['postprocess'] = process_ms
            except (OSError, ValueError):
                print(f'  ! Нет результата постобработки: {result_path}')
            finally:
                _remove_temp(result_path)
        _remove_temp(job['raw'])


def _stop_worker_quietly(worker):
    if worker is None or worker.poll() is not None:
        return
    try:
        worker.stdin.close()
    except OSError:
        pass
    worker.wait()


def _manifest_frames(results):
    frames = []
    for job in results:
        meta = dict(job['metadata'])
        meta.pop('overlay', None)
        meta['final'] = os.path.basename(job['final'])
        frames.append(meta)
    return frames


def _write_manifest(base, liquid_type, tag, shard_suffix, results,
                    timer=None):
    """Manifest серии: имя без timestamp, он уже в имени каталога."""
    name = manifest_filename(liquid_type, tag, shard_suffix)
    path = os.path.join(base, name)
    payload = {
        'tag': tag,
        'timestamp': os.path.basename(base),
        'liquid_type': liquid_type,
        'frames': _manifest_frames(results),
    }
    if timer is not None:
        summary = timer.summary()
        if summary:
            payload['timings'] = summary
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    print(f'  Manifest: {name}')


def _prepare_render_scene():
    """Общие настройки кадра: разрешение, сэмплы, RGBA, композитор."""
    scene = bpy.context.scene
    _configure_compositor(scene)
    scene.render.resolution_x = RENDER_RESOLUTION[0]
    scene.render.resolution_y = RENDER_RESOLUTION[1]
    scene.render.resolution_percentage = 100
    scene.cycles.samples = SCENE_CAMERA['samples']
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = 'PNG'
    scene.render.image_settings.color_mode = 'RGBA'
    return scene


def _render_one(scene, final_path, variant_name, metadata=None,
                postprocess=None, overlay=None, timings=None):
    """Рендер одного кадра; возвращает job для worker-постобработки.

    При включённой постобработке кадр пишется во временный raw, а job
    отправляется в долгоживущий worker. При выключенной — PNG сразу
    считается финальным; job нужен только для manifest.
    """
    opts = postprocess or SCENE_POSTPROCESS
    meta = _metadata_with_overlay(metadata, overlay)
    meta.setdefault('variant', variant_name)
    if opts.get('enabled', True):
        fd, raw_path = tempfile.mkstemp(
            prefix=f'injector3d_{variant_name}_', suffix='_raw.png')
        os.close(fd)
        _register_temp(raw_path)
        scene.render.filepath = raw_path
        with timing.timed(timings, 'render'):
            bpy.ops.render.render(write_still=True)
        print(f'  Кадр: {variant_name} -> raw')
        return {'raw': raw_path, 'final': final_path, 'metadata': meta}

    active = meta['overlay']
    if any(active.get(k) for k in
           ('volume', 'azimuth', 'tilt_deg', 'ellipse_center',
            'center_marker', 'liquid', 'bbox', 'labels')):
        print('  ! Постобработка выключена — подписи/маркер/рамка пропущены')
    raw_center = meta.get('ellipse_center_2d_raw_px')
    if raw_center is not None:
        meta['ellipse_center_2d_px'] = list(raw_center)
    scene.render.filepath = final_path
    with timing.timed(timings, 'render'):
        bpy.ops.render.render(write_still=True)
    bbox_data, size = _bbox_from_png(final_path)
    if bbox_data is not None:
        meta.update(bbox_data)
    if size is not None:
        meta['final_width_px'], meta['final_height_px'] = size
    print(f'  Рендер: {variant_name} -> {os.path.basename(final_path)}')
    return {'raw': None, 'final': final_path, 'metadata': meta}


def _tilt_deg_from_matrix(mw):
    """Угол оси трубки от мировой Z, градусы."""
    c = max(-1.0, min(1.0, float(mw[2][2])))
    return math.degrees(math.acos(c))


def _lensdistort_raw_px(x_px, y_px, width, height, distortion):
    """Пиксель до Lensdist -> пиксель после Lensdist (raw кадр).

    Формула получена из Blender-шейдера `compositor_radial_lens_distortion`
    и сверена с рендером калибровочной сетки: расхождение центроидов
    ≤ 1.2 px. `x_px`/`y_px` — координаты с началом в левом верхнем углу.
    """
    if abs(distortion) < 1e-6:
        return x_px, y_px
    cx, cy = width / 2.0, height / 2.0
    qx = (x_px - cx) / width
    qy = (y_px - cy) / height
    r2 = qx * qx + qy * qy
    factor = (1.0 + distortion) / (1.0 + 4.0 * distortion * r2)
    return cx + (x_px - cx) * factor, cy + (y_px - cy) * factor


def _project_center_raw(center_mm, scene):
    """3D-центр (мм) -> 2D raw-пиксель после Lensdist, левый верхний угол."""
    cam = scene.camera
    if cam is None or center_mm is None:
        return None
    point = Vector((center_mm[0] * MM,
                    center_mm[1] * MM,
                    center_mm[2] * MM))
    co = world_to_camera_view(scene, cam, point)
    width = scene.render.resolution_x
    height = scene.render.resolution_y
    x_pre = co.x * width
    y_pre = (1.0 - co.y) * height
    distortion = SCENE_CAMERA['distortion']
    if 'config' in cam and 'distortion' in cam['config']:
        distortion = float(cam['config']['distortion'])
    return _lensdistort_raw_px(
        x_pre, y_pre, width, height, distortion)


def _frame_metadata(volume_mm3, azimuth_deg, tilt_deg, center_mm, scene,
                    liquid_type=None, liquid_config=None,
                    target_volume_mm3=None):
    meta = {
        'volume_mm3': volume_mm3,
        'azimuth_deg': azimuth_deg,
        'tilt_deg': tilt_deg,
    }
    if target_volume_mm3 is not None:
        meta['target_volume_mm3'] = target_volume_mm3
    if liquid_type:
        meta['liquid_type'] = liquid_type
    if liquid_config and 'ior' in liquid_config:
        meta['liquid_ior'] = liquid_config['ior']
    cam = scene.camera
    if cam and 'config' in cam:
        c = cam['config']
        if 'name' in c:
            meta['camera_preset'] = c['name']
        meta['camera_distortion'] = c.get('distortion', 0.0)
    if center_mm is not None:
        meta['ellipse_center_3d_mm'] = list(center_mm)
        meta['ellipse_center_2d_raw_px'] = list(
            _project_center_raw(center_mm, scene))
    return meta


def _center_for_info(tube, info):
    if not info:
        return None
    if info.get('empty') or info.get('level_mm') is None:
        # Нулевой объём: поверхности нет, ключевая точка — проекция
        # центра внутреннего дна (z = BOTTOM_THICKNESS).
        p = tube.matrix_world @ Vector((0.0, 0.0, BOTTOM_THICKNESS * MM))
        return (p.x / MM, p.y / MM, p.z / MM)
    return contact_center_mm(tube.matrix_world, info['level_mm'],
                             flat=info.get('flat', False),
                             liquid_props=info.get('liquid_config'),
                             mixed=info.get('mixed', False))


def export_scene_glb(filepath, objects=None):
    """Экспорт выбранных объектов сцены в GLB (glTF 2.0 Binary).

    По умолчанию экспортирует объекты трубки и меши жидкости.
    """
    os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
    prev_active = bpy.context.view_layer.objects.active
    prev_selected = [o for o in bpy.context.selected_objects]

    try:
        bpy.ops.object.select_all(action='DESELECT')
        export_objs = []
        if objects:
            for obj in objects:
                if obj and obj.name in bpy.data.objects:
                    export_objs.append(bpy.data.objects[obj.name])
        else:
            for obj in bpy.data.objects:
                if obj.type == 'MESH' and ('Tube' in obj.name or 'Liquid' in obj.name):
                    export_objs.append(obj)

        for obj in export_objs:
            obj.select_set(True)
            obj.hide_viewport = False

        if export_objs:
            bpy.context.view_layer.objects.active = export_objs[0]

        bpy.ops.export_scene.gltf(
            filepath=filepath,
            export_format='GLB',
            use_selection=bool(export_objs),
            export_cameras=False,
            export_lights=False,
            export_apply=True,
            export_materials='EXPORT',
        )
        size_kb = os.path.getsize(filepath) // 1024
        print(f"  Экспорт GLB: {os.path.basename(filepath)} ({size_kb} КБ)")
    finally:
        bpy.ops.object.select_all(action='DESELECT')
        for o in prev_selected:
            if o.name in bpy.data.objects:
                o.select_set(True)
        if prev_active and prev_active.name in bpy.data.objects:
            bpy.context.view_layer.objects.active = prev_active


# ─── render_tube ─────────────────────────────────────────────────────

def render_tube(tube, camera, output_dir=None, on_tilt=None,
                postprocess=None, overlay=None, tag=None,
                liquid_type=None, timer=None, target_volume_mm3=None):
    """Рендер трубки под каждым из TILT_VARIANTS.

    Parameters
    ----------
    tube : bpy.types.Object
        Объект трубки.
    camera : bpy.types.Object
        Объект камеры.
    output_dir : str | None
        Каталог вывода (уже готовый каталог прогона).
        Если None — `config.OUTPUT_DIR/<timestamp>`.
    on_tilt : callable | None
        Вызывается после установки поворота трубки — перестроить жидкость
        под новую ориентацию (её уровень горизонтален в мировых координатах).
    postprocess, overlay : dict | None
        Настройки постобработки и подписей; None — значения из config.
    liquid_type : str | None
        Тип жидкости для имён файлов.
    timer : injector3d.timing.Timer | None
        Общий таймер прогона; None — создать локальный.
    """
    base = ensure_dir(output_dir
                      or render_dir(OUTPUT_DIR, new_timestamp()))
    timer = timer or timing.Timer(name='render_tube')

    attach_camera_to_tube(camera, tube)

    scene = _prepare_render_scene()
    opts = postprocess or SCENE_POSTPROCESS
    worker = (_start_postprocess_worker(opts)
              if opts.get('enabled', True) else None)
    results = []
    liq = liquid_type or DEFAULT_LIQUID_TYPE

    try:
        for variant_name, tilt_euler in TILT_VARIANTS:
            frame_ms = {} if timer.enabled else None
            with timing.timed(frame_ms, 'frame_total'):
                with timing.timed(frame_ms, 'tilt_apply'):
                    tube.rotation_euler = (
                        math.radians(tilt_euler[0]),
                        math.radians(tilt_euler[1]),
                        math.radians(tilt_euler[2]),
                    )
                    bpy.context.view_layer.update()
                with timing.timed(frame_ms, 'liquid_build'):
                    info = (on_tilt(variant_name, tilt_euler)
                            if on_tilt is not None else None)

                liq_type = info.get('liquid_type') if info else None
                liq_cfg = info.get('liquid_config') if info else None
                volume = info.get('volume_mm3') if info else None
                center = _center_for_info(tube, info)
                meta = _frame_metadata(
                    volume, None, _tilt_deg_from_matrix(tube.matrix_world),
                    center, scene, liquid_type=liq_type,
                    liquid_config=liq_cfg,
                    target_volume_mm3=target_volume_mm3)
                render_fname = render_filename(
                    liq_type, variant_name, tag, volume)
                final_path = os.path.join(base, render_fname)

                # Сохраняем .blend (историческое поведение render_tube)
                blend_fname = blend_filename(liq, variant_name, tag)
                blend_path = os.path.join(base, blend_fname)
                with timing.timed(frame_ms, 'save_blend'):
                    bpy.ops.wm.save_as_mainfile(filepath=blend_path)
                print(f'  Сохранён: {blend_fname}')

                job = _render_one(
                    scene, final_path, variant_name, meta, postprocess,
                    overlay, timings=frame_ms)
                if frame_ms is not None:
                    job['metadata']['timings_ms'] = frame_ms
                results.append(job)
                if worker is not None and job['raw']:
                    with timing.timed(frame_ms, 'postprocess_submit'):
                        _submit_postprocess(worker, job, opts)
        if worker is not None:
            _finish_postprocess_worker(
                worker, [job for job in results if job['raw']])
            worker = None
    finally:
        _stop_worker_quietly(worker)

    for job in results:
        timer.record(job['metadata'].get('timings_ms'))
    timer.finish()
    _write_manifest(base, liq, tag, '', results, timer=timer)


# ─── tilt_matrix ─────────────────────────────────────────────────────

def tilt_matrix(theta_rad, azimuth_deg):
    """Мировая матрица трубки: наклон θ в плоскости азимута φ вокруг опоры.

    Поворот `R(φ) = Rz(φ)·Ry(θ)·Rz(φ)⁻¹` — это наклон на θ вокруг
    горизонтальной оси `a(φ) = (-sin φ, cos φ, 0)`. Ось трубки уходит в
    `(sin θ·cos φ, sin θ·sin φ, cos θ)`: угол θ от мировой Z одинаков при
    любом φ, азимут выбирает только плоскость наклона.
    """
    phi = math.radians(azimuth_deg)
    pivot = Vector((0.0, 0.0, TILT_PIVOT_Z_MM * MM))
    axis = Vector((-math.sin(phi), math.cos(phi), 0.0))
    rot = Matrix.Rotation(theta_rad, 4, axis)
    return Matrix.Translation(pivot) @ rot @ Matrix.Translation(-pivot)


# ─── render_ring ─────────────────────────────────────────────────────

def render_ring(tube, liquid_upright, liquid_tilted,
                output_dir=None, n=4, angle=10.0,
                info_upright=None, info_tilted=None,
                postprocess=None, overlay=None, shard=None,
                tag=None, liquid_type=None, timer=None,
                target_volume_mm3=None):
    """Рендер N+1 кадров: вертикаль + N плоскостей наклона при угле `angle`.

    При `n=0` рендерится только вертикальный кадр; `liquid_tilted` и
    `info_tilted` могут быть `None`.

    Камера — дочерняя трубке (привязка сделана в `create_scene` на этапе
    вертикальной сцены) и в цикле не трогается вовсе: меняя единственный
    `tube.matrix_world`, мы перемещаем и наклоняем всю систему «трубка +
    камера» как единое целое.

    Жидкость — независимый меш в мировых координатах: её уровень
    горизонтален в мире и не должен наклоняться вместе с трубкой. Наклонный
    меш строится один раз при φ = 0 и для остальных φ поворачивается вокруг
    мировой Z.

    `shard=(index, count)` оставляет кадры для одного процесса: вертикаль
    достаётся shard 0, наклонные распределяются по `index % count`. Имена
    master и manifest получают суффикс `_s<index>of<count>`, а финальные
    PNG — нет, поэтому шарды не конфликтуют и серию можно слить.
    """
    all_variants = tilt_ring_variants(n, angle)
    if shard is None:
        variants = all_variants
        shard_suffix = ''
    else:
        shard_index, shard_count = shard
        if not (0 <= shard_index < shard_count):
            raise ValueError('shard-index должен быть в 0..shard-count-1')
        variants = []
        tilted = 0
        for name, azimuth in all_variants:
            if azimuth is None:
                if shard_index == 0:
                    variants.append((name, azimuth))
            else:
                if tilted % shard_count == shard_index:
                    variants.append((name, azimuth))
                tilted += 1
        shard_suffix = f'_s{shard_index}of{shard_count}'

    geometric_tag = 'n0' if n == 0 else f'a{angle:g}_n{n}'
    tag = f'{geometric_tag}_{tag}' if tag else geometric_tag
    base = ensure_dir(output_dir
                      or render_dir(OUTPUT_DIR, new_timestamp()))
    timer = timer or timing.Timer(name='render_ring')

    scene = _prepare_render_scene()

    theta_rad = math.radians(angle)
    tube.matrix_world = Matrix.Identity(4)
    if liquid_tilted is not None:
        liquid_tilted.rotation_euler = (0.0, 0.0, 0.0)
    if liquid_upright is not None:
        liquid_upright.hide_render = False
    if liquid_tilted is not None:
        liquid_tilted.hide_render = True
    bpy.context.view_layer.update()

    liq = liquid_type or DEFAULT_LIQUID_TYPE
    master_fname = master_blend_filename(liq, tag, shard_suffix)
    master_path = os.path.join(base, master_fname)
    with timing.timed(timer, 'master_blend'):
        bpy.ops.wm.save_as_mainfile(filepath=master_path)
    print(f'  Сохранён master: {master_fname}')

    opts = postprocess or SCENE_POSTPROCESS
    worker = (_start_postprocess_worker(opts)
              if opts.get('enabled', True) else None)
    results = []
    try:
        for variant_name, azimuth_deg in variants:
            frame_ms = {} if timer.enabled else None
            with timing.timed(frame_ms, 'frame_total'):
                with timing.timed(frame_ms, 'tilt_apply'):
                    if azimuth_deg is None:
                        tube.matrix_world = Matrix.Identity(4)
                        if liquid_upright is not None:
                            liquid_upright.hide_render = False
                        if liquid_tilted is not None:
                            liquid_tilted.hide_render = True
                        info = info_upright
                        tilt_deg = 0.0
                    else:
                        tube.matrix_world = tilt_matrix(
                            theta_rad, azimuth_deg)
                        if liquid_tilted is not None:
                            liquid_tilted.rotation_euler = (
                                0.0, 0.0, math.radians(azimuth_deg))
                        if liquid_upright is not None:
                            liquid_upright.hide_render = True
                        if liquid_tilted is not None:
                            liquid_tilted.hide_render = False
                        info = info_tilted
                        tilt_deg = angle
                    bpy.context.view_layer.update()

                liq_type = info.get('liquid_type') if info else None
                liq_cfg = info.get('liquid_config') if info else None
                volume = info.get('volume_mm3') if info else None
                center = _center_for_info(tube, info)
                meta = _frame_metadata(
                    volume, azimuth_deg, tilt_deg, center, scene,
                    liquid_type=liq_type, liquid_config=liq_cfg,
                    target_volume_mm3=target_volume_mm3)
                render_fname = render_filename(
                    liq_type, variant_name, tag, volume)
                final_path = os.path.join(base, render_fname)
                job = _render_one(
                    scene, final_path, variant_name, meta, postprocess,
                    overlay, timings=frame_ms)
                if frame_ms is not None:
                    job['metadata']['timings_ms'] = frame_ms
                results.append(job)
                if worker is not None and job['raw']:
                    with timing.timed(frame_ms, 'postprocess_submit'):
                        _submit_postprocess(worker, job, opts)
        if worker is not None:
            _finish_postprocess_worker(
                worker, [job for job in results if job['raw']])
            worker = None
    finally:
        _stop_worker_quietly(worker)

    for job in results:
        timer.record(job['metadata'].get('timings_ms'))
    timer.finish()
    _write_manifest(base, liq, tag, shard_suffix, results, timer=timer)

    # Оставляем сцену в вертикальном положении для сохранения/отладки.
    tube.matrix_world = Matrix.Identity(4)
    if liquid_tilted is not None:
        liquid_tilted.rotation_euler = (0.0, 0.0, 0.0)
    if liquid_upright is not None:
        liquid_upright.hide_render = False
    if liquid_tilted is not None:
        liquid_tilted.hide_render = True
    bpy.context.view_layer.update()

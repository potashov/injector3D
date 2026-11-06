"""Рендер трубки под разными углами наклона.

Камера во всех режимах жёстко привязана к трубке (`attach_camera_to_tube`
на этапе вертикальной сцены): трубка в кадре не меняется, наклоняется
только жидкость. Отличаются режимы типом поворота трубки:

- `render_tube()` (п. 1–4): наклон вокруг дна по историческим Эйлеровым
  `config.TILT_VARIANTS`;
- `render_ring()` (п. 4.5): наклон на θ в плоскости азимута φ вокруг точки
  опоры — `tilt_matrix(θ, φ) = Rz(φ)·Ry(θ)·Rz(φ)⁻¹` в точке опоры.

Свет остаётся в мировых координатах, жидкость — тоже (уровень горизонтален).
Имена файлов: `<step>_<ракурс>[_<tag>][_v<объём>]_<timestamp>.png`.
"""

import bpy
import json
import math
import os
import shutil
import subprocess
import tempfile
import time

from mathutils import Matrix, Vector
from bpy_extras.object_utils import world_to_camera_view

from .config import (
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
from .liquid import contact_center_mm
from .scene import attach_camera_to_tube


def _configure_compositor(scene, distortion=None, fit=False):
    """Включает barrel-дисторсию без обрезки кадра под неё.

    В Blender 5.x композитор переехал из `scene.node_tree` в
    `scene.compositing_node_group`, а `CompositorNodeComposite` заменён на
    `NodeGroupOutput`. Для 4.3 сохранена ветка со старым API.
    """
    if distortion is None:
        distortion = SCENE_CAMERA['distortion']

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
        os.remove(meta_path)


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
    job['result'] = result_path
    payload = {'postprocess': opts, 'job': job}
    worker.stdin.write(json.dumps(payload, ensure_ascii=False) + '\n')
    worker.stdin.flush()


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
            except (OSError, ValueError):
                print(f'  ! Нет результата постобработки: {result_path}')
            finally:
                try:
                    os.remove(result_path)
                except FileNotFoundError:
                    pass
        try:
            os.remove(job['raw'])
        except FileNotFoundError:
            pass


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


def _write_manifest(base, step, ts, tag, results):
    name = (f'{step}_manifest_{tag}_{ts}.json' if tag
            else f'{step}_manifest_{ts}.json')
    path = os.path.join(base, name)
    payload = {
        'step': step,
        'tag': tag,
        'timestamp': ts,
        'frames': _manifest_frames(results),
    }
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
                postprocess=None, overlay=None):
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
        scene.render.filepath = raw_path
        bpy.ops.render.render(write_still=True)
        print(f'  Кадр: {variant_name} -> raw')
        return {'raw': raw_path, 'final': final_path, 'metadata': meta}

    active = meta['overlay']
    if any(active.get(k) for k in
           ('volume', 'azimuth', 'tilt_deg', 'ellipse_center',
            'center_marker')):
        print('  ! Постобработка выключена — подписи/маркер пропущены')
    raw_center = meta.get('ellipse_center_2d_raw_px')
    if raw_center is not None:
        meta['ellipse_center_2d_px'] = list(raw_center)
    scene.render.filepath = final_path
    bpy.ops.render.render(write_still=True)
    print(f'  Рендер: {variant_name} -> {os.path.basename(final_path)}')
    return {'raw': None, 'final': final_path, 'metadata': meta}


def _volume_tag(volume_mm3):
    if volume_mm3 is None:
        return ''
    return f'_v{int(round(float(volume_mm3)))}'


def _render_filename(step, variant_name, ts, volume_mm3, tag=None):
    parts = [step, variant_name]
    if tag:
        parts.append(tag)
    stem = '_'.join(parts) + _volume_tag(volume_mm3)
    return f'{stem}_{ts}.png'


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
    return _lensdistort_raw_px(
        x_pre, y_pre, width, height, SCENE_CAMERA['distortion'])


def _frame_metadata(volume_mm3, azimuth_deg, tilt_deg, center_mm, scene):
    meta = {
        'volume_mm3': volume_mm3,
        'azimuth_deg': azimuth_deg,
        'tilt_deg': tilt_deg,
    }
    if center_mm is not None:
        meta['ellipse_center_3d_mm'] = list(center_mm)
        meta['ellipse_center_2d_raw_px'] = list(
            _project_center_raw(center_mm, scene))
    return meta


def _center_for_info(tube, info):
    if not info or info.get('level_mm') is None:
        return None
    return contact_center_mm(tube.matrix_world, info['level_mm'])


def render_tube(tube, camera, step='1', on_tilt=None,
                postprocess=None, overlay=None, timestamp=None, tag=None):
    """Рендер трубки под каждым из TILT_VARIANTS.

    Parameters
    ----------
    tube : bpy.types.Object
        Объект трубки.
    camera : bpy.types.Object
        Объект камеры.
    step : str
        Номер шага для именования файлов.
    on_tilt : callable | None
        Вызывается после установки поворота трубки — перестроить жидкость
        под новую ориентацию (её уровень горизонтален в мировых координатах).
    postprocess, overlay : dict | None
        Настройки постобработки и подписей; None — значения из config.
    """
    ts = timestamp or time.strftime('%Y%m%d_%H%M%S')
    base = os.path.join(OUTPUT_DIR, step)
    os.makedirs(base, exist_ok=True)

    attach_camera_to_tube(camera, tube)

    scene = _prepare_render_scene()
    opts = postprocess or SCENE_POSTPROCESS
    worker = (_start_postprocess_worker(opts)
              if opts.get('enabled', True) else None)
    results = []

    try:
        for variant_name, tilt_euler in TILT_VARIANTS:
            tube.rotation_euler = (
                math.radians(tilt_euler[0]),
                math.radians(tilt_euler[1]),
                math.radians(tilt_euler[2]),
            )
            bpy.context.view_layer.update()
            info = (on_tilt(variant_name, tilt_euler)
                    if on_tilt is not None else None)

            volume = info.get('volume_mm3') if info else None
            center = _center_for_info(tube, info)
            meta = _frame_metadata(
                volume, None, _tilt_deg_from_matrix(tube.matrix_world),
                center, scene)
            render_fname = _render_filename(
                step, variant_name, ts, volume, tag)
            final_path = os.path.join(base, render_fname)

            # Сохраняем .blend (историческое поведение render_tube)
            blend_fname = f'{step}_{variant_name}_{ts}.blend'
            blend_path = os.path.join(base, blend_fname)
            bpy.ops.wm.save_as_mainfile(filepath=blend_path)
            print(f'  Сохранён: {blend_fname}')

            job = _render_one(
                scene, final_path, variant_name, meta, postprocess, overlay)
            results.append(job)
            if worker is not None and job['raw']:
                _submit_postprocess(worker, job, opts)
        if worker is not None:
            _finish_postprocess_worker(
                worker, [job for job in results if job['raw']])
            worker = None
    finally:
        _stop_worker_quietly(worker)

    _write_manifest(base, step, ts, tag, results)


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


def render_ring(tube, liquid_upright, liquid_tilted,
                step='4.5', n=4, angle=10.0,
                info_upright=None, info_tilted=None,
                postprocess=None, overlay=None, shard=None,
                timestamp=None, tag=None):
    """Рендер N+1 кадров: вертикаль + N плоскостей наклона при угле `angle`.

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

    geometric_tag = f'a{angle:g}_n{n}'
    tag = f'{geometric_tag}_{tag}' if tag else geometric_tag
    ts = timestamp or time.strftime('%Y%m%d_%H%M%S')
    base = os.path.join(OUTPUT_DIR, step)
    os.makedirs(base, exist_ok=True)

    scene = _prepare_render_scene()

    theta_rad = math.radians(angle)
    tube.matrix_world = Matrix.Identity(4)
    liquid_tilted.rotation_euler = (0.0, 0.0, 0.0)
    liquid_upright.hide_render = False
    liquid_tilted.hide_render = True
    bpy.context.view_layer.update()

    master_fname = f'{step}_master_{tag}{shard_suffix}_{ts}.blend'
    master_path = os.path.join(base, master_fname)
    bpy.ops.wm.save_as_mainfile(filepath=master_path)
    print(f'  Сохранён master: {master_fname}')

    opts = postprocess or SCENE_POSTPROCESS
    worker = (_start_postprocess_worker(opts)
              if opts.get('enabled', True) else None)
    results = []
    try:
        for variant_name, azimuth_deg in variants:
            if azimuth_deg is None:
                tube.matrix_world = Matrix.Identity(4)
                liquid_upright.hide_render = False
                liquid_tilted.hide_render = True
                info = info_upright
                tilt_deg = 0.0
            else:
                tube.matrix_world = tilt_matrix(theta_rad, azimuth_deg)
                liquid_tilted.rotation_euler = (0.0, 0.0,
                                                math.radians(azimuth_deg))
                liquid_upright.hide_render = True
                liquid_tilted.hide_render = False
                info = info_tilted
                tilt_deg = angle
            bpy.context.view_layer.update()

            volume = info.get('volume_mm3') if info else None
            center = _center_for_info(tube, info)
            meta = _frame_metadata(volume, azimuth_deg, tilt_deg, center,
                                   scene)
            render_fname = _render_filename(
                step, variant_name, ts, volume, tag)
            final_path = os.path.join(base, render_fname)
            job = _render_one(
                scene, final_path, variant_name, meta, postprocess, overlay)
            results.append(job)
            if worker is not None and job['raw']:
                _submit_postprocess(worker, job, opts)
        if worker is not None:
            _finish_postprocess_worker(
                worker, [job for job in results if job['raw']])
            worker = None
    finally:
        _stop_worker_quietly(worker)

    _write_manifest(base, step, ts, tag + shard_suffix, results)

    # Оставляем сцену в вертикальном положении для сохранения/отладки.
    tube.matrix_world = Matrix.Identity(4)
    liquid_tilted.rotation_euler = (0.0, 0.0, 0.0)
    liquid_upright.hide_render = False
    liquid_tilted.hide_render = True
    bpy.context.view_layer.update()

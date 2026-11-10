"""Градуировка (шкала объёма) на внешней поверхности трубки.

Позиции рисок считаются из геометрии трубки, а не снимаются с
референсного кадра: высота столба жидкости объёма ``V`` в цилиндре радиуса
``R`` равна ``h = V / (pi * R^2)``, поэтому при ``INNER_DIAMETER = 19`` мм
один шаг шкалы в 2 мл составляет

    2000 / (pi * 9.5^2) = 7.0540 мм.

Капиллярный подъём мениска в шкале не участвует: деления отвечают
вместимости цилиндра, как на реальном мерном стекле. Нуль шкалы —
внутренняя плоскость дна ``z = BOTTOM_THICKNESS``: там же начинается
отсчёт объёма в ``liquid.capacity_mm3()``, поэтому риска ``V`` мл лежит
ровно на мировом уровне жидкости при ``--volume-mm3 V*1000``.

Длинные риски ставятся на ``major_volumes_ml`` (0, 10 и 20 мл), короткие —
на остальном шаге 2 мл; подписи — только для ``label_volumes_ml`` (10 и
20 мл) и повёрнуты на 180°, как на референсе 06120.

Риски и цифры — тонкие «подушки» краски на внешней стенке: плоский макет
наматывается на цилиндр в параметрах ``u`` (длина дуги) и ``v`` (ось
трубки), поэтому лежит снаружи стекла и не пересекает его. Объекты
разметки делаются дочерними трубке: при наклоне серии они уезжают вместе
со стеклом, а не остаются в мировых координатах, как жидкость.

Ориентация надписей. Параметризация, в которой ``u`` растёт «влево по
кадру» (к камере), левонруча относительно наружной нормали цилиндра,
поэтому намотанный как есть текст получался зеркальным. Правильный
переворот макета подбирается по ``wrap_handedness()``, а не задаётся
жёстко: так «повёрнуто на 180° и не зеркально» выполняется при любом
``arc_toward_camera``.
"""

import math
import os

from .config import (
    BOTTOM_THICKNESS,
    HEIGHT,
    INNER_RADIUS,
    MM,
    OUTER_RADIUS,
    SCENE_GRADUATION,
)


def _merged(params=None):
    """Параметры разметки: значения из конфига плюс переопределения."""
    p = dict(SCENE_GRADUATION)
    if params:
        p.update({k: v for k, v in params.items() if v is not None})
    return p


def cavity_area_mm2(radius_mm=None):
    """Площадь поперечного сечения полости, мм²."""
    r = INNER_RADIUS if radius_mm is None else float(radius_mm)
    return math.pi * r * r


def volume_height_mm(volume_ml, radius_mm=None):
    """Высота столба жидкости объёма ``volume_ml`` в мм.

    Чистая формула цилиндра ``h = V / (pi * R^2)``, без капиллярного
    подъёма; 1 мл = 1000 мм³.
    """
    return float(volume_ml) * 1000.0 / cavity_area_mm2(radius_mm)


def capacity_ml(radius_mm=None, height_mm=None, bottom_mm=None):
    """Вместимость полости в мл — для контроля верхней границы шкалы."""
    r = INNER_RADIUS if radius_mm is None else float(radius_mm)
    h = HEIGHT if height_mm is None else float(height_mm)
    t = BOTTOM_THICKNESS if bottom_mm is None else float(bottom_mm)
    return math.pi * r * r * max(0.0, h - t) / 1000.0


def format_volume(volume_ml):
    """Подпись объёма: целые без дробного нуля («10»), дробные — с ним."""
    return f'{float(volume_ml):g}'


def graduation_layout(params=None):
    """Расчётные позиции рисок и подписей в мм от мирового дна.

    Returns
    -------
    dict
        ``minor_step_ml`` — шаг шкалы;
        ``step_mm`` — высота одного шага (зависит от трубки);
        ``zero_z_mm`` — уровень нуля шкалы;
        ``ticks`` — ``{volume_ml, z_mm, kind, length_mm, u0_mm}``;
        ``labels`` — ``{volume_ml, z_mm, text, u_center_mm}``;
        ``capacity_ml`` — вместимость полости.
    """
    p = _merged(params)
    step_ml = float(p['minor_step_ml'])
    if step_ml <= 0:
        raise ValueError('minor_step_ml должен быть положительным')
    step_mm = volume_height_mm(step_ml)
    zero_z_mm = float(p.get('zero_z_mm', BOTTOM_THICKNESS))
    z_max = HEIGHT - float(p['top_margin_mm'])
    major = {float(v) for v in p['major_volumes_ml']}
    labeled = {float(v) for v in p['label_volumes_ml']}

    ticks = []
    labels = []
    index = 0
    while True:
        volume_ml = index * step_ml
        z_mm = zero_z_mm + index * step_mm
        if z_mm > z_max + 1e-9:
            break
        is_major = volume_ml in major
        ticks.append({
            'volume_ml': volume_ml,
            'z_mm': z_mm,
            'kind': 'long' if is_major else 'short',
            'length_mm': float(p['long_tick_mm'] if is_major
                               else p['short_tick_mm']),
            'u0_mm': float(p['tick_start_mm']),
        })
        if volume_ml in labeled:
            labels.append({
                'volume_ml': volume_ml,
                'z_mm': z_mm,
                'text': format_volume(volume_ml),
                'u_center_mm': float(p['label_center_mm']),
            })
        index += 1

    return {
        'minor_step_ml': step_ml,
        'step_mm': step_mm,
        'zero_z_mm': zero_z_mm,
        'ticks': ticks,
        'labels': labels,
        'capacity_ml': capacity_ml(),
    }


def arc_sign(params=None):
    """Знак прироста азимута при росте ``u``.

    ``arc_toward_camera=True`` даёт ``-1``: длина дуги откладывается от
    базового азимута в сторону камеры, то есть «влево по кадру» от правого
    силуэта трубки — как на референсе 06120.
    """
    p = _merged(params)
    return -1.0 if p['arc_toward_camera'] else 1.0


def wrap_handedness(params=None):
    """Ориентированность связки ``(u, v)`` относительно наружной нормали.

    ``+1`` — намотанный как есть макет читается внешним наблюдателем
    правильно; ``-1`` — был бы зеркальным, и макет нужно отразить.
    """
    return arc_sign(params)


def wrap_point(u_mm, v_mm, radius_mm, phi_rad, center_z_mm, params=None):
    """Точка плоского макета ``(u, v)`` на цилиндре, локальные мм трубки.

    ``u_mm`` — длина дуги от базового азимута ``phi_rad``, ``v_mm`` —
    смещение вдоль оси от ``center_z_mm``, ``radius_mm`` — фактический
    радиус точки.
    """
    angle = phi_rad + arc_sign(params) * u_mm / float(radius_mm)
    return (radius_mm * math.cos(angle),
            radius_mm * math.sin(angle),
            center_z_mm + v_mm)


def wrap_macro_transform(upside_down=False, params=None):
    """Коэффициенты отражения макета при намотке.

    Внешнему наблюдателю экранная пара связана с макетом как
    ``X_экрана = h * u``, ``Y_экрана = v`` (``h`` — handedness). Чтобы
    получить «читается как есть», нужен ``h * u = a``; чтобы получить
    поворот на 180° без зеркала — ``h * u = -a`` и ``v = -b``.
    """
    h = wrap_handedness(params)
    if upside_down:
        return (-h, -1.0)
    return (h, 1.0)


def wrap_geometry(verts_mm, center_z_mm, phi_rad, r_mid_mm,
                  u_offset_mm=0.0, upside_down=False, params=None):
    """Наматывает вершины плоского макета (мм) на стенку трубки.

    Макет задан в плоскости X-Y: X — направление чтения, Y — ось трубки
    («верх» глифа), Z — радиальный сдвиг подушки. Отражения подбираются
    ``wrap_macro_transform()``, поэтому при ``upside_down=True`` надпись
    повёрнута на 180° и остаётся не зеркальной.
    """
    k_u, k_v = wrap_macro_transform(upside_down, params)
    out = []
    for (x, y, z) in verts_mm:
        out.append(wrap_point(k_u * x + u_offset_mm, k_v * y,
                              r_mid_mm + z, phi_rad, center_z_mm, params))
    return out


def pad_radii_mm(params=None):
    """Радиусы внутренней и внешней граней подушки краски, мм."""
    p = _merged(params)
    r_in = OUTER_RADIUS + float(p['surface_gap_mm'])
    r_out = r_in + float(p['pad_thickness_mm'])
    return r_in, r_out


def tick_arc_span_rad(length_mm, radius_mm):
    """Угловой размер риски в радианах — для отчётов и проверок."""
    return float(length_mm) / float(radius_mm)


def _add_tick_box(bm, tick, r_in_mm, r_out_mm, p, segments=8):
    """Добавляет в bmesh один «кирпичик» риски, намотанный на цилиндр.

    Сечение кирпича — прямоугольник (высота риски × толщина подушки),
    длина — по дуге. Сечение повторяется ``segments + 1`` раз, поэтому
    наружная грань лежит на цилиндре, а не хордой под ним.
    """
    phi = math.radians(float(p['azimuth_deg']))
    half = 0.5 * float(p['tick_thickness_mm'])
    v0 = tick['z_mm'] - half
    v1 = tick['z_mm'] + half
    u0 = tick['u0_mm']
    u1 = u0 + tick['length_mm']

    rings = []
    for i in range(segments + 1):
        u = u0 + (u1 - u0) * i / segments
        quad = []
        for v, r in ((v0, r_in_mm), (v1, r_in_mm),
                     (v1, r_out_mm), (v0, r_out_mm)):
            x, y, z = wrap_point(u, v, r, phi, 0.0, p)
            quad.append(bm.verts.new((x * MM, y * MM, z * MM)))
        rings.append(quad)

    def _face(vs):
        try:
            bm.faces.new(vs)
        except ValueError:
            pass

    for i in range(segments):
        a = rings[i]
        b = rings[i + 1]
        for j in range(4):
            k = (j + 1) % 4
            _face((a[j], a[k], b[k], b[j]))
    _face(list(reversed(rings[0])))
    _face(rings[-1])


def _load_font(path):
    """Загружает TTF в ``bpy.data.fonts``; None — встроенный шрифт Blender."""
    import bpy

    if not path:
        return None
    resolved = os.path.abspath(path)
    if not os.path.exists(resolved):
        print(f'    ! Шрифт не найден: {path} — используется Bfont')
        return None
    for font in bpy.data.fonts:
        if os.path.abspath(font.filepath or '') == resolved:
            return font
    try:
        return bpy.data.fonts.load(resolved)
    except (OSError, RuntimeError) as exc:
        print(f'    ! Шрифт не загрузился ({exc}) — используется Bfont')
        return None


def _text_geometry(text, font_path, size_mm, extrude_mm):
    """Плоский макет надписи: вершины (мм) и полигоны.

    X — направление чтения, Y — ось трубки («верх» глифа), Z —
    радиальный сдвиг подушки. Кегль задаётся в мм (в метры сцены
    переводит ``MM``); высота цифр ≈ 0.7 кегля, поэтому ``font_size_mm``
    — это кегль, а не высота букв.
    """
    import bmesh
    import bpy

    curve = bpy.data.curves.new('GraduationText', type='FONT')
    curve.body = text
    curve.size = float(size_mm) * MM
    curve.extrude = float(extrude_mm) * MM
    curve.align_x = 'CENTER'
    curve.align_y = 'CENTER'
    font = _load_font(font_path)
    if font is not None:
        curve.font = font

    tmp = bpy.data.objects.new('GraduationTextTmp', curve)
    bpy.context.collection.objects.link(tmp)
    bpy.context.view_layer.update()
    deps = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(tmp.evaluated_get(deps),
                                         depsgraph=deps)
    bm = bmesh.new()
    try:
        bm.from_mesh(me)
        # Грань глифа плоская, а после намотки на цилиндр её хорда уходит
        # внутрь стекла на R(1 - cos(Δφ/2)). При подписи «20» и R = 10.5 мм
        # это миллиметры — больше толщины подушки краски, поэтому без
        # тесселяции буква «проваливается». Дробим рёбра до ~0.4 мм, чтобы
        # макет повторял кривизну стекла.
        for _ in range(6):
            long_edges = [e for e in bm.edges if e.calc_length() > 0.4 * MM]
            if not long_edges:
                break
            bmesh.ops.subdivide_edges(bm, edges=long_edges, cuts=1,
                                      use_grid_fill=True)
        verts = [(v.co.x / MM, v.co.y / MM, v.co.z / MM) for v in bm.verts]
        bm.verts.index_update()
        polys = [tuple(v.index for v in f.verts) for f in bm.faces]
    finally:
        bm.free()
        bpy.data.objects.remove(tmp, do_unlink=True)
        bpy.data.curves.remove(curve)
        bpy.data.meshes.remove(me)

    if not verts or not polys:
        raise RuntimeError(f'Не удалось построить надпись «{text}»')
    return verts, polys


def create_material_graduation(name='Graduation_Ink', params=None):
    """Материал разметки: непрозрачная «краска» с цветом из конфига."""
    import bpy

    p = _merged(params)
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    for node in nt.nodes:
        nt.nodes.remove(node)

    color = tuple(float(c) for c in p['color'])
    rgba = color if len(color) == 4 else color + (1.0,)

    bsdf = nt.nodes.new('ShaderNodeBsdfPrincipled')
    bsdf.name = 'Graduation_BSDF'
    bsdf.inputs['Base Color'].default_value = rgba
    bsdf.inputs['Roughness'].default_value = float(p['roughness'])
    bsdf.inputs['Transmission Weight'].default_value = 0.0
    bsdf.inputs['IOR'].default_value = 1.45

    mout = nt.nodes.new('ShaderNodeOutputMaterial')
    mout.name = 'Material_Output'
    nt.links.new(bsdf.outputs['BSDF'], mout.inputs['Surface'])

    mat.diffuse_color = rgba
    return mat


def _link_object(name, me, material, tube):
    """Ссылает меш в объект, красит его и делает дочерним трубке.

    Трубка в момент сборки вертикальна (``matrix_world`` единична),
    поэтому ``matrix_parent_inverse`` тоже единичен: разметка наследует
    любой будущий ``tube.matrix_world`` и наклоняется вместе со стеклом.
    """
    import bpy
    from mathutils import Matrix

    obj = bpy.data.objects.new(name, me)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(material)
    obj.parent = tube
    obj.matrix_parent_inverse = Matrix.Identity(4)
    return obj


def build_graduation(tube, params=None, name='Graduation'):
    """Строит разметку и возвращает список её объектов.

    Создаёт два меша: ``<name>_Ticks`` (риски) и ``<name>_Labels``
    (подписи). Оба — дочерние трубки, лежат в локальных координатах
    трубки и окрашены ``create_material_graduation``. При
    ``enabled=False`` возвращает пустой список и ничего не создаёт.
    """
    import bmesh
    import bpy

    p = _merged(params)
    if not p['enabled']:
        return []

    layout = graduation_layout(p)
    phi = math.radians(float(p['azimuth_deg']))
    r_in, r_out = pad_radii_mm(p)
    r_mid = 0.5 * (r_in + r_out)
    extrude = 0.5 * float(p['pad_thickness_mm'])
    material = create_material_graduation(params=p)

    # 1. Риски: один manifold-меш из намотанных «кирпичиков».
    bm = bmesh.new()
    for tick in layout['ticks']:
        _add_tick_box(bm, tick, r_in, r_out, p)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-7)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    if bm.calc_volume(signed=True) < 0:
        bmesh.ops.reverse_faces(bm, faces=bm.faces)
    ticks_me = bpy.data.meshes.new(name + '_Ticks_Mesh')
    bm.to_mesh(ticks_me)
    bm.free()
    ticks_me.update()
    objects = [_link_object(name + '_Ticks', ticks_me, material, tube)]

    # 2. Подписи: все надписи в один меш, повёрнутые на 180°.
    label_verts = []
    label_polys = []
    for label in layout['labels']:
        verts, polys = _text_geometry(label['text'], p['font_path'],
                                      p['font_size_mm'], extrude)
        wrapped = wrap_geometry(verts, label['z_mm'], phi, r_mid,
                                u_offset_mm=label['u_center_mm'],
                                upside_down=p['label_upside_down'],
                                params=p)
        offset = len(label_verts)
        label_verts.extend(wrapped)
        label_polys.extend(tuple(i + offset for i in poly) for poly in polys)

    if label_verts:
        bm = bmesh.new()
        grid = [bm.verts.new((x * MM, y * MM, z * MM))
                for (x, y, z) in label_verts]
        for poly in label_polys:
            try:
                bm.faces.new([grid[i] for i in poly])
            except ValueError:
                pass
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-7)
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        labels_me = bpy.data.meshes.new(name + '_Labels_Mesh')
        bm.to_mesh(labels_me)
        bm.free()
        labels_me.update()
        objects.append(_link_object(name + '_Labels', labels_me,
                                   material, tube))

    return objects


def describe(layout=None, params=None):
    """Короткая сводка разметки для лога прогона."""
    p = _merged(params)
    layout = layout or graduation_layout(p)
    ticks = layout['ticks']
    major = sum(1 for t in ticks if t['kind'] == 'long')
    top = ticks[-1]['volume_ml'] if ticks else 0.0
    labels = ', '.join(f"{l['text']} на {l['z_mm']:.2f} мм"
                       for l in layout['labels']) or 'нет'
    return (f"шаг {layout['minor_step_ml']:g} мл = "
            f"{layout['step_mm']:.3f} мм; "
            f"рисок {len(ticks)} (длинных {major}, "
            f"верх {top:g} мл, вместимость "
            f"{layout['capacity_ml']:.1f} мл); "
            f"подписи: {labels}")

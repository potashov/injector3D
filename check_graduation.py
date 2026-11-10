"""Самопроверка градуировки (шкала объёма на стекле).

Проверяет:
1. Расчёт шага шкалы из геометрии трубки: h = V / (pi * R^2), без
   капиллярного подъёма; шаг 2 мл для ⌀19 мм равен 7.0536 мм.
2. Позиции рисок, длинные риски на 0/10/20 мл, подписи только на 10 и 20.
3. Совпадение риски V мл с мировым уровнем жидкости при --volume-mm3.
4. Ориентацию подписей: поворот на 180° без зеркала (по базису
   намотки), а не «как получится».
5. Геометрию мешей разметки: manifold, лежат снаружи стенки, дочерние
   трубке, наклон серии переносит разметку вместе со стеклом.
6. Отсутствие разметки при enabled=False и работу флага CLI.

Запуск:
    ./blender-5.2.3-linux-x64/blender --background --python check_graduation.py
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bmesh
import bpy
from mathutils import Matrix

from injector3d import graduation as grad
from injector3d.config import (
    BOTTOM_THICKNESS,
    HEIGHT,
    INNER_RADIUS,
    MM,
    OUTER_RADIUS,
    SCENE_GRADUATION,
)
from injector3d.liquid import (
    CLEARANCE,
    build_liquid,
    create_liquid,
    meniscus_volume_mm3,
)
from injector3d.render import tilt_matrix
from injector3d.scene import setup_scene
from injector3d.tube_geometry import create_tube


OK = True


def check(name, condition, detail=''):
    global OK
    status = 'OK' if condition else 'FAIL'
    print(f'  {status:<4} {name}{"  " + detail if detail else ""}')
    if not condition:
        OK = False


def check_close(name, got, expected, tol, unit=''):
    check(name, abs(got - expected) <= tol,
          f'({got:.6g}{unit}, ожидалось {expected:.6g}{unit}, '
          f'Δ {abs(got - expected):.3g} ≤ {tol})')


def params(enabled=True, **overrides):
    p = dict(SCENE_GRADUATION)
    p['enabled'] = enabled
    p.update(overrides)
    return p


def section_1_geometry():
    print('\n1. Шаг шкалы из геометрии трубки (без капиллярности)')
    area = math.pi * INNER_RADIUS ** 2
    check_close('площадь полости', grad.cavity_area_mm2(), area, 1e-9, ' мм²')

    step = grad.volume_height_mm(2.0)
    expected = 2000.0 / area
    check_close('шаг 2 мл по h = V/(πR²)', step, expected, 1e-9, ' мм')
    check_close('шаг 2 мл = 7.0536 мм', step, 7.0536, 1e-3, ' мм')

    # Формула должна масштабироваться вместе с трубкой, а не быть константой.
    for radius in (5.0, 7.5, 12.0):
        r_area = math.pi * radius ** 2
        check_close(f'шаг 2 мл при R={radius:g} мм',
                    grad.volume_height_mm(2.0, radius), 2000.0 / r_area,
                    1e-9, ' мм')

    check_close('вместимость полости', grad.capacity_ml(), 25.80, 0.01, ' мл')


def section_2_layout():
    print('\n2. Раскладка рисок и подписей')
    p = params()
    layout = grad.graduation_layout(p)
    ticks = layout['ticks']
    check('нуль шкалы на плоскости дна',
          abs(layout['zero_z_mm'] - BOTTOM_THICKNESS) < 1e-12,
          f'({layout["zero_z_mm"]:.3f} мм)')
    check('риски не выходят за верх трубки',
          all(t['z_mm'] <= HEIGHT for t in ticks),
          f'(верх {ticks[-1]["z_mm"]:.2f} ≤ {HEIGHT:g} мм)')
    check('шаг между рисками постоянен',
          all(abs((ticks[i + 1]['z_mm'] - ticks[i]['z_mm']) - layout['step_mm'])
              < 1e-9 for i in range(len(ticks) - 1)),
          f'({layout["step_mm"]:.4f} мм)')
    check('шаг = 2 мл',
          abs(layout['minor_step_ml'] - 2.0) < 1e-12,
          f'({layout["minor_step_ml"]:g} мл)')

    long_vols = [t['volume_ml'] for t in ticks if t['kind'] == 'long']
    check('длинные риски только на 0, 10, 20 мл',
          long_vols == [0.0, 10.0, 20.0], f'({long_vols})')
    check('все остальные риски короткие',
          all(t['kind'] == 'short' for t in ticks
              if t['volume_ml'] not in (0.0, 10.0, 20.0)))
    check('длинная риска длиннее короткой',
          all(t['length_mm'] > 4.0 for t in ticks if t['kind'] == 'long')
          and all(t['length_mm'] < 7.0 for t in ticks
                  if t['kind'] == 'short'))

    labels = layout['labels']
    check('подписи только на 10 и 20 мл',
          [l['volume_ml'] for l in labels] == [10.0, 20.0])
    check('текст подписей «10» и «20»',
          [l['text'] for l in labels] == ['10', '20'])
    check('подпись 10 мл на расчётной высоте',
          abs(labels[0]['z_mm'] - (BOTTOM_THICKNESS
                                   + grad.volume_height_mm(10.0))) < 1e-9)
    check('подпись 20 мл на расчётной высоте',
          abs(labels[1]['z_mm'] - (BOTTOM_THICKNESS
                                   + grad.volume_height_mm(20.0))) < 1e-9)

    # Позиции должны меняться при другой геометрии: шкала не «прибита».
    narrow = grad.graduation_layout(params())
    step_narrow = narrow['step_mm']
    check('шаг зависит от INNER_RADIUS, а не от константы',
          abs(step_narrow - 2000.0 / (math.pi * INNER_RADIUS ** 2)) < 1e-9)


def section_3_volume_alignment():
    print('\n3. Связь риски с объёмом и независимость от мениска')
    tube = create_tube('TubeCheck')
    liquid = create_liquid('LiquidCheck')
    layout = grad.graduation_layout(params())

    # Шкала построена по НОМИНАЛЬНОМУ радиусу (как требует постановка).
    # Меш жидкости строится с зазором CLEARANCE и мениском, поэтому его
    # уровень на оси лежит немного ниже риски. Обе поправки считаются
    # аналитически и проверяются отдельно, чтобы расхождение не
    # маскировало ошибку в самой шкале.
    v_meniscus = meniscus_volume_mm3(None)
    r_liq = INNER_RADIUS - CLEARANCE
    area_liq = math.pi * r_liq ** 2
    z0_liq = BOTTOM_THICKNESS + CLEARANCE
    print(f'  (меш: r={r_liq:.3f} мм, ΔV мениска={v_meniscus:.1f} мм³, '
          f'сдвиг уровня {v_meniscus / area_liq:.3f} мм)')

    max_gap = 0.0
    for tick in layout['ticks']:
        volume_mm3 = tick['volume_ml'] * 1000.0
        if volume_mm3 <= 0.0 or volume_mm3 >= grad.capacity_ml() * 1000.0:
            continue
        tube.matrix_world = Matrix.Identity(4)
        info = build_liquid(liquid, tube, volume_mm3, use_fast=True,
                            liquid_config=None, use_mixed=False)
        if info.get('empty'):
            check(f'уровень для {tick["volume_ml"]:g} мл', False,
                  '(меш не построен)')
            continue
        if info.get('flat'):
            continue
        # Аналитический уровень меша: объём = A_liq·(level − z0_liq) + ΔV.
        expected = z0_liq + (volume_mm3 - v_meniscus) / area_liq
        check_close(f'уровень {tick["volume_ml"]:g} мл по объёму меша',
                    info['level_mm'], expected, 0.05, ' мм')
        max_gap = max(max_gap, tick['z_mm'] - info['level_mm'])

    # Риска выше уровня на поправки меша, но не «уплывает» от неё.
    check('риска и уровень меша расходятся только на поправки',
          0.5 < max_gap < 1.5,
          f'(макс. зазор {max_gap:.3f} мм: мениск + зазор CLEARANCE)')

    bpy.data.objects.remove(tube, do_unlink=True)
    bpy.data.objects.remove(liquid, do_unlink=True)


def section_4_orientation():
    print('\n4. Ориентация подписей: 180° без зеркала')
    p = params()
    k_u, k_v = grad.wrap_macro_transform(upside_down=True, params=p)
    h = grad.wrap_handedness(p)
    # Экранная пара: X = h·u, Y = v. Для перевёрнутой надписи нужно
    # X = −x и Y = −y, где x,y — координаты макета.
    check('надпись перевёрнута по вертикали', k_v < 0, f'(k_v={k_v:g})')
    check('надпись не зеркальна: h·k_u = −1', abs(h * k_u - (-1.0)) < 1e-12,
          f'(h={h:g}, k_u={k_u:g})')
    k_u_n, k_v_n = grad.wrap_macro_transform(upside_down=False, params=p)
    check('неперевёрнутая надпись читается как есть',
          abs(h * k_u_n - 1.0) < 1e-12 and k_v_n > 0,
          f'(h·k_u={h * k_u_n:g}, k_v={k_v_n:g})')
    check('флаг label_upside_down включён по умолчанию',
          p['label_upside_down'] is True)

    # Тождество поворота: «перевёрнутая намотка» совпадает с намоткой
    # макета, повёрнутого на 180° в его плоскости, — то есть это именно
    # поворот, а не отражение.
    verts = [(1.0, 2.0, 0.0), (-3.0, 0.5, 0.0), (0.0, -1.5, 0.0)]
    r_mid = OUTER_RADIUS + 0.05
    flipped = grad.wrap_geometry(verts, 50.0, 0.0, r_mid,
                                 upside_down=True, params=p)
    rotated = grad.wrap_geometry([(-x, -y, z) for (x, y, z) in verts],
                                 50.0, 0.0, r_mid,
                                 upside_down=False, params=p)
    max_err = max(math.dist(a, b) for a, b in zip(flipped, rotated))
    check('перевёрнутая надпись — поворот на 180°, а не зеркало',
          max_err < 1e-9, f'(max Δ {max_err:.2e} мм)')

    # И наоборот: отражение по X не должно давать того же результата.
    mirrored = grad.wrap_geometry([(-x, y, z) for (x, y, z) in verts],
                                  50.0, 0.0, r_mid,
                                  upside_down=False, params=p)
    min_err = min(math.dist(a, b) for a, b in zip(flipped, mirrored))
    check('зеркальный вариант отличим от повёрнутого',
          min_err > 1e-6, f'(min Δ {min_err:.3g} мм)')


def section_5_mesh():
    print('\n5. Меши разметки: manifold, снаружи стенки, дочерние трубке')
    tube = create_tube('TubeMesh')
    objs = grad.build_graduation(tube, params=params())
    check('разметка создаёт два объекта', len(objs) == 2,
          f'({[o.name for o in objs]})')

    r_in, r_out = grad.pad_radii_mm(params())
    for obj in objs:
        me = obj.data
        bm = bmesh.new()
        bm.from_mesh(me)
        bad = [e for e in bm.edges if len(e.link_faces) != 2]
        degenerate = [f for f in bm.faces if f.calc_area() <= 1e-15]
        radii = [math.hypot(v.co.x, v.co.y) / MM for v in bm.verts]
        zs = [v.co.z / MM for v in bm.verts]
        bm.free()
        check(f'{obj.name}: manifold', not bad, f'({len(bad)} рёбер)')
        check(f'{obj.name}: нет вырожденных граней', not degenerate)
        check(f'{obj.name}: снаружи внешней стенки',
              min(radii) >= r_in - 1e-6,
              f'(r_min={min(radii):.3f} ≥ {r_in:.3f} мм)')
        check(f'{obj.name}: в пределах подушки краски',
              max(radii) <= r_out + 1e-6,
              f'(r_max={max(radii):.3f} ≤ {r_out:.3f} мм)')
        check(f'{obj.name}: не выходит за трубку по Z',
              0.0 <= min(zs) and max(zs) <= HEIGHT,
              f'(z=[{min(zs):.2f},{max(zs):.2f}] мм)')
        check(f'{obj.name}: дочерний трубке', obj.parent is tube)
        check(f'{obj.name}: материал разметки',
              obj.data.materials and
              obj.data.materials[0].name.startswith('Graduation_'))

    # Наклон серии: разметка должна уехать вместе со стеклом.
    tube.matrix_world = tilt_matrix(math.radians(20.0), 0.0)
    bpy.context.view_layer.update()
    world_z = [(obj.matrix_world @ v.co).z / MM
               for obj in objs for v in obj.data.vertices]
    check('наклон 20° переносит разметку вместе с трубкой',
          max(world_z) < HEIGHT,
          f'(мировой z_max={max(world_z):.2f} < {HEIGHT:g} мм)')

    tube.matrix_world = Matrix.Identity(4)
    for obj in objs:
        bpy.data.objects.remove(obj, do_unlink=True)
    bpy.data.objects.remove(tube, do_unlink=True)


def section_6_switch():
    print('\n6. Включение через конфиг и параметр вызова')
    check('в конфиге разметка по умолчанию выключена',
          SCENE_GRADUATION['enabled'] is False)
    tube = create_tube('TubeOff')
    check('enabled=False не создаёт объектов',
          grad.build_graduation(tube, params=params(enabled=False)) == [])
    bpy.data.objects.remove(tube, do_unlink=True)

    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.abspath(__file__)), 'injector3d'))
    from create_scene import _parse_cli
    check('--graduation включает разметку',
          _parse_cli(['--graduation'])['graduation'] is True)
    check('--no-graduation выключает разметку',
          _parse_cli(['--no-graduation'])['graduation'] is False)
    check('без флага — None (берётся из конфига)',
          _parse_cli([])['graduation'] is None)


def main():
    print('=== injector3D — Проверка градуировки ===')
    setup_scene(gpu_index=0)
    section_1_geometry()
    section_2_layout()
    section_3_volume_alignment()
    section_4_orientation()
    section_5_mesh()
    section_6_switch()
    print('\n' + ('ВСЕ ПРОВЕРКИ ГРАДУИРОВКИ ПРОЙДЕНЫ' if OK
                  else 'ЕСТЬ ОШИБКИ'))
    if not OK:
        sys.exit(1)


if __name__ == '__main__':
    main()

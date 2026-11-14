#!/usr/bin/env python3
"""Проверки экспорта YOLO-pose датасета без Blender."""

import json
import os
import tempfile

from PIL import Image

from injector3d import dataset


def _make_png(path, width=100, height=200):
    Image.new('RGBA', (width, height), (1, 2, 3, 255)).save(path)


def _frame(volume=5000.0, **overrides):
    frame = {
        'final': 'frame.png',
        'target_volume_mm3': volume,
        'volume_mm3': volume,
        'bbox_center_px': [50.0, 100.0],
        'bbox_width_px': 20.0,
        'bbox_height_px': 40.0,
        'bbox_touches_border': False,
        'bbox_source': 'alpha',
        'ellipse_center_2d_px': [50.0, 150.0],
        'liquid_type': 'water',
        'camera_preset': 'reference',
        'variant': 'upright',
        'tilt_deg': 0.0,
        'azimuth_deg': None,
    }
    frame.update(overrides)
    return frame


def _write_manifest(run_dir, frames, name='water_manifest.json', **extra):
    _make_png(os.path.join(run_dir, 'frame.png'))
    payload = {
        'liquid_type': 'water',
        'tag': None,
        'frames': frames,
    }
    payload.update(extra)
    path = os.path.join(run_dir, name)
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(payload, fh)
    return path


def _expect_error(func, fragment):
    try:
        func()
    except dataset.DatasetError as exc:
        assert fragment in str(exc), (fragment, str(exc))
        return
    raise AssertionError(f'ожидалась DatasetError: {fragment}')


def main():
    assert dataset.class_id_for_volume_mm3(0.0) == 0
    assert dataset.class_id_for_volume_mm3(100.0) == 1
    assert dataset.class_id_for_volume_mm3(5000.0) == 50
    assert dataset.class_id_for_volume_mm3(20000.0) == 200
    assert dataset.class_name(200) == 'level_vol_20.0'
    _expect_error(lambda: dataset.class_id_for_volume_mm3(50.0),
                  'не кратен')
    _expect_error(lambda: dataset.class_id_for_volume_mm3(20100.0),
                  'вне классов')
    print('  OK  классы 0..200, шаг 100 мм³, последний class_id=200')

    line = dataset.label_line(_frame(), 100, 200, strict=True)
    expected = ('50 0.500000 0.500000 0.200000 0.200000 '
                '0.500000 0.750000')
    assert line == expected, line
    print('  OK  метка: класс, bbox и keypoint нормализованы без Y-flip')

    top = _frame(bbox_touches_border=True,
                 bbox_final_px=[10, 0, 90, 199],
                 final_width_px=100, final_height_px=200)
    assert dataset.label_line(top, 100, 200), 'верх/низ должны разрешаться'
    left = _frame(bbox_touches_border=True,
                  bbox_final_px=[0, 10, 90, 190],
                  final_width_px=100, final_height_px=200)
    _expect_error(lambda: dataset.label_line(left, 100, 200),
                  'запрещённой границы: left')
    source = _frame(bbox_source='rendered')
    _expect_error(lambda: dataset.label_line(source, 100, 200),
                  'bbox_source')
    missing_source = _frame()
    missing_source.pop('bbox_source')
    _expect_error(lambda: dataset.label_line(missing_source, 100, 200),
                  'bbox_source')
    print('  OK  верх/низ разрешены; левая граница и не-alpha отклоняются')

    yaml = dataset.data_yaml_text()
    assert 'kpt_shape:\n- 1\n- 2' in yaml
    assert '  0: level_vol_0.0' in yaml
    assert '  200: level_vol_20.0' in yaml
    assert 'path: .\ntrain: ./train.txt\nval: ./val.txt\ntest: ./test.txt' \
        in yaml
    print('  OK  data.yaml: 201 класс, kpt_shape [1, 2], split-файлы')

    name = dataset.dataset_filename(
        1, 0, 0.0, {'liquid_type': 'water', 'camera_preset': None,
                   'variant': 'upright'}, {'tag': 'n0'})
    assert 'None' not in name, name
    assert '_camera_' in name, name
    print('  OK  отсутствующий camera_preset даёт fallback, не None')

    with tempfile.TemporaryDirectory(prefix='injector3d_check_ds_') as tmp:
        run = os.path.join(tmp, 'run')
        root = os.path.join(tmp, 'dataset')
        os.makedirs(run)
        manifest = _write_manifest(run, [_frame()])
        summary = dataset.export_manifests(
            root, [manifest], split_ratios=(1.0, 0.0, 0.0), split_seed=0)
        assert summary['counts'] == {
            'images': 1, 'labels': 1, 'train': 1, 'val': 0, 'test': 0,
        }, summary['counts']
        image = os.path.join(
            root, 'images',
            'ds_0000001_050_water_v005000_reference_upright.png')
        label = os.path.join(
            root, 'labels',
            'ds_0000001_050_water_v005000_reference_upright.txt')
        assert os.path.exists(image), os.listdir(os.path.join(root, 'images'))
        assert os.path.exists(label), os.listdir(os.path.join(root, 'labels'))
        with open(label, 'r', encoding='utf-8') as fh:
            assert fh.read().strip() == line
        with open(os.path.join(root, 'train.txt'), 'r',
                  encoding='utf-8') as fh:
            assert fh.read().strip() == \
                'images/ds_0000001_050_water_v005000_reference_upright.png'
        with open(os.path.join(root, 'dataset_manifest.json'), 'r',
                  encoding='utf-8') as fh:
            payload = json.load(fh)
        assert payload['counts']['images'] == 1
        assert 'source_path' not in payload['frames'][0]
        print('  OK  плоский экспорт, имя, label, train.txt и manifest')

        again = dataset.export_manifests(
            root, [manifest], append=True,
            split_ratios=(1.0, 0.0, 0.0), split_seed=0)
        assert again['counts']['images'] == 1, again['counts']
        assert again['new_frames'] == 0, again['new_frames']
        print('  OK  повторный append не дублирует кадр')

        manifest2 = _write_manifest(
            run, [_frame(volume=0.0, variant='v0')],
            name='water_manifest_2.json')
        merged = dataset.export_manifests(
            root, [manifest2], append=True,
            split_ratios=(1.0, 0.0, 0.0), split_seed=0)
        assert merged['counts']['images'] == 2, merged['counts']
        assert merged['new_frames'] == 1, merged['new_frames']
        assert merged['frames'][1]['class_id'] == 0
        print('  OK  append добавляет V=0 отдельным кадром')

        _expect_error(
            lambda: dataset.export_manifests(
                root, [manifest], split_ratios=(1.0, 0.0, 0.0), split_seed=0),
            'уже существует')
        _expect_error(
            lambda: dataset.export_manifests(
                root, [manifest], append=True, overwrite=True),
            'взаимоисключающие')
        print('  OK  без append/overwrite датасет не трогается; режимы '
              'несовместимы')

        batch = os.path.join(tmp, 'batch')
        series_a = os.path.join(batch, 'water_v5000')
        series_b = os.path.join(batch, 'glycerol_v5100')
        os.makedirs(series_a)
        os.makedirs(series_b)
        _write_manifest(series_a, [_frame()])
        _write_manifest(
            series_b,
            [_frame(volume=5100.0, liquid_type='glycerol')])
        nested_root = os.path.join(tmp, 'dataset_batch')
        nested = dataset.export_run(
            nested_root, batch, recursive=True,
            split_ratios=(1.0, 0.0, 0.0), split_seed=0)
        assert nested['counts']['images'] == 2, nested['counts']
        assert len(dataset.find_manifests(batch, recursive=True)) == 2
        print('  OK  batch: manifest находится рекурсивно по сериям')

    print('\nВсе проверки датасета пройдены.')


if __name__ == '__main__':
    main()

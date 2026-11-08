#!/usr/bin/env python3
"""Проверки batch runner без Blender: матрица, команды, resume, dry-run."""

import json
import os
import subprocess
import sys
import tempfile

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_DIR)

import render_batch as rb


def _expect_exit(func, fragment):
    try:
        func()
    except SystemExit as exc:
        assert fragment in str(exc), (fragment, str(exc))
        return
    raise AssertionError(f'ожидался SystemExit: {fragment}')


def main():
    data = rb._normalize_config({
        'volumes_mm3': [2000, 5000],
        'tilt_angles': [10, 20],
        'tilt_counts': [8],
        'liquids': ['water', 'glycerol'],
        'camera_presets': ['reference'],
        'tag': 'dataset',
    })
    series = rb.build_series(data)
    assert len(series) == 8, len(series)
    assert len({item['id'] for item in series}) == 8
    assert series[0]['id'] == 'water_v2000_a10_n8_c_reference'
    assert series[0]['expected_frames'] == 9
    assert series[0]['volume_mm3'] == 2000.0
    assert series[0]['fill_fraction'] is None
    print('  OK  матрица 2 объёма × 2 угла × 2 жидкости = 8 серий')

    tube = rb.build_series(rb._normalize_config({
        'mode': 'tube',
        'fill_fractions': [0.25, 0.5],
        'liquids': ['water'],
        'camera_presets': ['studio'],
    }))
    assert len(tube) == 2, len(tube)
    assert tube[0]['id'] == 'water_f0p25_tube_c_studio', tube[0]['id']
    assert tube[0]['expected_frames'] == 5
    assert tube[0]['tilt_count'] is None
    print('  OK  tube-режим: доли заполнения, 5 кадров, камера studio')

    _expect_exit(
        lambda: rb.build_series(rb._normalize_config({
            'volumes_mm3': [2000, 2000],
            'liquids': ['water'],
        })), 'Дублирующаяся серия')
    print('  OK  дубликаты серий отклоняются')

    _expect_exit(
        lambda: rb._normalize_config({
            'volumes_mm3': [2000],
            'fill_fractions': [0.5],
        }), 'взаимоисключающие')
    print('  OK  объём и доля заполнения взаимоисключающие')

    _expect_exit(
        lambda: rb._normalize_config({'liquids': ['unknown']}),
        'неизвестные типы')
    print('  OK  неизвестная жидкость отклоняется')

    cmd = rb._series_command(
        series[0], '/tmp/batch', gpus='0,1', shards=2,
        blender='/opt/blender', keep_shards=True)
    text = ' '.join(cmd)
    assert '--timestamp water_v2000_a10_n8_c_reference' in text
    assert '--liquid-type water' in text
    assert '--volume-mm3 2000' in text
    assert '--tilt-count 8' in text
    assert '--tilt-angle 10' in text
    assert '--camera-preset reference' in text
    assert '--tag dataset' in text
    assert '--gpus 0,1' in text and '--shards 2' in text
    assert '--keep-shards' in text
    print('  OK  команда серии содержит матрицу, GPU, шарды и tag')

    assert len(rb._limit_series(series, 3)) == 3
    print('  OK  --limit ограничивает число серий')

    with tempfile.TemporaryDirectory(prefix='injector3d_batch_') as tmp:
        series_dir = os.path.join(tmp, 'water_v2000_a10_n8_c_reference')
        os.makedirs(series_dir)
        manifest = os.path.join(series_dir, 'water_manifest.json')
        with open(manifest, 'w', encoding='utf-8') as fh:
            json.dump({'frames': [{} for _ in range(9)]}, fh)
        assert rb._completed_manifest(series_dir, 9) == manifest
        assert rb._completed_manifest(series_dir, 10) is None
        print('  OK  resume: готовый manifest и неполный manifest')

        payload = rb._manifest_payload(
            'batch', tmp, data, [], '2026-11-08T12:00:00')
        path = rb._write_manifest(tmp, payload)
        with open(path, 'r', encoding='utf-8') as fh:
            saved = json.load(fh)
        assert saved['batch'] == 'batch'
        assert saved['series_count'] == 0
        assert saved['matrix']['liquids'] == ['water', 'glycerol']
        print('  OK  batch_manifest.json пишется атомарно')

        config = os.path.join(tmp, 'batch.json')
        with open(config, 'w', encoding='utf-8') as fh:
            json.dump({
                'volumes_mm3': [2000],
                'tilt_angles': [10],
                'tilt_counts': [1],
                'liquids': ['water'],
                'camera_presets': ['reference'],
                'tag': 'smoke',
                'extra_args': ['--no-postprocess'],
            }, fh)
        out_dir = os.path.join(tmp, 'out')
        result = subprocess.run(
            [sys.executable, os.path.join(PROJECT_DIR, 'render_batch.py'),
             '--config', config, '--output-dir', out_dir,
             '--timestamp', 'dry', '--gpus', '0', '--dry-run'],
            capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        assert 'water_v2000_a10_n1_c_reference' in result.stdout
        assert '--no-postprocess' in result.stdout
        assert not os.path.exists(out_dir), out_dir
        print('  OK  CLI --dry-run: одна серия, extra_args, без каталога')

    print('\nВСЕ ПРОВЕРКИ BATCH RUNNER ПРОЙДЕНЫ')


if __name__ == '__main__':
    main()

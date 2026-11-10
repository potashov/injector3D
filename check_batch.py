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
from injector3d import config as cfg


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

    assert cfg.tilt_ring_variants(0) == (('upright', None),)
    assert len(cfg.tilt_ring_variants(1, 10)) == 2
    try:
        cfg.tilt_ring_variants(-1)
    except ValueError as exc:
        assert '>= 0' in str(exc), exc
    else:
        raise AssertionError('отрицательный N принят')
    print('  OK  N=0: только вертикальный кадр, N=1: вертикаль + наклон')

    upright = rb.build_series(rb._normalize_config({
        'mode': 'upright',
        'volumes_mm3': [2000],
        'liquids': ['water'],
        'camera_presets': ['reference'],
    }))
    assert len(upright) == 1, upright
    assert upright[0]['id'] == 'water_v2000_upright_c_reference'
    assert upright[0]['expected_frames'] == 1
    assert upright[0]['tilt_count'] == 0
    cmd = rb._series_command(
        upright[0], '/tmp/batch', blender='/opt/blender')
    cmd_text = ' '.join(cmd)
    assert '--mode upright' in cmd_text
    assert '--tilt-count' not in cmd_text
    print('  OK  upright-режим: один кадр, без --tilt-count')
    ring0 = rb.build_series(rb._normalize_config({
        'volumes_mm3': [2000],
        'tilt_counts': [0],
        'liquids': ['water'],
        'camera_presets': ['reference'],
    }))
    assert ring0[0]['id'] == 'water_v2000_a10_n0_c_reference'
    assert ring0[0]['expected_frames'] == 1
    cmd0 = rb._series_command(
        ring0[0], '/tmp/batch', gpus='0,1', shards=2,
        blender='/opt/blender')
    assert '--shards 1' in ' '.join(cmd0)
    print('  OK  ring N=0: один вертикальный кадр, один шард')

    empty = rb.build_series(rb._normalize_config({
        'volumes_mm3': [0],
        'tilt_angles': [10],
        'tilt_counts': [1],
        'liquids': ['water'],
        'camera_presets': ['reference'],
    }))
    assert len(empty) == 1 and empty[0]['id'] == 'water_v0_a10_n1_c_reference'
    assert empty[0]['volume_mm3'] == 0.0
    empty_fill = rb.build_series(rb._normalize_config({
        'mode': 'tube',
        'fill_fractions': [0.0],
        'liquids': ['water'],
        'camera_presets': ['studio'],
    }))
    assert empty_fill[0]['id'] == 'water_f0_tube_c_studio'
    assert empty_fill[0]['fill_fraction'] == 0.0
    print('  OK  пустой цилиндр: volumes_mm3/fill_fractions = 0 допустимы')

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
        assert 'timings' in saved and 'by_liquid_type' in saved['timings']
        print('  OK  batch_manifest.json пишется атомарно, с timings')

        timed_records = [
            {'id': 'a', 'liquid_type': 'water', 'volume_mm3': 2000,
             'tilt_angle_deg': 10, 'tilt_count': 1,
             'camera_preset': 'reference', 'wall_ms': 1000.0,
             'timings': {'stages': {
                 'render': {'total_ms': 800.0, 'count': 2}}}},
            {'id': 'b', 'liquid_type': 'water', 'volume_mm3': 5000,
             'tilt_angle_deg': 20, 'tilt_count': 1,
             'camera_preset': 'reference', 'wall_ms': 2000.0,
             'timings': {'stages': {
                 'render': {'total_ms': 1700.0, 'count': 2}}}},
        ]
        summary = rb._batch_timings(timed_records)
        assert summary['wall_ms']['total_ms'] == 3000.0, summary
        assert summary['stages']['render']['total_ms'] == 2500.0, summary
        assert summary['by_volume_mm3']['2000']['series_count'] == 1
        print('  OK  _batch_timings: общие и групповые агрегаты')

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

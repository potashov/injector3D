#!/usr/bin/env python3
"""Проверки таймингов без Blender: Timer, кадры, серии, batch-агрегаты."""

import json
import os
import subprocess
import sys
import tempfile

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_DIR)

from injector3d import timing


def main():
    timer = timing.Timer(name='check')
    with timer.measure('stage_a'):
        pass
    timer.add('stage_a', 10.0)
    timer.add('stage_b', 20.0)
    timer.finish()
    summary = timer.summary()
    assert summary['name'] == 'check'
    assert summary['wall_ms'] >= 0.0
    assert summary['stages']['stage_a']['count'] == 2
    assert summary['stages']['stage_a']['total_ms'] >= 10.0
    assert summary['stages']['stage_b']['total_ms'] == 20.0
    print('  OK  Timer: измерения, запись, summary')

    frame = {}
    with timing.timed(frame, 'render'):
        pass
    with timing.timed(None, 'ignored'):
        pass
    assert 'render' in frame and frame['render'] >= 0.0
    assert 'ignored' not in frame
    print('  OK  timed(): dict и no-op target=None')

    frames = [
        {'timings_ms': {'render': 10.0, 'postprocess': 2.0, 'frame_total': 12.0}},
        {'timings_ms': {'render': 20.0, 'postprocess': 4.0, 'frame_total': 24.0}},
    ]
    frames_summary = timing.summarize_frames(frames)
    assert frames_summary['frames_with_timings'] == 2
    assert frames_summary['stages']['render']['total_ms'] == 30.0
    assert frames_summary['stages']['postprocess']['mean_ms'] == 3.0
    print('  OK  summarize_frames(): слитые кадры шардов')

    records = [
        {
            'id': 'a', 'liquid_type': 'water', 'volume_mm3': 2000,
            'tilt_angle_deg': 10, 'tilt_count': 1, 'camera_preset': 'reference',
            'wall_ms': 1000.0,
            'timings': {'stages': {
                'render': {'total_ms': 800.0, 'count': 2},
                'postprocess': {'total_ms': 100.0, 'count': 2},
            }},
        },
        {
            'id': 'b', 'liquid_type': 'water', 'volume_mm3': 5000,
            'tilt_angle_deg': 20, 'tilt_count': 1, 'camera_preset': 'reference',
            'wall_ms': 2000.0,
            'timings': {'stages': {
                'render': {'total_ms': 1700.0, 'count': 2},
                'postprocess': {'total_ms': 200.0, 'count': 2},
            }},
        },
    ]
    batch = timing.aggregate_records(records)
    assert batch['series_count'] == 2
    assert batch['wall_ms']['total_ms'] == 3000.0
    assert batch['stages']['render']['total_ms'] == 2500.0
    assert set(timing.group_records(records, 'volume_mm3')) == {'2000', '5000'}
    print('  OK  aggregate_records()/group_records(): batch-агрегаты')

    with tempfile.TemporaryDirectory(prefix='injector3d_timing_') as tmp:
        run_manifest = os.path.join(tmp, 'water_manifest.json')
        with open(run_manifest, 'w', encoding='utf-8') as fh:
            json.dump({
                'frames': frames,
                'timings': frames_summary,
            }, fh)
        batch_manifest = os.path.join(tmp, 'batch_manifest.json')
        with open(batch_manifest, 'w', encoding='utf-8') as fh:
            json.dump({
                'batch': 'check',
                'series': records,
                'timings': {
                    **batch,
                    'by_volume_mm3': timing.group_records(
                        records, 'volume_mm3'),
                },
            }, fh)
        for path in (run_manifest, batch_manifest):
            result = subprocess.run(
                [sys.executable,
                 os.path.join(PROJECT_DIR, 'summarize_timings.py'), path],
                capture_output=True, text=True)
            assert result.returncode == 0, result.stderr
            assert 'render' in result.stdout, result.stdout
        from render_parallel import _merge_manifests

        for shard, frame in enumerate(frames):
            shard_path = os.path.join(
                tmp, f'water_manifest_a10_n1_s{shard}of2.json')
            with open(shard_path, 'w', encoding='utf-8') as fh:
                json.dump({
                    'tag': 'a10_n1',
                    'liquid_type': 'water',
                    'frames': [{**frame, 'final': f'f{shard}.png'}],
                }, fh)
        _merge_manifests(tmp, 'ts', 2, keep=True)
        merged_path = os.path.join(tmp, 'water_manifest_a10_n1.json')
        with open(merged_path, 'r', encoding='utf-8') as fh:
            merged = json.load(fh)
        assert merged['shards'] == 2
        assert len(merged['frames']) == 2
        assert merged['timings']['stages']['render']['total_ms'] == 30.0
        print('  OK  _merge_manifests(): тайминги сливаются из кадров')

        csv_path = os.path.join(tmp, 'timings.csv')
        result = subprocess.run(
            [sys.executable,
             os.path.join(PROJECT_DIR, 'summarize_timings.py'),
             batch_manifest, '--format', 'csv', '--by', 'volume_mm3',
             '--output', csv_path],
            capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        with open(csv_path, 'r', encoding='utf-8') as fh:
            text = fh.read()
        assert 'scope,group,stage,count,total_ms' in text, text
        assert '2000' in text, text
        print('  OK  summarize_timings.py: table и CSV')

    print('\nВСЕ ПРОВЕРКИ ТАЙМИНГОВ ПРОЙДЕНЫ')


if __name__ == '__main__':
    main()

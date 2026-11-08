#!/usr/bin/env python3
"""Сводка таймингов из manifest прогона или batch_manifest.json.

Примеры:
    python3 summarize_timings.py output/20261108_1200/water_manifest.json
    python3 summarize_timings.py path/to/batch_manifest.json --by liquid_type
    python3 summarize_timings.py --batch path/to/batch_manifest.json \
        --format csv --output timings.csv
"""

import argparse
import csv
import json
import os
import sys

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_DIR)

from injector3d import timing


def _load(path):
    with open(path, 'r', encoding='utf-8') as fh:
        return json.load(fh)


def _stage_rows(stages):
    rows = []
    for name, stats in sorted((stages or {}).items()):
        if not isinstance(stats, dict):
            continue
        rows.append({
            'stage': name,
            'count': stats.get('count'),
            'total_ms': stats.get('total_ms'),
            'mean_ms': stats.get('mean_ms'),
            'median_ms': stats.get('median_ms'),
            'p95_ms': stats.get('p95_ms'),
            'min_ms': stats.get('min_ms'),
            'max_ms': stats.get('max_ms'),
        })
    return rows


def _wall_value(summary):
    wall = summary.get('wall_ms')
    if isinstance(wall, dict):
        return wall
    if wall is None:
        return {}
    return {'total_ms': wall, 'mean_ms': wall, 'median_ms': wall,
            'p95_ms': wall, 'min_ms': wall, 'max_ms': wall, 'count': 1}


def _summary_for_path(path):
    data = _load(path)
    if 'batch' in data:
        records = data.get('series') or []
        summary = data.get('timings') or timing.aggregate_records(records)
        return {'kind': 'batch', 'path': path, 'summary': summary,
                'records': records, 'data': data}
    if 'frames' in data:
        summary = data.get('timings') or timing.summarize_frames(
            data.get('frames') or [])
        return {'kind': 'run', 'path': path, 'summary': summary,
                'records': [], 'data': data}
    raise SystemExit(f'{path}: не найден блок frames или batch')


def _print_stage_table(stages):
    rows = _stage_rows(stages)
    if not rows:
        print('  нет данных timings')
        return
    print(f"\n  {'этап':<24} {'n':>4} {'total':>10} {'mean':>10} "
          f"{'p95':>10} {'max':>10}")
    for row in rows:
        print(f"  {row['stage']:<24} {row['count'] or 0:>4} "
              f"{_sec(row['total_ms']):>10} {_sec(row['mean_ms']):>10} "
              f"{_sec(row['p95_ms']):>10} {_sec(row['max_ms']):>10}")


def _groups_for(summary, by):
    groups = summary.get('by_' + by)
    if groups is None and by == 'liquid_type':
        groups = summary.get('by_liquid')
    return groups or {}


def _print_table(info, by=None):
    summary = info['summary']
    print(f"Тайминги: {info['path']}")
    if info['kind'] == 'batch':
        print(f"  серий: {summary.get('series_count', 0)}")
    wall = _wall_value(summary)
    if wall.get('total_ms') is not None:
        print(f"  wall: total={_sec(wall.get('total_ms'))} "
              f"mean={_sec(wall.get('mean_ms'))} "
              f"p95={_sec(wall.get('p95_ms'))}")
    _print_stage_table(summary.get('stages'))
    if by and info['kind'] == 'batch':
        groups = _groups_for(summary, by)
        for label, group_summary in groups.items():
            print(f'\n  {by}={label}:')
            _print_stage_table(group_summary.get('stages'))


def _print_markdown(info):
    summary = info['summary']
    print(f"### Тайминги\n\n`{info['path']}`\n")
    print('| этап | n | total | mean | p95 | max |')
    print('|---|---:|---:|---:|---:|---:|')
    for row in _stage_rows(summary.get('stages')):
        print(f"| {row['stage']} | {row['count'] or 0} | "
              f"{_sec(row['total_ms'])} | {_sec(row['mean_ms'])} | "
              f"{_sec(row['p95_ms'])} | {_sec(row['max_ms'])} |")


def _rows_for_csv(info, by=None):
    summary = info['summary']
    rows = []
    scopes = [('all', None, summary)]
    if by and info['kind'] == 'batch':
        groups = _groups_for(summary, by)
        for label, group_summary in groups.items():
            scopes.append((by, label, group_summary))
    for scope, label, scope_summary in scopes:
        wall = _wall_value(scope_summary)
        if wall.get('total_ms') is not None:
            rows.append({
                'scope': scope, 'group': label or '', 'stage': 'wall',
                **wall,
            })
        for row in _stage_rows(scope_summary.get('stages')):
            rows.append({'scope': scope, 'group': label or '', **row})
    return rows


def _write_csv(info, by, output):
    fields = ['scope', 'group', 'stage', 'count', 'total_ms', 'mean_ms',
              'median_ms', 'p95_ms', 'min_ms', 'max_ms']
    rows = _rows_for_csv(info, by)
    if output:
        fh = open(output, 'w', encoding='utf-8', newline='')
    else:
        fh = sys.stdout
    try:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    finally:
        if output:
            fh.close()


def _sec(value):
    if value is None:
        return '-'
    return f'{float(value) / 1000.0:.2f} с'


def main():
    parser = argparse.ArgumentParser(
        description='Сводка таймингов injector3D')
    parser.add_argument('path', nargs='?', help='manifest прогона или пакета')
    parser.add_argument('--batch', help='batch_manifest.json')
    parser.add_argument('--run', help='manifest серии')
    parser.add_argument('--by', default=None,
                        help='группировка batch: liquid_type, volume_mm3, '
                             'tilt_angle_deg, tilt_count, camera_preset')
    parser.add_argument('--format', choices=('table', 'csv', 'json', 'md'),
                        default='table')
    parser.add_argument('--output', help='файл для csv/json/md')
    args = parser.parse_args()
    path = args.batch or args.run or args.path
    if not path:
        parser.error('нужен путь к manifest или --batch/--run')
    info = _summary_for_path(path)
    if args.format == 'json':
        text = json.dumps(info['summary'], ensure_ascii=False, indent=2)
        if args.output:
            with open(args.output, 'w', encoding='utf-8') as fh:
                fh.write(text + '\n')
        else:
            print(text)
        return
    if args.format == 'csv':
        _write_csv(info, args.by, args.output)
        return
    if args.output:
        raise SystemExit('--output поддерживается для csv/json')
    if args.format == 'md':
        _print_markdown(info)
    else:
        _print_table(info, args.by)


if __name__ == '__main__':
    main()

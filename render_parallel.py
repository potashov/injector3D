#!/usr/bin/env python3
"""Параллельный рендер серии N+1 по нескольким GPU.

Запускает несколько процессов Blender, по одному на GPU. Каждый процесс
рендерит свою часть азимутов (`--shard-index/--shard-count`), после чего
manifest'ы шардов сливаются в один файл серии.

Пример:
    python3 render_parallel.py 4.5 --tilt-count 8 --tilt-angle 10 \
        --gpus 0,1
"""

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import time

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_BLENDER = os.path.join(
    PROJECT_DIR, 'blender-5.2.3-linux-x64', 'blender')


def _detect_gpus():
    try:
        out = subprocess.check_output(
            ['nvidia-smi', '--query-gpu=name', '--format=csv,noheader'],
            text=True, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        return []
    return list(range(len([line for line in out.splitlines() if line.strip()])))


def _blender_path(value):
    if value:
        return value
    if os.path.exists(DEFAULT_BLENDER):
        return DEFAULT_BLENDER
    found = shutil.which('blender')
    if found:
        return found
    raise SystemExit('Blender не найден: укажите --blender')


def _value_after(args, name):
    for i, arg in enumerate(args):
        if arg == name and i + 1 < len(args):
            return args[i + 1]
    return None


def _strip_options(args, names):
    out = []
    i = 0
    while i < len(args):
        if args[i] in names:
            i += 2
            continue
        out.append(args[i])
        i += 1
    return out


def _remove(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def _merge_manifests(step, ts, shards, keep):
    base = os.path.join(PROJECT_DIR, 'output', step)
    frames = []
    tag = None
    seen = set()
    manifest_paths = []
    for shard in range(shards):
        pattern = os.path.join(
            base, f'{step}_manifest_*_s{shard}of{shards}_{ts}.json')
        matches = sorted(glob.glob(pattern))
        if not matches:
            raise SystemExit(f'Не найден manifest шарда {shard}: {pattern}')
        path = matches[-1]
        manifest_paths.append(path)
        with open(path, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
        if tag is None:
            tag = data.get('tag', '')
            suffix = f'_s{shard}of{shards}'
            if tag.endswith(suffix):
                tag = tag[:-len(suffix)]
        for frame in data.get('frames', []):
            final = frame.get('final')
            if final and final in seen:
                continue
            if final:
                seen.add(final)
            frames.append(frame)

    frames.sort(key=lambda frame: (
        frame.get('variant') != 'upright',
        -1 if frame.get('azimuth_deg') is None else frame['azimuth_deg'],
        frame.get('variant', ''),
    ))
    name = (f'{step}_manifest_{tag}_{ts}.json' if tag
            else f'{step}_manifest_{ts}.json')
    path = os.path.join(base, name)
    payload = {
        'step': step,
        'tag': tag,
        'timestamp': ts,
        'shards': shards,
        'frames': frames,
    }
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    if not keep:
        for shard_path in manifest_paths:
            _remove(shard_path)
    print(f'  Объединён manifest: {name} ({len(frames)} кадров)')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('step', nargs='?', default='4.5')
    parser.add_argument('--gpus', default=None,
                        help='индексы GPU через запятую, например 0,1')
    parser.add_argument('--shards', type=int, default=None,
                        help='число шардов; по умолчанию — число GPU')
    parser.add_argument('--blender', default=None)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--keep-shards', action='store_true',
                        help='не удалять manifest шардов после слияния')
    args, forwarded = parser.parse_known_args()

    gpus = ([int(x) for x in args.gpus.split(',')]
            if args.gpus else _detect_gpus())
    if not gpus:
        print('GPU не найдены — один процесс на CPU')
        gpus = [None]
    shards = args.shards or len(gpus)
    if shards < 1:
        raise SystemExit('--shards должен быть >= 1')

    forwarded = _strip_options(
        forwarded, {'--gpu-index', '--shard-index', '--shard-count'})
    ts = _value_after(forwarded, '--timestamp')
    if ts is None:
        ts = time.strftime('%Y%m%d_%H%M%S')
        forwarded += ['--timestamp', ts]

    blender = _blender_path(args.blender)
    script = os.path.join(PROJECT_DIR, 'injector3d', 'create_scene.py')
    commands = []
    for shard in range(shards):
        gpu = gpus[shard % len(gpus)]
        cmd = [
            blender, '--background', '--python', script, '--', args.step,
            *forwarded,
            '--shard-index', str(shard),
            '--shard-count', str(shards),
        ]
        if gpu is not None:
            cmd += ['--gpu-index', str(gpu)]
        commands.append((shard, gpu, cmd))

    for shard, gpu, cmd in commands:
        target = f'GPU {gpu}' if gpu is not None else 'CPU'
        print(f'Шард {shard}/{shards} -> {target}')
        if args.dry_run:
            print('  ' + ' '.join(cmd))

    if args.dry_run:
        return

    procs = []
    for shard, gpu, cmd in commands:
        log_path = os.path.join(
            PROJECT_DIR, 'output', args.step,
            f'{args.step}_shard{shard}_{ts}.log')
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        log = open(log_path, 'w', encoding='utf-8')
        print(f'  лог: {log_path}')
        procs.append((shard, subprocess.Popen(
            cmd, stdout=log, stderr=subprocess.STDOUT, text=True), log))

    failed = []
    for shard, proc, log in procs:
        code = proc.wait()
        log.close()
        if code:
            failed.append((shard, code))
    if failed:
        for shard, code in failed:
            print(f'Шард {shard} завершился с кодом {code}')
        raise SystemExit(1)

    _merge_manifests(args.step, ts, shards, args.keep_shards)
    print(f'Готово: {shards} шардов, timestamp {ts}')


if __name__ == '__main__':
    main()

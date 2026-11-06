#!/usr/bin/env python3
"""Постобработка кадров с дисторсией Lensdist.

По умолчанию фон не заливается: RGBA-кадр сохраняется как есть. Белая
заливка, масштаб по высоте трубки, сдвиг и подписи включаются отдельно.
Запускается системным python3 (нужны PIL и numpy), не внутри Blender.
"""

import argparse
import json
import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont


def _parse_color(text):
    """Цвет ``R,G,B[,A]`` в диапазоне 0..255."""
    parts = [int(x.strip()) for x in text.split(',')]
    if len(parts) not in (3, 4):
        raise argparse.ArgumentTypeError('цвет: R,G,B или R,G,B,A')
    if any(v < 0 or v > 255 for v in parts):
        raise argparse.ArgumentTypeError('компоненты цвета: 0..255')
    if len(parts) == 3:
        parts.append(255)
    return tuple(parts)


def _alpha_bbox(image):
    """Bbox непрозрачных пикселей; fallback — не-белая маска."""
    data = np.asarray(image.convert('RGBA'))
    alpha = data[..., 3]
    mask = alpha > 0
    if not mask.any():
        rgb = data[..., :3]
        mask = rgb.max(axis=2) < 240
    ys = np.where(mask.any(axis=1))[0]
    xs = np.where(mask.any(axis=0))[0]
    if len(xs) == 0 or len(ys) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


_FONT_CACHE = {}


def _load_font(path, size):
    key = (path, int(size))
    font = _FONT_CACHE.get(key)
    if font is None:
        try:
            font = ImageFont.truetype(path, int(size))
        except OSError:
            font = ImageFont.load_default()
        _FONT_CACHE[key] = font
    return font


def _overlay_lines(meta):
    """Строки левого верхнего информационного блока."""
    overlay = meta.get('overlay') or {}
    lines = []
    if overlay.get('volume') and meta.get('volume_mm3') is not None:
        lines.append(f"V = {meta['volume_mm3']:.0f} mm3")
    if overlay.get('azimuth'):
        az = meta.get('azimuth_deg')
        lines.append("phi = -" if az is None else f"phi = {az:.0f} deg")
    if overlay.get('tilt_deg') and meta.get('tilt_deg') is not None:
        lines.append(f"theta = {meta['tilt_deg']:.0f} deg")
    if overlay.get('ellipse_center'):
        c3 = meta.get('ellipse_center_3d_mm')
        c2 = meta.get('ellipse_center_2d_px')
        if c3 is not None:
            lines.append(f"C3D = ({c3[0]:.2f}, {c3[1]:.2f}, {c3[2]:.2f}) mm")
        if c2 is not None:
            lines.append(f"C2D = ({c2[0]:.1f}, {c2[1]:.1f}) px")
    return lines


def _draw_overlay(image, meta):
    overlay = meta.get('overlay') or {}
    if not any(overlay.get(k) for k in
               ('volume', 'azimuth', 'tilt_deg', 'ellipse_center',
                'center_marker')):
        return image

    out = image.convert('RGBA')
    draw = ImageDraw.Draw(out)

    marker = overlay.get('center_marker')
    c2 = meta.get('ellipse_center_2d_px')
    if marker and c2 is not None:
        size = int(overlay.get('marker_size_px', 3))
        half = max(0, size // 2)
        x, y = int(round(c2[0])), int(round(c2[1]))
        color = tuple(overlay.get('marker_color', (255, 0, 0, 255)))
        draw.rectangle([x - half, y - half, x + half, y + half], fill=color)

    lines = _overlay_lines(meta)
    if lines:
        font = _load_font(overlay.get('font_path', ''),
                          int(overlay.get('font_size_px', 18)))
        margin = int(overlay.get('margin_px', 12))
        text_color = tuple(overlay.get('text_color', (0, 0, 0, 255)))
        stroke_color = tuple(overlay.get('stroke_color', (255, 255, 255, 255)))
        stroke_width = int(overlay.get('stroke_width_px', 1))
        line_step = int(overlay.get('font_size_px', 18)) + 6
        for i, line in enumerate(lines):
            draw.text((margin, margin + i * line_step), line,
                      font=font, fill=text_color,
                      stroke_width=stroke_width, stroke_fill=stroke_color)
    return out


def _finalize_center(meta, scale, shift):
    """Пересчитывает raw-центр в координаты итогового кадра.

    Ширина при `--height` не меняется (масштабируется только высота),
    затем весь кадр сдвигается вправо на `shift` и обрезается слева.
    """
    raw = meta.get('ellipse_center_2d_raw_px')
    if not raw or raw[0] is None or raw[1] is None:
        return
    meta['ellipse_center_2d_px'] = (
        float(raw[0]) + shift,
        float(raw[1]) * scale,
    )


def _process(raw_path, final_path, args, meta=None):
    if meta is None:
        meta = {}
        if args.metadata:
            with open(args.metadata, 'r', encoding='utf-8') as fh:
                meta = json.load(fh)

    image = Image.open(raw_path).convert('RGBA')
    bbox = _alpha_bbox(image)
    if bbox is None:
        raise RuntimeError(f'не найден объект в {raw_path}')
    raw_h = bbox[3] - bbox[1] + 1

    fill = args.fill
    fill_color = _parse_color(args.fill_color)
    if fill:
        background = Image.new('RGBA', image.size, fill_color)
        image = Image.alpha_composite(background, image)
        if fill_color[3] == 255:
            image = image.convert('RGB')

    scale = 1.0
    if args.height > 0:
        scale = args.height / raw_h
        new_h = int(round(image.height * scale))
        image = image.resize((image.width, new_h),
                             Image.Resampling.LANCZOS)

    shift = args.shift
    _finalize_center(meta, scale, shift)
    if not fill:
        canvas_color = (0, 0, 0, 0)
        canvas_mode = 'RGBA'
    elif fill_color[3] == 255:
        canvas_color = fill_color[:3]
        canvas_mode = 'RGB'
    else:
        canvas_color = fill_color
        canvas_mode = 'RGBA'

    canvas = Image.new(canvas_mode, (image.width + shift, image.height),
                       canvas_color)
    if canvas_mode == 'RGBA':
        canvas.paste(image, (shift, 0), image)
    else:
        canvas.paste(image, (shift, 0))
    final = canvas.crop((0, 0, image.width, image.height))
    final = _draw_overlay(final, meta)

    os.makedirs(os.path.dirname(os.path.abspath(final_path)), exist_ok=True)
    save_kwargs = {'compress_level': args.compression}
    if final.mode == 'RGBA':
        save_kwargs['optimize'] = False
    final.save(final_path, **save_kwargs)

    if args.verbose:
        result_bbox = _alpha_bbox(final)
        print(f'  raw tube height: {raw_h}px')
        print(f'  final bbox: {result_bbox}, bottom margin='
              f'{final.height - 1 - result_bbox[3]}px')
        data = np.asarray(final.convert('RGBA'))
        for y in (10, 50, 100, 300, 468, 700, 1050, 1100):
            if y >= final.height:
                continue
            row = np.where((data[y, :, 3] > 0))[0]
            if len(row):
                print(f'  y={y:4d}: width={row.max() - row.min() + 1:3d}px, '
                      f'cx={(row.min() + row.max()) / 2:.1f}px')


def _batch_namespace(job, opts):
    """Собирает аргументы одного кадра из batch-JSON."""
    return argparse.Namespace(
        shift=int(job.get('shift', opts.get('shift_x_px', 105))),
        height=int(job.get('height', opts.get('target_height_px', 0))),
        fill=bool(job.get('fill', opts.get('fill_background', False))),
        fill_color=job.get('fill_color', ','.join(
            str(int(c)) for c in opts.get('fill_color',
                                          (255, 255, 255, 255)))),
        metadata=None,
        compression=int(job.get('compression',
                                opts.get('compression', 3))),
        verbose=bool(job.get('verbose', opts.get('diagnostics', False))),
    )


def _process_batch(path):
    """Один процесс на серию: jobs из JSON, опции общие."""
    with open(path, 'r', encoding='utf-8') as fh:
        data = json.load(fh)
    opts = data.get('postprocess') or {}
    jobs = data.get('jobs') or []
    for i, job in enumerate(jobs, 1):
        args = _batch_namespace(job, opts)
        print(f'  [{i}/{len(jobs)}] {os.path.basename(job["final"])}',
              flush=True)
        _process(job['raw'], job['final'], args,
                 meta=job.get('metadata') or {})
    print(f'  Постобработка: {len(jobs)} кадров', flush=True)


def _worker():
    """Долгоживущий процесс: JSON-строки stdin, обработка по мере рендера."""
    count = 0
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        opts = msg.get('postprocess') or {}
        job = msg.get('job') or {}
        args = _batch_namespace(job, opts)
        print(f'  Постобработка: {os.path.basename(job["final"])}',
              flush=True)
        _process(job['raw'], job['final'], args,
                 meta=job.get('metadata') or {})
        count += 1
    print(f'  Постобработка завершена: {count} кадров', flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('raw', nargs='?')
    parser.add_argument('final', nargs='?')
    parser.add_argument('--batch', default=None,
                        help='JSON с jobs: один процесс на серию')
    parser.add_argument('--worker', action='store_true',
                        help='долгоживущий процесс: JSON-строки stdin')
    parser.add_argument('--shift', type=int, default=105)
    parser.add_argument('--height', type=int, default=0,
                        help='высота видимой трубки; 0 — без масштаба')
    parser.add_argument('--fill', action='store_true',
                        help='залить фон (по умолчанию не заливать)')
    parser.add_argument('--fill-color', default='255,255,255,255')
    parser.add_argument('--metadata', default=None,
                        help='JSON с объёмом, азимутом, углом и центром')
    parser.add_argument('--compression', type=int, default=3)
    parser.add_argument('--verbose', action='store_true')
    args = parser.parse_args()
    if args.worker:
        _worker()
        return
    if args.batch:
        _process_batch(args.batch)
        return
    if not args.raw or not args.final:
        parser.error('нужны raw и final или --batch')
    _process(args.raw, args.final, args)


if __name__ == '__main__':
    main()

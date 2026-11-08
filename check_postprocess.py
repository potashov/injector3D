#!/usr/bin/env python3
"""Проверки постобработки без Blender: alpha bbox, центр, fill, overlay."""

import argparse
import json
import os
import tempfile

from PIL import Image, ImageDraw

from postprocess_reference import (
    _alpha_bbox,
    _batch_namespace,
    _finalize_center,
    _process,
)


def _args(**overrides):
    values = {
        'shift': 0,
        'height': 0,
        'fill': False,
        'fill_color': '255,255,255,255',
        'metadata': None,
        'result': None,
        'compression': 3,
        'verbose': False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _make_raw(path):
    image = Image.new('RGBA', (200, 320), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle([60, 20, 140, 300], fill=(120, 130, 90, 255))
    image.save(path)


def _meta():
    return {
        'ellipse_center_2d_raw_px': [100.0, 100.0],
        'ellipse_center_3d_mm': [0.0, 0.0, 48.0],
        'overlay': {
            'ellipse_center': False,
            'center_marker': False,
            'marker_color': [255, 0, 0, 255],
            'marker_size_px': 3,
        },
    }


def main():
    with tempfile.TemporaryDirectory(prefix='injector3d_check_pd_') as tmp:
        raw = os.path.join(tmp, 'raw.png')
        _make_raw(raw)
        image = Image.open(raw)
        assert _alpha_bbox(image) == (60, 20, 140, 300), _alpha_bbox(image)
        print('  OK  alpha bbox: (60, 20, 140, 300)')

        meta = {'ellipse_center_2d_raw_px': [100.0, 80.0]}
        _finalize_center(meta, scale=2.0, shift=105)
        assert meta['ellipse_center_2d_px'] == (205.0, 160.0), meta
        print('  OK  финальный центр: raw -> shift + scale')

        final = os.path.join(tmp, 'rgba.png')
        result_path = os.path.join(tmp, 'result.json')
        meta = _meta()
        _process(raw, final, _args(shift=10, result=result_path), meta)
        out = Image.open(final)
        assert out.mode == 'RGBA', out.mode
        assert out.size == (200, 320), out.size
        assert meta['ellipse_center_2d_px'] == (110.0, 100.0), meta
        with open(result_path, 'r', encoding='utf-8') as fh:
            result = json.load(fh)
        assert result['ellipse_center_2d_px'] == [110.0, 100.0], result
        assert result['shift'] == 10
        assert result['timings_ms']['postprocess'] >= 0.0, result
        print('  OK  RGBA, shift, result JSON с длительностью')

        final_height = 160
        final = os.path.join(tmp, 'rgb.png')
        meta = _meta()
        _process(raw, final, _args(shift=5, height=final_height,
                                   fill=True), meta)
        out = Image.open(final)
        scale = final_height / 281.0
        assert out.mode == 'RGB', out.mode
        assert out.size == (200, int(round(320 * scale))), out.size
        assert (out.getpixel((0, 0)) == (255, 255, 255)), out.getpixel((0, 0))
        assert abs(meta['ellipse_center_2d_px'][1] - 100.0 * scale) < 1e-9
        print('  OK  fill, height/scale, белый фон')

        final = os.path.join(tmp, 'marker.png')
        meta = _meta()
        meta['overlay']['ellipse_center'] = True
        meta['overlay']['center_marker'] = True
        _process(raw, final, _args(shift=10), meta)
        out = Image.open(final).convert('RGBA')
        cx, cy = meta['ellipse_center_2d_px']
        assert out.getpixel((int(round(cx)), int(round(cy)))) == (
            255, 0, 0, 255), out.getpixel((int(round(cx)), int(round(cy))))
        print('  OK  маркер в финальном 2D-центре')

        job = {'final': 'x.png', 'raw': 'x_raw.png', 'result': 'r.json'}
        opts = {'shift_x_px': 7, 'target_height_px': 0,
                'fill_background': True,
                'fill_color': (1, 2, 3, 4), 'compression': 1}
        ns = _batch_namespace(job, opts)
        assert ns.shift == 7 and ns.fill and ns.result == 'r.json'
        assert ns.fill_color == '1,2,3,4', ns.fill_color
        print('  OK  batch namespace')

    print('\nВСЕ ПРОВЕРКИ ПОСТОБРАБОТКИ ПРОЙДЕНЫ')


if __name__ == '__main__':
    main()

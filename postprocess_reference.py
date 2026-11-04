#!/usr/bin/env python3
"""Постобработка кадра-референса 06120.

Убирает чёрные полосы Lensdist, масштабирует кадр по вертикали до
целевой высоты трубки и сдвигает её вправо.  Запускается системным
python3 (нужны PIL, numpy, scipy), а не внутри Blender.
"""

import argparse
import os

import numpy as np
from PIL import Image
from scipy import ndimage


def _border_black(rgb):
    black = rgb.max(axis=2) < 5
    labels, count = ndimage.label(black)
    if count == 0:
        return black
    border = set(labels[0, :]) | set(labels[-1, :])
    border |= set(labels[:, 0]) | set(labels[:, -1])
    border.discard(0)
    return np.isin(labels, list(border))


def _largest_component(mask):
    labels, count = ndimage.label(mask)
    if count == 0:
        return mask
    sizes = np.bincount(labels.ravel())
    sizes[0] = 0
    return labels == int(sizes.argmax())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('raw')
    parser.add_argument('final')
    parser.add_argument('--shift', type=int, default=105)
    parser.add_argument('--height', type=int, default=1124)
    args = parser.parse_args()

    image = Image.open(args.raw).convert('RGBA')
    data = np.asarray(image)
    rgb = data[..., :3].copy()
    alpha = data[..., 3]

    fill = (alpha == 0) | _border_black(rgb)
    rgb[fill] = 255

    solid = Image.fromarray(rgb, 'RGB')
    nonwhite = np.asarray(solid).max(axis=2) < 240
    tube = _largest_component(nonwhite)

    ys = np.where(tube.any(axis=1))[0]
    raw_h = int(ys.max() - ys.min() + 1)
    scale = args.height / raw_h
    new_h = int(round(1200 * scale))
    stretched = solid.resize((1200, new_h), Image.LANCZOS)

    canvas = Image.new('RGB', (1200 + args.shift, 1200), (255, 255, 255))
    canvas.paste(stretched, (args.shift, 0))
    final = canvas.crop((0, 0, 1200, 1200))

    os.makedirs(os.path.dirname(os.path.abspath(args.final)), exist_ok=True)
    final.save(args.final)

    result = np.asarray(final).max(axis=2) < 240
    result = _largest_component(result)
    ys = np.where(result.any(axis=1))[0]
    xs = np.where(result.any(axis=0))[0]
    print(f'  raw tube height: {raw_h}px, scale: {scale:.5f}')
    print(f'  final bbox: x={xs.min()}..{xs.max()} '
          f'({xs.max() - xs.min() + 1}px), '
          f'y={ys.min()}..{ys.max()} '
          f'({ys.max() - ys.min() + 1}px), '
          f'bottom margin={1199 - ys.max()}px')
    for y in (10, 50, 100, 300, 468, 700, 1050, 1100):
        row = np.where(result[y])[0]
        if len(row):
            print(f'  y={y:4d}: width={row.max() - row.min() + 1:3d}px, '
                  f'cx={(row.min() + row.max()) / 2:.1f}px')


if __name__ == '__main__':
    main()

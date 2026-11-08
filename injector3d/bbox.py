"""Bbox объекта по RGBA-кадру и пересчёт в координаты итогового кадра."""

import numpy as np


def alpha_bbox(rgba, alpha_threshold=0):
    """Bbox непрозрачных пикселей; fallback — не-белая маска.

    Координаты включительные и заданы сверху вниз: ``(x0, y0, x1, y1)``.
    Возвращает пару ``(bbox, source)``; ``bbox`` равен ``None``, если
    подходящих пикселей нет.
    """
    data = np.asarray(rgba)
    if data.ndim != 3 or data.shape[2] < 4:
        raise ValueError('ожидается RGBA-массив (h, w, 4)')

    alpha = data[..., 3]
    mask = alpha > float(alpha_threshold)
    source = 'alpha'
    if not mask.any():
        rgb = data[..., :3]
        white = 240.0 / 255.0 if np.issubdtype(
            data.dtype, np.floating) else 240
        mask = rgb.max(axis=2) < white
        source = 'non_white'

    ys = np.where(mask.any(axis=1))[0]
    xs = np.where(mask.any(axis=0))[0]
    if len(xs) == 0 or len(ys) == 0:
        return None, source
    return (int(xs.min()), int(ys.min()),
            int(xs.max()), int(ys.max())), source


def bbox_record(raw_bbox, raw_size, scale=1.0, shift=0,
                final_size=None, alpha_threshold=0, source='alpha'):
    """Поля bbox для metadata: raw, final, центр и размеры в пикселях."""
    if raw_bbox is None:
        return None

    x0, y0, x1, y1 = (int(v) for v in raw_bbox)
    raw_w, raw_h = (int(v) for v in raw_size)
    fin_w, fin_h = (int(v) for v in (final_size or raw_size))

    x0f = int(round(x0 + shift))
    x1f = int(round(x1 + shift))
    y0f = int(round(y0 * scale))
    y1f = int(round(y1 * scale))

    x0f = max(0, min(fin_w - 1, x0f))
    x1f = max(0, min(fin_w - 1, x1f))
    y0f = max(0, min(fin_h - 1, y0f))
    y1f = max(0, min(fin_h - 1, y1f))
    if x1f < x0f or y1f < y0f:
        return None

    raw_touch = (x0 <= 0 or y0 <= 0
                 or x1 >= raw_w - 1 or y1 >= raw_h - 1)
    final_touch = (x0f <= 0 or y0f <= 0
                   or x1f >= fin_w - 1 or y1f >= fin_h - 1)

    return {
        'bbox_raw_px': [x0, y0, x1, y1],
        'bbox_final_px': [x0f, y0f, x1f, y1f],
        'bbox_center_px': [(x0f + x1f) / 2.0, (y0f + y1f) / 2.0],
        'bbox_width_px': x1f - x0f + 1,
        'bbox_height_px': y1f - y0f + 1,
        'bbox_alpha_threshold': int(alpha_threshold),
        'bbox_touches_border': bool(raw_touch or final_touch),
        'bbox_source': source,
    }

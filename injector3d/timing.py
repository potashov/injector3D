"""Сбор и агрегация таймингов без зависимости от Blender.

Тайминги хранятся в миллисекундах. Для длительностей используется
`time.perf_counter()`: он монотонный и не зависит от перевода часов.
"""

import math
import time
from contextlib import contextmanager
from datetime import datetime


def now_ms():
    """Текущее монотонное время в миллисекундах."""
    return time.perf_counter() * 1000.0


def local_now_iso():
    """Локальная дата-время в ISO 8601 с точностью до миллисекунд."""
    return datetime.now().astimezone().isoformat(timespec='milliseconds')


def _round(value, digits=3):
    if value is None:
        return None
    return round(float(value), digits)


def _percentile(values, q):
    """Линейная интерполяция процентиля (q в диапазоне 0..1)."""
    if not values:
        return None
    ordered = sorted(float(v) for v in values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def stats(values):
    """Статистика по списку длительностей, мс.

    Возвращает `None` для пустого списка, чтобы вызывающий код мог не
    добавлять бессмысленный блок в JSON.
    """
    values = [float(v) for v in values if v is not None]
    if not values:
        return None
    total = sum(values)
    count = len(values)
    return {
        'count': count,
        'total_ms': _round(total),
        'mean_ms': _round(total / count),
        'median_ms': _round(_percentile(values, 0.5)),
        'p95_ms': _round(_percentile(values, 0.95)),
        'min_ms': _round(min(values)),
        'max_ms': _round(max(values)),
    }


class Timer:
    """Набор именованных длительностей с статистикой.

    `Timer` подходит и для всей сцены, и для серии кадров: каждый вызов
    `measure()` добавляет одно измерение, а `record()` переносит готовый
    словарь кадра (например, `timings_ms`) в общую статистику.
    """

    def __init__(self, enabled=True, name=None):
        self.enabled = bool(enabled)
        self.name = name
        self.samples = {}
        self.started_at = local_now_iso() if self.enabled else None
        self.finished_at = None
        self._started = now_ms() if self.enabled else None
        self.wall_ms = None

    def add(self, name, value_ms):
        """Добавляет одно измерение этапа."""
        if not self.enabled or value_ms is None:
            return
        self.samples.setdefault(name, []).append(float(value_ms))

    def record(self, values):
        """Добавляет все значения из словаря `{этап: мс}`."""
        if not self.enabled or not values:
            return
        for name, value_ms in values.items():
            self.add(name, value_ms)

    @contextmanager
    def measure(self, name):
        """Контекстный менеджер для измерения этапа."""
        if not self.enabled:
            yield
            return
        start = now_ms()
        try:
            yield
        finally:
            self.add(name, now_ms() - start)

    def finish(self):
        """Фиксирует wall-time и время окончания (идемпотентно)."""
        if self.enabled and self.wall_ms is None:
            self.wall_ms = now_ms() - self._started
            self.finished_at = local_now_iso()
        return self

    def summary(self, finish=False):
        """Словарь для manifest: wall-time и статистика по этапам."""
        if not self.enabled:
            return {}
        if finish:
            self.finish()
        wall_ms = self.wall_ms
        if wall_ms is None:
            wall_ms = now_ms() - self._started
        stages = {}
        for name in sorted(self.samples):
            stage_stats = stats(self.samples[name])
            if stage_stats is not None:
                stages[name] = stage_stats
        payload = {
            'started_at': self.started_at,
            'finished_at': self.finished_at,
            'wall_ms': _round(wall_ms),
            'stages': stages,
        }
        if self.name:
            payload['name'] = self.name
        return payload


@contextmanager
def timed(target, name):
    """Измеряет блок в `target`: `Timer` или обычный dict.

    Для dict повторные измерения одного этапа суммируются — это удобно
    для кадра, где один и тот же этап может встретиться несколько раз.
    `target=None` превращает вызов в no-op.
    """
    if target is None:
        yield
        return
    start = now_ms()
    try:
        yield
    finally:
        value = now_ms() - start
        if isinstance(target, dict):
            target[name] = target.get(name, 0.0) + value
        else:
            target.add(name, value)


def summarize_frames(frames):
    """Агрегирует `timings_ms` из списка кадров manifest."""
    samples = {}
    count = 0
    for frame in frames or []:
        timings = frame.get('timings_ms') or {}
        if not timings:
            continue
        count += 1
        for name, value_ms in timings.items():
            samples.setdefault(name, []).append(value_ms)
    stages = {}
    for name in sorted(samples):
        stage_stats = stats(samples[name])
        if stage_stats is not None:
            stages[name] = stage_stats
    return {'frames_with_timings': count, 'stages': stages}


def _series_stage_totals(record):
    timings = record.get('timings') or {}
    stages = timings.get('stages') or {}
    totals = {}
    for name, stage_stats in stages.items():
        if isinstance(stage_stats, dict):
            total = stage_stats.get('total_ms')
            if total is not None:
                totals[name] = total
    return totals


def aggregate_records(records):
    """Сводка по списку записей batch_manifest."""
    records = list(records or [])
    wall_values = [r.get('wall_ms') for r in records
                   if r.get('wall_ms') is not None]
    stage_samples = {}
    for record in records:
        for name, total_ms in _series_stage_totals(record).items():
            stage_samples.setdefault(name, []).append(total_ms)
    stages = {}
    for name in sorted(stage_samples):
        stage_stats = stats(stage_samples[name])
        if stage_stats is not None:
            stages[name] = stage_stats
    return {
        'series_count': len(records),
        'wall_ms': stats(wall_values),
        'stages': stages,
    }


def group_records(records, key):
    """Группирует записи по значению ключа и агрегирует каждую группу."""
    groups = {}
    for record in records or []:
        value = record.get(key)
        label = 'none' if value is None else str(value)
        groups.setdefault(label, []).append(record)
    return {label: aggregate_records(items)
            for label, items in sorted(groups.items())}

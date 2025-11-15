"""
Utilities for generating latitude/longitude grids over Tokyo.
"""
from __future__ import annotations

import math
from typing import Iterable, List, Sequence, Tuple

from .models import GridPoint

# Bounding box that covers most of Tokyo prefecture.
# (min_lat, min_lng, max_lat, max_lng)
TOKYO_BBOX: Tuple[float, float, float, float] = (35.4, 138.9, 36.2, 140.1)


def meters_to_lat_delta(meters: float) -> float:
    return meters / 111_000.0


def meters_to_lng_delta(meters: float, at_lat: float) -> float:
    return meters / (111_000.0 * math.cos(math.radians(at_lat)))


def generate_grid(
    bbox: Sequence[float] = TOKYO_BBOX,
    spacing_m: int = 800,
    radius_m: int = 800,
) -> List[GridPoint]:
    min_lat, min_lng, max_lat, max_lng = bbox
    lat_step = meters_to_lat_delta(spacing_m)
    mid_lat = (min_lat + max_lat) / 2
    lng_step = meters_to_lng_delta(spacing_m, mid_lat)
    points: List[GridPoint] = []
    lat = min_lat
    row_idx = 0
    while lat <= max_lat:
        lng = min_lng
        row_offset = lng_step / 2 if row_idx % 2 == 1 else 0.0
        while lng <= max_lng:
            points.append(
                GridPoint(lat=round(lat, 6), lng=round(min(lng + row_offset, max_lng), 6), radius_m=radius_m)
            )
            lng += lng_step
        lat += lat_step
        row_idx += 1
    return points


def chunked_grid(points: Sequence[GridPoint], chunk_size: int) -> Iterable[Sequence[GridPoint]]:
    for i in range(0, len(points), chunk_size):
        yield points[i : i + chunk_size]

#!/usr/bin/env python3
"""
meatmap.csv の座標が行政区ポリゴンと一致しているかを検査し、
疑わしいレコードをリストアップする。

方針:
  - 東京都の市区町村ポリゴン（geolonia/japanese-admins の GeoJSON）を
    data/japanese-admins/13/*.json から読み込む
  - 各行の座標をポイントインポリゴン判定し、含まれる行政区を特定
  - 住所に同じ市区町村名が含まれない場合、またはポリゴン外の場合を「疑惑」として抽出

出力:
  docs/output/suspicious_coords.csv
"""

from __future__ import annotations

import csv
import json
import math
import re
import unicodedata
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "docs" / "output" / "meatmap.csv"
POLYGON_DIR = ROOT / "data" / "japanese-admins" / "13"
OUT_PATH = ROOT / "docs" / "output" / "suspicious_coords.csv"


def normalize_text(value: str) -> str:
    if value is None:
        return ""
    s = unicodedata.normalize("NFKC", str(value))
    s = re.sub(r"[\\s\u3000]", "", s)
    return s


def point_in_ring(lon: float, lat: float, ring: List[Tuple[float, float]]) -> bool:
    """
    Ray casting による点と多角形の内外判定。
    """
    inside = False
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        intersect = (y1 > lat) != (y2 > lat) and lon < (x2 - x1) * (lat - y1) / (y2 - y1 + 1e-15) + x1
        if intersect:
            inside = not inside
    return inside


def point_in_geom(lon: float, lat: float, geom: Dict) -> bool:
    """
    Polygon / MultiPolygon の内外判定。穴を考慮して XOR で判定する。
    """
    gtype = geom.get("type")
    if gtype == "Polygon":
        rings = geom.get("coordinates", [])
        inside = False
        for ring in rings:
            inside ^= point_in_ring(lon, lat, ring)
        return inside
    if gtype == "MultiPolygon":
        for poly in geom.get("coordinates", []):
            inside = False
            for ring in poly:
                inside ^= point_in_ring(lon, lat, ring)
            if inside:
                return True
        return False
    return False


def bbox_from_geom(geom: Dict) -> Tuple[float, float, float, float]:
    xs: List[float] = []
    ys: List[float] = []

    def collect(coords):
        for pt in coords:
            if isinstance(pt[0], (float, int)):
                xs.append(float(pt[0]))
                ys.append(float(pt[1]))
            else:
                collect(pt)

    collect(geom.get("coordinates", []))
    return (min(xs), min(ys), max(xs), max(ys))


def load_polygons() -> List[Dict]:
    polygons: List[Dict] = []
    for path in sorted(POLYGON_DIR.glob("*.json")):
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        for feat in data.get("features", []):
            name = str(feat.get("properties", {}).get("name", ""))
            geom = feat.get("geometry", {})
            bbox = bbox_from_geom(geom)
            norm_name = normalize_text(name)
            plain_name = norm_name.replace("東京都", "")
            polygons.append(
                {
                    "name": name,
                    "norm": norm_name,
                    "plain": plain_name,
                    "geom": geom,
                    "bbox": bbox,
                }
            )
    return polygons


def locate_polygon(lon: float, lat: float, polygons: List[Dict]) -> Optional[Dict]:
    for poly in polygons:
        minx, miny, maxx, maxy = poly["bbox"]
        if not (minx <= lon <= maxx and miny <= lat <= maxy):
            continue
        if point_in_geom(lon, lat, poly["geom"]):
            return poly
    return None


def parse_rows(path: Path) -> Tuple[List[str], List[Dict[str, str]]]:
    with path.open(encoding="utf-8") as f:
        rows = [line for line in f if not line.startswith("#")]
    reader = csv.DictReader(rows)
    fieldnames = reader.fieldnames or []
    data = list(reader)
    return fieldnames, data


def main() -> None:
    polygons = load_polygons()
    fieldnames, rows = parse_rows(CSV_PATH)

    suspicious: List[Dict[str, str]] = []
    for idx, r in enumerate(rows):
        name = r.get("name", "")
        address = r.get("address", "")
        lat_str = r.get("lat") or ""
        lng_str = r.get("lng") or ""

        try:
            lat = float(lat_str)
            lng = float(lng_str)
        except Exception:
            lat = lng = math.nan

        reasons: List[str] = []
        detected = None
        if not (math.isfinite(lat) and math.isfinite(lng)):
            reasons.append("invalid_coords")
        else:
            poly = locate_polygon(lng, lat, polygons)
            if poly is None:
                reasons.append("outside_tokyo_polygon")
            else:
                detected = poly["name"]
                addr_norm = normalize_text(address)
                if poly["norm"] not in addr_norm and poly["plain"] not in addr_norm:
                    reasons.append("address_mismatch")

        if reasons:
            suspicious.append(
                {
                    "row_index": idx,
                    "name": name,
                    "address": address,
                    "lat": lat_str,
                    "lng": lng_str,
                    "detected_municipality": detected or "",
                    "reasons": "|".join(reasons),
                }
            )

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "row_index",
                "name",
                "address",
                "lat",
                "lng",
                "detected_municipality",
                "reasons",
            ],
        )
        writer.writeheader()
        writer.writerows(suspicious)

    print(f"suspicious rows: {len(suspicious)}")
    print(f"written: {OUT_PATH}")


if __name__ == "__main__":
    main()

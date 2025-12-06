#!/usr/bin/env python3
"""
meatmap.csv の座標が行政区ポリゴンと一致しているかを検査し、
疑わしいレコードをリストアップする。

方針:
  - 東京都の市区町村ポリゴン（geolonia/japanese-admins の GeoJSON）を
    data/japanese-admins/13/*.json から読み込む
  - 各行の座標をポイントインポリゴン判定し、含まれる行政区を特定
  - 住所に同じ市区町村名が含まれない、ポリゴン外、
    または住所から特定した丁目/街区代表点との距離が閾値超の場合を「疑惑」として抽出

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
CHOME_ZIP = ROOT / "data" / "13000-18.0b.zip"  # 大字・町丁目代表点（2024）
CHOME_CSV_NAME = "13000-18.0b/13_2024.csv"
GAIKU_ZIP = ROOT / "data" / "13000-23.0a.zip"  # 街区代表点（2024）
GAIKU_CSV_NAME = "13000-23.0a/13_2024.csv"
DIST_THRESH_M = 700.0


def normalize_text(value: str) -> str:
    if value is None:
        return ""
    s = unicodedata.normalize("NFKC", str(value))
    s = re.sub(r"[\s\u3000]", "", s)
    return s


KANJI_DIGITS = {
    0: "〇",
    1: "一",
    2: "二",
    3: "三",
    4: "四",
    5: "五",
    6: "六",
    7: "七",
    8: "八",
    9: "九",
}


def int_to_kanji(num: int) -> str:
    if num == 0:
        return KANJI_DIGITS[0]
    parts = []
    if num >= 10:
        tens, ones = divmod(num, 10)
        if tens > 1 and tens in KANJI_DIGITS:
            parts.append(KANJI_DIGITS[tens])
        elif tens == 1:
            pass
        else:
            parts.append(str(tens))
        parts.append("十")
        if ones:
            parts.append(KANJI_DIGITS.get(ones, str(ones)))
    else:
        parts.append(KANJI_DIGITS.get(num, str(num)))
    return "".join(parts)


def normalize_address_for_chome(addr: str) -> str:
    s = unicodedata.normalize("NFKC", str(addr))
    s = re.sub(r"[\s\u3000]", "", s)
    s = re.sub(r"([0-9]+)丁目", lambda m: int_to_kanji(int(m.group(1))) + "丁目", s)
    if "丁目" not in s:
        m = re.search(r"([0-9]+)", s)
        if m:
            num = int(m.group(1))
            tail = s[m.end():]
            if tail.startswith("-"):
                tail = tail[1:]
            s = s[: m.start()] + int_to_kanji(num) + "丁目" + tail
    return s


def parse_address_numbers(addr: str) -> Tuple[Optional[int], Optional[int]]:
    nums = re.findall(r"([0-9]+)", addr)
    chome = int(nums[0]) if len(nums) >= 1 else None
    gaiku = int(nums[1]) if len(nums) >= 2 else None
    return chome, gaiku


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


def load_chome_points() -> List[Dict]:
    import zipfile

    points: List[Dict] = []
    if not CHOME_ZIP.exists():
        return points
    with zipfile.ZipFile(CHOME_ZIP) as zf:
        with zf.open(CHOME_CSV_NAME) as f:
            reader = csv.DictReader((line.decode("shift_jis", errors="ignore") for line in f))
            for row in reader:
                try:
                    lat = float(row.get("緯度", "") or "nan")
                    lng = float(row.get("経度", "") or "nan")
                except Exception:
                    continue
                city_raw = row.get("市区町村名", "")
                chome_raw = row.get("大字町丁目名", "")
                city = normalize_text(city_raw)
                chome = normalize_text(chome_raw)
                if not city or not chome:
                    continue
                points.append(
                    {
                        "city": city,
                        "chome": chome,
                        "lat": lat,
                        "lng": lng,
                        "raw_city": city_raw,
                        "raw_chome": chome_raw,
                    }
                )
    return points


def load_gaiku_points() -> List[Dict]:
    import zipfile

    points: List[Dict] = []
    if not GAIKU_ZIP.exists():
        return points
    with zipfile.ZipFile(GAIKU_ZIP) as zf:
        with zf.open(GAIKU_CSV_NAME) as f:
            reader = csv.DictReader((line.decode("shift_jis", errors="ignore") for line in f))
            for row in reader:
                try:
                    lat = float(row.get("緯度", "") or "nan")
                    lng = float(row.get("経度", "") or "nan")
                except Exception:
                    continue
                city_raw = row.get("市区町村名", "") or row.get("市区町村名・漢字", "")
                chome_raw = row.get("大字_丁目名", "") or row.get("大字・町丁目名", "")
                gaiku_raw = row.get("街区符号_地番", "") or row.get("街区符号・地番", "")
                city = normalize_text(city_raw)
                chome = normalize_text(chome_raw)
                gaiku = gaiku_raw.strip()
                if not city or not chome or not gaiku:
                    continue
                points.append(
                    {
                        "city": city,
                        "chome": chome,
                        "gaiku": gaiku,
                        "lat": lat,
                        "lng": lng,
                        "raw_city": city_raw,
                        "raw_chome": chome_raw,
                        "raw_gaiku": gaiku_raw,
                    }
                )
    return points


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


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
    chome_points = load_chome_points()
    gaiku_points = load_gaiku_points()
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
        target_city = ""
        target_chome = ""
        target_gaiku = ""
        target_lat = ""
        target_lng = ""
        distance_m = ""
        level = ""

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

                # 市区町村が一致する場合は丁目/街区との距離も確認
                target_city = poly["plain"] or poly["norm"]
                chome_num, gaiku_num = parse_address_numbers(address)
                addr_norm_chome = normalize_address_for_chome(address)
                addr_norm_plain = normalize_text(addr_norm_chome)

                # 街区優先
                best_gaiku = None
                if chome_num and gaiku_num:
                    gaiku_str = str(gaiku_num)
                    chome_kanji = int_to_kanji(chome_num) + "丁目"
                    city_gaiku = [p for p in gaiku_points if p["city"].endswith(target_city)]
                    for p in city_gaiku:
                        if chome_kanji in p["chome"] and p["gaiku"] == gaiku_str:
                            best_gaiku = p
                            break

                best_chome = None
                if best_gaiku is None:
                    city_chome = [p for p in chome_points if p["city"].endswith(target_city)]
                    best_len = -1
                    for p in city_chome:
                        if p["chome"] and p["chome"] in addr_norm_plain:
                            l = len(p["chome"])
                            if l > best_len:
                                best_chome = p
                                best_len = l

                if best_gaiku:
                    tgt_lat = best_gaiku["lat"]
                    tgt_lng = best_gaiku["lng"]
                    level = "gaiku"
                    target_chome = best_gaiku["raw_chome"]
                    target_gaiku = best_gaiku["raw_gaiku"]
                elif best_chome:
                    tgt_lat = best_chome["lat"]
                    tgt_lng = best_chome["lng"]
                    level = "chome"
                    target_chome = best_chome["raw_chome"]
                else:
                    tgt_lat = tgt_lng = None

                if tgt_lat is not None and tgt_lng is not None:
                    target_lat = str(tgt_lat)
                    target_lng = str(tgt_lng)
                    dist = haversine(lat, lng, tgt_lat, tgt_lng)
                    distance_m = f"{dist:.1f}"
                    if dist > DIST_THRESH_M:
                        reasons.append(f"far_from_{level}")

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
                    "target_city": target_city,
                    "target_chome": target_chome,
                    "target_gaiku": target_gaiku,
                    "target_lat": target_lat,
                    "target_lng": target_lng,
                    "distance_m": distance_m,
                    "level": level,
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
                "target_city",
                "target_chome",
                "target_gaiku",
                "target_lat",
                "target_lng",
                "distance_m",
                "level",
            ],
        )
        writer.writeheader()
        writer.writerows(suspicious)

    print(f"suspicious rows: {len(suspicious)}")
    print(f"written: {OUT_PATH}")


if __name__ == "__main__":
    main()

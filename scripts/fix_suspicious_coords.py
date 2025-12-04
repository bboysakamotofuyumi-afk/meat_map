#!/usr/bin/env python3
"""
meatmap.csv の座標を行政区ポリゴンで検査し、住所に一致する区に収まるよう補正案を生成する。

やること:
  - geolonia/japanese-admins の東京都ポリゴン (data/japanese-admins/13/*.json) を読み込み
  - 座標がポリゴン外 / 住所と区が不一致の行を検出
  - 住所から推定した区のポリゴン内に収まるように座標を補正（ミッドポイントで寄せていき、だめなら重心を使用）
  - docs/output/suspicious_coords.csv に補正案を付加して書き出す

注意:
  - meatmap.csv は書き換えません
  - ポリゴンは東京都のみ。都外住所はターゲットを特定できず unresolved とする
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
CHOME_ZIP = ROOT / "data" / "13000-18.0b.zip"  # 最新の大字・町丁目代表点（2024）
CHOME_CSV_NAME = "13000-18.0b/13_2024.csv"
GAIKU_ZIP = ROOT / "data" / "13000-23.0a.zip"  # 街区代表点（2024）
GAIKU_CSV_NAME = "13000-23.0a/13_2024.csv"


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
            # 10〜19 の先頭は「十」
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

    def repl(m: re.Match) -> str:
        num = int(m.group(1))
        return int_to_kanji(num) + "丁目"

    s = re.sub(r"([0-9]+)丁目", repl, s)
    # 「丁目」がまだ無い場合、最初に出る数字を丁目に補完（例: 根岸3-25-8 → 根岸三丁目25-8）
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
    """
    住所から丁目と街区の番号を取り出す。最初の数字を丁目、2番目の数字を街区とみなす。
    例: 「根岸3-25-8」 -> (3, 25)
    """
    nums = re.findall(r"([0-9]+)", addr)
    chome = int(nums[0]) if len(nums) >= 1 else None
    gaiku = int(nums[1]) if len(nums) >= 2 else None
    return chome, gaiku


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def point_in_ring(lon: float, lat: float, ring: List[Tuple[float, float]]) -> bool:
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


def centroid_from_geom(geom: Dict) -> Tuple[float, float]:
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
    if not xs or not ys:
        return (0.0, 0.0)
    return (sum(xs) / len(xs), sum(ys) / len(ys))


def load_polygons() -> List[Dict]:
    polygons: List[Dict] = []
    for path in sorted(POLYGON_DIR.glob("*.json")):
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        for feat in data.get("features", []):
            name = str(feat.get("properties", {}).get("name", ""))
            geom = feat.get("geometry", {})
            bbox = bbox_from_geom(geom)
            centroid = centroid_from_geom(geom)
            norm_name = normalize_text(name)
            plain_name = norm_name.replace("東京都", "")
            polygons.append(
                {
                    "name": name,
                    "norm": norm_name,
                    "plain": plain_name,
                    "geom": geom,
                    "bbox": bbox,
                    "centroid": centroid,
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
                        "norm_city": city,
                        "norm_chome": chome,
                        "raw_city": city_raw,
                        "raw_chome": chome_raw,
                    }
                )
    return points


def load_gaiku_points() -> List[Dict]:
    """
    街区代表点（番地レベルの代表点）を読み込む。
    """
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


def target_polygon_from_address(address: str, polygons: List[Dict]) -> Optional[Dict]:
    addr_norm = normalize_text(address)
    candidates = []
    for poly in polygons:
        if poly["norm"] and poly["norm"] in addr_norm:
            candidates.append(poly)
        elif poly["plain"] and poly["plain"] in addr_norm:
            candidates.append(poly)
    if not candidates:
        return None
    # 最長一致を優先
    candidates.sort(key=lambda p: len(p["norm"]), reverse=True)
    return candidates[0]


def snap_to_polygon(lon: float, lat: float, poly: Dict) -> Tuple[float, float, str]:
    # もともと inside ならそのまま
    if point_in_geom(lon, lat, poly["geom"]):
        return lon, lat, "already_inside"
    cx, cy = poly["centroid"]
    cur_lon, cur_lat = lon, lat
    for _ in range(10):
        cur_lon = (cur_lon + cx) / 2
        cur_lat = (cur_lat + cy) / 2
        if point_in_geom(cur_lon, cur_lat, poly["geom"]):
            return cur_lon, cur_lat, "snapped_midpoint"
    # 最後の手段として重心
    return cx, cy, "set_centroid"


def main() -> None:
    polygons = load_polygons()
    chome_points = load_chome_points()
    gaiku_points = load_gaiku_points()
    fieldnames, rows = parse_rows(CSV_PATH)

    out_rows: List[Dict[str, str]] = []

    for idx, r in enumerate(rows):
        name = r.get("name", "")
        address = r.get("address", "")
        lat_str = r.get("lat") or ""
        lng_str = r.get("lng") or ""

        try:
            lat = float(lat_str)
            lng = float(lng_str)
            coords_ok = math.isfinite(lat) and math.isfinite(lng)
        except Exception:
            coords_ok = False
            lat = lng = math.nan

        reasons: List[str] = []
        detected_poly = None
        if coords_ok:
            detected_poly = locate_polygon(lng, lat, polygons)
            if detected_poly is None:
                reasons.append("outside_tokyo_polygon")
            else:
                addr_norm = normalize_text(address)
                if detected_poly["norm"] not in addr_norm and detected_poly["plain"] not in addr_norm:
                    reasons.append("address_mismatch")
        else:
            reasons.append("invalid_coords")

        if not reasons:
            # 問題なしは出力しない
            continue

        target_poly = target_polygon_from_address(address, polygons)
        corrected_lon = ""
        corrected_lat = ""
        status = "unresolved_no_target"
        gt_chome_name = ""
        gt_chome_lat = ""
        gt_chome_lng = ""
        dist_to_gt = ""
        chome_match = ""

        if target_poly:
            chome_num, gaiku_num = parse_address_numbers(address)
            # まず丁目ポイントが住所にマッチするか試す
            addr_norm_chome = normalize_address_for_chome(address)
            addr_norm_plain = normalize_text(addr_norm_chome)
            target_city = normalize_text(target_poly["name"]).replace("東京都", "")
            city_points = [p for p in chome_points if p["norm_city"].endswith(target_city)]
            gaiku_city_points = [p for p in gaiku_points if p["city"].endswith(target_city)]

            best_gaiku = None
            if chome_num and gaiku_num:
                gaiku_str = str(gaiku_num)
                chome_kanji = int_to_kanji(chome_num) + "丁目"
                for p in gaiku_city_points:
                    if chome_kanji in p["chome"] and p["gaiku"] == gaiku_str:
                        best_gaiku = p
                        break

            best = None
            best_len = -1
            for p in city_points:
                if p["norm_chome"] and p["norm_chome"] in addr_norm_plain:
                    l = len(p["norm_chome"])
                    if l > best_len:
                        best = p
                        best_len = l

            if best_gaiku:
                corrected_lon = str(best_gaiku["lng"])
                corrected_lat = str(best_gaiku["lat"])
                status = "fixed_gaiku_point"
                gt_chome_name = f"{best_gaiku['raw_city']}{best_gaiku['raw_chome']}街区:{best_gaiku['raw_gaiku']}"
                gt_chome_lat = str(best_gaiku["lat"])
                gt_chome_lng = str(best_gaiku["lng"])
            elif best:
                corrected_lon = str(best["lng"])
                corrected_lat = str(best["lat"])
                status = "fixed_chome_point"
                gt_chome_name = f"{best['raw_city']}{best['raw_chome']}"
                gt_chome_lat = str(best["lat"])
                gt_chome_lng = str(best["lng"])
            else:
                if coords_ok:
                    lon2, lat2, snap_status = snap_to_polygon(lng, lat, target_poly)
                else:
                    cx, cy = target_poly["centroid"]
                    lon2, lat2, snap_status = cx, cy, "set_centroid"

                corrected_lon = str(lon2)
                corrected_lat = str(lat2)
                if point_in_geom(lon2, lat2, target_poly["geom"]):
                    status = f"fixed_{snap_status}"
                else:
                    status = f"unresolved_after_{snap_status}"

            if gt_chome_lat and gt_chome_lng and corrected_lat and corrected_lon:
                try:
                    dist = haversine(float(corrected_lat), float(corrected_lon), float(gt_chome_lat), float(gt_chome_lng))
                    dist_to_gt = f"{dist:.1f}"
                    chome_match = "ok" if dist <= 200 else "far"
                except Exception:
                    pass

        out_rows.append(
            {
                "row_index": idx,
                "name": name,
                "address": address,
                "lat": lat_str,
                "lng": lng_str,
                "detected_municipality": detected_poly["name"] if detected_poly else "",
                "reasons": "|".join(reasons),
                "target_municipality": target_poly["name"] if target_poly else "",
                "corrected_lat": f"{corrected_lat}" if target_poly else "",
                "corrected_lng": f"{corrected_lon}" if target_poly else "",
                "correction_status": status,
                "gt_chome_name": gt_chome_name,
                "gt_chome_lat": gt_chome_lat,
                "gt_chome_lng": gt_chome_lng,
                "dist_to_gt_m": dist_to_gt,
                "chome_match": chome_match,
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
                "target_municipality",
                "corrected_lat",
                "corrected_lng",
                "correction_status",
                "gt_chome_name",
                "gt_chome_lat",
                "gt_chome_lng",
                "dist_to_gt_m",
                "chome_match",
            ],
        )
        writer.writeheader()
        writer.writerows(out_rows)

    print(f"written: {OUT_PATH} (rows={len(out_rows)})")


if __name__ == "__main__":
    main()

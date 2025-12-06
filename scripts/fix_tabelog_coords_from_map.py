#!/usr/bin/env python3
"""
docs/output/meatmap.csv の tabelog ソース行について、
https_learn/data/raw 配下の *_map.html に含まれる JSON-LD から
緯度経度を取得して上書きするスクリプト。

やること:
  - ../https_learn/data/raw/*_map.html を走査し、
    JSON-LD の Restaurant / geo 情報から
    「@id (店舗URL) -> (lat, lng)」のマップを作成
  - meatmap.csv の sources に tabelog を含む行を対象に、
    url 列と @id が一致する店舗の lat / lng を上書き
  - 古い meatmap.csv は .pre_tabelog_coords.bak としてバックアップ

注意:
  - tabelog 以外のソース行は変更しません
  - URL が一致しない店舗はスキップします（警告を標準出力に出します）
"""

from __future__ import annotations

import csv
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple


ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "docs" / "output" / "meatmap.csv"
RAW_DIR = ROOT.parent / "https_learn" / "data" / "raw"


def normalize_url_key(url: str) -> str:
    """末尾スラッシュを揃えて、比較用のキーを作る。"""
    u = (url or "").strip()
    if not u:
        return ""
    # クエリやフラグメントは今回使わないので落とす
    u = u.split("#", 1)[0].split("?", 1)[0]
    return u.rstrip("/")


def build_tabelog_coord_map(raw_dir: Path) -> Dict[str, Tuple[float, float]]:
    """
    *_map.html から @id(URL) -> (lat, lng) のマップを作成。
    """
    if not raw_dir.exists():
        raise SystemExit(f"raw dir not found: {raw_dir}")

    pattern = re.compile(
        r'\{"@context":"https?://schema\.org","@type":"Restaurant","@id":"([^"]+)"'
        r'.*?"geo":\{"@type":"GeoCoordinates","latitude":([0-9.+\-eE]+),'
        r'"longitude":([0-9.+\-eE]+)\}',
        re.DOTALL,
    )

    mapping: Dict[str, Tuple[float, float]] = {}
    files = sorted(raw_dir.glob("*_map.html"))
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:  # noqa: BLE001
            continue
        m = pattern.search(text)
        if not m:
            continue
        url, lat_s, lng_s = m.group(1), m.group(2), m.group(3)
        try:
            lat = float(lat_s)
            lng = float(lng_s)
        except ValueError:
            continue
        if not (math.isfinite(lat) and math.isfinite(lng)):
            continue
        key = normalize_url_key(url)
        if not key:
            continue
        # 同じ URL が複数ファイルにあっても最初のものを優先
        if key not in mapping:
            mapping[key] = (lat, lng)
    return mapping


def load_meatmap(path: Path) -> Tuple[List[str], List[dict]]:
    if not path.exists():
        raise SystemExit(f"CSV not found: {path}")
    with path.open(encoding="utf-8") as f:
        data_lines = [line for line in f if not line.startswith("#")]
    reader = csv.DictReader(data_lines)
    if not reader.fieldnames:
        raise SystemExit("no header in meatmap.csv")
    rows = list(reader)
    return list(reader.fieldnames), rows


def main() -> int:
    coord_map = build_tabelog_coord_map(RAW_DIR)
    print(f"loaded tabelog coords from map.html: {len(coord_map)} entries")

    fieldnames, rows = load_meatmap(CSV_PATH)

    updated = 0
    skipped_no_url = 0
    skipped_not_found = 0

    for row in rows:
        sources = (row.get("sources") or "").lower()
        if "tabelog" not in sources:
            continue
        url = (row.get("url") or "").strip()
        if not url:
            skipped_no_url += 1
            continue
        key = normalize_url_key(url)
        if not key or key not in coord_map:
            skipped_not_found += 1
            continue
        lat, lng = coord_map[key]
        old_lat = row.get("lat")
        old_lng = row.get("lng")
        new_lat = f"{lat:.10f}"
        new_lng = f"{lng:.10f}"
        if old_lat == new_lat and old_lng == new_lng:
            continue
        row["lat"] = new_lat
        row["lng"] = new_lng
        updated += 1

    print(f"rows: {len(rows)}, updated_coords: {updated}, no_url: {skipped_no_url}, not_found_in_map: {skipped_not_found}")

    backup = CSV_PATH.with_suffix(".csv.pre_tabelog_coords.bak")
    if not backup.exists():
        CSV_PATH.replace(backup)
        print(f"backup created: {backup}")
        current_path = backup
    else:
        print(f"backup already exists: {backup}")
        current_path = backup

    # バックアップから再読み込みしてコメント行を維持しつつ上書きする
    with current_path.open(encoding="utf-8") as f:
        lines = f.readlines()
    comment_lines = [line for line in lines if line.startswith("#")]

    with CSV_PATH.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        ts = datetime.now(timezone.utc).isoformat()
        # コメント行を上書き（generated_at_utc / total_records）
        f.write(f"# generated_at_utc={ts}\n")
        f.write(f"# total_records={len(rows)}\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    print(f"written updated CSV: {CSV_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


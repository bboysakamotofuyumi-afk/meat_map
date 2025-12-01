#!/usr/bin/env python3
"""
docs/output/meatmap.csv の欠損している緯度経度を
Nominatim で補完するバッチスクリプト。

- 対象: lat / lng が空 or 数値でない行
- キー: 住所を NFKC 正規化 + 空白除去 + 小文字化したもの
- 同一住所は 1 回だけジオコーディングし、結果を再利用
- キャッシュ: docs/output/geocode_cache.json に保存

注意:
- Nominatim の利用規約に従い、過剰なリクエストを避けること
- デフォルトでは --max-requests 300 件まで / 実行
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import time
import unicodedata
from pathlib import Path
from typing import Dict, List, Tuple, Optional

import requests


ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "docs" / "output" / "meatmap.csv"
CACHE_PATH = ROOT / "docs" / "output" / "geocode_cache.json"


def normalize_address(address: str) -> str:
    """住所文字列をキャッシュキー用に正規化する（キャッシュキー用）。"""
    if address is None:
        return ""
    s = unicodedata.normalize("NFKC", str(address))
    # 半角/全角スペースを除去
    s = "".join(ch for ch in s if ch not in {" ", "\u3000"})
    return s.lower().strip()


def simplify_address_for_geocode(address: str) -> str:
    """
    ジオコーディング用に住所を「丁目・番地」レベルまでに丸める。

    例:
      - 東京都 港区 六本木 6-10-1 六本木ヒルズ ウェストウォーク 5F
        -> 東京都 港区 六本木 6-10-1
    """
    import re

    if not address:
        return ""
    s = unicodedata.normalize("NFKC", str(address))
    # 空白を統一
    s = re.sub(r"[\\s\u3000]+", " ", s).strip()
    tokens = s.split(" ")
    if not tokens:
        return s
    # 「数字とハイフンのみ」で構成される最後のトークンを番地として扱う
    last_idx = -1
    for i, t in enumerate(tokens):
        if re.fullmatch(r"[0-9]+(?:-[0-9]+)*", t):
            last_idx = i
    if last_idx >= 0:
        return " ".join(tokens[: last_idx + 1])
    # 番地らしきトークンがなければ元の住所をそのまま返す
    return s


def load_csv(path: Path) -> Tuple[List[str], List[Dict[str, str]]]:
    """コメント行(#...)を除いた CSV を読み込む。"""
    if not path.exists():
        raise SystemExit(f"CSV not found: {path}")
    with path.open(encoding="utf-8", newline="") as f:
        lines = [line for line in f if not line.startswith("#")]
    if not lines:
        raise SystemExit("CSV has no data rows")
    reader = csv.DictReader(lines)
    rows: List[Dict[str, str]] = list(reader)
    if not reader.fieldnames:
        raise SystemExit("CSV has no header")
    return list(reader.fieldnames), rows


def load_cache(path: Path) -> Dict[str, Dict[str, float]]:
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] failed to load cache: {exc}", file=sys.stderr)
    return {}


def save_cache(path: Path, cache: Dict[str, Dict[str, float]]) -> None:
    try:
        with path.open("w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] failed to save cache: {exc}", file=sys.stderr)


def build_session() -> requests.Session:
    session = requests.Session()
    email = os.getenv("NOMINATIM_EMAIL", "").strip()
    ua = "meat_map_geocoder/1.0"
    if email:
        ua += f" ({email})"
    session.headers.update(
        {
            "User-Agent": ua,
            "Accept-Language": "ja",
        }
    )
    return session


def parse_float(value: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan


def collect_missing_addresses(
    rows: List[Dict[str, str]],
) -> Dict[str, Dict[str, object]]:
    """
    緯度経度が欠損している行から、住所ごとにインデックスをまとめる。

    戻り値: {normalized_address: {\"address\": str, \"indices\": [int, ...]}}
    """
    result: Dict[str, Dict[str, object]] = {}
    for idx, row in enumerate(rows):
        lat = parse_float(row.get("lat"))
        lng = parse_float(row.get("lng"))
        if math.isfinite(lat) and math.isfinite(lng):
            continue
        raw_address = (row.get("address") or "").strip()
        if not raw_address:
            continue
        query_address = simplify_address_for_geocode(raw_address)
        if not query_address:
            continue
        key = normalize_address(query_address)
        if not key:
            continue
        entry = result.setdefault(
            key,
            {"address": raw_address, "query_address": query_address, "indices": []},
        )
        indices: List[int] = entry["indices"]  # type: ignore[assignment]
        indices.append(idx)
    return result


def geocode_address(session: requests.Session, address: str) -> Optional[Tuple[float, float]]:
    url = "https://nominatim.openstreetmap.org/search"
    params = {
        "format": "json",
        "limit": 1,
        "countrycodes": "jp",
        "q": address,
    }
    try:
        res = session.get(url, params=params, timeout=20)
        if not res.ok:
            print(f"[warn] HTTP {res.status_code} for address: {address}", file=sys.stderr)
            return None
        data = res.json()
        if not data:
            return None
        hit = data[0]
        lat = parse_float(hit.get("lat"))
        lon = parse_float(hit.get("lon"))
        if not (math.isfinite(lat) and math.isfinite(lon)):
            return None
        return lat, lon
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] geocode error for '{address}': {exc}", file=sys.stderr)
        return None


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Fill missing lat/lng in meatmap.csv via Nominatim")
    parser.add_argument(
        "--max-requests",
        type=int,
        default=300,
        help="この実行で新規に投げる Nominatim リクエストの最大数 (default: 300)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.1,
        help="Nominatim リクエスト間のスリープ秒数 (default: 1.1)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="ファイルを書き換えずに、更新対象件数などだけ表示する",
    )
    args = parser.parse_args(argv)

    fieldnames, rows = load_csv(CSV_PATH)
    cache = load_cache(CACHE_PATH)

    addr_map = collect_missing_addresses(rows)
    total_missing_rows = sum(len(entry["indices"]) for entry in addr_map.values())
    print(f"total rows: {len(rows)}")
    print(f"rows with missing lat/lng: {total_missing_rows}")
    print(f"unique addresses to consider: {len(addr_map)}")

    session = build_session()
    new_requests = 0

    # まずキャッシュにあるものだけで緯度経度を埋める
    updated_from_cache = 0
    for key, entry in addr_map.items():
        cached = cache.get(key)
        if not cached:
            continue
        lat = parse_float(cached.get("lat"))
        lng = parse_float(cached.get("lng"))
        if not (math.isfinite(lat) and math.isfinite(lng)):
            continue
        for idx in entry["indices"]:  # type: ignore[index]
            row = rows[idx]
            if not (math.isfinite(parse_float(row.get("lat"))) and math.isfinite(parse_float(row.get("lng")))):
                row["lat"] = str(lat)
                row["lng"] = str(lng)
                updated_from_cache += 1

    print(f"updated rows from cache: {updated_from_cache}")

    # キャッシュにない住所だけジオコーディング
    updated_from_api = 0
    for key, entry in addr_map.items():
        if key in cache:
            continue
        if new_requests >= args.max_requests:
            break
        display_address = entry["address"]  # type: ignore[assignment]
        query_address = entry.get("query_address") or display_address  # type: ignore[assignment]
        print(f"[{new_requests+1}/{args.max_requests}] geocoding: {display_address} -> {query_address}")
        coords = geocode_address(session, str(query_address))
        new_requests += 1
        if coords is None:
            # 解決できなかった住所はキャッシュに「失敗」として記録し、
            # 次回以降の実行で同じ住所を再試行しないようにする。
            cache[key] = {"lat": None, "lng": None}
            time.sleep(args.delay)
            continue
        lat, lng = coords
        cache[key] = {"lat": lat, "lng": lng}
        for idx in entry["indices"]:  # type: ignore[index]
            row = rows[idx]
            if not (math.isfinite(parse_float(row.get("lat"))) and math.isfinite(parse_float(row.get("lng")))):
                row["lat"] = str(lat)
                row["lng"] = str(lng)
                updated_from_api += 1
        time.sleep(args.delay)

    print(f"new API requests sent: {new_requests}")
    print(f"updated rows from API: {updated_from_api}")

    if args.dry_run:
        print("dry-run: not writing CSV / cache")
        return 0

    save_cache(CACHE_PATH, cache)

    backup_path = CSV_PATH.with_suffix(".csv.pre_geocode.bak")
    if not backup_path.exists():
        CSV_PATH.replace(backup_path)
        print(f"backup created: {backup_path}")
    else:
        print(f"backup already exists: {backup_path}")

    from datetime import datetime, timezone

    with CSV_PATH.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        ts = datetime.now(timezone.utc).isoformat()
        f.write(f"# generated_at_utc={ts}\n")
        f.write(f"# total_records={len(rows)}\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    print(f"written updated CSV: {CSV_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

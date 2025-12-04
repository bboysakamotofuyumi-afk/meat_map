#!/usr/bin/env python3
"""
docs/output/meatmap.csv の欠損している緯度経度を
GSI 住所検索 API で補完するバッチスクリプト。

- 対象: lat / lng が空 or 数値でない行
- 住所を「丁目・番地」まで丸めて検索し、ヒットしない場合は「店名 + 住所」でも検索
- 同一クエリはキャッシュを使い回す (docs/output/geocode_cache_gsi.json)
- 実行ごとに API リクエスト上限を指定できる (--max-requests)
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests


ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "docs" / "output" / "meatmap.csv"
CACHE_PATH = ROOT / "docs" / "output" / "geocode_cache_gsi.json"

PREFS = [
    "北海道",
    "青森県",
    "岩手県",
    "宮城県",
    "秋田県",
    "山形県",
    "福島県",
    "茨城県",
    "栃木県",
    "群馬県",
    "埼玉県",
    "千葉県",
    "東京都",
    "神奈川県",
    "新潟県",
    "富山県",
    "石川県",
    "福井県",
    "山梨県",
    "長野県",
    "岐阜県",
    "静岡県",
    "愛知県",
    "三重県",
    "滋賀県",
    "京都府",
    "大阪府",
    "兵庫県",
    "奈良県",
    "和歌山県",
    "鳥取県",
    "島根県",
    "岡山県",
    "広島県",
    "山口県",
    "徳島県",
    "香川県",
    "愛媛県",
    "高知県",
    "福岡県",
    "佐賀県",
    "長崎県",
    "熊本県",
    "大分県",
    "宮崎県",
    "鹿児島県",
    "沖縄県",
]


def normalize_text(value: str) -> str:
    """NFKC + 空白除去 + 小文字化（キャッシュキー用）。"""
    if value is None:
        return ""
    s = unicodedata.normalize("NFKC", str(value))
    s = "".join(ch for ch in s if ch not in {" ", "\u3000"})
    return s.lower().strip()


def simplify_address_for_geocode(address: str) -> str:
    """
    ジオコーディング用に住所を「丁目・番地」レベルまでに丸める。

    例:
      - 東京都 港区 六本木 6-10-1 六本木ヒルズ ウェストウォーク 5F
        -> 東京都 港区 六本木 6-10-1
    """
    if not address:
        return ""
    s = unicodedata.normalize("NFKC", str(address))
    s = re.sub(r"[\\s\u3000]+", " ", s).strip()
    tokens = s.split(" ")
    last_idx = -1
    for i, t in enumerate(tokens):
        if re.fullmatch(r"[0-9]+(?:-[0-9]+)*", t):
            last_idx = i
    if last_idx >= 0:
        return " ".join(tokens[: last_idx + 1])
    return s


def extract_prefecture(address: str) -> Optional[str]:
    for pref in PREFS:
        if pref in address:
            return pref
    return None


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
    session.headers.update(
        {
            "User-Agent": "meat_map_geocoder_gsi/1.0",
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
    include_existing: bool = False,
    sources_filter: Optional[str] = None,
) -> Dict[str, Dict[str, object]]:
    """
    対象となる行（欠損、もしくは include_existing=True のとき既存値を含む）を
    住所ごとにインデックスでまとめる。

    戻り値: {normalized_address: {"address": str, "query_address": str, "names": set, "indices": [int, ...]}}
    """
    result: Dict[str, Dict[str, object]] = {}
    for idx, row in enumerate(rows):
        lat = parse_float(row.get("lat"))
        lng = parse_float(row.get("lng"))
        has_latlng = math.isfinite(lat) and math.isfinite(lng)
        if has_latlng and not include_existing:
            continue
        if sources_filter:
            src = (row.get("sources") or "").lower()
            if sources_filter.lower() not in src:
                continue
        raw_address = (row.get("address") or "").strip()
        name = (row.get("name") or "").strip()
        if not raw_address:
            continue
        query_address = simplify_address_for_geocode(raw_address)
        if not query_address:
            continue
        key = normalize_text(query_address)
        if not key:
            continue
        entry = result.setdefault(
            key,
            {"address": raw_address, "query_address": query_address, "names": set(), "indices": []},
        )
        indices: List[int] = entry["indices"]  # type: ignore[assignment]
        indices.append(idx)
        names: set[str] = entry["names"]  # type: ignore[assignment]
        if name:
            names.add(name)
    return result


def build_queries(name: str, address: str) -> List[str]:
    trimmed = simplify_address_for_geocode(address)
    queries: List[str] = []
    if trimmed:
        queries.append(trimmed)
    if name and trimmed:
        queries.append(f"{name} {trimmed}")
    if address:
        queries.append(address)
    if name:
        queries.append(name)
    seen = set()
    unique: List[str] = []
    for q in queries:
        k = normalize_text(q)
        if not k or k in seen:
            continue
        seen.add(k)
        unique.append(q)
    return unique


def geocode_gsi(
    session: requests.Session,
    query: str,
    prefect_hint: Optional[str],
    cache: Dict[str, Dict[str, float]],
    state: Dict[str, int],
    max_requests: int,
    delay: float,
) -> Optional[Tuple[float, float]]:
    key = normalize_text(query)
    if key in cache:
        hit = cache[key]
        lat = parse_float(hit.get("lat"))
        lng = parse_float(hit.get("lng"))
        if math.isfinite(lat) and math.isfinite(lng):
            return lat, lng

    if state["new_requests"] >= max_requests:
        return None

    url = "https://msearch.gsi.go.jp/address-search/AddressSearch"
    try:
        res = session.get(url, params={"q": query}, timeout=15)
        state["new_requests"] += 1
        if delay > 0:
            time.sleep(delay)
        if not res.ok:
            print(f"[warn] HTTP {res.status_code} for query: {query}", file=sys.stderr)
            return None
        data = res.json()
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] request failed for '{query}': {exc}", file=sys.stderr)
        return None

    candidates: List[Tuple[float, float, str]] = []
    for item in data:
        coords = item.get("geometry", {}).get("coordinates") or []
        if len(coords) < 2:
            continue
        lng, lat = coords[0], coords[1]
        title = item.get("properties", {}).get("title", "")
        candidates.append((lat, lng, title))
    if not candidates:
        return None

    def pick_candidate() -> Tuple[float, float]:
        if prefect_hint:
            for lat, lng, title in candidates:
                if prefect_hint in title:
                    return lat, lng
        lat, lng, _ = candidates[0]
        return lat, lng

    lat, lng = pick_candidate()
    cache[key] = {"lat": lat, "lng": lng}
    return lat, lng


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Fill missing lat/lng in meatmap.csv via GSI AddressSearch")
    parser.add_argument(
        "--max-requests",
        type=int,
        default=250,
        help="この実行で新規に投げる GSI 住所検索リクエストの最大数 (default: 250)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.2,
        help="API リクエスト間のスリープ秒数 (default: 0.2)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="ファイルを書き換えずに、更新対象件数などだけ表示する",
    )
    parser.add_argument(
        "--force-all",
        action="store_true",
        help="lat/lng が既に入っている行も再ジオコーディング対象に含める",
    )
    parser.add_argument(
        "--sources-filter",
        type=str,
        default=None,
        help="sources にこの文字列を含む行だけを対象にする（例: tabelog）",
    )
    args = parser.parse_args(argv)

    fieldnames, rows = load_csv(CSV_PATH)
    cache = load_cache(CACHE_PATH)

    addr_map = collect_missing_addresses(
        rows,
        include_existing=args.force_all,
        sources_filter=args.sources_filter,
    )
    total_targets = sum(len(entry["indices"]) for entry in addr_map.values())
    print(f"total rows: {len(rows)}")
    print(f"target rows: {total_targets} (unique addresses: {len(addr_map)})")
    if args.sources_filter:
        print(f"sources filter: {args.sources_filter}")
    print(f"include existing lat/lng: {args.force_all}")

    session = build_session()
    state = {"new_requests": 0}

    updated_from_cache = 0
    updated_from_api = 0
    unchanged_same_value = 0
    failed = 0

    for key, entry in addr_map.items():
        names: set[str] = entry["names"]  # type: ignore[assignment]
        name = next(iter(names)) if names else ""
        address = entry.get("query_address") or entry.get("address") or ""  # type: ignore[assignment]
        prefect_hint = extract_prefecture(str(entry.get("address") or ""))
        queries = build_queries(name, str(address))

        latlng: Optional[Tuple[float, float]] = None
        hit_from_cache = False

        for q in queries:
            before = state["new_requests"]
            latlng = geocode_gsi(
                session=session,
                query=q,
                prefect_hint=prefect_hint,
                cache=cache,
                state=state,
                max_requests=args.max_requests,
                delay=args.delay,
            )
            if latlng:
                hit_from_cache = state["new_requests"] == before
                break

        if not latlng:
            failed += len(entry["indices"])  # type: ignore[arg-type]
            print(f"[skip] no result for: {name} / {address}")
            continue

        lat, lng = latlng
        for idx in entry["indices"]:  # type: ignore[index]
            row = rows[idx]
            old_lat = parse_float(row.get("lat"))
            old_lng = parse_float(row.get("lng"))
            had_latlng = math.isfinite(old_lat) and math.isfinite(old_lng)
            # 更新判定
            if had_latlng and abs(old_lat - lat) < 1e-6 and abs(old_lng - lng) < 1e-6:
                unchanged_same_value += 1
                continue
            row["lat"] = f"{lat:.7f}"
            row["lng"] = f"{lng:.7f}"
            if hit_from_cache:
                updated_from_cache += 1
            else:
                updated_from_api += 1
        source = "cache" if hit_from_cache else "api"
        print(f"[ok/{source}] {name} -> {lat:.7f}, {lng:.7f}")

    print(f"new API requests sent: {state['new_requests']}")
    print(f"updated rows from cache: {updated_from_cache}")
    print(f"updated rows from API: {updated_from_api}")
    print(f"unchanged (same value): {unchanged_same_value}")
    print(f"failed (no result): {failed}")

    if args.dry_run:
        print("dry-run: not writing CSV / cache")
        return 0

    save_cache(CACHE_PATH, cache)

    backup_path = CSV_PATH.with_suffix(".csv.pre_geocode_gsi.bak")
    if not backup_path.exists():
        CSV_PATH.replace(backup_path)
        print(f"backup created: {backup_path}")
    else:
        print(f"backup already exists: {backup_path}")

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

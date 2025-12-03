#!/usr/bin/env python3
"""
docs/output/meatmap.csv のうち「名古屋より西側（lng < 136.9）」に
位置してしまっている行を、住所ベースで再ジオコーディングする補正スクリプト。

- 住所を「丁目・番地」までに丸めてから検索することで京都方面への誤ヒットを避ける
- GSI の住所検索 API (https://msearch.gsi.go.jp/address-search/AddressSearch) を利用
- 店名 + 住所でもう一度検索する fallback 付き
- キャッシュ: docs/output/geocode_cache_gsi.json
- 対象: lng が数値かつ threshold（デフォルト 136.9）より小さい行
- 実行時に docs/output/meatmap_outliers_west_of_nagoya.csv を再生成
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import requests


ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "docs" / "output" / "meatmap.csv"
OUTLIER_LIST_PATH = ROOT / "docs" / "output" / "meatmap_outliers_west_of_nagoya.csv"
CACHE_PATH = ROOT / "docs" / "output" / "geocode_cache_gsi.json"
DEFAULT_THRESHOLD_LNG = 136.9  # 名古屋近辺

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


def simplify_address(address: str) -> str:
    """
    「丁目・番地」までに丸める。
    例: 東京都 港区 六本木 6-10-1 六本木ヒルズ -> 東京都 港区 六本木 6-10-1
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
    if not path.exists():
        raise SystemExit(f"CSV not found: {path}")
    with path.open(encoding="utf-8") as f:
        data_lines = [line for line in f if not line.startswith("#")]
    reader = csv.DictReader(data_lines)
    if not reader.fieldnames:
        raise SystemExit("CSV has no header")
    rows = list(reader)
    return list(reader.fieldnames), rows


def save_csv(path: Path, fieldnames: List[str], rows: List[Dict[str, str]]) -> None:
    bak = path.with_suffix(".csv.pre_fix_west_gsi.bak")
    if not bak.exists():
        path.replace(bak)
        print(f"backup created: {bak}")
    else:
        print(f"backup already exists: {bak}")

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        ts = datetime.now(timezone.utc).isoformat()
        f.write(f"# generated_at_utc={ts}\n")
        f.write(f"# total_records={len(rows)}\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    print(f"written updated CSV: {path}")


def save_outlier_list(
    path: Path,
    fieldnames: List[str],
    rows: Iterable[Dict[str, str]],
    threshold_lng: float,
) -> int:
    rows = list(rows)
    with path.open("w", encoding="utf-8", newline="") as f:
        ts = datetime.now(timezone.utc).isoformat()
        f.write(f"# extracted_at_utc={ts}\n")
        f.write(f"# condition=lng<{threshold_lng} (west of Nagoya)\n")
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"outlier list written: {path} ({len(rows)} rows)")
    return len(rows)


def load_cache(path: Path) -> Dict[str, Dict[str, float]]:
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] failed to load cache: {exc}")
    return {}


def save_cache(path: Path, cache: Dict[str, Dict[str, float]]) -> None:
    try:
        with path.open("w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] failed to save cache: {exc}")


def parse_float(value: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan


def make_key(name: str, address: str) -> str:
    return normalize_text(name) + "||" + normalize_text(address)


def detect_targets(
    rows: List[Dict[str, str]],
    threshold_lng: float,
    target_keys: Optional[set[str]] = None,
) -> List[int]:
    indices: List[int] = []
    for idx, row in enumerate(rows):
        if target_keys is not None:
            key = make_key(row.get("name", ""), row.get("address", ""))
            if key not in target_keys:
                continue
        lng = parse_float(row.get("lng"))
        if math.isfinite(lng) and lng < threshold_lng:
            indices.append(idx)
    return indices


def load_target_keys_from_csv(path: Path) -> set[str]:
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(line for line in f if not line.startswith("#"))
        return {make_key(row.get("name", ""), row.get("address", "")) for row in reader}


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
        return hit.get("lat"), hit.get("lng")

    if state["new_requests"] >= max_requests:
        return None

    url = "https://msearch.gsi.go.jp/address-search/AddressSearch"
    try:
        res = session.get(url, params={"q": query}, timeout=15)
        state["new_requests"] += 1
        if delay > 0:
            time.sleep(delay)
        if not res.ok:
            print(f"[warn] HTTP {res.status_code} for query: {query}")
            return None
        data = res.json()
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] request failed for '{query}': {exc}")
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


def build_queries(name: str, address: str) -> List[str]:
    trimmed = simplify_address(address)
    queries: List[str] = []
    if trimmed:
        queries.append(trimmed)
    if name and trimmed:
        queries.append(f"{name} {trimmed}")
    if address:
        queries.append(address)
    if name:
        queries.append(name)
    # 重複除去（順序保持）
    seen = set()
    unique: List[str] = []
    for q in queries:
        k = normalize_text(q)
        if not k or k in seen:
            continue
        seen.add(k)
        unique.append(q)
    return unique


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Re-geocode rows west of Nagoya using GSI AddressSearch")
    parser.add_argument("--csv", type=Path, default=CSV_PATH, help="入力となる meatmap.csv パス")
    parser.add_argument(
        "--out-csv",
        type=Path,
        default=CSV_PATH,
        help="更新後 CSV の出力先 (default: 上書き)",
    )
    parser.add_argument(
        "--targets-csv",
        type=Path,
        default=None,
        help="対象行のリスト (name,address を持つ CSV)。指定しない場合は lng<threshold の行を自動抽出",
    )
    parser.add_argument(
        "--threshold-lng",
        type=float,
        default=DEFAULT_THRESHOLD_LNG,
        help="これより西の行を再ジオコーディング対象とする",
    )
    parser.add_argument(
        "--max-requests",
        type=int,
        default=250,
        help="API への新規リクエスト数の上限 (キャッシュヒットは含まない)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.1,
        help="API リクエスト間のウェイト秒数",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="書き込みを行わず、ヒット件数のみ確認する",
    )
    parser.add_argument(
        "--outlier-list",
        type=Path,
        default=OUTLIER_LIST_PATH,
        help="再生成する外れ値リストの出力先",
    )
    args = parser.parse_args(argv)

    fieldnames, rows = load_csv(args.csv)
    target_keys = load_target_keys_from_csv(args.targets_csv) if args.targets_csv else None
    target_indices = detect_targets(rows, args.threshold_lng, target_keys)

    print(f"target rows: {len(target_indices)} (threshold lng<{args.threshold_lng})")
    if not target_indices:
        return 0

    cache = load_cache(CACHE_PATH)
    session = requests.Session()
    state = {"new_requests": 0}

    updated = 0
    for idx in target_indices:
        row = rows[idx]
        name = (row.get("name") or "").strip()
        address = (row.get("address") or "").strip()
        prefect_hint = extract_prefecture(address)
        queries = build_queries(name, address)

        latlng: Optional[Tuple[float, float]] = None
        for q in queries:
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
                break

        if not latlng:
            print(f"[skip] no result for: {name} / {address}")
            continue

        lat, lng = latlng
        row["lat"] = f"{lat:.7f}"
        row["lng"] = f"{lng:.7f}"
        updated += 1
        print(f"[ok] {name} -> {lat:.7f}, {lng:.7f}")

    print(f"updated rows: {updated}")
    print(f"new API requests: {state['new_requests']}")

    # 外れ値リストを再生成（書き込み前後のデータ差分を反映するため row の現在値を利用）
    if args.dry_run:
        print("dry-run: CSV / cache / outlier list は書き換えていません")
        return 0

    remaining_outliers = []
    for row in rows:
        lng = parse_float(row.get("lng"))
        if math.isfinite(lng) and lng < args.threshold_lng:
            remaining_outliers.append(row)
    save_outlier_list(args.outlier_list, fieldnames, remaining_outliers, args.threshold_lng)

    save_cache(CACHE_PATH, cache)

    save_csv(args.out_csv, fieldnames, rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

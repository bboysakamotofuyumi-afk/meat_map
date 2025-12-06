#!/usr/bin/env python3
"""
docs/output/meatmap.csv の tabelog ソース行について、
../https_learn/data/raw 配下の *_map.html から
ランチ / ディナーの予算レンジを取得して反映するスクリプト。

やること:
  - *_map.html を走査し、JSON-LD の Restaurant @id から店舗URLを特定
  - その HTML 内の「予算」テーブルから
      - Dinner: c-rating-v3__time--dinner
      - Lunch:  c-rating-v3__time--lunch
    直後の価格レンジ（例: ￥3,000～￥3,999, ～￥999）を抽出
  - meatmap.csv の sources に tabelog を含む行について、
    url と @id(URL) が一致する店舗に次の列を追加/更新する:
      - lunch_budget_min, lunch_budget_max
      - dinner_budget_min, dinner_budget_max

注意:
  - 価格レンジは数値（円）として保存する（カンマや「￥」「～」は除去）
  - 値が無い / 「-」のときは空文字として出力する
"""

from __future__ import annotations

import csv
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple


ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "docs" / "output" / "meatmap.csv"
RAW_DIR = ROOT.parent / "https_learn" / "data" / "raw"


def normalize_url_key(url: str) -> str:
    """末尾スラッシュを揃えて、比較用のキーを作る。"""
    u = (url or "").strip()
    if not u:
        return ""
    u = u.split("#", 1)[0].split("?", 1)[0]
    return u.rstrip("/")


def parse_budget_range(text: str) -> Tuple[Optional[int], Optional[int]]:
    """
    「￥3,000～￥3,999」「～￥999」「-」のような文字列を数値レンジに変換。
    戻り値は (min, max) [いずれも円・int]。取れない場合は (None, None)。
    """
    if not text:
        return None, None
    s = text.strip()
    if s in {"-", "－"}:
        return None, None
    # 記号類を揃えておく
    s = s.replace("円", "").replace(",", "").replace("￥", "")
    # 例: "1000～1999" / "～999"
    if "～" in s:
        left, right = s.split("～", 1)
        left = left.strip()
        right = right.strip()
        min_v = int(left) if left and left.isdigit() else None
        max_v = int(right) if right and right.isdigit() else None
        if min_v is None and max_v is None:
            return None, None
        return min_v, max_v
    # 想定外だが、単一値だけが来た場合
    s = s.strip()
    if s.isdigit():
        v = int(s)
        return v, v
    return None, None


def build_tabelog_budget_map(raw_dir: Path) -> Dict[str, Dict[str, Tuple[Optional[int], Optional[int]]]]:
    """
    *_map.html から URL -> { "lunch": (min,max), "dinner": (min,max) } のマップを作成。
    """
    if not raw_dir.exists():
        raise SystemExit(f"raw dir not found: {raw_dir}")

    # 店舗URL取得用 JSON-LD
    url_pattern = re.compile(
        r'\{"@context":"https?://schema\.org","@type":"Restaurant","@id":"([^"]+)"',
        re.DOTALL,
    )
    # 予算(ディナー)
    dinner_pattern = re.compile(
        r'c-rating-v3__time[^"]*c-rating-v3__time--dinner"[^>]*>.*?(?:<a[^>]*class="rdheader-budget__price-target"|<em)>([^<]+)(?:</a>|</em>)',
        re.DOTALL,
    )
    # 予算(ランチ)
    lunch_pattern = re.compile(
        r'c-rating-v3__time[^"]*c-rating-v3__time--lunch"[^>]*>.*?(?:<a[^>]*class="rdheader-budget__price-target"|<em)>([^<]+)(?:</a>|</em>)',
        re.DOTALL,
    )

    mapping: Dict[str, Dict[str, Tuple[Optional[int], Optional[int]]]] = {}

    for path in sorted(raw_dir.glob("*_map.html")):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:  # noqa: BLE001
            continue

        m_url = url_pattern.search(text)
        if not m_url:
            continue
        url = m_url.group(1)
        key = normalize_url_key(url)
        if not key:
            continue

        m_d = dinner_pattern.search(text)
        m_l = lunch_pattern.search(text)
        dinner_range = parse_budget_range(m_d.group(1)) if m_d else (None, None)
        lunch_range = parse_budget_range(m_l.group(1)) if m_l else (None, None)

        # どちらも取れない場合はスキップ
        if dinner_range == (None, None) and lunch_range == (None, None):
            continue

        if key not in mapping:
            mapping[key] = {}
        mapping[key]["dinner"] = dinner_range
        mapping[key]["lunch"] = lunch_range

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
    budget_map = build_tabelog_budget_map(RAW_DIR)
    print(f"loaded tabelog budgets from map.html: {len(budget_map)} entries")

    fieldnames, rows = load_meatmap(CSV_PATH)

    extra_cols = [
        "lunch_budget_min",
        "lunch_budget_max",
        "dinner_budget_min",
        "dinner_budget_max",
    ]
    for col in extra_cols:
        if col not in fieldnames:
            fieldnames.append(col)

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
        if not key or key not in budget_map:
            skipped_not_found += 1
            continue

        info = budget_map[key]
        dinner_min, dinner_max = info.get("dinner", (None, None))
        lunch_min, lunch_max = info.get("lunch", (None, None))

        def to_str(v: Optional[int]) -> str:
            return str(v) if v is not None and math.isfinite(v) else ""

        new_vals = {
            "lunch_budget_min": to_str(lunch_min),
            "lunch_budget_max": to_str(lunch_max),
            "dinner_budget_min": to_str(dinner_min),
            "dinner_budget_max": to_str(dinner_max),
        }

        changed = False
        for col, val in new_vals.items():
            old = row.get(col, "")
            if old != val:
                row[col] = val
                changed = True
        if changed:
            updated += 1

    print(
        f"rows: {len(rows)}, updated_budget_rows: {updated}, "
        f"no_url: {skipped_no_url}, not_found_in_map: {skipped_not_found}",
    )

    backup = CSV_PATH.with_suffix(".csv.pre_tabelog_budget.bak")
    if not backup.exists():
        CSV_PATH.replace(backup)
        print(f"backup created: {backup}")
        current_path = backup
    else:
        print(f"backup already exists: {backup}")
        current_path = backup

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


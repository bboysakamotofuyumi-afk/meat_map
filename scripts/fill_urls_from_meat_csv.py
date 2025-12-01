#!/usr/bin/env python3
"""
docs/output/meat.csv から URL 情報を取り出し、
name + address をキーに docs/output/meatmap.csv の url 欄を補完するスクリプト。

- meat.csv の先頭行にヘッダーと1件目のレコードが同一行に混ざっている不具合を
  自前で補正してから読み込む。
- すでに url が入っている行は上書きしない。
- 主に sources に tabelog を含む店舗向けの URL 補完が目的。
"""

from __future__ import annotations

import csv
import math
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple


ROOT = Path(__file__).resolve().parents[1]
MEAT_PATH = ROOT / "docs" / "output" / "meat.csv"
MEATMAP_PATH = ROOT / "docs" / "output" / "meatmap.csv"


def normalize_text(s: str) -> str:
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", str(s))
    s = "".join(ch for ch in s if ch not in {" ", "\u3000"})
    return s.lower().strip()


def make_key(name: str, address: str) -> str:
    return normalize_text(name) + "||" + normalize_text(address)


def load_meat_rows(path: Path) -> Dict[str, str]:
    """
    meat.csv を読み込み、(name, address) -> url のマップを作る。

    先頭行にヘッダーと1行目が混ざっているケースを補正してから DictReader に渡す。
    """
    text = path.read_text(encoding="utf-8")
    header = "name,address,genre,sources,url"
    if text.startswith(header + "\n"):
        fixed = text
    elif text.startswith(header):
        # 先頭行に 1レコード目がくっついている想定:
        # name,address,genre,sources,urlシュラスコ&ビアレストラン...
        rest = text[len(header) :]
        fixed = header + "\n" + rest
    else:
        fixed = text

    rows = list(csv.DictReader(fixed.splitlines()))
    url_map: Dict[str, str] = {}
    for row in rows:
        name = (row.get("name") or "").strip()
        address = (row.get("address") or "").strip()
        url = (row.get("url") or "").strip()
        if not name or not address or not url:
            continue
        key = make_key(name, address)
        # 複数ヒットした場合は最初の URL を優先
        url_map.setdefault(key, url)
    return url_map


def load_meatmap(path: Path) -> Tuple[List[str], List[dict]]:
    if not path.exists():
        raise SystemExit(f"meatmap.csv not found: {path}")
    with path.open(encoding="utf-8") as f:
        data_lines = [line for line in f if not line.startswith("#")]
    reader = csv.DictReader(data_lines)
    if not reader.fieldnames:
        raise SystemExit("meatmap.csv has no header")
    rows = list(reader)
    return list(reader.fieldnames), rows


def is_finite(value: str) -> bool:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(x)


def main() -> int:
    url_map = load_meat_rows(MEAT_PATH)
    fieldnames, rows = load_meatmap(MEATMAP_PATH)

    updated = 0
    total_tabelog = 0
    for row in rows:
        sources = (row.get("sources") or "").strip()
        if "tabelog" not in sources:
            continue
        total_tabelog += 1
        if (row.get("url") or "").strip():
            continue
        name = (row.get("name") or "").strip()
        address = (row.get("address") or "").strip()
        key = make_key(name, address)
        url = url_map.get(key)
        if not url:
            continue
        row["url"] = url
        updated += 1

    print(f"tabelog rows in meatmap.csv: {total_tabelog}")
    print(f"rows whose url was filled: {updated}")

    # バックアップ
    bak = MEATMAP_PATH.with_suffix(".csv.pre_url_fix.bak")
    if not bak.exists():
        MEATMAP_PATH.replace(bak)
        print(f"backup created: {bak}")
    else:
        print(f"backup already exists: {bak}")

    # 書き戻し
    with MEATMAP_PATH.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        ts = datetime.now(timezone.utc).isoformat()
        f.write(f"# generated_at_utc={ts}\n")
        f.write(f"# total_records={len(rows)}\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    print(f"written updated CSV: {MEATMAP_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


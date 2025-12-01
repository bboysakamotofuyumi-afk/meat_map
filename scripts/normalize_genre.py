#!/usr/bin/env python3
"""
meatmap.csv の genre を要件どおり正規化するスクリプト。

最終的に genre は次のいずれか 1 つになる:
  - 焼肉
  - ステーキ
  - シュラスコ
  - もつ焼き
  - 焼き鳥
  - しゃぶしゃぶ
  - 韓国
  - 中華
  - その他
"""

from __future__ import annotations

import csv
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Tuple


ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "docs" / "output" / "meatmap.csv"

TARGET_GENRES = [
    "焼肉",
    "ステーキ",
    "シュラスコ",
    "もつ焼き",
    "焼き鳥",
    "しゃぶしゃぶ",
    "韓国",
    "中華",
    "その他",
]


def norm(s: str) -> str:
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", str(s))
    return s.lower()


def classify_primary(text: str) -> str | None:
    """
    一次分類: 焼肉 / ステーキ / シュラスコ / もつ焼き / 焼き鳥 / しゃぶしゃぶ
    text: 店名 or 元 genre
    """
    t = norm(text)

    if "焼肉" in t or "yakiniku" in t:
        return "焼肉"
    if "ステーキ" in t or "steak" in t:
        return "ステーキ"
    if "シュラスコ" in t or "churrasco" in t or "rc041103" in t:
        return "シュラスコ"
    if "もつ焼" in t or "ホルモン" in t or "rc010604" in t:
        return "もつ焼き"
    if "焼き鳥" in t or "焼鳥" in t or "yakitori" in t:
        return "焼き鳥"
    if "しゃぶしゃぶ" in t or "shabushabu" in t or "しゃぶ" in t:
        return "しゃぶしゃぶ"
    return None


def classify_secondary(text: str) -> str | None:
    """
    二次分類: 韓国 / 中華
    text: 主に店名を想定
    """
    t = norm(text)
    if "韓国" in t or "コリア" in t:
        return "韓国"
    if "中華" in t or "中国" in t:
        return "中華"
    return None


def classify_row(name: str, raw_genre: str) -> str:
    """
    1. 店名から一次分類
    2. ジャンルから一次分類
    3. 店名から二次分類（韓国/中華）
    4. どれにも当てはまらなければ その他
    """
    # 1. 店名から
    g_name = classify_primary(name)
    if g_name:
        return g_name

    # 2. 既存ジャンルから
    g_genre = classify_primary(raw_genre)
    if g_genre:
        return g_genre

    # 3. 店名から韓国 / 中華
    g_sec = classify_secondary(name)
    if g_sec:
        return g_sec

    return "その他"


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
    fieldnames, rows = load_meatmap(CSV_PATH)

    changed = 0
    for row in rows:
        name = (row.get("name") or "").strip()
        raw_genre = (row.get("genre") or "").strip()
        new_genre = classify_row(name, raw_genre)
        if new_genre not in TARGET_GENRES:
            raise SystemExit(f"unexpected genre '{new_genre}'")
        if raw_genre != new_genre:
            changed += 1
            row["genre"] = new_genre

    print(f"rows: {len(rows)}, changed_genre: {changed}")

    backup = CSV_PATH.with_suffix(".csv.pre_genre_norm.bak")
    if not backup.exists():
        CSV_PATH.replace(backup)
        print(f"backup created: {backup}")
    else:
        print(f"backup already exists: {backup}")

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


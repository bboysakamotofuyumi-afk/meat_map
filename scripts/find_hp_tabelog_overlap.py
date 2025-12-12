#!/usr/bin/env python3
"""
HotPepper と食べログの重複疑惑件数を集計するスクリプト。

ルール:
- address の「〇丁目」まで（なければ最初のハイフン直前まで）をキーにクラスタリング
- クラスタ内で HotPepper と食べログの組み合わせを全探索し、
  name から「店」「本店」を除いた最長共通部分文字列の長さが閾値を超えるものを疑義とする
"""

from __future__ import annotations

import argparse
import csv
import re
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


DEFAULT_CSV = Path("docs/output/meatmap.csv")
DEFAULT_THRESHOLD = 3


def load_rows(path: Path) -> Iterable[Dict[str, str]]:
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(line for line in f if not line.startswith("#"))
        for row in reader:
            yield row


def normalize_address_key(address: Optional[str]) -> Optional[str]:
    if not address:
        return None
    s = unicodedata.normalize("NFKC", address).replace("　", "").strip()
    m = re.search(r"(.+?丁目)", s)
    if m:
        return m.group(1)
    m = re.search(r"(.+?\d+-)", s)
    if m:
        return m.group(1)
    return None


def _strip_accents(s: str) -> str:
    # ローマ字の揺れ（大文字小文字・アクセント）を吸収
    nkfd = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in nkfd if not unicodedata.combining(ch))


def normalize_name(name: Optional[str]) -> str:
    if not name:
        return ""
    s = unicodedata.normalize("NFKC", name)
    s = s.replace("本店", "")
    s = s.replace("店", "")
    # 記号・空白を除去
    s = re.sub(r"[\\s・･.,/()（）［］\\[\\]\\-‐―ー−‐'\"、。!！?？＆&]", "", s)
    # ローマ字は小文字化し、アクセントを除去して揺れを抑える
    s = _strip_accents(s).lower()
    return s


def lcs_length(a: str, b: str) -> int:
    """最長共通部分文字列の長さを返す。"""
    if not a or not b:
        return 0
    dp = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    best = 0
    for i, ca in enumerate(a, start=1):
        for j, cb in enumerate(b, start=1):
            if ca == cb:
                dp[i][j] = dp[i - 1][j - 1] + 1
                if dp[i][j] > best:
                    best = dp[i][j]
    return best


def parse_sources(value: Optional[str]) -> Sequence[str]:
    if not value:
        return []
    return [s.strip().lower() for s in value.split(",") if s.strip()]


def find_suspects(rows: Iterable[Dict[str, str]], threshold: int) -> Tuple[int, List[Dict[str, object]]]:
    clusters: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    scanned = 0
    for row in rows:
        key = normalize_address_key(row.get("address"))
        if not key:
            continue
        clusters[key].append(row)
        scanned += 1

    suspects: List[Dict[str, object]] = []
    for key, items in clusters.items():
        hp = [r for r in items if "hotpepper" in parse_sources(r.get("sources"))]
        tb = [r for r in items if "tabelog" in parse_sources(r.get("sources"))]
        if not hp or not tb:
            continue
        for h in hp:
            hn = normalize_name(h.get("name"))
            for t in tb:
                tn = normalize_name(t.get("name"))
                score = lcs_length(hn, tn)
                if score > threshold:
                    suspects.append(
                        {
                            "address_key": key,
                            "score": score,
                            "hotpepper_name": h.get("name", ""),
                            "tabelog_name": t.get("name", ""),
                        }
                    )
    suspects.sort(key=lambda x: x["score"], reverse=True)
    return scanned, suspects, len(clusters)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Find suspected duplicates between HotPepper and Tabelog.")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV, help="Path to meatmap.csv (default: docs/output/meatmap.csv)")
    parser.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD, help="LCS length threshold (default: 3)")
    parser.add_argument("--limit", type=int, default=20, help="How many suspect pairs to show (default: 20)")
    args = parser.parse_args(argv)

    if not args.csv.exists():
        raise SystemExit(f"CSV not found: {args.csv}")

    scanned, suspects, cluster_count = find_suspects(load_rows(args.csv), args.threshold)
    print(f"rows clustered: {scanned}")
    print(f"clusters total: {cluster_count}")
    print(f"clusters with suspects: {len(set(s['address_key'] for s in suspects))}")
    print(f"suspect pairs (score > {args.threshold}): {len(suspects)}")
    for entry in suspects[: args.limit]:
        print(
            f"[{entry['address_key']}] score={entry['score']} "
            f"HP='{entry['hotpepper_name']}' / Tabe='{entry['tabelog_name']}'"
        )
    if len(suspects) > args.limit:
        print(f"... and {len(suspects) - args.limit} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

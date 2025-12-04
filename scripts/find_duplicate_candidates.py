#!/usr/bin/env python3
"""
meatmap.csv から「重複の疑いがある」店舗ペア/グループを抽出し、
CSV に書き出すユーティリティ。

検出ロジック（2段階クラスタリング）:
  1. 緯度経度距離が閾値以下（デフォルト 20m）で連結成分を作る
  2. 同じ距離グループ内で、「地名＋店」を除いた正規化店名が
     「連続する共通部分が閾値以上（デフォルト3文字）」あるもの同士でクラスタ化

出力:
  docs/output/duplicate_candidates.csv
  クラスタID単位で行を持ち、同クラスタ内の全店舗をまとめる。
"""

from __future__ import annotations

import argparse
import csv
import math
import re
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "docs" / "output" / "meatmap.csv"
OUT_PATH = ROOT / "docs" / "output" / "duplicate_candidates.csv"

GENERIC_TERMS = ["食べ放題", "本店", "店", "個室", "居酒屋", "焼肉", "しゃぶしゃぶ", "本格", "ホルモン"]


def normalize_text(value: str) -> str:
    if value is None:
        return ""
    s = unicodedata.normalize("NFKC", str(value))
    s = "".join(ch for ch in s if ch not in {" ", "\u3000"})
    return s.lower().strip()


def strip_branch_suffix(name: str) -> str:
    """
    「渋谷店」のような地名＋店の末尾を外した店名を返す。
    単語区切りは半角/全角スペースを想定し、末尾単語が「店」で終わる場合のみ除去する。
    """
    if not name:
        return ""
    s = unicodedata.normalize("NFKC", str(name))
    s = re.sub(r"[\\s\u3000]+", " ", s).strip()
    tokens = s.split(" ")
    if len(tokens) >= 2 and tokens[-1].endswith("店"):
        tokens = tokens[:-1]
    return " ".join(tokens)


def name_length_for_match(name: str) -> int:
    """クラスタ用の店名文字数（地名＋店を除き、空白除去・小文字化後の長さ）。"""
    base = strip_branch_suffix(name)
    normalized = normalize_text(base)
    return len(normalized)


def name_key_for_match(name: str) -> str:
    """クラスタ用の店名キー（地名＋店を除き、空白除去・小文字化後の文字列から記号を除去）。"""
    base = strip_branch_suffix(name)
    normalized = normalize_text(base)
    # 記号・句読点・長音などのノイズを除去
    normalized = re.sub(r"[、，．・/／\\\-－‑–—―ー]+", "", normalized)
    for term in GENERIC_TERMS:
        normalized = normalized.replace(term, "")
    return normalized


def longest_common_substring_length(a: str, b: str) -> int:
    """連続部分文字列の最長長さを返す（空なら0）。"""
    if not a or not b:
        return 0
    m, n = len(a), len(b)
    prev = [0] * (n + 1)
    best = 0
    for i in range(1, m + 1):
        curr = [0] * (n + 1)
        ca = a[i - 1]
        for j in range(1, n + 1):
            if ca == b[j - 1]:
                curr[j] = prev[j - 1] + 1
                if curr[j] > best:
                    best = curr[j]
        prev = curr
    return best


def parse_rows(path: Path) -> Tuple[List[str], List[Dict[str, str]]]:
    with path.open(encoding="utf-8") as f:
        rows = [line for line in f if not line.startswith("#")]
    reader = csv.DictReader(rows)
    fieldnames = reader.fieldnames or []
    data = list(reader)
    return fieldnames, data


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def build_clusters(rows: List[Dict[str, str]], dist_thresh: float, common_len: int) -> List[List[int]]:
    n = len(rows)
    adj = [set() for _ in range(n)]

    # 前計算
    coords = []
    for r in rows:
        try:
            lat = float(r.get("lat") or "nan")
            lng = float(r.get("lng") or "nan")
        except Exception:
            lat = lng = math.nan
        coords.append((lat, lng))

    # 近距離（距離計算）
    for i in range(n):
        lat1, lng1 = coords[i]
        if not (math.isfinite(lat1) and math.isfinite(lng1)):
            continue
        for j in range(i + 1, n):
            lat2, lng2 = coords[j]
            if not (math.isfinite(lat2) and math.isfinite(lng2)):
                continue
            d = haversine(lat1, lng1, lat2, lng2)
            if d <= dist_thresh:
                adj[i].add(j)
                adj[j].add(i)

    # 距離のみで連結成分を作成
    visited = [False] * n
    distance_groups = []
    for i in range(n):
        if visited[i] or not adj[i]:
            continue
        stack = [i]
        comp = []
        visited[i] = True
        while stack:
            v = stack.pop()
            comp.append(v)
            for nb in adj[v]:
                if not visited[nb]:
                    visited[nb] = True
                    stack.append(nb)
        distance_groups.append(comp)

    # 距離グループ内で、店舗名（地名＋店を除く）の連続共通部分が3文字以上のペアをクラスタ化
    clusters = []
    for group in distance_groups:
        if len(group) < 2:
            continue
        # ペア単位で一致判定し、隣接リストを作る
        sub_adj = defaultdict(set)
        keys = {idx: name_key_for_match(rows[idx].get("name", "")) for idx in group}
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                ka, kb = keys[a], keys[b]
                if len(ka) >= common_len and len(kb) >= common_len and longest_common_substring_length(ka, kb) >= common_len:
                    sub_adj[a].add(b)
                    sub_adj[b].add(a)

        # sub_adj にエッジがある連結成分のみ採用
        sub_visited = set()
        for start in list(sub_adj.keys()):
            if start in sub_visited:
                continue
            stack = [start]
            comp = []
            sub_visited.add(start)
            while stack:
                v = stack.pop()
                comp.append(v)
                for nb in sub_adj[v]:
                    if nb not in sub_visited:
                        sub_visited.add(nb)
                        stack.append(nb)
            if len(comp) > 1:
                clusters.append(sorted(comp))

    return clusters


def main():
    ap = argparse.ArgumentParser(description="Find duplicate candidate stores in meatmap.csv")
    ap.add_argument("--dist-thresh", type=float, default=20.0, help="距離閾値 (メートル, default 20)")
    ap.add_argument("--common-len", type=int, default=3, help="店名の最長連続共通長の閾値 (デフォルト3)")
    ap.add_argument("--output", type=Path, default=OUT_PATH, help="出力CSVパス")
    args = ap.parse_args()

    fieldnames, rows = parse_rows(CSV_PATH)
    clusters = build_clusters(rows, args.dist_thresh, args.common_len)

    # 出力: クラスタ単位で一行にまとめる（可読性重視）
    with args.output.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "cluster_id",
                "size",
                "names",
                "addresses",
                "lats",
                "lngs",
                "sources",
                "urls",
            ]
        )
        for cid, comp in enumerate(clusters, start=1):
            names = [rows[i].get("name", "") for i in comp]
            addrs = [rows[i].get("address", "") for i in comp]
            lats = [rows[i].get("lat", "") for i in comp]
            lngs = [rows[i].get("lng", "") for i in comp]
            srcs = [rows[i].get("sources", "") for i in comp]
            urls = [rows[i].get("url", "") for i in comp]
            writer.writerow(
                [
                    cid,
                    len(comp),
                    " | ".join(names),
                    " | ".join(addrs),
                    " | ".join(lats),
                    " | ".join(lngs),
                    " | ".join(srcs),
                    " | ".join(urls),
                ]
            )
    print(f"clusters found: {len(clusters)}")
    print(f"written: {args.output}")


if __name__ == "__main__":
    main()

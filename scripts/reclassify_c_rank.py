#!/usr/bin/env python3
"""
ランク C かつ「シュラスコ / 韓国 / その他」に入っている店舗を
店名検索（DuckDuckGo）で補助情報を拾い、ジャンルを再分類するスクリプト。

検索→HTML本文に含まれるキーワードでジャンル推定。
ネット接続が必要（--offline を付けるとキャッシュのみ参照）。
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests


ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "docs" / "output" / "meatmap.csv"
CACHE_PATH = ROOT / "docs" / "output" / "search_cache.json"

TARGET_GENRES = {"シュラスコ", "韓国", "その他"}
HEADERS = {
    # ブラウザ寄りの UA / リファラで 403 回避を狙う
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en;q=0.8",
    "Referer": "https://www.google.com/",
}

# キーワードと優先順位（上にあるほど優先）
GENRE_RULES: List[Tuple[str, List[str]]] = [
    ("シュラスコ", ["シュラスコ", "churrasco", "バーベキュー"]),
    ("韓国", ["韓国", "サムギョプサル", "チゲ", "キムチ", "プルコギ", "タッカルビ", "コリアン"]),
    ("焼き鳥・もつ焼き", ["焼き鳥", "やきとり", "Yakitori", "もつ焼", "やきとん", "ホルモン"]),
    ("焼肉", ["焼肉", "焼き肉", " yakiniku ", "ホルモン"]),
    ("ステーキ", ["ステーキ", "steak", "ハンバーグ", "グリル", "肉バル"]),
    ("中華", ["中華", "中華料理", "餃子", "麻婆", "刀削麺"]),
]


def load_csv(path: Path) -> Tuple[List[str], List[Dict[str, str]]]:
    if not path.exists():
        raise SystemExit(f"CSV not found: {path}")
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(line for line in f if not line.startswith("#"))
        rows = list(reader)
    if not reader.fieldnames:
        raise SystemExit("CSV has no header")
    return list(reader.fieldnames), rows


def save_csv(
    path: Path,
    fieldnames: List[str],
    rows: List[Dict[str, str]],
) -> None:
    backup = path.with_suffix(".csv.pre_c_rank_reclass.bak")
    if not backup.exists():
        path.replace(backup)
        print(f"backup created: {backup}")
    else:
        print(f"backup already exists: {backup}")

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        ts = datetime.now(timezone.utc).isoformat()
        f.write(f"# generated_at_utc={ts}\n")
        f.write(f"# total_records={len(rows)}\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    print(f"written updated CSV: {path}")


def load_cache(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] cache load failed: {exc}", file=sys.stderr)
    return {}


def save_cache(path: Path, cache: Dict[str, str]) -> None:
    try:
        path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] cache save failed: {exc}", file=sys.stderr)


def duckduckgo_search(session: requests.Session, query: str) -> Optional[str]:
    urls = [
        "https://duckduckgo.com/html/",
        "https://html.duckduckgo.com/html/",
    ]
    for url in urls:
        try:
            res = session.get(url, params={"q": query}, headers=HEADERS, timeout=20)
            if not res.ok:
                print(f"[warn] HTTP {res.status_code} for query: {query}", file=sys.stderr)
                continue
            return res.text
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] search failed for '{query}': {exc}", file=sys.stderr)
            continue
    return None


def fetch_url(session: requests.Session, url: str) -> Optional[str]:
    try:
        res = session.get(url, headers=HEADERS, timeout=20)
        if not res.ok:
            print(f"[warn] HTTP {res.status_code} for url: {url}", file=sys.stderr)
            return None
        return res.text
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] fetch failed for '{url}': {exc}", file=sys.stderr)
        return None


def guess_genre_from_text(text: str) -> Optional[str]:
    hay = text.lower()
    for genre, keywords in GENRE_RULES:
        for kw in keywords:
            if kw.lower() in hay:
                return genre
    return None


def build_query(name: str, address: str) -> str:
    base = name.strip()
    if "東京都" not in address:
        return base
    return f"{base} 東京都"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Reclassify C-rank churrasco/korean/other rows by name search")
    parser.add_argument("--max-requests", type=int, default=80, help="新規検索の最大件数")
    parser.add_argument("--delay", type=float, default=1.0, help="検索リクエスト間のスリープ秒")
    parser.add_argument("--dry-run", action="store_true", help="CSVを書き換えない")
    parser.add_argument("--offline", action="store_true", help="キャッシュだけで判定し、新規検索を行わない")
    parser.add_argument(
        "--prefer-url",
        action="store_true",
        help="店舗のURLがある場合はそれを優先して取得（デフォルトは検索優先）",
    )
    parser.add_argument(
        "--tabelog-only",
        action="store_true",
        help="sources に tabelog を含む行だけを対象にする（誤分類が少ない場合の限定用）",
    )
    args = parser.parse_args(argv)

    fieldnames, rows = load_csv(CSV_PATH)
    cache = load_cache(CACHE_PATH)
    session = requests.Session()

    targets: List[int] = []
    for idx, row in enumerate(rows):
        if (row.get("carnivore_rank") or "").strip() != "C":
            continue
        genre = (row.get("genre") or "").strip()
        if genre not in TARGET_GENRES:
            continue
        if args.tabelog_only:
            if "tabelog" not in (row.get("sources") or ""):
                continue
        targets.append(idx)

    print(f"target rows: {len(targets)} (Cランクかつ {', '.join(sorted(TARGET_GENRES))})")
    if not targets:
        return 0

    updated = 0
    new_requests = 0
    url_requests = 0
    for idx in targets:
        row = rows[idx]
        name = (row.get("name") or "").strip()
        address = (row.get("address") or "").strip()
        key = name
        url = (row.get("url") or "").strip()
        if args.tabelog_only and url and "tabelog.com" not in url:
            # tabelog限定の場合は、食べログ以外のURLは無視する（検索優先に切り替え）
            url = ""

        # 1) URL優先（オプション指定時）
        text = ""
        if args.prefer_url and url:
            cache_key = f"url:{url}"
            cached = cache.get(cache_key)
            text = cached or ""
            if not cached and not args.offline and url_requests < args.max_requests:
                text = fetch_url(session, url) or ""
                cache[cache_key] = text
                url_requests += 1
                if args.delay > 0:
                    time.sleep(args.delay)

        # 2) URLで取れなければ、従来の検索
        if not text:
            cached = cache.get(key)
            text = cached or ""
            if not cached and not args.offline and new_requests < args.max_requests:
                query = build_query(name, address)
                text = duckduckgo_search(session, query) or ""
                cache[key] = text
                new_requests += 1
                if args.delay > 0:
                    time.sleep(args.delay)

        if not text:
            print(f"[skip] no text for {name}")
            continue

        new_genre = guess_genre_from_text(text)
        if not new_genre:
            # tabelog限定かつ Cランク/対象ジャンルのみ -> ヒットしない場合は「その他」に寄せる
            if args.tabelog_only:
                new_genre = "その他"
                print(f"[fallback] no keyword match for {name}; force -> その他")
            else:
                print(f"[skip] no keyword match for {name}")
                continue

        old_genre = (row.get("genre") or "").strip()
        if old_genre == new_genre:
            continue
        row["genre"] = new_genre
        updated += 1
        print(f"[ok] {name}: {old_genre} -> {new_genre}")

    print(f"updated rows: {updated}")
    print(f"new search requests: {new_requests}")

    save_cache(CACHE_PATH, cache)

    # サマリを別ファイルに残す
    summary_path = CSV_PATH.parent / "meatmap_reclass_summary.txt"
    ts = datetime.now(timezone.utc).isoformat()
    summary = (
        f"timestamp_utc={ts}\n"
        f"updated_rows={updated}\n"
        f"new_requests={new_requests + url_requests}\n"
        f"mode={'tabelog_only' if args.tabelog_only else 'all_sources'}\n"
    )
    try:
        summary_path.write_text(summary, encoding="utf-8")
        print(f"summary written: {summary_path}")
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] failed to write summary: {exc}")

    if args.dry_run:
        print("dry-run: CSVは書き換えていません")
        return 0

    save_csv(CSV_PATH, fieldnames, rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

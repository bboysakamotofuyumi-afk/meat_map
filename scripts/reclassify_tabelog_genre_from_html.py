#!/usr/bin/env python3
"""
docs/output/meatmap.csv のうち、tabelog ソース行の genre を
食べログ HTML (map.html) 由来の公式ジャンル情報から再分類するスクリプト。

参照する情報:
  - JSON-LD の servesCuisine
  - 店舗情報テーブル内の「ジャンル」行 (<th>ジャンル</th> ... <span>...</span>)
  - 広告ターゲティングに埋め込まれたカテゴリコード (t_category2 / t_category3, 例: RC010604)

分類ロジック:
  - normalize_genre.py の classify_row(name, raw_genre) を再利用
  - raw_genre の代わりに「servesCuisine + ジャンル表示 + RCコード文字列」を渡す
  - 既存ルールどおり「店名での一次分類 > ジャンルテキスト > 店名から韓国/中華」の優先度
"""

from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

from normalize_genre import TARGET_GENRES, classify_row  # type: ignore[import]


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


def extract_restaurant_json_ld(text: str) -> Tuple[str, str]:
    """
    HTML 文字列から Restaurant JSON-LD を探し、
    (@id, servesCuisine_text) を返す。見つからない場合は ("", "")。
    """
    script_pattern = re.compile(
        r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
        re.DOTALL | re.IGNORECASE,
    )
    for m in script_pattern.finditer(text):
        content = m.group(1).strip()
        if not content:
            continue
        # JSON-LD が複数オブジェクト配列の場合もあるので両方試す
        for candidate in (content, content.strip()[1:-1] if content.strip().startswith("[") and content.strip().endswith("]") else None):
            if not candidate:
                continue
            try:
                data = json.loads(candidate)
            except Exception:  # noqa: BLE001
                continue
            if isinstance(data, dict) and data.get("@type") == "Restaurant":
                url = str(data.get("@id") or data.get("url") or "")
                sc = data.get("servesCuisine")
                if isinstance(sc, list):
                    serves = "、".join(str(x) for x in sc)
                elif isinstance(sc, str):
                    serves = sc
                else:
                    serves = ""
                return url, serves
    return "", ""


def extract_table_genre(text: str) -> str:
    """
    店舗情報テーブルの「ジャンル」行からテキストを抽出。
    例: <th>ジャンル</th><td><span>おでん、もつ焼き、居酒屋</span></td>
    """
    m = re.search(
        r'<th>\s*ジャンル\s*</th>.*?<td[^>]*>.*?<span[^>]*>([^<]+)</span>',
        text,
        re.DOTALL,
    )
    if not m:
        return ""
    return m.group(1).strip()


def extract_rc_codes(text: str) -> List[str]:
    """
    t_category2 / t_category3 に含まれる RC コードを抽出。
    例: setTargeting('t_category2', "RC0108,RC0106,RC2101")
    """
    codes: List[str] = []
    for key in ("t_category2", "t_category3"):
        m = re.search(rf"setTargeting\('{key}', \"([^\"]*)\"", text)
        if not m:
            continue
        raw = m.group(1).strip()
        if not raw:
            continue
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        codes.extend(parts)
    return codes


def build_tabelog_feature_map(raw_dir: Path) -> Dict[str, str]:
    """
    *_map.html から:
      URL(@id) -> 「servesCuisine + ジャンル + RCコード」文字列
    のマップを構築する。
    """
    if not raw_dir.exists():
        raise SystemExit(f"raw dir not found: {raw_dir}")

    mapping: Dict[str, str] = {}
    for path in sorted(raw_dir.glob("*_map.html")):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:  # noqa: BLE001
            continue

        url, serves = extract_restaurant_json_ld(text)
        if not url:
            continue
        key = normalize_url_key(url)
        if not key:
            continue

        table_genre = extract_table_genre(text)
        rc_codes = extract_rc_codes(text)

        parts: List[str] = []
        if serves:
            parts.append(serves)
        if table_genre and table_genre not in parts:
            parts.append(table_genre)
        if rc_codes:
            parts.append(",".join(rc_codes))

        feature_text = " ".join(parts).strip()
        if not feature_text:
            continue

        # 既に登録済みの URL については最初のものを優先
        if key not in mapping:
            mapping[key] = feature_text
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
    feature_map = build_tabelog_feature_map(RAW_DIR)
    print(f"built tabelog feature map from map.html: {len(feature_map)} entries")

    fieldnames, rows = load_meatmap(CSV_PATH)

    total_tabelog = 0
    updated = 0
    missing_feature = 0

    for row in rows:
        sources = (row.get("sources") or "").lower()
        if "tabelog" not in sources:
            continue
        total_tabelog += 1
        url = (row.get("url") or "").strip()
        key = normalize_url_key(url)
        if not key or key not in feature_map:
            missing_feature += 1
            continue

        name = (row.get("name") or "").strip()
        raw_text = feature_map[key]
        new_genre = classify_row(name, raw_text)
        if new_genre not in TARGET_GENRES:
            # 予期せぬものは無理に書き換えずスキップ
            continue

        old_genre = (row.get("genre") or "").strip()
        if old_genre != new_genre:
            row["genre"] = new_genre
            updated += 1

    print(
        f"rows: {len(rows)}, tabelog_rows: {total_tabelog}, "
        f"updated_genre: {updated}, no_feature_for_tabelog: {missing_feature}",
    )

    backup = CSV_PATH.with_suffix(".csv.pre_tabelog_genre.bak")
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

    print(f"written updated CSV with reclassified tabelog genres: {CSV_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


#!/usr/bin/env python3
"""
docs/output/meatmap.csv に平均ディナー予算カラムを追加するスクリプト。

やること:
  - カラム名: avg_dinner_budget（単位: 円, ざっくり夜ディナーの平均）
  - sources に tabelog を含む行:
      - dinner_budget_min, dinner_budget_max から平均値を計算して設定
  - sources に hotpepper を含み、tabelog を含まない行:
      - オプション --with-hotpepper が指定されている場合のみ、
        HotPepper API から budget 情報を取得して平均ディナー予算を設定

注意:
  - 既に avg_dinner_budget が埋まっている行は上書きしない
  - HOTPEPPER_API_KEY が未設定の場合は HotPepper からの取得はスキップする
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from meatmap.sources import HotPepperClient


ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "docs" / "output" / "meatmap.csv"


def load_meatmap(path: Path) -> Tuple[List[str], List[Dict[str, str]]]:
    if not path.exists():
        raise SystemExit(f"CSV not found: {path}")
    with path.open(encoding="utf-8") as f:
        data_lines = [line for line in f if not line.startswith("#")]
    reader = csv.DictReader(data_lines)
    if not reader.fieldnames:
        raise SystemExit("no header in meatmap.csv")
    rows = list(reader)
    # csv.DictReader の fieldnames は参照のためコピーしておく
    return list(reader.fieldnames), rows


def parse_int_or_none(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    s = value.strip()
    if not s:
        return None
    if not s.isdigit():
        return None
    return int(s)


def avg_from_range(min_str: Optional[str], max_str: Optional[str]) -> Optional[int]:
    """
    min/max の文字列から大まかな平均値を計算する。

    ルール:
      - 両方あれば (min + max) / 2 を四捨五入
      - どちらか片方だけあれば、その値をそのまま平均とみなす
      - どちらも取れなければ None
    """
    min_v = parse_int_or_none(min_str)
    max_v = parse_int_or_none(max_str)
    if min_v is None and max_v is None:
        return None
    if min_v is None:
        return max_v
    if max_v is None:
        return min_v
    return int(round((min_v + max_v) / 2))


HP_ID_PATTERN = re.compile(r"/str([A-Z]\d+)")


def extract_hotpepper_id(url: str) -> Optional[str]:
    """
    HotPepper の店舗URLから API 用の id (例: J001234567) を取り出す。
    """
    if not url:
        return None
    m = HP_ID_PATTERN.search(url)
    if not m:
        return None
    return m.group(1)


def hotpepper_avg_dinner_budget(client: HotPepperClient, shop_id: str) -> Optional[int]:
    """
    HotPepper API から該当店舗の平均ディナー予算を取得する。

    仕様上、budget.average は「平均ディナー予算」とされているので、
    そこから数字を抽出して平均値っぽいものを返す。
    """
    if not shop_id:
        return None
    try:
        results = client._request({"id": shop_id})  # type: ignore[arg-type]
    except Exception:
        return None
    shops: Sequence[dict] = results.get("shop", [])  # type: ignore[assignment]
    if not shops:
        return None
    budget = shops[0].get("budget") or {}
    if not isinstance(budget, dict):
        return None

    # name に「～1000」「2001～3000」等が入っていれば、まずはこちらを使う
    name_text = str(budget.get("name") or "").strip()
    if name_text:
        avg = _avg_from_free_text(name_text)
        if avg is not None:
            return avg

    # なければ average ("4000円" や「フリー2500円　宴会3500円」等) から数字を平均
    avg_text = str(budget.get("average") or "").strip()
    if avg_text:
        avg = _avg_from_free_text(avg_text)
        if avg is not None:
            return avg
    return None


def _avg_from_free_text(text: str) -> Optional[int]:
    """
    「4000円」「フリー2500円　宴会3500円」「～2000円」などの文字列から
    数値を抽出し、ざっくり平均を返す。
    """
    if not text:
        return None
    # 数字部分（カンマ付きも含む）をすべて抜き出す
    nums: List[int] = []
    for m in re.finditer(r"\d[\d,]*", text):
        s = m.group(0).replace(",", "")
        try:
            v = int(s)
        except ValueError:
            continue
        nums.append(v)
    if not nums:
        return None
    if len(nums) == 1:
        return nums[0]
    # 複数ある場合は単純平均
    return int(round(sum(nums) / len(nums)))


def update_avg_dinner_budget(with_hotpepper: bool) -> None:
    fieldnames, rows = load_meatmap(CSV_PATH)

    if "avg_dinner_budget" not in fieldnames:
        fieldnames.append("avg_dinner_budget")

    # まず tabelog から埋める
    updated_tabelog = 0
    for row in rows:
        sources = (row.get("sources") or "").lower()
        if "tabelog" not in sources:
            continue
        if row.get("avg_dinner_budget"):
            continue

        avg = avg_from_range(row.get("dinner_budget_min"), row.get("dinner_budget_max"))
        if avg is None:
            continue
        row["avg_dinner_budget"] = str(avg)
        updated_tabelog += 1

    updated_hotpepper = 0
    if with_hotpepper:
        api_key = os.getenv("HOTPEPPER_API_KEY")
        if not api_key:
            print("HOTPEPPER_API_KEY が未設定のため HotPepper からの取得はスキップします。")
        else:
            client = HotPepperClient(api_key=api_key)
            for row in rows:
                if row.get("avg_dinner_budget"):
                    continue
                sources = (row.get("sources") or "").lower()
                # tabelog がある店舗は tabelog を優先するので対象外
                if "hotpepper" not in sources or "tabelog" in sources:
                    continue
                url = (row.get("url") or "").strip()
                shop_id = extract_hotpepper_id(url)
                if not shop_id:
                    continue
                avg = hotpepper_avg_dinner_budget(client, shop_id)
                if avg is None:
                    continue
                row["avg_dinner_budget"] = str(avg)
                updated_hotpepper += 1

    print(
        f"updated avg_dinner_budget: tabelog={updated_tabelog}, "
        f"hotpepper={updated_hotpepper}",
    )

    backup = CSV_PATH.with_suffix(".csv.pre_avg_dinner.bak")
    if not backup.exists():
        CSV_PATH.replace(backup)
        print(f"backup created: {backup}")
        current_path = backup
    else:
        print(f"backup already exists: {backup}")
        current_path = backup

    # コメント行はバックアップ側から再利用
    meta_lines: Sequence[str] = []
    with current_path.open(encoding="utf-8") as f:
        for line in f:
            if line.startswith("#"):
                meta_lines.append(line)
            else:
                break

    with CSV_PATH.open("w", encoding="utf-8", newline="") as f:
        for line in meta_lines:
            f.write(line)
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    print(f"written updated CSV with avg_dinner_budget: {CSV_PATH}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Add avg_dinner_budget column to meatmap.csv")
    parser.add_argument(
        "--with-hotpepper",
        action="store_true",
        help="HotPepper API からも平均ディナー予算を取得する（HOTPEPPER_API_KEY 必須）",
    )
    args = parser.parse_args(argv)
    update_avg_dinner_budget(with_hotpepper=args.with_hotpepper)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


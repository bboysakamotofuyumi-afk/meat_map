from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.validate_public_site import extract_braced_javascript


ROOT = Path(__file__).resolve().parents[1]
MAP_HTML = ROOT / "docs" / "map_demo.html"


def extract_javascript_function(source: str, name: str) -> str:
    declaration = re.search(rf"\bfunction\s+{re.escape(name)}\s*\([^)]*\)\s*\{{", source)
    assert declaration is not None, f"{name} が見つかりません"
    opening_index = declaration.end() - 1
    body = extract_braced_javascript(source, opening_index)
    assert body is not None, f"{name} の関数本体が不完全です"
    return source[declaration.start() : opening_index] + body


def run_javascript(expression: str, *function_names: str):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js がない環境ではマップJavaScriptテストを省略します")
    source = MAP_HTML.read_text(encoding="utf-8")
    functions = "\n".join(extract_javascript_function(source, name) for name in function_names)
    script = f'{functions}\nprocess.stdout.write(JSON.stringify({expression}));\n'
    completed = subprocess.run(
        [node, "-"],
        input=script,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(completed.stdout)


def test_classify_genre_uses_store_name_and_avoids_lamb_substring_false_positives():
    cases = [
        {"name": "ステーキ銀座", "genre": "焼肉", "expected": "ステーキ"},
        {"name": "ホルモン新宿", "genre": "焼肉・ホルモン", "expected": "ホルモン"},
        {"name": "通常店", "genre": "焼肉・ホルモン", "expected": "焼肉"},
        {"name": "やきとん太郎", "genre": "居酒屋", "expected": "もつ焼き"},
        {"name": "焼き鳥花子", "genre": "居酒屋", "expected": "焼き鳥"},
        {"name": "生ラム専門店", "genre": "居酒屋", "expected": "ジンギスカン"},
        {"name": "トラジ 国際フォーラム横店", "genre": "焼肉・ホルモン", "expected": "焼肉"},
        {"name": "タイ東北モーラム酒店", "genre": "その他", "expected": "その他"},
        {"name": "すき焼き本舗", "genre": "しゃぶしゃぶ", "expected": "すき焼き"},
        {"name": "不明", "genre": "", "expected": "その他"},
    ]
    results = run_javascript(
        f"{json.dumps(cases, ensure_ascii=False)}.map(item => classifyGenre(item))",
        "normalizeGenreSearchText",
        "classifyGenre",
    )

    assert results == [case["expected"] for case in cases]


def test_price_matches_exact_budget_boundaries_and_unknown():
    results = run_javascript(
        "["
        "priceMatches('p3k', 3000), priceMatches('p3k', 3001),"
        "priceMatches('p5k', 3001), priceMatches('p5k', 5000), priceMatches('p5k', 5001),"
        "priceMatches('p10k', 5001), priceMatches('p10k', 10000), priceMatches('p10k', 10001),"
        "priceMatches('p10kplus', 10001), priceMatches('unknown', null),"
        "priceMatches('unknown', 3000), priceMatches('', null)"
        "]",
        "priceMatches",
    )

    assert results == [
        True,
        False,
        True,
        True,
        False,
        True,
        True,
        False,
        True,
        True,
        False,
        True,
    ]

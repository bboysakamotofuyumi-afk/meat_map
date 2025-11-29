"""
Rule-based scoring to estimate how carnivore-friendly a store is.
"""
from __future__ import annotations

from typing import Iterable, List, Sequence, Tuple

from .models import StoreRecord

# スコア閾値はここで一元管理する（CLI などもこの基準でフィルタする前提）。
S_THRESHOLD = 80.0
A_THRESHOLD = 60.0
B_THRESHOLD = 45.0
MAX_SCORE = 100.0

GENRE_WEIGHTS: Sequence[Tuple[str, int]] = [
    ("焼肉", 35),
    ("ホルモン", 32),
    ("ステーキ", 30),
    ("鉄板焼", 28),
    ("シュラスコ", 30),
    ("ジンギスカン", 28),
    ("肉バル", 25),
    ("ローストビーフ", 25),
    ("しゃぶしゃぶ", 22),
    ("すき焼き", 22),
    ("バーベキュー", 20),
    ("焼き鳥", 26),
    ("焼きとん", 25),
    ("鶏料理", 24),
    ("牛タン", 24),
    ("もつ鍋", 22),
    ("水炊き", 22),
    ("ラム", 24),
    ("ジビエ", 24),
]

KEYWORD_WEIGHTS: Sequence[Tuple[str, int]] = [
    ("食べ放題", 12),
    ("肉盛り", 15),
    ("塊肉", 18),
    ("グリル", 10),
    ("熟成肉", 14),
    ("BBQ", 12),
    ("焼き鳥", 30),
    ("焼きとん", 26),
    ("串焼", 20),
    ("牛タン", 24),
    ("鶏料理", 22),
    ("水炊き", 22),
    ("もつ鍋", 22),
    ("しゃぶしゃぶ", 18),
    ("すき焼き", 18),
    ("サムギョプサル", 24),
    ("韓国焼肉", 22),
    ("シュラスコ", 26),
    ("アメリカンBBQ", 22),
    ("テキサスBBQ", 22),
    ("ポークリブ", 20),
    ("ラムチョップ", 22),
    ("羊肉串", 20),
    ("タンドリーチキン", 18),
    ("シークカバブ", 18),
]


def score_store(record: StoreRecord) -> StoreRecord:
    score = compute_score(record)
    record.carnivore_score = score
    record.carnivore_rank = rank_score(score)
    if not record.notes:
        top_genres = ", ".join(record.genres[:2])
        record.notes = f"主ジャンル: {top_genres}" if top_genres else None
    return record


def score_records(records: Iterable[StoreRecord]) -> List[StoreRecord]:
    return [score_store(record) for record in records]


def compute_score(record: StoreRecord) -> float:
    score = 0.0
    text = _aggregate_text(record)
    for keyword, weight in GENRE_WEIGHTS:
        if any(keyword in genre for genre in record.genres):
            score += weight
    for keyword, weight in KEYWORD_WEIGHTS:
        if keyword in text:
            score += weight
    if record.rating:
        score += (record.rating - 3.0) * 8
    if record.review_count:
        score += min(record.review_count, 500) * 0.05
    return min(score, MAX_SCORE)


def rank_score(score: float) -> str:
    if score >= S_THRESHOLD:
        return "S"
    if score >= A_THRESHOLD:
        return "A"
    if score >= B_THRESHOLD:
        return "B"
    return "C"


def _aggregate_text(record: StoreRecord) -> str:
    parts = [record.name or "", record.description or ""]
    return " ".join(part for part in parts if part)

from meatmap import scoring
from meatmap.models import StoreRecord


def build_store(**overrides) -> StoreRecord:
    base = dict(
        store_id="s1",
        name="焼肉キング",
        address="東京都渋谷区",
        lat=35.0,
        lng=139.0,
        phone=None,
        genres=["焼肉", "ホルモン"],
        description="塊肉と食べ放題コース",
        budget_lunch=None,
        budget_dinner=5000,
        rating=4.6,
        review_count=200,
        sources={"hotpepper"},
        external_ids={"hotpepper": "p1"},
        url=None,
        notes=None,
    )
    base.update(overrides)
    return StoreRecord(**base)


def test_score_store_assigns_rank():
    store = build_store()
    scored = scoring.score_store(store)
    assert scored.carnivore_score is not None
    assert scored.carnivore_rank in {"S", "A"}
    assert "主ジャンル" in (scored.notes or "")


def test_rank_score_thresholds():
    assert scoring.rank_score(85) == "S"
    assert scoring.rank_score(65) == "A"
    assert scoring.rank_score(50) == "B"
    assert scoring.rank_score(30) == "C"


def test_keyword_scoring_boosts_yakitori():
    store = build_store(genres=["居酒屋"], description="炭火焼き鳥専門店")
    scored = scoring.score_store(store)
    assert scored.carnivore_rank in {"A", "B"}

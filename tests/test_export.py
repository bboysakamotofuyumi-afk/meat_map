from pathlib import Path

from meatmap import export
from meatmap.models import StoreRecord


def build_store(store_id: str, rank: str) -> StoreRecord:
    return StoreRecord(
        store_id=store_id,
        name=f"店{store_id}",
        address="東京都港区",
        lat=35.0,
        lng=139.0,
        phone=None,
        genres=["焼肉"],
        description=None,
        budget_lunch=None,
        budget_dinner=None,
        rating=4.0,
        review_count=50,
        sources={"hotpepper"},
        external_ids={"hotpepper": store_id},
        carnivore_score=80.0 if rank == "S" else 55.0,
        carnivore_rank=rank,
        url=None,
        notes=None,
    )


def test_export_filters_rank(tmp_path):
    records = [build_store("s1", "S"), build_store("b1", "B")]
    output = tmp_path / "meatmap.csv"
    export.export_to_csv(records, output, include_ranks=("S",))
    text = output.read_text(encoding="utf-8")
    assert "店s1" in text
    assert "店b1" not in text

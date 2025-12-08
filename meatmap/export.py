"""
CSV exporter compatible with Google My Maps.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable, List, Optional, Sequence
from datetime import datetime, timezone

from .models import StoreRecord

DEFAULT_FIELDS: Sequence[str] = [
    "name",
    "address",
    "lat",
    "lng",
    "carnivore_rank",
    "carnivore_score",
    "genre",
    "rating",
    "review_count",
    "sources",
    "url",
    "notes",
    "budget_lunch",
    "budget_dinner",
    "avg_dinner_budget",
]


def export_to_csv(
    records: Iterable[StoreRecord],
    output_path: Path,
    include_ranks: Optional[Sequence[str]] = ("S", "A", "B"),
    encoding: str = "utf-8",
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    filtered = [record for record in records if not include_ranks or record.carnivore_rank in include_ranks]
    with output_path.open("w", newline="", encoding=encoding) as fh:
        writer = csv.DictWriter(fh, fieldnames=DEFAULT_FIELDS)
        # メタ情報をコメント行として付与（Google My Maps はヘッダー以外を無視するので上部に置く）
        timestamp = datetime.now(timezone.utc).isoformat()
        fh.write(f"# generated_at_utc={timestamp}\n")
        fh.write(f"# total_records={len(filtered)}\n")
        writer.writeheader()
        for record in filtered:
            writer.writerow(record_to_row(record))
    return output_path


def record_to_row(record: StoreRecord) -> dict:
    return {
        "name": record.name,
        "address": record.address,
        "lat": record.lat,
        "lng": record.lng,
        "carnivore_rank": record.carnivore_rank,
        "carnivore_score": record.carnivore_score,
        "genre": ", ".join(record.genres),
        "rating": record.rating,
        "review_count": record.review_count,
        "sources": ", ".join(sorted(record.sources)),
        "url": record.url,
        "notes": record.notes,
        "budget_lunch": record.budget_lunch,
        "budget_dinner": record.budget_dinner,
        "avg_dinner_budget": record.budget_dinner if record.budget_dinner is not None else record.budget_lunch,
    }

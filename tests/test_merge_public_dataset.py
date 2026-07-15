from __future__ import annotations

import re

import pytest

from scripts.merge_public_dataset import (
    CsvDataset,
    MergeError,
    legacy_id_from_attributes,
    merge_datasets,
    render_csv,
)


CURRENT_DATE = "2026-07-15"
LEGACY_DATE = "2025-12-06"
GENERATED_AT = "2026-07-15T12:00:00+00:00"


def dataset(rows: list[dict[str, str]], *, current: bool) -> CsvDataset:
    generated = f"{CURRENT_DATE if current else LEGACY_DATE}T00:00:00+00:00"
    return CsvDataset(
        metadata={"generated_at_utc": generated, "total_records": str(len(rows))},
        fieldnames=list(rows[0]) if rows else [],
        rows=rows,
    )


def row(
    name: str,
    *,
    url: str,
    sources: str,
    lat: str = "35.680000",
    lng: str = "139.760000",
    address: str = "東京都千代田区丸の内1-1-1",
    **extra: str,
) -> dict[str, str]:
    values = {
        "name": name,
        "address": address,
        "lat": lat,
        "lng": lng,
        "carnivore_rank": "A",
        "carnivore_score": "67",
        "genre": "焼肉",
        "rating": "",
        "review_count": "",
        "sources": sources,
        "url": url,
        "notes": "",
        "lunch_budget_min": "",
        "lunch_budget_max": "",
        "dinner_budget_min": "",
        "dinner_budget_max": "",
        "budget_lunch": "",
        "budget_dinner": "",
        "avg_dinner_budget": "",
    }
    values.update(extra)
    return values


def hp_url(identifier: str) -> str:
    return f"https://www.hotpepper.jp/str{identifier}/"


def old_validation_url(identifier: str) -> str:
    return f"https://tabelog.com/tokyo/A0000/A000000/{identifier}/"


def legacy_token(number: int) -> str:
    return f"legacy_{number:020x}"


def merge(current_rows: list[dict[str, str]], legacy_rows: list[dict[str, str]]):
    return merge_datasets(
        dataset(current_rows, current=True),
        dataset(legacy_rows, current=False),
        generated_at=GENERATED_AT,
    )


def test_same_hotpepper_id_uses_fresh_values_and_keeps_legacy_ranges() -> None:
    legacy = row(
        "旧店名",
        url=hp_url("J000000001"),
        sources="hotpepper",
        dinner_budget_min="3000",
        dinner_budget_max="3999",
    )
    current = row(
        "新店名",
        url=hp_url("J000000001"),
        sources="hotpepper",
        budget_dinner="4500",
    )

    result = merge([current], [legacy])

    assert len(result.rows) == 1
    merged = result.rows[0]
    assert merged["name"] == "新店名"
    assert merged["budget_dinner"] == "4500"
    assert merged["dinner_budget_min"] == "3000"
    assert merged["sources"] == "hotpepper"
    assert merged["data_status"] == "current"
    assert result.stats.hotpepper_id_updates == 1


def test_old_validation_fields_migrate_to_opaque_legacy_id_without_public_url() -> None:
    legacy = row(
        "旧ローカル店",
        url=old_validation_url("13000011"),
        sources="tabelog",
        tabelog_url=old_validation_url("13000011"),
    )

    result = merge([], [legacy])

    migrated = result.rows[0]
    assert migrated["legacy_id"] == legacy_id_from_attributes(legacy)
    assert re.fullmatch(r"legacy_[0-9a-f]{20}", migrated["legacy_id"])
    assert migrated["sources"] == "legacy_local"
    assert migrated["url"] == ""
    assert "tabelog_url" not in migrated
    assert "tabelog" not in render_csv(result).lower()
    assert result.stats.migrated_legacy_ids == 1


def test_hotpepper_row_missing_from_fresh_is_retained_as_unverified() -> None:
    current = row("現行店", url=hp_url("J000000010"), sources="hotpepper")
    old_hp = row(
        "旧HP店",
        url=hp_url("J000000011"),
        sources="hotpepper",
        lng="139.80",
    )

    result = merge([current], [old_hp])

    assert len(result.rows) == 2
    retained = next(item for item in result.rows if item["name"] == "旧HP店")
    assert retained["sources"] == "hotpepper"
    assert retained["data_status"] == "legacy_unverified"
    assert retained["last_verified_at"] == LEGACY_DATE
    assert retained["url"] == hp_url("J000000011")


def test_malformed_hotpepper_url_is_rejected() -> None:
    malformed = row(
        "不正URL店",
        url="https://www.hotpepper.jp/strJ000000012evil/",
        sources="hotpepper",
    )

    with pytest.raises(MergeError, match="有効なURLがありません"):
        merge([malformed], [])


def test_mixed_old_row_keeps_hp_url_and_becomes_mixed_local_lineage() -> None:
    legacy = row(
        "混在店",
        url=hp_url("J000000020"),
        sources="hotpepper,tabelog",
        hotpepper_url=hp_url("J000000020"),
        tabelog_url=old_validation_url("13000020"),
    )
    current = row("混在店 最新", url=hp_url("J000000020"), sources="hotpepper")

    result = merge([current], [legacy])
    merged = result.rows[0]

    assert merged["sources"] == "hotpepper,legacy_local"
    assert merged["hotpepper_url"] == hp_url("J000000020")
    assert merged["legacy_id"] == legacy_id_from_attributes(legacy)
    assert merged["url"] == merged["hotpepper_url"]


def test_existing_legacy_id_is_preserved() -> None:
    identifier = legacy_token(21)
    legacy = row(
        "ID保持店",
        url="",
        sources="legacy_local",
        legacy_id=identifier,
    )

    result = merge([], [legacy])

    assert result.rows[0]["legacy_id"] == identifier
    assert result.stats.migrated_legacy_ids == 0


def test_normalized_name_and_nearby_one_to_one_rows_are_merged() -> None:
    current = row(
        "焼肉・テスト 店",
        url=hp_url("J000000030"),
        sources="hotpepper",
        lat="35.680050",
    )
    legacy = row(
        "焼肉テスト店",
        url="",
        sources="legacy_local",
        legacy_id=legacy_token(30),
        lat="35.680000",
    )

    result = merge([current], [legacy])

    assert len(result.rows) == 1
    assert result.rows[0]["sources"] == "hotpepper,legacy_local"
    assert result.stats.cross_source_matches == 1


def test_ambiguous_nearby_candidates_are_kept_separately() -> None:
    current = row("同名店", url=hp_url("J000000040"), sources="hotpepper")
    legacy_a = row(
        "同名店",
        url="",
        sources="legacy_local",
        legacy_id=legacy_token(40),
        lat="35.680010",
    )
    legacy_b = row(
        "同名店",
        url="",
        sources="legacy_local",
        legacy_id=legacy_token(41),
        lat="35.680020",
    )

    result = merge([current], [legacy_a, legacy_b])

    assert len(result.rows) == 3
    assert result.stats.cross_source_matches == 0
    assert result.stats.ambiguous_cross_source_candidates == 2


def test_reissued_id_matching_does_not_reuse_exactly_updated_current_row() -> None:
    exact_current = row("同名店", url=hp_url("J000000060"), sources="hotpepper")
    reissued_current = row(
        "同名店",
        url=hp_url("J000000061"),
        sources="hotpepper",
        lat="35.680020",
    )
    exact_legacy = row("同名店", url=hp_url("J000000060"), sources="hotpepper")
    old_identifier = row(
        "同名店",
        url=hp_url("J000000062"),
        sources="hotpepper",
        lat="35.680020",
    )

    result = merge([exact_current, reissued_current], [exact_legacy, old_identifier])

    assert len(result.rows) == 2
    assert result.stats.hotpepper_id_updates == 1
    assert result.stats.hotpepper_reissued_matches == 1


def test_hp_with_existing_legacy_id_does_not_consume_another_legacy_row() -> None:
    current = row("同名店", url=hp_url("J000000070"), sources="hotpepper")
    mixed_legacy = row(
        "同名店",
        url=hp_url("J000000070"),
        sources="hotpepper,legacy_local",
        hotpepper_url=hp_url("J000000070"),
        legacy_id=legacy_token(70),
    )
    separate_legacy = row(
        "同名店",
        url="",
        sources="legacy_local",
        legacy_id=legacy_token(71),
        lat="35.680010",
    )

    result = merge([current], [mixed_legacy, separate_legacy])

    assert len(result.rows) == 2
    assert {item["legacy_id"] for item in result.rows} == {
        legacy_token(70),
        legacy_token(71),
    }


def test_invalid_concatenated_public_budgets_are_removed() -> None:
    current = row(
        "予算異常店",
        url=hp_url("J000000080"),
        sources="hotpepper",
        budget_dinner="25004000",
        avg_dinner_budget="25004000",
    )

    result = merge([current], [])

    assert result.rows[0]["budget_dinner"] == ""
    assert result.rows[0]["avg_dinner_budget"] == ""
    assert result.stats.sanitized_budget_values == 2


def test_merged_output_is_idempotent_as_next_legacy_input() -> None:
    current = row("冪等店", url=hp_url("J000000050"), sources="hotpepper")
    legacy = row(
        "冪等店",
        url=old_validation_url("13000050"),
        sources="tabelog",
        lat="35.680010",
    )
    first = merge([current], [legacy])
    integrated_legacy = CsvDataset(
        metadata=first.metadata,
        fieldnames=list(first.rows[0]),
        rows=first.rows,
    )

    second = merge_datasets(
        dataset([current], current=True),
        integrated_legacy,
        generated_at=GENERATED_AT,
    )

    assert second.rows == first.rows
    assert second.metadata == first.metadata


def test_legacy_hash_collision_stops_migration() -> None:
    first = row(
        "衝突店",
        url=old_validation_url("13000101"),
        sources="tabelog",
    )
    second = row(
        "衝突店",
        url=old_validation_url("13000102"),
        sources="tabelog",
    )

    with pytest.raises(MergeError, match="legacy_id.*重複|出典IDが重複"):
        merge([], [first, second])


def test_new_legacy_local_row_without_id_is_rejected() -> None:
    legacy = row("ID欠落店", url="", sources="legacy_local")

    with pytest.raises(MergeError, match="legacy_idがありません"):
        merge([], [legacy])

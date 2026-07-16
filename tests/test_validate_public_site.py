from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

from scripts import validate_public_site as validator


FIELDS = [
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
    "lunch_budget_min",
    "lunch_budget_max",
    "dinner_budget_min",
    "dinner_budget_max",
    "budget_lunch",
    "budget_dinner",
    "avg_dinner_budget",
    "hotpepper_url",
    "legacy_id",
    "data_status",
    "last_verified_at",
]


def valid_map_text() -> str:
    genre_entries = "\n".join(
        f'      "{genre}": {{ icon: "肉" }},'
        for genre in validator.REQUIRED_GENRE_META_KEYS
    )
    return f"""
<!doctype html>
<html lang="ja">
  <body>
    <select id="budget-filter"></select>
    <input id="current-only-filter" type="checkbox">
    <button id="filter-reset" type="button">解除</button>
    <output id="filter-result-count"></output>
    <script>
      const genreMeta = Object.freeze({{
{genre_entries}
      }});
      function classifyGenre(row) {{
        const searchText = `${{row.name ?? ""}} ${{row.genre ?? ""}}`;
        return searchText.includes("焼肉") ? "焼肉" : "その他";
      }}
      const candidate = new URL("./output/meatmap.csv", location.href);
      if (candidate.origin === location.origin) {{
        console.log("data_status last_verified_at legacy_unverified legacy_local");
      }}
    </script>
  </body>
</html>
"""


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(f"# generated_at_utc={datetime.now(timezone.utc).isoformat()}\n")
        handle.write("# source_scope=hotpepper,legacy_local\n")
        handle.write(f"# total_records={len(rows)}\n")
        handle.write("# current_snapshot_at=2026-07-15\n")
        handle.write("# legacy_snapshot_at=2025-12-06\n")
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def base_row(**overrides: str) -> dict[str, str]:
    row = {field: "" for field in FIELDS}
    row.update(
        {
            "name": "焼肉テスト",
            "address": "東京都新宿区",
            "lat": "35.69",
            "lng": "139.70",
            "genre": "焼肉",
            "sources": "hotpepper",
            "url": "https://www.hotpepper.jp/strJ000000001/",
            "hotpepper_url": "https://www.hotpepper.jp/strJ000000001/",
            "data_status": "current",
            "last_verified_at": "2026-07-15",
        }
    )
    row.update(overrides)
    return row


def test_validate_csv_accepts_current_and_legacy_local_sources(tmp_path, monkeypatch):
    public_csv = tmp_path / "meatmap.csv"
    write_csv(
        public_csv,
        [
            base_row(),
            base_row(
                name="ステーキテスト",
                sources="legacy_local",
                url="",
                hotpepper_url="",
                legacy_id="legacy_00000000000000000001",
                data_status="legacy_unverified",
                last_verified_at="2025-12-06",
            ),
        ],
    )
    monkeypatch.setattr(validator, "PUBLIC_CSV", public_csv)
    monkeypatch.setattr(validator, "MINIMUM_PUBLIC_RECORDS", 2)
    monkeypatch.setattr(validator, "MINIMUM_LEGACY_IDS", 1)

    errors: list[str] = []
    warnings: list[str] = []
    validator.validate_csv(errors, warnings)

    assert errors == []


def test_validate_csv_accepts_mixed_row_with_hp_as_primary_url(tmp_path, monkeypatch):
    public_csv = tmp_path / "meatmap.csv"
    write_csv(
        public_csv,
        [
            base_row(
                sources="hotpepper,legacy_local",
                legacy_id="legacy_00000000000000000002",
            ),
            base_row(
                name="旧ローカル店",
                sources="legacy_local",
                url="",
                hotpepper_url="",
                legacy_id="legacy_00000000000000000003",
                data_status="legacy_unverified",
                last_verified_at="2025-12-06",
            ),
        ],
    )
    monkeypatch.setattr(validator, "PUBLIC_CSV", public_csv)
    monkeypatch.setattr(validator, "MINIMUM_PUBLIC_RECORDS", 2)
    monkeypatch.setattr(validator, "MINIMUM_LEGACY_IDS", 2)

    errors: list[str] = []
    validator.validate_csv(errors, [])

    assert errors == []


def test_validate_csv_rejects_data_loss_and_duplicate_source_url(tmp_path, monkeypatch):
    public_csv = tmp_path / "meatmap.csv"
    write_csv(public_csv, [base_row(), base_row(name="重複店")])
    monkeypatch.setattr(validator, "PUBLIC_CSV", public_csv)
    monkeypatch.setattr(validator, "MINIMUM_PUBLIC_RECORDS", 3)
    monkeypatch.setattr(validator, "MINIMUM_LEGACY_IDS", 0)

    errors: list[str] = []
    validator.validate_csv(errors, [])

    assert any("最低維持件数=3" in error for error in errors)
    assert any("Hot Pepper 店舗IDが重複" in error for error in errors)


def test_validate_csv_rejects_legacy_only_url_and_duplicate_id(tmp_path, monkeypatch):
    public_csv = tmp_path / "meatmap.csv"
    identifier = "legacy_00000000000000000004"
    write_csv(
        public_csv,
        [
            base_row(),
            base_row(
                name="旧店A",
                sources="legacy_local",
                url="https://example.com/store/a",
                hotpepper_url="",
                legacy_id=identifier,
                data_status="legacy_unverified",
            ),
            base_row(
                name="旧店B",
                sources="legacy_local",
                url="",
                hotpepper_url="",
                legacy_id=identifier,
                data_status="legacy_unverified",
            ),
        ],
    )
    monkeypatch.setattr(validator, "PUBLIC_CSV", public_csv)
    monkeypatch.setattr(validator, "MINIMUM_PUBLIC_RECORDS", 3)
    monkeypatch.setattr(validator, "MINIMUM_LEGACY_IDS", 1)

    errors: list[str] = []
    validator.validate_csv(errors, [])

    assert any("旧ローカルのみの行に主URL" in error for error in errors)
    assert any("legacy_idが重複" in error for error in errors)


def test_validate_csv_rejects_local_validation_service_token(tmp_path, monkeypatch):
    public_csv = tmp_path / "meatmap.csv"
    write_csv(
        public_csv,
        [
            base_row(notes="食べログの検証情報は公開しない"),
            base_row(
                name="旧店",
                sources="legacy_local",
                url="",
                hotpepper_url="",
                legacy_id="legacy_00000000000000000005",
                data_status="legacy_unverified",
            ),
        ],
    )
    monkeypatch.setattr(validator, "PUBLIC_CSV", public_csv)
    monkeypatch.setattr(validator, "MINIMUM_PUBLIC_RECORDS", 2)
    monkeypatch.setattr(validator, "MINIMUM_LEGACY_IDS", 1)

    errors: list[str] = []
    validator.validate_csv(errors, [])

    assert any("ローカル検証用サービス" in error for error in errors)


def test_validate_csv_rejects_legacy_lineage_loss(tmp_path, monkeypatch):
    public_csv = tmp_path / "meatmap.csv"
    write_csv(
        public_csv,
        [
            base_row(),
            base_row(
                name="旧ローカル店",
                sources="legacy_local",
                url="",
                hotpepper_url="",
                legacy_id="legacy_00000000000000000006",
                data_status="legacy_unverified",
            ),
        ],
    )
    monkeypatch.setattr(validator, "PUBLIC_CSV", public_csv)
    monkeypatch.setattr(validator, "MINIMUM_PUBLIC_RECORDS", 2)
    monkeypatch.setattr(validator, "MINIMUM_LEGACY_IDS", 2)

    errors: list[str] = []
    validator.validate_csv(errors, [])

    assert any("legacy_id件数=1 が最低維持件数=2" in error for error in errors)


def test_validate_public_text_rejects_tabelog_domain_anywhere_in_docs(tmp_path, monkeypatch):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "map_demo.html").write_text(valid_map_text(), encoding="utf-8")
    (docs_dir / "memo.md").write_text("https://tabelog.com/example", encoding="utf-8")
    monkeypatch.setattr(validator, "ROOT", tmp_path)
    monkeypatch.setattr(validator, "DOCS_DIR", docs_dir)

    errors: list[str] = []
    validator.validate_public_text(errors)

    assert any("tabelog.com" in error for error in errors)


def test_validate_public_text_accepts_genre_and_filter_contract(tmp_path, monkeypatch):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "map_demo.html").write_text(valid_map_text(), encoding="utf-8")
    monkeypatch.setattr(validator, "ROOT", tmp_path)
    monkeypatch.setattr(validator, "DOCS_DIR", docs_dir)

    errors: list[str] = []
    validator.validate_public_text(errors)

    assert errors == []


def test_validate_public_text_rejects_genre_filter_and_bounds_regressions(tmp_path, monkeypatch):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    invalid_map = (
        valid_map_text()
        .replace('"ジンギスカン": { icon: "肉" },', "")
        .replace('id="budget-filter"', 'id="removed-budget-filter"')
        .replace('row.name ?? ""', 'row.title ?? ""')
        .replace(
            'console.log("data_status last_verified_at legacy_unverified legacy_local");',
            'console.log("data_status last_verified_at legacy_unverified legacy_local");\n'
            "        bounds.contains(marker.getLatLng());",
        )
    )
    (docs_dir / "map_demo.html").write_text(invalid_map, encoding="utf-8")
    monkeypatch.setattr(validator, "ROOT", tmp_path)
    monkeypatch.setattr(validator, "DOCS_DIR", docs_dir)

    errors: list[str] = []
    validator.validate_public_text(errors)

    assert any("genreMeta に ジンギスカン 分類" in error for error in errors)
    assert any("予算フィルター (#budget-filter)" in error for error in errors)
    assert any("店名 row.name を分類に使用" in error for error in errors)
    assert any("bounds.contains(marker.getLatLng())" in error for error in errors)

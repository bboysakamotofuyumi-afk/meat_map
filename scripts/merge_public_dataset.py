#!/usr/bin/env python3
"""Fresh Hot Pepper データと前回公開データを安全に増分統合する。

ネットワークアクセスやスクレイピングは行わない。旧形式の公開CSVに含まれる
検証用サービスの識別子やURLは公開出力へ引き継がず、非サービス属性から作る
匿名 ``legacy_id`` へ一度だけ移行する。以後は23列CSVを legacy 入力に再利用する。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import math
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_CSV_GIT_PATH = "docs/output/meatmap.csv"
DEFAULT_MINIMUM_RECORDS = 8_444
DEFAULT_MATCH_RADIUS_METERS = 50.0
PUBLIC_BUDGET_FIELDS = ("budget_lunch", "budget_dinner", "avg_dinner_budget")
MAX_PUBLIC_BUDGET = 100_000

BASE_FIELDS = [
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
]
OUTPUT_FIELDS = BASE_FIELDS + [
    "hotpepper_url",
    "legacy_id",
    "data_status",
    "last_verified_at",
]

HOTPEPPER_ID_RE = re.compile(r"/str(J\d+)(?:/|$)", re.IGNORECASE)
LEGACY_ID_RE = re.compile(r"^legacy_[0-9a-f]{20}$")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
LOCAL_VALIDATION_SERVICE_RE = re.compile(r"(?:tabelog|食べログ)", re.IGNORECASE)
LEGACY_HASH_FIELDS = ("name", "address", "lat", "lng", "genre")


class MergeError(ValueError):
    """入力不正や安全条件違反。"""


@dataclass(frozen=True)
class CsvDataset:
    metadata: dict[str, str]
    fieldnames: list[str]
    rows: list[dict[str, str]]


@dataclass
class MergeStats:
    current_input: int = 0
    legacy_input: int = 0
    hotpepper_id_updates: int = 0
    hotpepper_reissued_matches: int = 0
    cross_source_matches: int = 0
    migrated_legacy_ids: int = 0
    ambiguous_reissue_candidates: int = 0
    ambiguous_cross_source_candidates: int = 0
    sanitized_budget_values: int = 0


@dataclass(frozen=True)
class MergeResult:
    metadata: dict[str, str]
    rows: list[dict[str, str]]
    stats: MergeStats


def parse_csv_text(
    text: str,
    *,
    label: str,
    required_fields: set[str] | None = None,
) -> CsvDataset:
    metadata: dict[str, str] = {}
    lines = text.splitlines()
    header_index = 0
    for header_index, line in enumerate(lines):
        if not line.startswith("#"):
            break
        key, separator, value = line.removeprefix("#").strip().partition("=")
        if separator:
            metadata[key.strip()] = value.strip()
    else:
        raise MergeError(f"{label}: CSVヘッダーがありません")

    csv_text = "\n".join(lines[header_index:])
    reader = csv.DictReader(io.StringIO(csv_text))
    fieldnames = list(reader.fieldnames or [])
    if not fieldnames:
        raise MergeError(f"{label}: CSVヘッダーがありません")
    required = required_fields or {"name", "address", "lat", "lng", "sources", "url"}
    missing = sorted(required - set(fieldnames))
    if missing:
        raise MergeError(f"{label}: 必須列が不足しています: {', '.join(missing)}")

    rows: list[dict[str, str]] = []
    for line_number, raw in enumerate(reader, start=header_index + 2):
        if raw.get(None):
            raise MergeError(f"{label}:{line_number}: 列数がヘッダーと一致しません")
        row = {key: (value or "").strip() for key, value in raw.items() if key is not None}
        if not row.get("name"):
            raise MergeError(f"{label}:{line_number}: 店名が空です")
        rows.append(row)
    return CsvDataset(metadata=metadata, fieldnames=fieldnames, rows=rows)


def read_csv_path(path: Path) -> CsvDataset:
    return parse_csv_text(path.read_text(encoding="utf-8-sig"), label=str(path))


def git_show_text(ref: str, git_path: str) -> str:
    completed = subprocess.run(
        ["git", "show", f"{ref}:{git_path}"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or "git show に失敗しました"
        raise MergeError(f"{ref}:{git_path}: {detail}")
    return completed.stdout


def read_csv_ref(ref: str, git_path: str = PUBLIC_CSV_GIT_PATH) -> CsvDataset:
    return parse_csv_text(git_show_text(ref, git_path), label=f"{ref}:{git_path}")


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value or "").casefold()
    return "".join(character for character in normalized if character.isalnum())


def hotpepper_id_from_url(value: str) -> str | None:
    parsed = urlsplit((value or "").strip())
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in {
        "hotpepper.jp",
        "www.hotpepper.jp",
    }:
        return None
    match = HOTPEPPER_ID_RE.search(parsed.path)
    return match.group(1).upper() if match else None


def valid_hotpepper_url(value: str) -> str:
    value = (value or "").strip()
    return value if hotpepper_id_from_url(value) else ""


def source_tokens(value: str) -> set[str]:
    return {token.strip().lower() for token in (value or "").split(",") if token.strip()}


def valid_legacy_id(value: str) -> str:
    value = (value or "").strip().lower()
    return value if LEGACY_ID_RE.fullmatch(value) else ""


def legacy_id_from_attributes(row: dict[str, str]) -> str:
    """第三者サービスの識別子を使わず、公開可能属性から匿名IDを作る。"""
    seed = "\x1f".join(normalize_text(row.get(field, "")) for field in LEGACY_HASH_FIELDS)
    if not normalize_text(row.get("name", "")) or not normalize_text(row.get("address", "")):
        raise MergeError("legacy_id生成には店名と住所が必要です")
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:20]
    return f"legacy_{digest}"


def source_string(row: dict[str, str]) -> str:
    has_hotpepper = bool(row.get("hotpepper_url"))
    raw_legacy_id = (row.get("legacy_id") or "").strip()
    if raw_legacy_id and not valid_legacy_id(raw_legacy_id):
        raise MergeError(f"legacy_idの形式が不正です: {raw_legacy_id!r}")
    has_legacy = bool(raw_legacy_id)
    if has_hotpepper and has_legacy:
        return "hotpepper,legacy_local"
    if has_hotpepper:
        return "hotpepper"
    if has_legacy:
        return "legacy_local"
    raise MergeError(f"データ系統を特定できない行です: {row.get('name', '')}")


def metadata_date(metadata: dict[str, str], key: str = "generated_at_utc") -> str:
    raw = metadata.get(key, "").strip()
    if ISO_DATE_RE.fullmatch(raw):
        return raw
    if raw:
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            pass
    raise MergeError(f"{key} から検証日を取得できません: {raw!r}")


def coordinates(row: dict[str, str]) -> tuple[float, float]:
    try:
        lat = float(row.get("lat", ""))
        lng = float(row.get("lng", ""))
    except ValueError as error:
        raise MergeError(f"緯度経度が数値ではありません: {row.get('name', '')}") from error
    if not (math.isfinite(lat) and math.isfinite(lng)):
        raise MergeError(f"緯度経度が有限値ではありません: {row.get('name', '')}")
    if not (24.0 <= lat <= 36.0 and 138.0 <= lng <= 154.0):
        raise MergeError(f"東京の検査範囲外です: {row.get('name', '')} ({lat}, {lng})")
    return lat, lng


def distance_meters(left: dict[str, str], right: dict[str, str]) -> float:
    left_lat, left_lng = coordinates(left)
    right_lat, right_lng = coordinates(right)
    phi1 = math.radians(left_lat)
    phi2 = math.radians(right_lat)
    delta_phi = math.radians(right_lat - left_lat)
    delta_lambda = math.radians(right_lng - left_lng)
    value = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return 6_371_000.0 * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))


def copy_output_row(row: dict[str, str]) -> dict[str, str]:
    return {field: (row.get(field) or "").strip() for field in OUTPUT_FIELDS}


def prepare_legacy_rows(
    dataset: CsvDataset,
    *,
    stats: MergeStats,
) -> list[dict[str, str]]:
    snapshot_date = dataset.metadata.get("legacy_snapshot_at") or metadata_date(dataset.metadata)
    prepared: list[dict[str, str]] = []
    for original in dataset.rows:
        row = copy_output_row(original)
        declared_sources = source_tokens(original.get("sources", ""))
        unknown_sources = declared_sources - {"hotpepper", "legacy_local", "tabelog"}
        if unknown_sources:
            raise MergeError(
                f"legacy入力に未対応のsourcesがあります: {', '.join(sorted(unknown_sources))}"
            )
        primary_url = original.get("url", "")
        row["hotpepper_url"] = valid_hotpepper_url(
            original.get("hotpepper_url", "") or primary_url
        )

        raw_legacy_id = (original.get("legacy_id") or "").strip()
        if raw_legacy_id and not valid_legacy_id(raw_legacy_id):
            raise MergeError(f"legacy_idの形式が不正です: {raw_legacy_id!r}")
        row["legacy_id"] = valid_legacy_id(raw_legacy_id)

        # 旧公開形式の検証用サービス名・URLは、存在判定にだけ使う。
        # IDの生成には店名・住所・座標・ジャンル以外を一切含めない。
        old_validation_marker = "tabelog" in declared_sources or any(
            "tabelog" in (value or "").lower()
            for value in (primary_url, original.get("tabelog_url", ""))
        ) or bool(
            (original.get("tabelog_id") or "").strip()
        )
        if not row["legacy_id"] and old_validation_marker:
            row["legacy_id"] = legacy_id_from_attributes(original)
            stats.migrated_legacy_ids += 1
        elif not row["legacy_id"] and "legacy_local" in declared_sources:
            raise MergeError(f"legacy_local 行にlegacy_idがありません: {row['name']}")

        if "hotpepper" in declared_sources and not row["hotpepper_url"]:
            raise MergeError(f"Hot Pepper行に有効なURLがありません: {row['name']}")
        row["sources"] = source_string(row)
        row["url"] = row["hotpepper_url"]
        row["data_status"] = "legacy_unverified"
        prior_verified = original.get("last_verified_at", "").strip()
        row["last_verified_at"] = (
            prior_verified if ISO_DATE_RE.fullmatch(prior_verified) else snapshot_date
        )
        coordinates(row)
        prepared.append(row)
    return prepared


def prepare_current_rows(dataset: CsvDataset) -> list[dict[str, str]]:
    snapshot_date = metadata_date(dataset.metadata)
    prepared: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for original in dataset.rows:
        row = copy_output_row(original)
        primary_url = original.get("hotpepper_url") or original.get("url", "")
        row["hotpepper_url"] = valid_hotpepper_url(primary_url)
        if not row["hotpepper_url"]:
            raise MergeError(f"fresh Hot Pepper入力に有効なURLがありません: {row['name']}")
        identifier = hotpepper_id_from_url(row["hotpepper_url"])
        if identifier in seen_ids:
            raise MergeError(f"fresh Hot Pepper入力で店舗IDが重複しています: {identifier}")
        seen_ids.add(identifier or "")
        row["legacy_id"] = ""
        row["sources"] = "hotpepper"
        row["url"] = row["hotpepper_url"]
        row["data_status"] = "current"
        row["last_verified_at"] = snapshot_date
        coordinates(row)
        prepared.append(row)
    return prepared


def prefer_overlay(base: dict[str, str], preferred: dict[str, str]) -> dict[str, str]:
    """base のデータ系統を残し、preferred の最新共通情報を優先する。"""
    merged = copy_output_row(base)
    for field in BASE_FIELDS:
        if field in {"sources", "url", "lunch_budget_min", "lunch_budget_max", "dinner_budget_min", "dinner_budget_max"}:
            continue
        value = preferred.get(field, "").strip()
        if value:
            merged[field] = value
    merged["hotpepper_url"] = preferred.get("hotpepper_url") or base.get("hotpepper_url", "")
    merged["legacy_id"] = preferred.get("legacy_id") or base.get("legacy_id", "")
    merged["sources"] = source_string(merged)
    merged["url"] = merged["hotpepper_url"]
    if preferred.get("data_status") == "current" or base.get("data_status") == "current":
        merged["data_status"] = "current"
    else:
        merged["data_status"] = "legacy_unverified"
    dates = [
        value
        for value in (base.get("last_verified_at", ""), preferred.get("last_verified_at", ""))
        if ISO_DATE_RE.fullmatch(value)
    ]
    merged["last_verified_at"] = max(dates) if dates else ""
    return merged


def sanitize_public_budgets(row: dict[str, str], *, stats: MergeStats) -> None:
    """旧処理で連結された異常な金額を公開値として引き継がない。"""
    for field in PUBLIC_BUDGET_FIELDS:
        value = (row.get(field) or "").strip()
        if not value:
            continue
        try:
            amount = int(float(value))
        except (TypeError, ValueError, OverflowError):
            amount = 0
        if not (0 < amount <= MAX_PUBLIC_BUDGET):
            row[field] = ""
            stats.sanitized_budget_values += 1
        else:
            row[field] = str(amount)


def unique_id_index(
    rows: Sequence[dict[str, str]],
    id_getter,
    *,
    label: str,
) -> dict[str, int]:
    result: dict[str, int] = {}
    for index, row in enumerate(rows):
        identifier = id_getter(row)
        if not identifier:
            continue
        if identifier in result:
            raise MergeError(f"{label}で出典IDが重複しています: {identifier}")
        result[identifier] = index
    return result


def hp_id(row: dict[str, str]) -> str | None:
    return hotpepper_id_from_url(row.get("hotpepper_url", ""))


def legacy_id(row: dict[str, str]) -> str | None:
    return valid_legacy_id(row.get("legacy_id", "")) or None


def one_to_one_near_matches(
    left_rows: Sequence[dict[str, str]],
    right_rows: Sequence[dict[str, str]],
    *,
    radius_meters: float,
) -> tuple[list[tuple[int, int]], int]:
    right_by_name: dict[str, list[int]] = defaultdict(list)
    for right_index, row in enumerate(right_rows):
        right_by_name[normalize_text(row.get("name", ""))].append(right_index)

    candidates: list[tuple[int, int]] = []
    left_degree: Counter[int] = Counter()
    right_degree: Counter[int] = Counter()
    for left_index, left in enumerate(left_rows):
        name_key = normalize_text(left.get("name", ""))
        if not name_key:
            continue
        for right_index in right_by_name.get(name_key, []):
            if distance_meters(left, right_rows[right_index]) <= radius_meters:
                candidates.append((left_index, right_index))
                left_degree[left_index] += 1
                right_degree[right_index] += 1

    matches = [
        pair
        for pair in candidates
        if left_degree[pair[0]] == 1 and right_degree[pair[1]] == 1
    ]
    ambiguous = sum(
        1
        for left_index, right_index in candidates
        if left_degree[left_index] > 1 or right_degree[right_index] > 1
    )
    return matches, ambiguous


def merge_datasets(
    current: CsvDataset,
    legacy: CsvDataset,
    *,
    radius_meters: float = DEFAULT_MATCH_RADIUS_METERS,
    generated_at: str | None = None,
) -> MergeResult:
    if radius_meters <= 0:
        raise MergeError("match radius は正数で指定してください")
    stats = MergeStats(current_input=len(current.rows), legacy_input=len(legacy.rows))
    current_rows = prepare_current_rows(current)
    legacy_rows = prepare_legacy_rows(legacy, stats=stats)

    current_by_id = unique_id_index(current_rows, hp_id, label="fresh Hot Pepper入力")
    unique_id_index(legacy_rows, hp_id, label="legacy Hot Pepper入力")
    unique_id_index(legacy_rows, legacy_id, label="旧ローカルデータ入力")

    # 同じ Hot Pepper ID は fresh を正とし、旧ローカルの系統IDだけを継承する。
    missing_legacy_hp: list[dict[str, str]] = []
    legacy_local_only: list[dict[str, str]] = []
    exact_current_indexes: set[int] = set()
    for legacy_row in legacy_rows:
        identifier = hp_id(legacy_row)
        if identifier and identifier in current_by_id:
            index = current_by_id[identifier]
            current_rows[index] = prefer_overlay(legacy_row, current_rows[index])
            exact_current_indexes.add(index)
            stats.hotpepper_id_updates += 1
        elif identifier:
            missing_legacy_hp.append(legacy_row)
        else:
            legacy_local_only.append(legacy_row)

    # Hot Pepper がIDを再発行した場合も、店名一致・50m以内・1対1だけを統合する。
    reissue_current_indexes = [
        index for index in range(len(current_rows)) if index not in exact_current_indexes
    ]
    reissue_current_rows = [current_rows[index] for index in reissue_current_indexes]
    reissue_matches, ambiguous = one_to_one_near_matches(
        missing_legacy_hp,
        reissue_current_rows,
        radius_meters=radius_meters,
    )
    stats.ambiguous_reissue_candidates = ambiguous
    matched_missing_indexes: set[int] = set()
    for legacy_index, candidate_index in reissue_matches:
        current_index = reissue_current_indexes[candidate_index]
        current_rows[current_index] = prefer_overlay(
            missing_legacy_hp[legacy_index], current_rows[current_index]
        )
        matched_missing_indexes.add(legacy_index)
        stats.hotpepper_reissued_matches += 1
    retained_legacy_hp = [
        row for index, row in enumerate(missing_legacy_hp) if index not in matched_missing_indexes
    ]

    hp_rows = current_rows + retained_legacy_hp

    # 旧ローカルのみの行との統合も、同じ厳格な1対1条件だけで行う。
    # 既にlegacy_idを持つHP行には別の旧ローカル行を消費させない。
    cross_hp_indexes = [index for index, row in enumerate(hp_rows) if not legacy_id(row)]
    cross_hp_rows = [hp_rows[index] for index in cross_hp_indexes]
    cross_matches, ambiguous = one_to_one_near_matches(
        cross_hp_rows,
        legacy_local_only,
        radius_meters=radius_meters,
    )
    stats.ambiguous_cross_source_candidates = ambiguous
    matched_legacy_indexes: set[int] = set()
    for candidate_index, legacy_index in cross_matches:
        hp_index = cross_hp_indexes[candidate_index]
        hp_rows[hp_index] = prefer_overlay(legacy_local_only[legacy_index], hp_rows[hp_index])
        matched_legacy_indexes.add(legacy_index)
        stats.cross_source_matches += 1
    retained_legacy = [
        row
        for index, row in enumerate(legacy_local_only)
        if index not in matched_legacy_indexes
    ]

    rows = hp_rows + retained_legacy
    for row in rows:
        sanitize_public_budgets(row, stats=stats)
    rows.sort(
        key=lambda row: (
            normalize_text(row.get("name", "")),
            normalize_text(row.get("address", "")),
            hp_id(row) or "",
            legacy_id(row) or "",
        )
    )
    validate_merged_rows(rows)
    expected_legacy_ids = {
        identifier for row in legacy_rows if (identifier := legacy_id(row))
    }
    output_legacy_ids = {identifier for row in rows if (identifier := legacy_id(row))}
    if output_legacy_ids != expected_legacy_ids:
        missing = sorted(expected_legacy_ids - output_legacy_ids)
        unexpected = sorted(output_legacy_ids - expected_legacy_ids)
        raise MergeError(
            "統合前後でlegacy_idが一致しません"
            f" (missing={missing[:3]}, unexpected={unexpected[:3]})"
        )

    current_snapshot = metadata_date(current.metadata)
    legacy_snapshot = legacy.metadata.get("legacy_snapshot_at") or metadata_date(legacy.metadata)
    if generated_at is None:
        generated_at = datetime.now(timezone.utc).isoformat()
    else:
        try:
            parsed_generated = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
        except ValueError as error:
            raise MergeError(f"generated_at の形式が不正です: {generated_at}") from error
        if parsed_generated.tzinfo is None:
            raise MergeError("generated_at にはタイムゾーンが必要です")

    metadata = {
        "generated_at_utc": generated_at,
        "source_scope": "hotpepper,legacy_local",
        "current_snapshot_at": current_snapshot,
        "legacy_snapshot_at": legacy_snapshot,
        "match_radius_meters": f"{radius_meters:g}",
        "total_records": str(len(rows)),
    }
    return MergeResult(metadata=metadata, rows=rows, stats=stats)


def validate_merged_rows(rows: Sequence[dict[str, str]]) -> None:
    seen_hotpepper: set[str] = set()
    seen_legacy: set[str] = set()
    allowed_statuses = {"current", "legacy_unverified"}
    for row in rows:
        if any(
            LOCAL_VALIDATION_SERVICE_RE.search(row.get(field) or "")
            for field in OUTPUT_FIELDS
        ):
            raise MergeError(
                f"公開行にローカル検証用サービスの識別子またはURLがあります: "
                f"{row.get('name', '')}"
            )
        coordinates(row)
        hp_identifier = hp_id(row)
        legacy_identifier = legacy_id(row)
        expected_sources = source_string(row)
        if row.get("sources") != expected_sources:
            raise MergeError(f"sourcesとデータ系統が一致しません: {row.get('name', '')}")
        expected_primary_url = row.get("hotpepper_url", "")
        if row.get("url") != expected_primary_url:
            raise MergeError(f"主URLがHot Pepper URLと一致しません: {row.get('name', '')}")
        if hp_identifier:
            if hp_identifier in seen_hotpepper:
                raise MergeError(f"統合後Hot Pepper IDが重複しています: {hp_identifier}")
            seen_hotpepper.add(hp_identifier)
        if legacy_identifier:
            if legacy_identifier in seen_legacy:
                raise MergeError(f"統合後legacy_idが重複しています: {legacy_identifier}")
            seen_legacy.add(legacy_identifier)
        if row.get("data_status") not in allowed_statuses:
            raise MergeError(f"data_statusが不正です: {row.get('data_status', '')}")
        if row.get("data_status") == "current" and not hp_identifier:
            raise MergeError(f"current行にHot Pepper IDがありません: {row.get('name', '')}")
        if not ISO_DATE_RE.fullmatch(row.get("last_verified_at", "")):
            raise MergeError(f"last_verified_atが不正です: {row.get('name', '')}")


def render_csv(result: MergeResult) -> str:
    buffer = io.StringIO(newline="")
    for key, value in result.metadata.items():
        buffer.write(f"# {key}={value}\n")
    writer = csv.DictWriter(buffer, fieldnames=OUTPUT_FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(result.rows)
    return buffer.getvalue()


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = (path.stat().st_mode & 0o777) if path.exists() else 0o644
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, mode)
        os.replace(temp_path, path)
        os.chmod(path, mode)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


def print_summary(result: MergeResult, *, output: Path | None) -> None:
    stats = result.stats
    status_counts = Counter(row["data_status"] for row in result.rows)
    source_counts = Counter(row["sources"] for row in result.rows)
    print(f"fresh_hotpepper_records={stats.current_input}")
    print(f"legacy_input_records={stats.legacy_input}")
    print(f"hotpepper_id_updates={stats.hotpepper_id_updates}")
    print(f"hotpepper_reissued_matches={stats.hotpepper_reissued_matches}")
    print(f"cross_source_matches={stats.cross_source_matches}")
    print(f"migrated_legacy_ids={stats.migrated_legacy_ids}")
    print(f"sanitized_budget_values={stats.sanitized_budget_values}")
    print(f"ambiguous_candidates_kept={stats.ambiguous_reissue_candidates + stats.ambiguous_cross_source_candidates}")
    print(f"output_records={len(result.rows)}")
    for status in ("current", "legacy_unverified"):
        print(f"status_{status}={status_counts[status]}")
    for source in ("hotpepper", "legacy_local", "hotpepper,legacy_local"):
        print(f"source_{source.replace(',', '_')}={source_counts[source]}")
    print(f"output={'dry-run' if output is None else output}")


def add_input_group(
    parser: argparse.ArgumentParser,
    *,
    path_flags: Iterable[str],
    path_dest: str,
    ref_flag: str,
    help_text: str,
) -> None:
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(*path_flags, dest=path_dest, type=Path, help=f"{help_text}のCSVパス")
    group.add_argument(ref_flag, dest=f"{path_dest}_ref", help=f"{help_text}を読むGit ref")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    add_input_group(
        parser,
        path_flags=("--current", "--fresh-hotpepper"),
        path_dest="current",
        ref_flag="--current-ref",
        help_text="fresh Hot Pepper",
    )
    add_input_group(
        parser,
        path_flags=("--legacy",),
        path_dest="legacy",
        ref_flag="--legacy-ref",
        help_text="前回公開legacy",
    )
    parser.add_argument("--output", required=True, type=Path, help="統合CSVの出力先")
    parser.add_argument(
        "--match-radius-meters",
        type=float,
        default=DEFAULT_MATCH_RADIUS_METERS,
        help=f"店名一致後の近接判定距離（既定: {DEFAULT_MATCH_RADIUS_METERS:g}m）",
    )
    parser.add_argument(
        "--minimum-records",
        type=int,
        default=DEFAULT_MINIMUM_RECORDS,
        help=f"最低出力件数（既定: {DEFAULT_MINIMUM_RECORDS}）",
    )
    parser.add_argument("--generated-at", help="再現試験用の生成日時（ISO 8601）")
    parser.add_argument("--dry-run", action="store_true", help="検査・集計のみ行い出力しない")
    return parser


def load_cli_dataset(path: Path | None, ref: str | None) -> CsvDataset:
    if path is not None:
        return read_csv_path(path)
    if ref is not None:
        return read_csv_ref(ref)
    raise AssertionError("argparseで入力指定を保証しています")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.minimum_records < 0:
            raise MergeError("minimum-records は0以上で指定してください")
        if args.current is not None and args.output.resolve() == args.current.resolve():
            raise MergeError("fresh Hot Pepper入力とoutputを同じパスにはできません")
        current = load_cli_dataset(args.current, args.current_ref)
        legacy = load_cli_dataset(args.legacy, args.legacy_ref)
        result = merge_datasets(
            current,
            legacy,
            radius_meters=args.match_radius_meters,
            generated_at=args.generated_at,
        )
        required_count = max(args.minimum_records, len(legacy.rows))
        if len(result.rows) < required_count:
            raise MergeError(
                f"統合後件数 {len(result.rows)} が安全下限 {required_count} を下回ります"
            )
        if args.dry_run:
            print_summary(result, output=None)
            return 0
        atomic_write_text(args.output, render_csv(result))
        print_summary(result, output=args.output)
        return 0
    except (MergeError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

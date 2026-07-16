#!/usr/bin/env python3
"""GitHub Pages に不要・危険なファイルを公開しないための検査。"""

from __future__ import annotations

import csv
import re
import sys
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = ROOT / "docs"
PUBLIC_CSV = DOCS_DIR / "output" / "meatmap.csv"
ALLOWED_OUTPUT_FILES = {"meatmap.csv"}
REQUIRED_CSV_FIELDS = {
    "name",
    "address",
    "lat",
    "lng",
    "genre",
    "sources",
    "url",
    "hotpepper_url",
    "legacy_id",
    "data_status",
    "last_verified_at",
}
MAX_PUBLIC_CSV_BYTES = 5 * 1024 * 1024
FRESHNESS_WARNING_DAYS = 45
MINIMUM_PUBLIC_RECORDS = 8_444
MINIMUM_LEGACY_IDS = 4_847
ALLOWED_SOURCES = {"hotpepper", "legacy_local"}
ALLOWED_DATA_STATUSES = {"current", "legacy_unverified"}
SOURCE_SCOPE = "hotpepper,legacy_local"
HOTPEPPER_HOSTS = {"www.hotpepper.jp", "hotpepper.jp"}
HOTPEPPER_ID_PATTERN = re.compile(r"/str(J\d+)(?:/|$)", re.IGNORECASE)
LEGACY_ID_PATTERN = re.compile(r"^legacy_[0-9a-f]{20}$")
LOCAL_VALIDATION_SERVICE_PATTERN = re.compile(r"(?:tabelog|食べログ)", re.IGNORECASE)

SECRET_PATTERNS = {
    "Google API key": re.compile(r"AIza[0-9A-Za-z_-]{30,}"),
    "GitHub token": re.compile(r"(?:gh[opsu]_[0-9A-Za-z]{30,}|github_pat_[0-9A-Za-z_]{20,})"),
    "OpenAI API key": re.compile(r"sk-[0-9A-Za-z_-]{20,}"),
}

FORBIDDEN_PUBLIC_TEXT = {
    "genkishimura2000.github.io": "旧GitHub Pages URL",
    "github.com/genkishimura2000/": "旧GitHubリポジトリURL",
    "github.com/GenkiShimura2000/": "旧GitHubリポジトリURL",
    "make-a-big-promise": "廃止済み公開ブランチ",
    "buymeacoffee.com/yourname": "未設定の支援リンク",
    "fundingchoicesmessages.google.com": "未申告の広告同意スクリプト",
    "pagead2.googlesyndication.com": "未申告の広告スクリプト",
    "googletagmanager.com/gtag/js": "未申告のアクセス解析スクリプト",
    "assets/new_pins": "廃止済み巨大画像",
    "assets/pins": "廃止済み巨大画像",
    "tabelog.com": "ローカル検証にのみ使用したサービスの公開URL",
}

REQUIRED_GENRE_META_KEYS = (
    "焼肉",
    "ホルモン",
    "ステーキ",
    "焼き鳥",
    "もつ焼き",
    "しゃぶしゃぶ",
    "すき焼き",
    "韓国",
    "シュラスコ",
    "ジンギスカン",
    "肉バル",
    "ハンバーグ",
    "その他",
)
REQUIRED_MAP_UI_IDS = {
    "budget-filter": "予算フィルター",
    "current-only-filter": "現行確認済みフィルター",
    "filter-reset": "絞り込み解除ボタン",
    "filter-result-count": "絞り込み結果件数",
}
BOUNDS_MARKER_FILTER_PATTERN = re.compile(
    r"\bbounds\s*\.\s*contains\s*\(\s*marker\s*\.\s*getLatLng\s*\(\s*\)\s*\)"
)


class LocalReferenceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.references: list[tuple[str, int]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for key, value in attrs:
            if key in {"href", "src"} and value:
                self.references.append((value, self.getpos()[0]))


def extract_braced_javascript(text: str, opening_index: int) -> str | None:
    """単純なJSオブジェクト/関数から対応する波括弧までを抽出する。"""
    if opening_index < 0 or opening_index >= len(text) or text[opening_index] != "{":
        return None

    depth = 0
    quote: str | None = None
    escaped = False
    for index in range(opening_index, len(text)):
        character = text[index]
        if quote is not None:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
            continue

        if character in {'"', "'", "`"}:
            quote = character
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return text[opening_index : index + 1]
    return None


def validate_map_ui_contract(map_text: str, errors: list[str]) -> None:
    genre_declaration = re.search(r"\b(?:const|let)\s+genreMeta\s*=", map_text)
    genre_meta: str | None = None
    if genre_declaration is None:
        errors.append("docs/map_demo.html: genreMeta が見つかりません")
    else:
        object_start = map_text.find("{", genre_declaration.end())
        statement_end = map_text.find(";", genre_declaration.end())
        if object_start < 0 or (statement_end >= 0 and statement_end < object_start):
            errors.append("docs/map_demo.html: genreMeta のオブジェクト定義が見つかりません")
        else:
            genre_meta = extract_braced_javascript(map_text, object_start)
            if genre_meta is None:
                errors.append("docs/map_demo.html: genreMeta のオブジェクト定義が不完全です")

    if genre_meta is not None:
        for genre in REQUIRED_GENRE_META_KEYS:
            key_pattern = rf'(?:["\']{re.escape(genre)}["\']|\b{re.escape(genre)}\b)\s*:'
            if not re.search(key_pattern, genre_meta):
                errors.append(f"docs/map_demo.html: genreMeta に {genre} 分類がありません")

    for element_id, label in REQUIRED_MAP_UI_IDS.items():
        if not re.search(rf'\bid\s*=\s*["\']{re.escape(element_id)}["\']', map_text):
            errors.append(f"docs/map_demo.html: {label} (#{element_id}) が見つかりません")

    classifier = re.search(r"\bfunction\s+classifyGenre\s*\(\s*row\s*\)\s*\{", map_text)
    if classifier is None:
        errors.append("docs/map_demo.html: classifyGenre(row) が見つかりません")
    else:
        classifier_body = extract_braced_javascript(map_text, classifier.end() - 1)
        name_reference = re.compile(
            r"(?:\brow\s*\??\.\s*name\b|\{[^{}]*\bname\b[^{}]*\}\s*=\s*row\b)"
        )
        if classifier_body is None or not name_reference.search(classifier_body):
            errors.append("docs/map_demo.html: classifyGenre(row) が店名 row.name を分類に使用していません")

    if BOUNDS_MARKER_FILTER_PATTERN.search(map_text):
        errors.append(
            "docs/map_demo.html: 地図範囲外の店舗を欠落させる "
            "bounds.contains(marker.getLatLng()) は使用できません"
        )


def iter_public_text_files() -> list[Path]:
    suffixes = {".html", ".js", ".json", ".webmanifest", ".md", ".txt", ".csv"}
    return [path for path in DOCS_DIR.rglob("*") if path.is_file() and path.suffix.lower() in suffixes]


def validate_output_directory(errors: list[str]) -> None:
    output_dir = DOCS_DIR / "output"
    actual = {path.name for path in output_dir.iterdir() if path.is_file()}
    unexpected = sorted(actual - ALLOWED_OUTPUT_FILES)
    if unexpected:
        errors.append(f"docs/output に公開禁止ファイルがあります: {', '.join(unexpected)}")
    if not PUBLIC_CSV.is_file():
        errors.append("配信用CSV docs/output/meatmap.csv がありません")


def validate_public_text(errors: list[str]) -> None:
    for path in iter_public_text_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        relative = path.relative_to(ROOT)
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                errors.append(f"{relative}: {label} らしき文字列を検出しました")
        for needle, label in FORBIDDEN_PUBLIC_TEXT.items():
            if needle.lower() in text.lower():
                errors.append(f"{relative}: {label} ({needle})")

    map_text = (DOCS_DIR / "map_demo.html").read_text(encoding="utf-8")
    if re.search(r"\.innerHTML\s*=", map_text):
        errors.append("docs/map_demo.html: innerHTML への代入は禁止です")
    if "candidate.origin === location.origin" not in map_text:
        errors.append("docs/map_demo.html: CSV URL の同一オリジン検査が見つかりません")
    required_mixed_source_ui = {
        "data_status": "データ確認状態の表示",
        "last_verified_at": "最終確認日の表示",
        "legacy_unverified": "未検証状態の表示",
        "legacy_local": "旧ローカルデータの表示",
    }
    for needle, label in required_mixed_source_ui.items():
        if needle not in map_text:
            errors.append(f"docs/map_demo.html: {label}が見つかりません")
    validate_map_ui_contract(map_text, errors)


def validate_source_url(
    value: str,
    *,
    allowed_hosts: set[str],
    label: str,
    line_number: int,
    id_pattern: re.Pattern[str],
    errors: list[str],
) -> str | None:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in allowed_hosts:
        errors.append(f"公開CSV {line_number}行目: {label} URLが不正です")
        return None
    match = id_pattern.search(parsed.path)
    if not match:
        errors.append(f"公開CSV {line_number}行目: {label} URLに店舗IDがありません")
        return None
    return match.group(1).lower()


def parse_iso_date(
    value: str,
    *,
    label: str,
    line_number: int | None,
    errors: list[str],
) -> date | None:
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        location = f" {line_number}行目" if line_number is not None else ""
        errors.append(f"公開CSV{location}: {label} の日付形式が不正です")
        return None
    if parsed > datetime.now(timezone.utc).date():
        location = f" {line_number}行目" if line_number is not None else ""
        errors.append(f"公開CSV{location}: {label} が未来日です")
        return None
    return parsed


def validate_csv(errors: list[str], warnings: list[str]) -> None:
    if not PUBLIC_CSV.is_file():
        return
    if PUBLIC_CSV.stat().st_size > MAX_PUBLIC_CSV_BYTES:
        errors.append(f"公開CSVが {MAX_PUBLIC_CSV_BYTES // 1024 // 1024} MiB を超えています")
    public_csv_text = PUBLIC_CSV.read_text(encoding="utf-8", errors="replace")
    if LOCAL_VALIDATION_SERVICE_PATTERN.search(public_csv_text):
        errors.append("公開CSV: ローカル検証用サービスの識別子またはURLが残っています")

    metadata: dict[str, str] = {}
    with PUBLIC_CSV.open(encoding="utf-8", newline="") as handle:
        while True:
            line = handle.readline()
            if not line.startswith("#"):
                header = line
                break
            key, separator, value = line.removeprefix("#").strip().partition("=")
            if separator:
                metadata[key.strip()] = value.strip()
        reader = csv.DictReader([header, *handle])
        fields = set(reader.fieldnames or [])
        missing = sorted(REQUIRED_CSV_FIELDS - fields)
        if missing:
            errors.append(f"公開CSVの必須列が不足しています: {', '.join(missing)}")
            return

        row_count = 0
        source_counts = {source: 0 for source in ALLOWED_SOURCES}
        data_status_counts = {status: 0 for status in ALLOWED_DATA_STATUSES}
        seen_hotpepper_ids: set[str] = set()
        seen_legacy_ids: set[str] = set()
        for line_number, row in enumerate(reader, start=2 + len(metadata)):
            row_count += 1
            if not (row.get("name") or "").strip():
                errors.append(f"公開CSV {line_number}行目: 店名が空です")

            raw_sources = (row.get("sources") or "").strip().lower()
            sources = {value.strip() for value in raw_sources.split(",") if value.strip()}
            unknown_sources = sources - ALLOWED_SOURCES
            if not sources or unknown_sources:
                errors.append(f"公開CSV {line_number}行目: 未許可のデータソース {raw_sources!r}")
            for source in sources & ALLOWED_SOURCES:
                source_counts[source] += 1

            try:
                lat = float(row.get("lat") or "")
                lng = float(row.get("lng") or "")
            except ValueError:
                errors.append(f"公開CSV {line_number}行目: 緯度経度が数値ではありません")
            else:
                # 東京都の島しょ部（伊豆・小笠原）を含む概略範囲。
                if not (24.0 <= lat <= 36.0 and 138.0 <= lng <= 154.0):
                    errors.append(f"公開CSV {line_number}行目: 東京の検査範囲外です ({lat}, {lng})")

            primary_url = (row.get("url") or "").strip()
            hotpepper_url = (row.get("hotpepper_url") or "").strip()
            legacy_id = (row.get("legacy_id") or "").strip().lower()

            if "hotpepper" in sources:
                if not hotpepper_url:
                    errors.append(f"公開CSV {line_number}行目: Hot Pepper URLがありません")
                else:
                    hotpepper_id = validate_source_url(
                        hotpepper_url,
                        allowed_hosts=HOTPEPPER_HOSTS,
                        label="Hot Pepper",
                        line_number=line_number,
                        id_pattern=HOTPEPPER_ID_PATTERN,
                        errors=errors,
                    )
                    if hotpepper_id:
                        if hotpepper_id in seen_hotpepper_ids:
                            errors.append(f"公開CSV {line_number}行目: Hot Pepper 店舗IDが重複しています")
                        seen_hotpepper_ids.add(hotpepper_id)
            elif hotpepper_url:
                errors.append(f"公開CSV {line_number}行目: sourcesにないHot Pepper URLがあります")

            if "legacy_local" in sources:
                if not legacy_id:
                    errors.append(f"公開CSV {line_number}行目: legacy_idがありません")
                elif not LEGACY_ID_PATTERN.fullmatch(legacy_id):
                    errors.append(f"公開CSV {line_number}行目: legacy_idの形式が不正です")
                elif legacy_id in seen_legacy_ids:
                    errors.append(f"公開CSV {line_number}行目: legacy_idが重複しています")
                else:
                    seen_legacy_ids.add(legacy_id)
            elif legacy_id:
                errors.append(f"公開CSV {line_number}行目: sourcesにないlegacy_idがあります")

            expected_sources: set[str] = set()
            if hotpepper_url:
                expected_sources.add("hotpepper")
            if legacy_id:
                expected_sources.add("legacy_local")
            if sources and not unknown_sources and sources != expected_sources:
                errors.append(f"公開CSV {line_number}行目: sourcesとデータ系統が一致しません")

            if hotpepper_url:
                if primary_url != hotpepper_url:
                    errors.append(f"公開CSV {line_number}行目: 主URLがHot Pepper URLと一致しません")
            elif primary_url:
                errors.append(f"公開CSV {line_number}行目: 旧ローカルのみの行に主URLがあります")

            data_status = (row.get("data_status") or "").strip().lower()
            if data_status not in ALLOWED_DATA_STATUSES:
                errors.append(f"公開CSV {line_number}行目: data_status が不正です ({data_status!r})")
            else:
                data_status_counts[data_status] += 1
            if data_status == "current" and "hotpepper" not in sources:
                errors.append(f"公開CSV {line_number}行目: current 行にHot Pepper出典がありません")

            last_verified_at = (row.get("last_verified_at") or "").strip()
            if not last_verified_at:
                errors.append(f"公開CSV {line_number}行目: last_verified_at がありません")
            else:
                parse_iso_date(
                    last_verified_at,
                    label="last_verified_at",
                    line_number=line_number,
                    errors=errors,
                )

            for field in ("budget_lunch", "budget_dinner", "avg_dinner_budget"):
                budget = (row.get(field) or "").strip()
                if not budget:
                    continue
                try:
                    amount = int(float(budget))
                except ValueError:
                    errors.append(f"公開CSV {line_number}行目: {field} が数値ではありません")
                else:
                    if not (0 < amount <= 100_000):
                        errors.append(f"公開CSV {line_number}行目: {field} が異常値です ({amount})")

        expected_count = metadata.get("total_records")
        if expected_count is None or not expected_count.isdigit():
            errors.append("公開CSV: total_records メタデータがありません")
        elif int(expected_count) != row_count:
            errors.append(f"公開CSV: total_records={expected_count} と実件数={row_count} が一致しません")

        if row_count < MINIMUM_PUBLIC_RECORDS:
            errors.append(
                f"公開CSV: 実件数={row_count} が最低維持件数={MINIMUM_PUBLIC_RECORDS}を下回っています"
            )
        if len(seen_legacy_ids) < MINIMUM_LEGACY_IDS:
            errors.append(
                "公開CSV: "
                f"legacy_id件数={len(seen_legacy_ids)} が最低維持件数={MINIMUM_LEGACY_IDS}を下回っています"
            )

        for source, count in source_counts.items():
            if count == 0:
                errors.append(f"公開CSV: {source} の店舗がありません")
        for status, count in data_status_counts.items():
            if count == 0:
                errors.append(f"公開CSV: data_status={status} の店舗がありません")

        if metadata.get("source_scope") != SOURCE_SCOPE:
            errors.append(f"公開CSV: source_scope={SOURCE_SCOPE} がありません")

        for metadata_field in ("current_snapshot_at", "legacy_snapshot_at"):
            value = metadata.get(metadata_field)
            if not value:
                errors.append(f"公開CSV: {metadata_field} がありません")
            else:
                parse_iso_date(value, label=metadata_field, line_number=None, errors=errors)

        generated_at = metadata.get("generated_at_utc")
        if not generated_at:
            errors.append("公開CSV: generated_at_utc がありません")
        else:
            try:
                generated = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
                if generated.tzinfo is None:
                    generated = generated.replace(tzinfo=timezone.utc)
                age_days = (datetime.now(timezone.utc) - generated.astimezone(timezone.utc)).days
                if age_days > FRESHNESS_WARNING_DAYS:
                    warnings.append(f"公開CSVは {age_days} 日前に生成されています（更新推奨）")
            except ValueError:
                errors.append("公開CSV: generated_at_utc の日時形式が不正です")


def resolve_local_reference(page: Path, reference: str) -> Path | None:
    parsed = urlsplit(reference)
    if parsed.scheme or parsed.netloc or reference.startswith(("#", "mailto:", "tel:", "data:")):
        return None
    clean_path = unquote(parsed.path)
    if not clean_path:
        return None
    if clean_path.startswith("/"):
        target = DOCS_DIR / clean_path.lstrip("/")
    else:
        target = page.parent / clean_path
    return target.resolve()


def validate_html_references(errors: list[str]) -> None:
    docs_resolved = DOCS_DIR.resolve()
    for page in DOCS_DIR.rglob("*.html"):
        parser = LocalReferenceParser()
        parser.feed(page.read_text(encoding="utf-8"))
        for reference, line in parser.references:
            target = resolve_local_reference(page, reference)
            if target is None:
                continue
            try:
                target.relative_to(docs_resolved)
            except ValueError:
                errors.append(f"{page.relative_to(ROOT)}:{line}: docs外への参照です: {reference}")
                continue
            if target.is_dir():
                target = target / "index.html"
            if not target.exists():
                errors.append(f"{page.relative_to(ROOT)}:{line}: 参照先がありません: {reference}")


def validate_public_site() -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    validate_output_directory(errors)
    validate_public_text(errors)
    validate_csv(errors, warnings)
    validate_html_references(errors)
    return errors, warnings


def main() -> int:
    errors, warnings = validate_public_site()
    for warning in warnings:
        print(f"WARNING: {warning}")
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        print(f"公開サイト検査: {len(errors)} 件の問題", file=sys.stderr)
        return 1
    print("公開サイト検査: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

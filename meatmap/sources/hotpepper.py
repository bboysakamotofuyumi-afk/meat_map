"""
Client for the HotPepper Gourmet API.
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional, Sequence

import requests

from ..models import RawStoreRecord

TOKYO_AREA_CODES = ["Z011"]  # Tokyo prefecture
MEAT_GENRE_CODES = ["G008", "G029", "G016", "G017", "G048"]
MEAT_KEYWORDS: Sequence[str] = [
    "焼肉専門店",
    "ホルモン焼き",
    "牛タン専門店",
    "ジンギスカン",
    "ステーキハウス",
    "鉄板焼き 和牛",
    "ローストビーフ",
    "ローストポーク",
    "とんかつ 肉厚",
    "焼き鳥 塩",
    "焼きとん",
    "鶏料理 専門店",
    "しゃぶしゃぶ 食べ放題",
    "すき焼き",
    "もつ鍋 専門店",
    "牛すじ 料理",
    "テールスープ",
    "韓国焼肉",
    "サムギョプサル",
    "シュラスコ",
    "アメリカンBBQ",
    "ポークリブ",
    "ラムチョップ",
    "羊肉串",
    "タンドリーチキン",
    "シークカバブ",
]
EXCLUDED_KEYWORDS: Sequence[str] = ("お好み焼", "もんじゃ")


PER_PAGE_GENRE = 30  # Smaller per-page size to avoid oversized responses.
PER_PAGE_KEYWORD = 20


class HotPepperClient:
    BASE_URL = "https://webservice.recruit.co.jp/hotpepper/gourmet/v1/"

    def __init__(
        self,
        api_key: Optional[str] = None,
        session: Optional[requests.Session] = None,
        per_page_genre: int = PER_PAGE_GENRE,
        per_page_keyword: int = PER_PAGE_KEYWORD,
    ) -> None:
        self.api_key = api_key or os.getenv("HOTPEPPER_API_KEY")
        if not self.api_key:
            raise ValueError("HOTPEPPER_API_KEY is not set")
        self.session = session or requests.Session()
        self.per_page_genre = per_page_genre
        self.per_page_keyword = per_page_keyword

    def _request(self, params: Dict[str, str]) -> Dict:
        merged_params = {
            "key": self.api_key,
            "format": "json",
            **params,
        }
        response = self.session.get(self.BASE_URL, params=merged_params, timeout=30)
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            snippet = response.text[:400]
            raise RuntimeError(
                f"HotPepper API returned invalid JSON (status={response.status_code}): {exc}; snippet={snippet!r}"
            ) from exc
        results = payload.get("results")
        if not results:
            raise RuntimeError("HotPepper API response missing 'results'")
        errors = results.get("error")
        if errors:
            message = "; ".join(err.get("message", "Unknown error") for err in errors if isinstance(err, dict))
            raise RuntimeError(f"HotPepper API error: {message}")
        return results

    def _fetch_shops(
        self,
        area_codes: Sequence[str],
        param_name: str,
        param_values: Sequence[str],
        count: int = 100,
    ) -> List[RawStoreRecord]:
        records: List[RawStoreRecord] = []
        for area in area_codes:
            for value in param_values:
                if not value:
                    continue
                start = 1
                while True:
                    params = {
                        "large_area": area,
                        param_name: value,
                        "count": count,
                        "start": start,
                        "order": 4,  # rating / recommended
                    }
                    results = self._request(params)
                    shops = results.get("shop", [])
                    if not shops:
                        break
                    for shop in shops:
                        records.append(normalize_hotpepper_store(shop))
                    results_available = _safe_int(results.get("results_available"))
                    start += len(shops)
                    if (results_available and start > results_available) or len(shops) < count:
                        break
        return records

    def fetch_shops_by_genre(
        self,
        area_codes: Sequence[str],
        genre_codes: Sequence[str],
        count: Optional[int] = None,
    ) -> List[RawStoreRecord]:
        return self._fetch_shops(area_codes, "genre", genre_codes, count=count or self.per_page_genre)

    def fetch_shops_by_keyword(
        self,
        area_codes: Sequence[str],
        keywords: Sequence[str],
        count: Optional[int] = None,
    ) -> List[RawStoreRecord]:
        # keyword検索はヒット数が多いため1回の取得数を抑える。
        return self._fetch_shops(area_codes, "keyword", keywords, count=count or self.per_page_keyword)

    def fetch_tokyo_meat_shops(self) -> List[RawStoreRecord]:
        records_by_id: Dict[str, RawStoreRecord] = {}
        for record in self.fetch_shops_by_genre(TOKYO_AREA_CODES, MEAT_GENRE_CODES):
            if record.external_id and not _is_excluded_store(record):
                records_by_id[record.external_id] = record
        for record in self.fetch_shops_by_keyword(TOKYO_AREA_CODES, MEAT_KEYWORDS):
            if (
                record.external_id
                and record.external_id not in records_by_id
                and not _is_excluded_store(record)
            ):
                records_by_id[record.external_id] = record
        return list(records_by_id.values())


def normalize_hotpepper_store(shop: dict) -> RawStoreRecord:
    genre_names = []
    if "genre" in shop and shop["genre"]:
        main_genre = shop["genre"].get("name") if isinstance(shop["genre"], dict) else shop["genre"]
        if main_genre:
            genre_names.append(main_genre)
    for sub in _iter_sub_genres(shop.get("sub_genre")):
        if sub:
            genre_names.append(sub)
    return RawStoreRecord(
        source="hotpepper",
        external_id=shop.get("id", ""),
        name=shop.get("name", ""),
        address=shop.get("address"),
        lat=float(shop.get("lat") or 0.0),
        lng=float(shop.get("lng") or 0.0),
        phone=shop.get("tel"),
        genres=[genre for genre in genre_names if genre],
        description=shop.get("catch") or shop.get("access"),
        budget_lunch=None,
        budget_dinner=_safe_budget(shop.get("budget")),
        rating=None,
        review_count=None,
        url=_extract_url(shop),
        raw=shop,
    )


def _safe_budget(budget: Optional[dict]) -> Optional[int]:
    if not budget:
        return None
    if budget.get("average"):
        digits = "".join(ch for ch in budget["average"] if ch.isdigit())
        if digits.isdigit():
            return int(digits)
    return None


def _extract_url(shop: dict) -> Optional[str]:
    urls = shop.get("urls", {})
    return urls.get("pc") or urls.get("sp")


def _iter_sub_genres(value: Optional[object]) -> List[str]:
    if not value:
        return []
    entries: List[str] = []
    raw_items: List = []
    if isinstance(value, dict):
        raw_items = [value]
    elif isinstance(value, str):
        raw_items = [{"name": value}]
    elif isinstance(value, list):
        raw_items = value
    else:
        return []
    for item in raw_items:
        if isinstance(item, dict):
            name = item.get("name")
            if name:
                entries.append(name)
        elif isinstance(item, str):
            entries.append(item)
    return entries


def _safe_int(value: Optional[object]) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_excluded_store(record: RawStoreRecord) -> bool:
    content_parts = [
        record.name or "",
        record.description or "",
        " ".join(record.genres or []),
    ]
    haystack = " ".join(content_parts)
    return any(keyword in haystack for keyword in EXCLUDED_KEYWORDS)

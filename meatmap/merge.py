"""
Utilities to merge and deduplicate store records collected from different sources.
"""
from __future__ import annotations

import math
import re
from typing import Dict, List, Optional

from .models import RawStoreRecord, StoreRecord


def merge_records(
    records: List[RawStoreRecord],
    distance_threshold_m: float = 150.0,
) -> List[StoreRecord]:
    merged: List[StoreRecord] = []
    for raw in records:
        existing = _find_match(merged, raw, distance_threshold_m)
        if existing:
            _merge(existing, raw)
        else:
            merged.append(_create_store(raw))
    return merged


def _create_store(raw: RawStoreRecord) -> StoreRecord:
    store_id = f"{raw.source}:{raw.external_id}"
    genres = list(dict.fromkeys([genre for genre in raw.genres if genre]))
    return StoreRecord(
        store_id=store_id,
        name=raw.name,
        address=raw.address,
        lat=raw.lat,
        lng=raw.lng,
        phone=_normalize_phone(raw.phone),
        genres=genres,
        description=raw.description,
        budget_lunch=raw.budget_lunch,
        budget_dinner=raw.budget_dinner,
        rating=raw.rating,
        review_count=raw.review_count,
        sources={raw.source},
        external_ids={raw.source: raw.external_id},
        url=raw.url,
        notes=None,
    )


def _merge(store: StoreRecord, raw: RawStoreRecord) -> None:
    store.sources.add(raw.source)
    store.external_ids.setdefault(raw.source, raw.external_id)
    if raw.url and not store.url:
        store.url = raw.url
    store.genres = list(dict.fromkeys(store.genres + [genre for genre in raw.genres if genre]))
    if not store.address and raw.address:
        store.address = raw.address
    if not store.phone and raw.phone:
        store.phone = _normalize_phone(raw.phone)
    if store.rating is None and raw.rating is not None:
        store.rating = raw.rating
    if store.review_count is None and raw.review_count is not None:
        store.review_count = raw.review_count
    if store.description is None and raw.description:
        store.description = raw.description
    if store.budget_lunch is None and raw.budget_lunch is not None:
        store.budget_lunch = raw.budget_lunch
    if store.budget_dinner is None and raw.budget_dinner is not None:
        store.budget_dinner = raw.budget_dinner
    # Update coordinates if missing or zero in the existing record.
    if (store.lat == 0 or store.lng == 0) and (raw.lat and raw.lng):
        store.lat = raw.lat
        store.lng = raw.lng


def _find_match(stores: List[StoreRecord], raw: RawStoreRecord, threshold: float) -> Optional[StoreRecord]:
    raw_phone = _normalize_phone(raw.phone)
    raw_name = _normalize_name(raw.name)
    for store in stores:
        if raw_phone and store.phone and store.phone == raw_phone:
            return store
        if raw_name and _normalize_name(store.name) == raw_name:
            if _distance_m(store.lat, store.lng, raw.lat, raw.lng) <= threshold:
                return store
    return None


def _normalize_phone(phone: Optional[str]) -> Optional[str]:
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    return digits or None


def _normalize_name(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    normalized = re.sub(r"\s+", "", name).lower()
    return normalized or None


def _distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    radius = 6371_000.0
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lng = math.radians(lng2 - lng1)
    a = math.sin(delta_lat / 2) ** 2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lng / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return radius * c

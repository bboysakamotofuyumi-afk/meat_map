"""
Core data models used across the meatmap pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set


@dataclass(frozen=True)
class GridPoint:
    lat: float
    lng: float
    radius_m: int


@dataclass
class RawStoreRecord:
    source: str
    external_id: str
    name: str
    address: Optional[str]
    lat: float
    lng: float
    phone: Optional[str] = None
    genres: Sequence[str] = field(default_factory=list)
    description: Optional[str] = None
    budget_lunch: Optional[int] = None
    budget_dinner: Optional[int] = None
    rating: Optional[float] = None
    review_count: Optional[int] = None
    url: Optional[str] = None
    raw: Optional[Dict] = None


@dataclass
class StoreRecord:
    store_id: str
    name: str
    address: Optional[str]
    lat: float
    lng: float
    phone: Optional[str]
    genres: List[str]
    description: Optional[str]
    budget_lunch: Optional[int]
    budget_dinner: Optional[int]
    rating: Optional[float]
    review_count: Optional[int]
    sources: Set[str]
    external_ids: Dict[str, str]
    carnivore_score: Optional[float] = None
    carnivore_rank: Optional[str] = None
    url: Optional[str] = None
    notes: Optional[str] = None

from meatmap import merge
from meatmap.models import RawStoreRecord


def make_raw(
    source: str,
    external_id: str,
    name: str = "肉バル",
    address: str = "東京都渋谷区",
    lat: float = 35.0,
    lng: float = 139.0,
    phone: str | None = None,
    genres: list[str] | None = None,
    description: str | None = None,
    budget_lunch: int | None = None,
    budget_dinner: int | None = None,
    rating: float | None = None,
    review_count: int | None = None,
    url: str | None = None,
) -> RawStoreRecord:
    return RawStoreRecord(
        source=source,
        external_id=external_id,
        name=name,
        address=address,
        lat=lat,
        lng=lng,
        phone=phone,
        genres=genres or [],
        description=description,
        budget_lunch=budget_lunch,
        budget_dinner=budget_dinner,
        rating=rating,
        review_count=review_count,
        url=url,
        raw=None,
    )


def test_merge_records_by_phone():
    alt_source = make_raw(
        "alt_source",
        "p1",
        name="肉バルA",
        phone="03-1111-2222",
        genres=["焼肉"],
        rating=4.3,
        review_count=140,
    )
    hotpepper = make_raw(
        "hotpepper",
        "h1",
        name="肉バルA 渋谷",
        phone="03-1111-2222",
        genres=["焼肉・ホルモン"],
        budget_dinner=4500,
    )
    merged = merge.merge_records([alt_source, hotpepper])
    assert len(merged) == 1
    store = merged[0]
    assert store.sources == {"alt_source", "hotpepper"}
    assert store.budget_dinner == 4500
    assert store.rating == 4.3


def test_merge_records_by_position_and_name():
    steak = make_raw("alt_source", "p2", name="ステーキハウス29", lat=35.1, lng=139.1)
    steak_hp = make_raw("hotpepper", "h2", name="ステーキハウス 29", lat=35.1002, lng=139.1001)
    merged = merge.merge_records([steak, steak_hp])
    assert len(merged) == 1

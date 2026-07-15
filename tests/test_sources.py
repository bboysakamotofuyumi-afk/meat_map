import json
from pathlib import Path

import pytest
import requests

from meatmap.models import RawStoreRecord
from meatmap.sources.hotpepper import HotPepperClient, _safe_budget, normalize_hotpepper_store

DATA_DIR = Path(__file__).parent / "data"


def test_normalize_hotpepper_store():
    payload = json.loads((DATA_DIR / "sample_hotpepper.json").read_text(encoding="utf-8"))
    shop = payload["shop"][0]
    shop["sub_genre"] = "ホルモン"
    record = normalize_hotpepper_store(shop)
    assert record.source == "hotpepper"
    assert record.phone == "03-1234-5678"
    assert record.budget_dinner == 4000
    assert any("焼肉" in genre for genre in record.genres)
    assert any("ホルモン" in genre for genre in record.genres)


def _dummy_record(external_id: str, name: str) -> RawStoreRecord:
    return RawStoreRecord(
        source="hotpepper",
        external_id=external_id,
        name=name,
        address=None,
        lat=0.0,
        lng=0.0,
    )


def test_fetch_tokyo_meat_shops_dedup(monkeypatch: pytest.MonkeyPatch):
    client = HotPepperClient(api_key="dummy-key")
    genre_record = _dummy_record("A1", "焼肉専門店")
    keyword_record = _dummy_record("B2", "シュラスコ")

    monkeypatch.setattr(
        client,
        "fetch_shops_by_genre",
        lambda *args, **kwargs: [genre_record],
    )
    monkeypatch.setattr(
        client,
        "fetch_shops_by_keyword",
        lambda *args, **kwargs: [genre_record, keyword_record],
    )

    records = client.fetch_tokyo_meat_shops()
    assert {record.external_id for record in records} == {"A1", "B2"}


def test_fetch_tokyo_meat_shops_filters_okonomiyaki(monkeypatch: pytest.MonkeyPatch):
    client = HotPepperClient(api_key="dummy-key")
    okonomiyaki = _dummy_record("O1", "お好み焼き 肉粉屋")
    okonomiyaki.genres = ["お好み焼き"]

    monkeypatch.setattr(
        client,
        "fetch_shops_by_genre",
        lambda *args, **kwargs: [okonomiyaki],
    )
    monkeypatch.setattr(
        client,
        "fetch_shops_by_keyword",
        lambda *args, **kwargs: [],
    )

    records = client.fetch_tokyo_meat_shops()
    assert records == []


def test_request_error_does_not_leak_api_key(monkeypatch: pytest.MonkeyPatch):
    secret = "secret-api-key"

    class FailingSession:
        def get(self, *args, **kwargs):
            raise requests.HTTPError(f"failed URL contains key={secret}")

    monkeypatch.setattr("meatmap.sources.hotpepper.time.sleep", lambda *_: None)
    client = HotPepperClient(api_key=secret, session=FailingSession())

    with pytest.raises(RuntimeError) as exc_info:
        client._request({"large_area": "Z011"})

    assert secret not in str(exc_info.value)


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("4000円", 4000),
        ("1,000～2,000円", 1500),
        ("3001～4000円", 3500),
        ("0円", None),
        ("料金未定", None),
        ("お会計10％OFF◆飲み放題コース多数", None),
    ],
)
def test_safe_budget(label: str, expected: int | None):
    assert _safe_budget({"average": label}) == expected


def test_safe_budget_prefers_standard_range_over_promotional_text():
    budget = {
        "name": "3001～4000円",
        "average": "お会計10％OFF◆飲み放題コースなどのクーポン多数",
    }
    assert _safe_budget(budget) == 3500

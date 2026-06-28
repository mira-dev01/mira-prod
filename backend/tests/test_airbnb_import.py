import json
from pathlib import Path

from app.services.airbnb_import import parse_airbnb_listing

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "airbnb_listing_sample.json"


def _load_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text())


def test_parse_airbnb_listing_extracts_core_fields():
    parsed = parse_airbnb_listing(_load_fixture())
    fields = parsed["fields"]

    assert "Glasshouse" in fields["name"]
    assert fields["city"] == "Siolim"
    assert fields["max_guests"] == 3
    assert "Kitchen" in fields["amenities"]
    assert fields["check_in_time"] == "14:00"
    assert fields["check_out_time"] == "11:00"
    assert "Check-in after" in fields["house_rules"]


def test_parse_airbnb_listing_extracts_faq_entries():
    parsed = parse_airbnb_listing(_load_fixture())
    faq_by_category = {entry["category"]: entry for entry in parsed["faq_entries"]}

    assert "1 bedroom" in faq_by_category["layout"]["answer"]
    assert "North Goa" in faq_by_category["neighbourhood"]["answer"]
    assert "cancellation policy" in faq_by_category["booking"]["answer"].lower()
    assert "Guest Favorite" in faq_by_category["reputation"]["answer"]
    assert "Pause Project" in faq_by_category["description"]["answer"]
    assert "alarm" in faq_by_category["safety"]["answer"].lower()


def test_parse_airbnb_listing_handles_empty_input():
    parsed = parse_airbnb_listing({})
    assert parsed == {"fields": {}, "faq_entries": []}


async def test_import_creates_property_and_faq_entries(client, auth_headers):
    with open(FIXTURE_PATH, "rb") as f:
        resp = await client.post(
            "/api/v1/properties/import",
            files={"files": ("737471759834870714.json", f, "application/json")},
            headers=auth_headers,
        )

    assert resp.status_code == 200
    results = resp.json()
    assert len(results) == 1
    assert results[0]["status"] == "created"
    assert "Glasshouse" in results[0]["property"]["name"]
    assert results[0]["property"]["airbnb_listing_id"] == "737471759834870714"

    faq_resp = await client.get("/api/v1/faq", headers=auth_headers)
    categories = {entry["category"] for entry in faq_resp.json()}
    assert categories == {"layout", "neighbourhood", "booking", "reputation", "description", "safety"}
    assert all(entry["status"] == "verified" for entry in faq_resp.json())


async def test_reimport_same_listing_updates_instead_of_duplicating(client, auth_headers):
    with open(FIXTURE_PATH, "rb") as f:
        await client.post(
            "/api/v1/properties/import",
            files={"files": ("737471759834870714.json", f, "application/json")},
            headers=auth_headers,
        )

    with open(FIXTURE_PATH, "rb") as f:
        resp = await client.post(
            "/api/v1/properties/import",
            files={"files": ("737471759834870714.json", f, "application/json")},
            headers=auth_headers,
        )

    assert resp.json()[0]["status"] == "updated"

    list_resp = await client.get("/api/v1/properties", headers=auth_headers)
    assert len(list_resp.json()) == 1

    # FAQ entries should be replaced, not duplicated, on re-import.
    faq_resp = await client.get("/api/v1/faq", headers=auth_headers)
    assert len(faq_resp.json()) == 6


async def test_reimport_preserves_host_added_faq_entries(client, auth_headers):
    with open(FIXTURE_PATH, "rb") as f:
        first = await client.post(
            "/api/v1/properties/import",
            files={"files": ("737471759834870714.json", f, "application/json")},
            headers=auth_headers,
        )
    property_id = first.json()[0]["property"]["id"]

    host_entry = await client.post(
        "/api/v1/faq",
        json={"property_id": property_id, "question": "Custom Q", "answer": "Custom A"},
        headers=auth_headers,
    )
    assert host_entry.status_code == 201

    with open(FIXTURE_PATH, "rb") as f:
        await client.post(
            "/api/v1/properties/import",
            files={"files": ("737471759834870714.json", f, "application/json")},
            headers=auth_headers,
        )

    faq_resp = await client.get("/api/v1/faq", headers=auth_headers)
    questions = {entry["question"] for entry in faq_resp.json()}
    assert "Custom Q" in questions
    assert len(faq_resp.json()) == 7  # 6 auto-imported + 1 host-added


async def test_import_bad_json_reports_error_without_failing_batch(client, auth_headers):
    resp = await client.post(
        "/api/v1/properties/import",
        files={"files": ("broken.json", b"not valid json", "application/json")},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()[0]["status"] == "error"

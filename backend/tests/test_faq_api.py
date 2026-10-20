async def test_create_and_list_faq_entry(client, auth_headers, test_property):
    payload = {
        "property_id": str(test_property.id),
        "question": "Is parking available?",
        "answer": "Yes, free parking on-site.",
        "category": "parking",
        "status": "verified",
        "verified_by": "host",
    }
    create_resp = await client.post("/api/v1/faq", json=payload, headers=auth_headers)
    assert create_resp.status_code == 201

    list_resp = await client.get("/api/v1/faq", headers=auth_headers)
    assert list_resp.status_code == 200
    entries = list_resp.json()
    assert len(entries) == 1
    assert entries[0]["question"] == "Is parking available?"


async def test_update_faq_entry_verification(client, auth_headers, test_property):
    create_resp = await client.post(
        "/api/v1/faq",
        json={"question": "Pets allowed?", "answer": "No pets.", "status": "pending"},
        headers=auth_headers,
    )
    faq_id = create_resp.json()["id"]

    update_resp = await client.patch(
        f"/api/v1/faq/{faq_id}", json={"status": "verified", "verified_by": "host"}, headers=auth_headers
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["status"] == "verified"


async def test_delete_faq_entry(client, auth_headers):
    create_resp = await client.post(
        "/api/v1/faq", json={"question": "Q", "answer": "A"}, headers=auth_headers
    )
    faq_id = create_resp.json()["id"]

    delete_resp = await client.delete(f"/api/v1/faq/{faq_id}", headers=auth_headers)
    assert delete_resp.status_code == 204

    list_resp = await client.get("/api/v1/faq", headers=auth_headers)
    assert list_resp.json() == []


async def test_faq_create_rejects_property_not_owned(client, auth_headers):
    resp = await client.post(
        "/api/v1/faq",
        json={
            "property_id": "00000000-0000-0000-0000-000000000000",
            "question": "Q",
            "answer": "A",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 404


async def test_property_faq_editor_entries_visible_on_global_faq_tab(client, auth_headers, test_property):
    """Regression: the per-property FAQ editor used to write into the legacy
    Property.faq JSON column, invisible to GET /faq (the global FAQ tab's
    data source) -- creating with property_id set must now show up there."""
    create_resp = await client.post(
        "/api/v1/faq",
        json={
            "property_id": str(test_property.id),
            "question": "Is early check-in available?",
            "answer": "Subject to availability, ask ahead.",
            "status": "verified",
            "verified_by": "host",
        },
        headers=auth_headers,
    )
    assert create_resp.status_code == 201

    list_resp = await client.get("/api/v1/faq", headers=auth_headers)
    entries = list_resp.json()
    assert len(entries) == 1
    assert entries[0]["property_id"] == str(test_property.id)
    assert entries[0]["question"] == "Is early check-in available?"


async def test_list_verified_property_faq_scopes_by_property_and_status(db_session, test_user, test_property):
    from app.models.faq_entry import FaqEntry
    from app.models.property import Property
    from app.services import faq_service

    other_property = Property(user_id=test_user.id, name="Other Property", base_price=1000)
    db_session.add(other_property)
    await db_session.commit()
    await db_session.refresh(other_property)

    db_session.add_all(
        [
            FaqEntry(
                user_id=test_user.id,
                property_id=test_property.id,
                question="Verified for this property",
                answer="A",
                status="verified",
            ),
            FaqEntry(
                user_id=test_user.id,
                property_id=test_property.id,
                question="Still pending",
                answer="A",
                status="pending",
            ),
            FaqEntry(
                user_id=test_user.id,
                property_id=other_property.id,
                question="Verified for a different property",
                answer="A",
                status="verified",
            ),
            FaqEntry(
                user_id=test_user.id,
                property_id=None,
                question="Verified but portfolio-wide",
                answer="A",
                status="verified",
            ),
        ]
    )
    await db_session.commit()

    entries = await faq_service.list_verified_property_faq(db_session, test_property.id)
    questions = {entry.question for entry in entries}
    assert questions == {"Verified for this property"}

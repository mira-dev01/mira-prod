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

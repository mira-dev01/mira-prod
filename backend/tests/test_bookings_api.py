from datetime import date

from app.models.booking import Booking


async def test_cancel_booking_unblocks_dates(test_property, client, auth_headers, db_session):
    booking = Booking(
        property_id=test_property.id,
        check_in=date(2026, 7, 10),
        check_out=date(2026, 7, 12),
        platform="manual",
        status="confirmed",
    )
    db_session.add(booking)
    await db_session.commit()
    await db_session.refresh(booking)

    avail_before = await client.post(
        "/api/v1/bookings/check-availability",
        json={"property_id": str(test_property.id), "check_in": "2026-07-10", "check_out": "2026-07-12"},
        headers=auth_headers,
    )
    assert avail_before.json()["available"] is False

    resp = await client.delete(f"/api/v1/bookings/{booking.id}", headers=auth_headers)
    assert resp.status_code == 204

    avail_after = await client.post(
        "/api/v1/bookings/check-availability",
        json={"property_id": str(test_property.id), "check_in": "2026-07-10", "check_out": "2026-07-12"},
        headers=auth_headers,
    )
    assert avail_after.json()["available"] is True


async def test_cancel_booking_not_found(client, auth_headers):
    resp = await client.delete(
        "/api/v1/bookings/00000000-0000-0000-0000-000000000000", headers=auth_headers
    )
    assert resp.status_code == 404


async def test_cancel_booking_from_other_host_forbidden(test_property, client, db_session):
    from app.models.user import User

    other_user = User(email="other-host@example.com", name="Other Host")
    db_session.add(other_user)
    await db_session.commit()
    await db_session.refresh(other_user)

    booking = Booking(
        property_id=test_property.id,
        check_in=date(2026, 8, 1),
        check_out=date(2026, 8, 3),
        platform="manual",
        status="confirmed",
    )
    db_session.add(booking)
    await db_session.commit()
    await db_session.refresh(booking)

    resp = await client.delete(
        f"/api/v1/bookings/{booking.id}", headers={"Authorization": f"Bearer {other_user.id}"}
    )
    assert resp.status_code == 404

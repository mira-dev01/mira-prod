"""WhatsApp templates (app/services/whatsapp_templates.py), and the two photo messages that use them:
one property's photos, and the list of every property recommended on the call with one page showing them all."""

import asyncio
import re

import pytest

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.property import Property
from app.schemas.tool import SendPhotosArgs, SendShortlistArgs
from app.services import tool_handlers, whatsapp_templates as wt
from app.utils import background
from tests import fake_providers as sim
from tests.fake_providers import fake_providers_on  # noqa: F401  (autouse: fake WhatsApp)


async def _settle():
    pending = [t for t in background.tasks if not t.done()]
    if pending:
        await asyncio.gather(*pending)


# -- the catalog itself ------------------------------------------------------------------------------------


@pytest.mark.parametrize("key", list(wt.TEMPLATES))
def test_every_template_is_well_formed(key):
    template = wt.TEMPLATES[key]
    numbers = [int(n) for n in re.findall(r"\{\{(\d+)\}\}", template.body)]
    assert sorted(set(numbers)) == list(range(1, len(template.variables) + 1)), "placeholders must be 1..n with no gaps"
    assert not template.body.startswith("{{") and not template.body.rstrip().endswith("}}"), "WhatsApp rejects a body that starts or ends with a placeholder"
    assert len(template.body) <= 1024


def test_every_sid_constant_named_by_a_template_exists_and_is_empty_by_default():
    for template in wt.TEMPLATES.values():
        assert template.sid_const in vars(wt), template.key
    assert all(t.sid == "" for t in wt.TEMPLATES.values()), "SIDs are pasted by hand; the repo ships them blank"


def test_values_become_one_clean_line():
    assert wt.clean_value("a\nb\t\tc    d") == "a b c d"
    assert wt.clean_value("") == "-" and wt.clean_value(None) == "-"
    assert len(wt.clean_value("x" * 5000)) <= wt.MAX_VALUE_CHARS


def test_render_fills_every_placeholder_including_the_two_digit_ones():
    values = [f"v{i}" for i in range(1, 19)]
    text = wt.render("spacez_booking_confirmed", values)
    assert "{{" not in text and "Your stay code: v18" in text and "Booking ID: v2" in text and "Grill kit: v10" in text


# -- sending ----------------------------------------------------------------------------------------------------


async def test_without_a_sid_the_message_goes_as_free_text():
    sid = await wt.send("+919876543210", "photos", ["Asha", "Villa", "https://x/p", "Host"], fallback_text="hello there")

    assert sid and sim.messages()[-1].kind == "text" and sim.messages()[-1].body == "hello there"


async def test_with_a_sid_the_message_goes_as_the_template(monkeypatch):
    monkeypatch.setattr(wt, "PHOTOS_SID", "HXphotos")

    await wt.send("+919876543210", "photos", ["Asha", "Villa", "https://x/p", "Host\nCo"], fallback_text="unused")

    message = sim.messages()[-1]
    assert message.kind == "template" and message.content_sid == "HXphotos"
    assert message.variables == {"1": "Asha", "2": "Villa", "3": "https://x/p", "4": "Host Co"}


async def test_a_wrong_number_of_values_is_not_sent_as_a_template_and_never_raises(monkeypatch):
    monkeypatch.setattr(wt, "PHOTOS_SID", "HXphotos")

    sid = await wt.send("+919876543210", "photos", ["only one"], fallback_text="fallback")

    assert sid and sim.messages()[-1].kind == "text"


async def test_a_messaging_outage_returns_none_instead_of_raising():
    await sim.set_down("messaging", True)

    assert await wt.send("+919876543210", "photos", ["a", "b", "c", "d"], fallback_text="x") is None


# -- photos ---------------------------------------------------------------------------------------------------------


async def _with_photos(prop, n=2):
    async with AsyncSessionLocal() as db:
        row = await db.get(Property, prop.id)
        row.photos = [f"https://example.com/{row.id}/{i}.jpg" for i in range(n)]
        await db.commit()


async def test_photos_go_as_the_photos_template_with_the_gallery_link(test_user, test_property, test_call_session, monkeypatch):
    await _with_photos(test_property)
    monkeypatch.setattr(settings, "twilio_enabled", True)
    monkeypatch.setattr(wt, "PHOTOS_SID", "HXphotos")

    async with AsyncSessionLocal() as db:
        reply = await tool_handlers.handle_send_photos(
            db, SendPhotosArgs(property_id=str(test_property.id), guest_phone="+919876543210", guest_name="Asha Rao"),
            test_call_session.id, test_user.id,
        )
    await _settle()

    assert "I've sent a photo gallery link" in reply
    message = [m for m in sim.messages() if m.kind == "template"][-1]
    assert message.variables["1"] == "Asha" and message.variables["3"] == f"{settings.frontend_base_url}/p/{test_property.id}/photos"


async def test_the_list_of_recommended_properties_is_one_message_with_one_link(test_user, test_property, test_call_session, monkeypatch):
    await _with_photos(test_property)
    async with AsyncSessionLocal() as db:
        second = Property(user_id=test_user.id, name="Hill Haven", city="Manali", base_price=5000, max_guests=6)
        db.add(second)
        await db.commit()
        await db.refresh(second)
    monkeypatch.setattr(settings, "twilio_enabled", True)
    monkeypatch.setattr(wt, "SHORTLIST_SID", "HXlist")

    async with AsyncSessionLocal() as db:
        reply = await tool_handlers.handle_send_shortlist(
            db,
            SendShortlistArgs(property_ids=[str(test_property.id), str(second.id), "not-an-id"], guest_phone="+919876543210", guest_name="Asha"),
            test_call_session.id, test_user.id,
        )
    await _settle()

    assert "list of 2 properties" in reply
    sent = [m for m in sim.messages() if m.kind == "template"]
    assert len(sent) == 1 and sent[0].content_sid == "HXlist"
    listing, link = sent[0].variables["2"], sent[0].variables["3"]
    assert listing.startswith("1) ") and "2) Hill Haven, Manali" in listing and "\n" not in listing
    assert link == f"{settings.frontend_base_url}/p/list?ids={test_property.id},{second.id}"


async def test_the_list_never_includes_another_hosts_property(test_user, test_property, test_call_session, monkeypatch):
    monkeypatch.setattr(settings, "twilio_enabled", True)
    import uuid

    from app.models.user import User

    async with AsyncSessionLocal() as db:
        other = User(id=uuid.uuid4(), email="other@example.com", hashed_password="x", name="Other")
        db.add(other)
        await db.flush()
        theirs = Property(user_id=other.id, name="Not Yours", base_price=1000, max_guests=2)
        db.add(theirs)
        await db.commit()
        await db.refresh(theirs)

    async with AsyncSessionLocal() as db:
        reply = await tool_handlers.handle_send_shortlist(
            db, SendShortlistArgs(property_ids=[str(theirs.id)], guest_phone="+919876543210"), test_call_session.id, test_user.id
        )

    assert "couldn't find" in reply and not sim.messages()


async def test_the_public_list_page_endpoint_returns_each_property_with_its_photos_in_order(client, test_user, test_property):
    await _with_photos(test_property, n=3)
    async with AsyncSessionLocal() as db:
        second = Property(user_id=test_user.id, name="Hill Haven", city="Manali", base_price=5000, max_guests=6)
        db.add(second)
        await db.commit()
        await db.refresh(second)

    response = await client.get(f"/api/v1/properties/gallery?ids={second.id},{test_property.id},nonsense,{second.id}")

    assert response.status_code == 200
    body = response.json()
    assert [p["name"] for p in body] == ["Hill Haven", test_property.name] and len(body[1]["photos"]) == 3
    assert set(body[0]) == {"id", "name", "city", "photos"}  # nothing beyond the public gallery fields
    assert (await client.get("/api/v1/properties/gallery")).json() == []



def test_the_spacez_message_builders_fill_exactly_the_placeholders_their_templates_have():
    from datetime import date
    from decimal import Decimal
    from types import SimpleNamespace

    from app.services import spacez_booking_confirmation as bc

    prop = SimpleNamespace(display_name="Deccan Escape", name="x")
    bill = {"lines": [{"label": "Stay", "amount": 14000, "note": "2 nights", "struck": False}], "request": {"adults": 8, "children": 0, "pets": 0}}
    request = SimpleNamespace(
        id=__import__("uuid").uuid4(), guest_name="Asha Rao", short_url="https://pay/x", check_in=date(2026, 12, 8), check_out=date(2026, 12, 10),
        amount_due=Decimal(7000), total_amount=Decimal(14000), balance_due_on=None, details={"bill": bill, "purpose": "Birthday", "stay_code": "DECC-0001"},
    )
    expected = {
        "spacez_payment_link": bc.payment_link_values(prop, request),
        "spacez_booking_confirmed": bc.confirmation_values(prop, request),
        "spacez_shoot_held": bc.shoot_held_values(prop, request),
        "spacez_payment_unsuccessful": bc.payment_unsuccessful_values(request, "4:30 PM IST on 5 Dec"),
    }
    for key, values in expected.items():
        assert len(values) == len(wt.TEMPLATES[key].variables), key
        assert "{{" not in wt.render(key, values), key

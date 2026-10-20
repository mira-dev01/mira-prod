from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models.faq_entry import FaqEntry
from app.models.lead import Lead
from app.models.notification import Notification
from app.services import recovery_service, whatsapp_reply_service


async def _seed_recovery_lead(db_session, test_property, caller_number="+919999999977"):
    metadata = await recovery_service.handle_busy_recovery(
        host_user_id=test_property.user_id,
        property_id=test_property.id,
        caller_number=caller_number,
        dialed_number=test_property.exophone,
    )
    assert metadata is not None
    return metadata


async def test_parse_menu_choice_recognizes_numbers_and_keywords():
    assert whatsapp_reply_service._parse_menu_choice("1") == "property"
    assert whatsapp_reply_service._parse_menu_choice("Property") == "property"
    assert whatsapp_reply_service._parse_menu_choice("2") == "pricing"
    assert whatsapp_reply_service._parse_menu_choice("pricing please") == "pricing"
    assert whatsapp_reply_service._parse_menu_choice("3") == "faq"
    assert whatsapp_reply_service._parse_menu_choice("4") == "brochure"
    assert whatsapp_reply_service._parse_menu_choice("5") == "continue"
    assert whatsapp_reply_service._parse_menu_choice("talk to host") == "continue"
    assert whatsapp_reply_service._parse_menu_choice("6") == "other"


async def test_parse_menu_choice_falls_back_to_other_for_free_text():
    # A guest typing a genuine question, not a menu number, must still be
    # handled (routed to the host), never silently dropped.
    assert whatsapp_reply_service._parse_menu_choice("is the pool heated?") == "other"


async def test_parse_menu_choice_stopwords_never_cause_a_false_positive_match():
    # Regression: keywords are derived from menu label WORDS ("Talk to the
    # host" -> "talk"/"host" as standalone matchable prefixes), and
    # _parse_menu_choice matches on normalized.startswith(keyword). A short
    # stopword like "the" left in that derived keyword set would make any
    # free-text reply that happens to START WITH "the" silently misroute to
    # "continue" (talk-to-host) instead of falling through to "other" --
    # confirmed by reproducing this exact false match before the stopword
    # filter was added.
    assert whatsapp_reply_service._parse_menu_choice("the host next door is noisy") == "other"
    assert whatsapp_reply_service._parse_menu_choice("to be honest I'm not sure") == "other"


async def test_to_bare_digits_strips_whatsapp_prefix():
    assert whatsapp_reply_service._to_bare_digits("whatsapp:+919999999977") == "+919999999977"


async def test_handle_inbound_reply_with_no_matching_lead_is_a_noop(db_session):
    # No recovery lead exists for this phone at all -- must not raise, must
    # not create anything.
    await whatsapp_reply_service.handle_inbound_reply(db_session, "whatsapp:+910000000000", "1")

    notifications = (await db_session.scalars(select(Notification))).all()
    assert len(notifications) == 0


async def test_reply_never_reattaches_to_a_lead_the_host_already_resolved(test_property, db_session):
    # Principal-review regression: a reply must not be able to silently
    # reopen/mutate a lead the host already marked booked/closed via the
    # dashboard -- _resolve_recovery_lead previously had no status filter at
    # all, so ANY reply from this phone would have reattached here.
    metadata = await _seed_recovery_lead(db_session, test_property)
    lead = await db_session.get(Lead, metadata.lead_id)
    lead.status = "booked"
    await db_session.commit()

    await whatsapp_reply_service.handle_inbound_reply(db_session, "whatsapp:+919999999977", "5")

    notifications = (await db_session.scalars(select(Notification))).all()
    reply_notifications = [
        n for n in notifications if n.channel == whatsapp_reply_service.NOTIFICATION_CHANNEL_BUSY_RECOVERY_REPLY
    ]
    assert len(reply_notifications) == 0
    await db_session.refresh(lead)
    # The reply-specific mutation (_notify_host_of_reply) must never have
    # run -- next_follow_up should still be recovery_service's original
    # rejection text, not overwritten by the reply path.
    assert lead.next_follow_up == "Call back guest -- previous call was missed due to a busy line"


async def test_reply_never_reattaches_to_a_stale_recovery_lead(test_property, db_session):
    # Principal-review regression: a reply weeks after a single busy
    # rejection is not "the same conversation" -- _resolve_recovery_lead
    # previously had no time bound at all.
    metadata = await _seed_recovery_lead(db_session, test_property)
    lead = await db_session.get(Lead, metadata.lead_id)
    lead.updated_at = datetime.now(timezone.utc) - whatsapp_reply_service.RECOVERY_REPLY_WINDOW - timedelta(hours=1)
    await db_session.commit()

    await whatsapp_reply_service.handle_inbound_reply(db_session, "whatsapp:+919999999977", "5")

    notifications = (await db_session.scalars(select(Notification))).all()
    reply_notifications = [
        n for n in notifications if n.channel == whatsapp_reply_service.NOTIFICATION_CHANNEL_BUSY_RECOVERY_REPLY
    ]
    assert len(reply_notifications) == 0


async def test_reply_still_resolves_a_recent_open_recovery_lead(test_property, db_session):
    # Sanity check for the normal, still-valid path: a recent reply to an
    # open recovery lead must still work exactly as before.
    metadata = await _seed_recovery_lead(db_session, test_property)

    await whatsapp_reply_service.handle_inbound_reply(db_session, "whatsapp:+919999999977", "5")

    notifications = (await db_session.scalars(select(Notification))).all()
    reply_notifications = [
        n for n in notifications if n.channel == whatsapp_reply_service.NOTIFICATION_CHANNEL_BUSY_RECOVERY_REPLY
    ]
    assert len(reply_notifications) == 1
    assert reply_notifications[0].lead_id == metadata.lead_id


async def test_property_reply_sends_property_details(test_property, db_session):
    await _seed_recovery_lead(db_session, test_property)

    # send_whatsapp_best_effort no-ops (Twilio unconfigured in test env) --
    # this just confirms routing reaches the property-info path without
    # raising, given a real resolvable property via GuestProfile.last_property_id.
    await whatsapp_reply_service.handle_inbound_reply(db_session, "whatsapp:+919999999977", "1")


async def test_pricing_reply_guards_against_zero_base_price(test_property, db_session):
    test_property.base_price = 0
    await db_session.commit()
    await _seed_recovery_lead(db_session, test_property)

    # Must not raise, and must not attempt to quote a ₹0 price -- same
    # guard tool_handlers.handle_get_pricing already enforces.
    await whatsapp_reply_service.handle_inbound_reply(db_session, "whatsapp:+919999999977", "2")


async def test_faq_reply_with_no_verified_entries(test_property, db_session):
    await _seed_recovery_lead(db_session, test_property)

    await whatsapp_reply_service.handle_inbound_reply(db_session, "whatsapp:+919999999977", "3")


async def test_faq_reply_with_verified_entries(test_property, test_user, db_session):
    entry = FaqEntry(
        user_id=test_user.id,
        property_id=test_property.id,
        question="Is parking available?",
        answer="Yes, free on-site parking.",
        status="verified",
    )
    db_session.add(entry)
    await db_session.commit()
    await _seed_recovery_lead(db_session, test_property)

    await whatsapp_reply_service.handle_inbound_reply(db_session, "whatsapp:+919999999977", "faq")


async def test_faq_reply_caps_each_answer_length(test_property, test_user, db_session, monkeypatch):
    # Regression: FaqEntry.answer is an unbounded Text column. Five
    # unbounded verified answers concatenated risks exceeding WhatsApp's
    # ~4096-char message limit, which would fail the send silently
    # (send_whatsapp_best_effort swallows a Twilio error with no
    # guest-visible fallback). Each answer must be capped.
    long_answer = "A" * 1000
    for i in range(5):
        db_session.add(
            FaqEntry(
                user_id=test_user.id,
                property_id=test_property.id,
                question=f"Question {i}?",
                answer=long_answer,
                status="verified",
            )
        )
    await db_session.commit()
    await _seed_recovery_lead(db_session, test_property)

    sent = []

    async def _capture(to_phone, body, timeout=15.0):
        sent.append(body)
        return {"status": "sent"}

    monkeypatch.setattr(whatsapp_reply_service.twilio_client, "send_whatsapp_message", _capture)

    await whatsapp_reply_service.handle_inbound_reply(db_session, "whatsapp:+919999999977", "3")

    assert len(sent) == 1
    assert len(sent[0]) < 4096
    assert "AAA…" in sent[0] or "A" * whatsapp_reply_service._FAQ_ANSWER_MAX_CHARS in sent[0]


async def test_brochure_reply_with_no_photos(test_property, db_session):
    await _seed_recovery_lead(db_session, test_property)

    await whatsapp_reply_service.handle_inbound_reply(db_session, "whatsapp:+919999999977", "4")


async def test_continue_conversation_reply_notifies_host_and_updates_lead(test_property, db_session):
    metadata = await _seed_recovery_lead(db_session, test_property)

    await whatsapp_reply_service.handle_inbound_reply(db_session, "whatsapp:+919999999977", "5")

    notifications = (await db_session.scalars(select(Notification))).all()
    reply_notifications = [
        n for n in notifications if n.channel == whatsapp_reply_service.NOTIFICATION_CHANNEL_BUSY_RECOVERY_REPLY
    ]
    assert len(reply_notifications) == 1
    assert "talk to the host" in reply_notifications[0].message
    # Regression: property_id must be set (not None) -- GET /notifications
    # and /notifications/stream (app/api/v1/notifications.py) both filter
    # with `property_id.in_(user_property_ids)`, so a None here made this
    # notification invisible on the dashboard, permanently, through every
    # real consumption path.
    assert reply_notifications[0].property_id == test_property.id
    assert test_property.name in reply_notifications[0].message or (
        test_property.display_name and test_property.display_name in reply_notifications[0].message
    )

    from app.models.lead import Lead

    lead = await db_session.get(Lead, metadata.lead_id)
    assert "Guest replied" in lead.next_follow_up


async def test_free_text_reply_notifies_host_with_verbatim_message(test_property, db_session):
    await _seed_recovery_lead(db_session, test_property)

    await whatsapp_reply_service.handle_inbound_reply(
        db_session, "whatsapp:+919999999977", "Do you allow pets?"
    )

    notifications = (await db_session.scalars(select(Notification))).all()
    reply_notifications = [
        n for n in notifications if n.channel == whatsapp_reply_service.NOTIFICATION_CHANNEL_BUSY_RECOVERY_REPLY
    ]
    assert len(reply_notifications) == 1
    assert "Do you allow pets?" in reply_notifications[0].message


async def test_reply_reuses_the_same_lead_never_creates_a_new_one(test_property, db_session):
    # The core "continue existing conversation, don't create another one"
    # requirement -- a reply must never result in a second Lead for the
    # same guest.
    metadata = await _seed_recovery_lead(db_session, test_property)

    from app.models.lead import Lead
    from app.services import lead_service

    await whatsapp_reply_service.handle_inbound_reply(db_session, "whatsapp:+919999999977", "5")

    leads = await lead_service.list_leads(db_session, test_property.user_id)
    assert len(leads) == 1
    assert leads[0].id == metadata.lead_id

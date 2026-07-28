from app.api.v1.properties import _renormalize_one, backfill_missing_display_names
from app.models.property import Property


async def test_backfill_populates_display_name_for_pre_existing_properties(test_user, db_session):
    # Simulates a property imported before the canonical-name feature
    # existed -- raw_name/display_name/spoken_name all NULL, exactly the
    # state that made real live calls speak the raw scraped title verbatim
    # (confirmed live 2026-07-28).
    stale = Property(
        user_id=test_user.id,
        name="Nile w/pool & projector - Pause Project 1bhk",
        base_price=4298,
        max_guests=3,
        exophone="+918011191001",
    )
    db_session.add(stale)
    await db_session.commit()

    count = await backfill_missing_display_names(db_session)
    assert count == 1

    await db_session.refresh(stale)
    assert stale.spoken_name == "Nile w/pool & projector"
    assert stale.display_name is not None
    assert stale.raw_name == "Nile w/pool & projector - Pause Project 1bhk"


async def test_backfill_is_idempotent_and_skips_already_backfilled_properties(test_user, db_session):
    already_clean = Property(
        user_id=test_user.id,
        name="Pine - Glasshouse Suite w/bathtub | Pause Project",
        display_name="Pine - Suite w/bathtub",
        spoken_name="Pine",
        base_price=5700,
        max_guests=2,
        exophone="+918011191002",
    )
    db_session.add(already_clean)
    await db_session.commit()

    count = await backfill_missing_display_names(db_session)
    assert count == 0

    await db_session.refresh(already_clean)
    assert already_clean.spoken_name == "Pine"  # untouched


async def test_backfill_runs_across_multiple_hosts(test_user, db_session):
    from app.models.user import User

    other_host = User(
        email="other-host@example.com", hashed_password="x", name="Other Host",
    )
    db_session.add(other_host)
    await db_session.commit()
    await db_session.refresh(other_host)

    p1 = Property(
        user_id=test_user.id, name="Mocha| 1bhk w/Projector| Pool| 5 min walk2Thalassa",
        base_price=4300, max_guests=3, exophone="+918011191003",
    )
    p2 = Property(
        user_id=other_host.id, name="Terra - Glasshouse Studio w/pool - Pause Project",
        base_price=4498, max_guests=3, exophone="+918011191004",
    )
    db_session.add_all([p1, p2])
    await db_session.commit()

    count = await backfill_missing_display_names(db_session)
    assert count == 2

    await db_session.refresh(p1)
    await db_session.refresh(p2)
    assert p1.spoken_name == "Mocha"
    assert p2.spoken_name == "Terra"


async def test_backfill_is_genuinely_idempotent_even_when_normalizer_derives_nothing(test_user, db_session):
    # Regression: a title that reduces to nothing after the normalizer
    # strips a bare property-type word (e.g. "glasshouse ") produces
    # display_name=None from normalize_property_name. _renormalize_one
    # must still set Property.display_name to *something* non-NULL
    # (falling back to the raw title) so this row is actually removed
    # from the `WHERE display_name IS NULL` scan -- otherwise it would be
    # re-selected and re-processed (LLM fallback call included) on every
    # single app restart forever, confirmed live during testing.
    unresolvable = Property(
        user_id=test_user.id, name="glasshouse ", base_price=3000, max_guests=2,
        exophone="+918011191005",
    )
    db_session.add(unresolvable)
    await db_session.commit()

    first_run_count = await backfill_missing_display_names(db_session)
    assert first_run_count == 1

    await db_session.refresh(unresolvable)
    assert unresolvable.display_name is not None

    second_run_count = await backfill_missing_display_names(db_session)
    assert second_run_count == 0


async def test_startup_backfill_never_calls_the_llm_fallback(test_user, db_session, monkeypatch):
    # The startup path (allow_llm_fallback=False) must never make a network
    # call, however many low-confidence rows exist -- an uncapped LLM call
    # per still-unresolved row on every deploy/restart is real, uncapped
    # cost this path must not incur. Patch the fallback to raise so this
    # test fails loudly if it's ever invoked from the backfill path.
    from app.api.v1 import properties as properties_module

    async def _should_not_be_called(raw_name, description=None):
        raise AssertionError("LLM fallback must never be called from the startup backfill path")

    monkeypatch.setattr(properties_module, "normalize_property_name_llm_fallback", _should_not_be_called)

    low_confidence_property = Property(
        user_id=test_user.id, name="random title with no delimiters or type words at all",
        base_price=3000, max_guests=2, exophone="+918011191006",
    )
    db_session.add(low_confidence_property)
    await db_session.commit()

    count = await backfill_missing_display_names(db_session)
    assert count == 1


async def test_renormalize_one_llm_fallback_still_enabled_by_default(test_user, db_session, monkeypatch):
    # The host-triggered renormalize endpoints must keep using the LLM
    # fallback by default (allow_llm_fallback defaults True) -- only the
    # startup backfill opts out explicitly.
    from app.api.v1 import properties as properties_module
    from app.services.property_normalizer import NormalizedName

    called = {"count": 0}

    async def _fake_fallback(raw_name, description=None):
        called["count"] += 1
        return NormalizedName(display_name="Cleaned Name", spoken_name="Cleaned", confidence="high")

    monkeypatch.setattr(properties_module, "normalize_property_name_llm_fallback", _fake_fallback)

    property_ = Property(
        user_id=test_user.id, name="random title with no delimiters or type words at all",
        base_price=3000, max_guests=2, exophone="+918011191007",
    )
    db_session.add(property_)
    await db_session.commit()

    await properties_module._renormalize_one(db_session, property_)
    assert called["count"] == 1
    assert property_.spoken_name == "Cleaned"

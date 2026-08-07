"""Scale Readiness ("Phase 17"): _scheduled_ical_sync was the one startup
task with no try/except of its own -- every sibling task
(_check_db_health, _backfill_property_display_names) already wraps its
real work. Confirms the fix: an exception from sync_all_properties must be
caught and logged, never propagate (which would otherwise surface only as
asyncio's own terse "Task exception was never retrieved" warning when this
runs via the bare asyncio.create_task(...) at startup, not the structured
logger.exception every other startup task's failure gets)."""

import app.main as main_module


async def test_scheduled_ical_sync_survives_a_failure(monkeypatch):
    async def _raising_sync_all_properties(db):
        raise RuntimeError("simulated iCal sync failure")

    monkeypatch.setattr(main_module, "sync_all_properties", _raising_sync_all_properties)

    # Must not raise -- this is the actual regression this fix guards
    # against (an unhandled exception here previously propagated out of
    # the bare asyncio.create_task(...) call site at startup).
    await main_module._scheduled_ical_sync()


async def test_scheduled_ical_sync_logs_results_on_success(monkeypatch, caplog):
    async def _fake_sync_all_properties(db):
        return {"prop-1": 3}

    monkeypatch.setattr(main_module, "sync_all_properties", _fake_sync_all_properties)

    await main_module._scheduled_ical_sync()

    assert any("iCal sync complete" in record.message for record in caplog.records)

"""Which lines of a transcript were Mira escalating to the host (shown highlighted on Leads -> Tickets)."""

from app.database import AsyncSessionLocal
from app.services import call_service, transcript_markers


def _call(name, **args):
    return {"role": "assistant", "content": None, "tool_calls": [{"id": "1", "type": "function", "function": {"name": name, "arguments": "{}"}}]}


def test_the_guests_ask_and_miras_answer_around_an_escalation_are_marked():
    messages = [
        {"role": "system", "content": "You are Mira"},
        {"role": "user", "content": "Hi, is the villa free?"},
        {"role": "assistant", "content": "Yes it is."},
        {"role": "user", "content": "The geyser is broken, please send someone."},
        _call("dispatch_technician"),
        {"role": "tool", "content": "ok"},
        {"role": "assistant", "content": "I've let the plumber know."},
    ]
    assert transcript_markers.build_markers(messages) == [
        {"tool": "dispatch_technician", "guest": "The geyser is broken, please send someone.", "mira": "I've let the plumber know."}
    ]


def test_what_mira_says_alongside_the_tool_call_is_her_line():
    call = _call("escalate_to_host") | {"content": "I'm flagging this to the host now."}
    markers = transcript_markers.build_markers([{"role": "user", "content": "I want a refund"}, call])
    assert markers == [{"tool": "escalate_to_host", "guest": "I want a refund", "mira": "I'm flagging this to the host now."}]


def test_other_tools_and_unreadable_contexts_give_no_markers():
    assert transcript_markers.build_markers([{"role": "user", "content": "hi"}, _call("search_faq")]) == []
    assert transcript_markers.build_markers([None, "junk"]) == []  # never raises into the end of the call
    assert transcript_markers.build_markers([]) == []


async def test_the_markers_are_saved_with_the_transcript_and_read_back(client, auth_headers, test_call_session):
    markers = [{"tool": "escalate_to_host", "guest": "refund please", "mira": "I'll flag it."}]
    async with AsyncSessionLocal() as db:
        await call_service.finalize_call_session(db, test_call_session.id, "user: refund please\nassistant: I'll flag it.", escalation_markers=markers)
    detail = (await client.get(f"/api/v1/calls/{test_call_session.id}", headers=auth_headers)).json()
    assert detail["escalation_markers"] == markers


async def test_a_call_that_escalated_nothing_is_told_apart_from_one_never_recorded(client, auth_headers, test_call_session):
    async with AsyncSessionLocal() as db:
        await call_service.finalize_call_session(db, test_call_session.id, "user: hi", escalation_markers=[])
    assert (await client.get(f"/api/v1/calls/{test_call_session.id}", headers=auth_headers)).json()["escalation_markers"] == []

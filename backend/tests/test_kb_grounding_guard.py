"""Knowledge-base grounding guard (app/voice/kb_grounding_guard.py)."""

import asyncio

import pytest
from pipecat.frames.frames import LLMFullResponseEndFrame, LLMFullResponseStartFrame, LLMTextFrame
from pipecat.tests.utils import run_test

from app.prompts.system_prompt import KB_MISS_LINE
from app.voice.kb_grounding_guard import (
    KnowledgeGroundingGuardProcessor,
    OFFER_FALLBACK_LINE,
    SupportCorpus,
    analyze_offer,
    analyze_sentence,
    build_portfolio_text,
    extract_clock_times,
    extract_measures,
    guest_text_from_context,
)


def _corpus(facts=(), tools=(), static="", guest="") -> SupportCorpus:
    corpus = SupportCorpus()
    corpus.add_facts(list(facts))
    for text in tools:
        corpus.add_tool_text(text)
    corpus.set_static(static)
    corpus.set_guest_text(guest)
    return corpus


FACTS = [
    "Neighborhood: Mall Road is a 10 minute drive away. The nearest bus stand is 2 km from the villa.",
    "Amenities: WiFi, Free parking, Kitchen.",
    "House rule: No smoking indoors. Quiet hours after 10 PM.",
    "Check-in time is 14:00. Check-out time is 11:00.",
]


def _kinds(sentence, corpus):
    return [(v.kind, v.detail) for v in analyze_sentence(sentence, corpus)]


# --- measure / time extraction ---


def test_measures_digits_words_and_unit_conversion():
    assert ("time", 360.0) in extract_measures("It is 6 hours away")
    assert ("time", 360.0) in extract_measures("It is six hours away")
    assert ("time", 360.0) in extract_measures("a 6-hour drive")
    assert ("time", 60.0) in extract_measures("about an hour")
    assert ("time", 30.0) in extract_measures("half an hour")
    assert ("time", 90.0) in extract_measures("one and a half hours")
    assert ("dist", 2000.0) in extract_measures("2 km")
    assert ("dist", 2000.0) in extract_measures("two kilometres")
    assert extract_measures("2000 metres") == {("dist", 2000.0)}
    assert ("time", 120.0) in extract_measures("2 ghante")


def test_measure_ranges_extract_both_ends():
    assert {("time", 5.0), ("time", 10.0)} <= extract_measures("5 to 10 minutes")


def test_clock_times_normalize_across_formats():
    assert extract_clock_times("2 PM") == extract_clock_times("14:00") == {14 * 60}
    assert extract_clock_times("10:30 am") == {10 * 60 + 30}
    assert extract_clock_times("12 am") == {0}


# --- the incident ---


def test_regression_six_hours_from_delhi_is_unsupported():
    corpus = _corpus(FACTS, guest="how far is Delhi")
    assert ("measure", "360 minutes") in _kinds("It's about 6 hours from Delhi.", corpus)


def test_same_claim_is_fine_when_on_file():
    corpus = _corpus(["Neighborhood: Delhi is about 6 hours away by road."], guest="how far is Delhi")
    assert _kinds("Delhi is about six hours away by road.", corpus) == []


# --- supported statements pass ---


@pytest.mark.parametrize(
    "sentence",
    [
        "Mall Road is ten minutes by car.",
        "The bus stand is two kilometres away.",
        "Check-in is at 2 PM and check-out is at 11 AM.",
        "Smoking is not allowed indoors.",
        "Quiet hours start at 10 PM.",
        "There is WiFi and free parking.",
        "Which date works for you?",
        "I don't have that detail on file, and I'd rather not guess.",
        "Sure, I can help with that.",
    ],
)
def test_grounded_or_non_claim_sentences_pass(sentence):
    assert _kinds(sentence, _corpus(FACTS)) == []


# --- unsupported statements are caught ---


@pytest.mark.parametrize(
    "sentence,kind",
    [
        ("Mall Road is five minutes by car.", "measure"),
        ("The airport is 40 minutes away.", "measure"),
        ("Check-in is at 4 PM.", "clock_time"),
        ("Pets are allowed.", "topic"),
        ("There is a swimming pool.", "topic"),
        ("The beach is right nearby.", "topic"),
        ("It's a few hours away.", "vague_measure"),
        ("It's close to Connaught Place.", "place"),
    ],
)
def test_unsupported_claims_are_flagged(sentence, kind):
    assert kind in [k for k, _ in _kinds(sentence, _corpus(FACTS))]


def test_guest_words_ground_places_and_times_but_never_topics():
    corpus = _corpus(FACTS, guest="We land in Chandigarh at 9 pm, and we have a dog")
    assert _kinds("From Chandigarh it's a drive.", corpus) == []  # a place the guest named
    assert _kinds("Arriving at 9 PM is fine.", corpus) == []
    assert ("topic", "pets") in _kinds("Dogs are welcome here.", corpus)  # asking isn't evidence


def test_tool_results_and_static_text_ground_claims():
    corpus = _corpus(
        FACTS,
        tools=["For Sunset Villa, that's 4500 per night, 9000 total for 2 nights (3 hour free cancellation)."],
        static="Sunset Villa Manali check-in 14:00 sleeps 6",
    )
    assert _kinds("Sunset Villa in Manali sleeps six guests.", corpus) == []
    assert _kinds("You get a 3 hour free cancellation window.", corpus) == []


def test_vague_quantity_not_legitimized_by_a_specific_one_elsewhere():
    assert ("vague_measure", "vague time with none on file") in _kinds("It's a few hours away.", _corpus(FACTS))


def test_spoken_measure_tolerates_unit_change_but_not_rounding():
    corpus = _corpus(["It is 1 hour from the airport."])
    assert _kinds("The airport is sixty minutes away.", corpus) == []
    assert _kinds("The airport is 55 minutes away.", corpus) != []


# --- processor behavior ---


def _response(*chunks: str) -> list:
    return [LLMFullResponseStartFrame(), *[LLMTextFrame(c) for c in chunks], LLMFullResponseEndFrame()]


def _spoken(down_frames) -> str:
    return "".join(f.text for f in down_frames if isinstance(f, LLMTextFrame))


def _guard(mode="enforce", gaps=None, facts=FACTS, guest="how far is Delhi"):
    sink_calls = [] if gaps is None else gaps

    async def sink(question, source):
        sink_calls.append((question, source))

    guard = KnowledgeGroundingGuardProcessor(
        mode=mode,
        miss_line=KB_MISS_LINE,
        gap_sink=sink,
        guest_text_provider=lambda: guest,
        last_guest_utterance_provider=lambda: guest,
    )
    guard.record_search("found", list(facts))
    return guard


async def test_enforce_replaces_the_unsupported_sentence_and_keeps_the_rest():
    gaps = []
    guard = _guard("enforce", gaps)

    down, _ = await run_test(
        guard,
        frames_to_send=_response("Happy to help. ", "It's about 6 hours from Delhi. ", "Anything else?"),
    )
    await asyncio.sleep(0)

    text = _spoken(down)
    assert "6 hours" not in text and "Delhi" not in text
    assert KB_MISS_LINE in text
    assert "Happy to help." in text and "Anything else?" in text
    assert gaps == [("how far is Delhi", "guard_blocked")]


async def test_shadow_mode_never_changes_what_is_spoken():
    gaps = []
    guard = _guard("shadow", gaps)

    down, _ = await run_test(guard, frames_to_send=_response("It's about 6 hours from Delhi."))
    await asyncio.sleep(0)

    assert _spoken(down) == "It's about 6 hours from Delhi."
    assert gaps == []  # shadow logs the would-block line, writes nothing


async def test_a_grounded_reply_passes_untouched_even_split_across_chunks():
    guard = _guard("enforce")

    down, _ = await run_test(
        guard, frames_to_send=_response("Mall Road is ", "ten minutes ", "by car. ", "Check-in is at 2 PM.")
    )

    assert _spoken(down).replace("  ", " ").strip() == "Mall Road is ten minutes by car. Check-in is at 2 PM."


async def test_only_one_replacement_per_response():
    guard = _guard("enforce")

    down, _ = await run_test(
        guard, frames_to_send=_response("The airport is 40 minutes away. The beach is 5 minutes away. ")
    )

    assert _spoken(down).count(KB_MISS_LINE) == 1


async def test_miss_line_after_possible_result_is_logged_as_a_gap():
    gaps = []
    guard = _guard("enforce", gaps)
    guard.record_search("possible", ["House rule: Quiet hours after 10 PM."])

    down, _ = await run_test(guard, frames_to_send=_response(KB_MISS_LINE))
    await asyncio.sleep(0)

    assert KB_MISS_LINE in _spoken(down)
    assert gaps == [("how far is Delhi", "miss_line_spoken")]


async def test_miss_line_after_a_none_result_is_not_logged_twice():
    gaps = []
    guard = _guard("enforce", gaps)
    guard.record_search("none", [])  # search_faq already logged this gap itself

    await run_test(guard, frames_to_send=_response(KB_MISS_LINE))
    await asyncio.sleep(0)

    assert gaps == []


async def test_guard_error_passes_text_through(monkeypatch):
    from app.voice import kb_grounding_guard as mod

    monkeypatch.setattr(mod, "analyze_sentence", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    guard = _guard("enforce")

    down, _ = await run_test(guard, frames_to_send=_response("It's about 6 hours from Delhi."))

    assert _spoken(down) == "It's about 6 hours from Delhi."


async def test_state_resets_between_responses():
    guard = _guard("enforce")

    down, _ = await run_test(
        guard,
        frames_to_send=_response("The airport is 40 minutes away.") + _response("The airport is 40 minutes away."),
    )

    assert _spoken(down).count(KB_MISS_LINE) == 2


# --- outgoing messages & context helpers ---


def test_outgoing_whatsapp_text_is_checked_like_speech():
    guard = _guard("enforce")
    assert guard.check_outgoing("Your check-in is at 2 PM. Mall Road is ten minutes away.") == []
    flagged = guard.check_outgoing("Your villa is 6 hours from Delhi.")
    assert [v.kind for v in flagged] == ["measure"]
    assert guard.enforcing


def test_guest_text_from_context_reads_string_and_part_messages():
    class Ctx:
        messages = [
            {"role": "system", "content": "rules mentioning airport"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": [{"type": "text", "text": "how far is Delhi"}]},
        ]

    text = guest_text_from_context(Ctx())
    assert "hello" in text and "how far is Delhi" in text and "airport" not in text
    assert guest_text_from_context(None) == ""


# --- offers (questions naming something the portfolio may not have) ---------

PORTFOLIO = "villa cabin Valley views and a shared swimming pool. Pine forest, riverside walks."


def _offers(sentence, portfolio=PORTFOLIO):
    corpus = SupportCorpus()
    corpus.set_portfolio_text(portfolio)
    return [v.detail for v in analyze_offer(sentence, corpus)]


@pytest.mark.parametrize(
    "sentence, expected",
    [
        ("Would you prefer a beach or the mountains?", ["beach"]),
        ("Are you looking for a hot tub?", ["amenity:bathtub"]),
        ("Would you like a private pool or a market nearby?", ["market"]),
    ],
)
def test_offer_naming_something_the_portfolio_lacks_is_flagged(sentence, expected):
    assert _offers(sentence) == expected


@pytest.mark.parametrize(
    "sentence",
    [
        "Would you like a place with a pool?",  # "private pool" and "shared pool" both canonicalize to pool
        "Are you travelling with pets?",  # policy topic: about the guest, not a menu item
        "How many guests will be staying?",
        "Would you like me to check the dates?",
        "I don't have a beach option, would a lake work?",  # disclaimer
        "We have a pool.",  # statements are analyze_sentence's job
    ],
)
def test_supported_or_non_offer_questions_pass(sentence):
    assert _offers(sentence) == []


def test_offer_check_fails_open_without_portfolio_text():
    assert _offers("Would you prefer a beach?", portfolio="") == []


def test_portfolio_text_never_supports_an_assertion_about_a_property():
    corpus = SupportCorpus()
    corpus.set_portfolio_text(PORTFOLIO)
    assert _kinds("This villa has a pool.", corpus) == [("topic", "amenity:pool")]


def test_build_portfolio_text_collects_type_style_amenities_and_blurbs():
    from types import SimpleNamespace

    prop = SimpleNamespace(
        property_type="villa",
        property_style="beachfront",
        usp="Sunset deck.",
        neighborhood_info=None,
        amenities=["Private pool", {"ignored": True}],
    )
    assert build_portfolio_text([prop]) == " \n".join(["villa", "beachfront", "Sunset deck.", "Private pool"])


async def test_enforce_swaps_an_unsupported_offer_for_the_fixed_question():
    guard = _guard("enforce")
    guard.set_portfolio_text(PORTFOLIO)

    down, _ = await run_test(guard, frames_to_send=_response("Happy to help. ", "Would you like a beach stay? ", "Great."))
    await asyncio.sleep(0)

    text = _spoken(down)
    assert "beach" not in text
    assert OFFER_FALLBACK_LINE in text
    assert "Happy to help." in text and "Great." in text


async def test_shadow_leaves_an_unsupported_offer_untouched():
    guard = _guard("shadow")
    guard.set_portfolio_text(PORTFOLIO)

    down, _ = await run_test(guard, frames_to_send=_response("Would you like a beach stay? "))
    await asyncio.sleep(0)

    assert "beach" in _spoken(down)

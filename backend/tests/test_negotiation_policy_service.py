"""Phase 4E (generalized negotiation policy authoring pipeline) -- direct,
DB-free unit tests for app/services/negotiation_policy_service.py's
_clean_stages/_extract_json_rules functions: the parser's OUTPUT CONTRACT
for representing a host's natural-language progressive discount as
structured `stages`, independent of which LLM produced the raw JSON (no
network calls, no monkeypatched provider -- these feed already-parsed JSON
directly into the same validation the real LLM response goes through).

Deliberately uses ARBITRARY, VARIED stage counts and values throughout --
no single "canonical" host policy is reused, per this phase's own
no-host-overfitting constraint (see Step 15 of the brief).
"""

import json

from app.services.negotiation_policy_service import _clean_stages, _extract_json_rules


def _rules_json(*rules: dict) -> str:
    return json.dumps({"rules": list(rules)})


# ---------------------------------------------------------------------------
# 1. Flat discount extraction (unchanged by this phase).
# ---------------------------------------------------------------------------


def test_flat_discount_extraction_unchanged():
    raw = _rules_json(
        {"rule_type": "discount_guest_requests", "condition": {}, "discount_percent": 6, "stages": None, "label": None}
    )
    cleaned = _extract_json_rules(raw)
    assert len(cleaned) == 1
    assert cleaned[0]["discount_percent"] == 6.0
    assert cleaned[0]["stages"] is None


# ---------------------------------------------------------------------------
# 2/3/4/5. Progressive discount extraction, arbitrary stage count, ordering,
# arbitrary values.
# ---------------------------------------------------------------------------


def test_progressive_discount_extraction_arbitrary_count_and_values():
    """5 stages, deliberately non-round values -- proves no hardcoded count
    or "nice number" assumption."""
    raw = _rules_json(
        {
            "rule_type": "discount_guest_requests",
            "condition": {},
            "discount_percent": None,
            "stages": [
                {"order": 0, "value": 1.5},
                {"order": 1, "value": 4},
                {"order": 2, "value": 9.25},
                {"order": 3, "value": 12},
                {"order": 4, "value": 17.5},
            ],
            "label": None,
        }
    )
    cleaned = _extract_json_rules(raw)
    assert len(cleaned) == 1
    assert cleaned[0]["stages"] == [
        {"order": 0, "value": 1.5},
        {"order": 1, "value": 4.0},
        {"order": 2, "value": 9.25},
        {"order": 3, "value": 12.0},
        {"order": 4, "value": 17.5},
    ]
    # Staged supersedes flat -- discount_percent is never set alongside a
    # usable stages list on the same drafted row.
    assert cleaned[0]["discount_percent"] is None


def test_progressive_discount_two_stages_minimum():
    raw = _rules_json(
        {"rule_type": "custom", "condition": {}, "discount_percent": None, "stages": [{"order": 0, "value": 5}, {"order": 1, "value": 10}], "label": "Villa concession"}
    )
    cleaned = _extract_json_rules(raw)
    assert cleaned[0]["stages"] == [{"order": 0, "value": 5.0}, {"order": 1, "value": 10.0}]


def test_stage_ordering_is_normalized_even_if_llm_returns_out_of_order():
    raw = _rules_json(
        {
            "rule_type": "discount_guest_requests",
            "condition": {},
            "discount_percent": None,
            "stages": [{"order": 2, "value": 20}, {"order": 0, "value": 4}, {"order": 1, "value": 11}],
            "label": None,
        }
    )
    cleaned = _extract_json_rules(raw)
    assert cleaned[0]["stages"] == [
        {"order": 0, "value": 4.0},
        {"order": 1, "value": 11.0},
        {"order": 2, "value": 20.0},
    ]


def test_arbitrary_large_stage_count_not_hardcoded_to_three():
    """7 stages -- explicitly proves the parser doesn't assume the classic
    3-tier example from the brief is the only shape."""
    stages = [{"order": i, "value": 2 * i + 1} for i in range(7)]
    raw = _rules_json({"rule_type": "discount_guest_requests", "condition": {}, "discount_percent": None, "stages": stages, "label": None})
    cleaned = _extract_json_rules(raw)
    assert len(cleaned[0]["stages"]) == 7
    assert cleaned[0]["stages"][-1] == {"order": 6, "value": 13.0}


# ---------------------------------------------------------------------------
# 6. Ambiguous progressive wording -- no invented intermediate values.
# ---------------------------------------------------------------------------


def test_single_stage_collapses_to_none_never_kept_as_a_one_item_ladder():
    """A length-1 'stages' is never a real progression (see _clean_stages'
    own docstring) -- the parser must not represent a flat value as a
    one-stage ladder even if the LLM mistakenly produces one."""
    raw = _rules_json(
        {"rule_type": "discount_guest_requests", "condition": {}, "discount_percent": None, "stages": [{"order": 0, "value": 5}], "label": None}
    )
    cleaned = _extract_json_rules(raw)
    assert cleaned[0]["stages"] is None


def test_stages_with_duplicate_order_is_rejected_entirely():
    """Ambiguous/malformed stage data (two entries claiming the same
    order) must not silently pick one -- the whole stages value is
    dropped, falling back to flat/no-value rather than guessing."""
    raw = _rules_json(
        {
            "rule_type": "discount_guest_requests",
            "condition": {},
            "discount_percent": None,
            "stages": [{"order": 0, "value": 5}, {"order": 0, "value": 9}],
            "label": None,
        }
    )
    cleaned = _extract_json_rules(raw)
    assert cleaned[0]["stages"] is None


def test_stages_with_out_of_range_value_is_rejected():
    raw = _rules_json(
        {
            "rule_type": "discount_guest_requests",
            "condition": {},
            "discount_percent": None,
            "stages": [{"order": 0, "value": 5}, {"order": 1, "value": 150}],
            "label": None,
        }
    )
    cleaned = _extract_json_rules(raw)
    assert cleaned[0]["stages"] is None


def test_stages_with_non_numeric_value_is_rejected():
    raw = _rules_json(
        {
            "rule_type": "discount_guest_requests",
            "condition": {},
            "discount_percent": None,
            "stages": [{"order": 0, "value": "a lot"}, {"order": 1, "value": 9}],
            "label": None,
        }
    )
    cleaned = _extract_json_rules(raw)
    assert cleaned[0]["stages"] is None


def test_stages_with_negative_order_is_rejected():
    raw = _rules_json(
        {
            "rule_type": "discount_guest_requests",
            "condition": {},
            "discount_percent": None,
            "stages": [{"order": -1, "value": 5}, {"order": 0, "value": 9}],
            "label": None,
        }
    )
    cleaned = _extract_json_rules(raw)
    assert cleaned[0]["stages"] is None


def test_stages_not_a_list_is_rejected():
    raw = _rules_json(
        {"rule_type": "discount_guest_requests", "condition": {}, "discount_percent": None, "stages": "3% then 5%", "label": None}
    )
    cleaned = _extract_json_rules(raw)
    assert cleaned[0]["stages"] is None


# ---------------------------------------------------------------------------
# 7. No invented values -- stages restricted to stageable rule_types only.
# ---------------------------------------------------------------------------


def test_stages_on_a_non_stageable_rule_type_is_silently_ignored():
    """discount_repeat_guest (loyalty) is not a pushback-progression concept
    -- a stray stages value here must never be applied, since nothing in
    this parser's extraction prompt asks for a loyalty ladder and honoring
    one here would be inventing behavior the host didn't describe."""
    raw = _rules_json(
        {
            "rule_type": "discount_repeat_guest",
            "condition": {},
            "discount_percent": 8,
            "stages": [{"order": 0, "value": 5}, {"order": 1, "value": 10}],
            "label": None,
        }
    )
    cleaned = _extract_json_rules(raw)
    assert cleaned[0]["stages"] is None
    assert cleaned[0]["discount_percent"] == 8.0  # flat value still honored


def test_stages_on_minimum_stay_nights_is_ignored():
    raw = _rules_json(
        {
            "rule_type": "minimum_stay_nights",
            "condition": {"min_nights": 3},
            "discount_percent": None,
            "stages": [{"order": 0, "value": 5}, {"order": 1, "value": 10}],
            "label": None,
        }
    )
    cleaned = _extract_json_rules(raw)
    assert cleaned[0]["stages"] is None


def test_no_stages_and_no_discount_percent_produces_a_valueless_rule_not_a_guess():
    """If neither a usable stages list nor a flat value is extractable, the
    rule is produced with both fields None rather than the parser
    inventing either -- the host sees an incomplete draft to reject/edit,
    never a fabricated number."""
    raw = _rules_json(
        {"rule_type": "discount_guest_requests", "condition": {}, "discount_percent": None, "stages": None, "label": None}
    )
    cleaned = _extract_json_rules(raw)
    assert cleaned[0]["discount_percent"] is None
    assert cleaned[0]["stages"] is None


def test_clean_stages_directly_arbitrary_values():
    """Direct unit coverage of _clean_stages itself, arbitrary/varied
    values, not routed through the full rule dict."""
    assert _clean_stages([{"order": 0, "value": 33}, {"order": 1, "value": 66.5}]) == [
        {"order": 0, "value": 33.0},
        {"order": 1, "value": 66.5},
    ]
    assert _clean_stages(None) is None
    assert _clean_stages([]) is None
    assert _clean_stages([{"order": 0, "value": 5}]) is None  # single entry
    assert _clean_stages([{"order": 0}]) is None  # missing value
    assert _clean_stages([{"value": 5}]) is None  # missing order


def test_clean_stages_accepts_integer_valued_float_order():
    """Self-review regression: JSON's number type doesn't distinguish
    int/float, so an LLM emitting well-formed JSON may legitimately write
    "order": 0.0 instead of 0 -- Python's json module parses that as a
    float. An earlier version of _clean_stages required order to be a
    strict `int`, silently dropping an otherwise-valid staged rule purely
    over this formatting difference, while the Pydantic schema this data
    eventually flows through (NegotiationStage) already coerced a float
    order to int without complaint -- an inconsistency between the parser
    and its own downstream consumer. Fixed to accept any integer-valued
    numeric order, normalizing it to a real int."""
    assert _clean_stages([{"order": 0.0, "value": 5}, {"order": 1, "value": 9}]) == [
        {"order": 0, "value": 5.0},
        {"order": 1, "value": 9.0},
    ]
    assert _clean_stages([{"order": 0, "value": 5}, {"order": 1.0, "value": 9}]) == [
        {"order": 0, "value": 5.0},
        {"order": 1, "value": 9.0},
    ]
    # A genuinely fractional order has no sensible meaning as a sequence
    # position -- still rejected, not silently rounded.
    assert _clean_stages([{"order": 0.5, "value": 5}, {"order": 1, "value": 9}]) is None

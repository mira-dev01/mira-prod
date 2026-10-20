"""Covers amenity_checklist_note (Recommendation conversations, "Phase X"):
required_amenities is a soft ranking preference now (filter_builder.py's
apply_amenity_boost), not a hard filter, so a returned property can
genuinely have some but not all of a guest's accumulated amenity requests.
Per explicit product direction, both what's present AND what's missing must
be stated explicitly when the match is partial -- one unit test per branch,
per this codebase's own established convention (test_property_card_match_reasons.py).
"""

from app.services.property.card import amenity_checklist_note


def test_no_note_when_fewer_than_two_amenities_requested():
    """A single requested amenity is already covered by match_reasons_for_card's
    own "has the X you asked for" clause -- no need to duplicate it here."""
    assert amenity_checklist_note(["pool"], ["pool"]) == ""
    assert amenity_checklist_note(None, ["pool"]) == ""
    assert amenity_checklist_note([], ["pool"]) == ""


def test_no_note_when_all_requested_amenities_present():
    """An all-matched property already reads as a clean fit -- no need for
    an explicit checklist restating what's already implied."""
    assert amenity_checklist_note(["pool", "pet friendly"], ["pool", "pets_allowed"]) == ""


def test_no_note_when_all_requested_amenities_missing():
    """An all-missing property states nothing present -- a checklist of
    pure absence would read oddly; the boost already ranks it last."""
    assert amenity_checklist_note(["pool", "pet friendly"], []) == ""


def test_partial_match_states_both_present_and_missing_explicitly():
    """The actual case this function exists for: has SOME but not all --
    both halves must be spoken so the guest can decide for themselves."""
    note = amenity_checklist_note(["pool", "pet friendly"], ["pool"])
    assert note == "has pool but not pet friendly"


def test_partial_match_uses_canonical_amenity_matching():
    """"swimming pool" (a real, differently-worded amenity_tags entry) must
    still match a guest asking for "pool" -- same canonicalize_amenity
    normalization match_reasons_for_card and filter_builder.py already use,
    not a second, looser/stricter comparison invented just for this."""
    note = amenity_checklist_note(["pool", "pet friendly"], ["pool"])
    assert "pool" in note and "pet friendly" in note


def test_three_requested_amenities_lists_all_present_and_all_missing():
    note = amenity_checklist_note(["pool", "pet friendly", "wifi"], ["pool", "wifi"])
    assert "pool" in note
    assert "wifi" in note
    assert "pet friendly" in note
    assert note.startswith("has ")
    assert " but not " in note

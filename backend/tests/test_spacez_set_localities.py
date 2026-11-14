"""The Spacez title parser: right on the titles that state an area, silent on the ones that don't.

A wrong locality is worse than none (it would send a guest to the wrong stay), so every ambiguous title below
must come back None and be left for the hand-checked override file.
"""

import pytest

from scripts.spacez_set_localities import override_for, parse_locality


@pytest.mark.parametrize(
    ("name", "city", "expected"),
    [
        ("4BHK Jugglers Rest Spacez Villa in Indiranagar", "Bengaluru", "Indiranagar"),
        ("Classic Charm Spacez Villa in Koramangala", "Bengaluru", "Koramangala"),
        ("6BHK Spacez Villa w/ Patio In Sarjapur", "Bengaluru", "Sarjapur"),
        ("2BHK Penthouse In Madhapur", "Hyderabad", "Madhapur"),
    ],
)
def test_a_clean_in_area_ending_is_read(name, city, expected):
    assert parse_locality(name, city) == expected


@pytest.mark.parametrize(
    ("name", "city"),
    [
        ("Marigold Manor 3BHK Luxury villa in Hyderabad", "Hyderabad"),  # the city itself
        ("1BHK In Jakkur Lucent Elite Apartment", "Bengaluru"),  # area plus a building name
        ("5BHK villa In Kukatpally Ivory Court", "Hyderabad"),
        ("Glasshouse 5BHK Spacez Villa w/ Patio Koramangala", "Bengaluru"),  # no "in"
        ("Casa Blu 4BHK Spacez Villa with Outdoor Pool & Garden", "Bengaluru"),
        ("Aurora Heights Spacez Luxe Cine Villa", "Bengaluru"),
        ("Soul Club", "Mumbai"),
        ("", None),
    ],
)
def test_an_ambiguous_or_missing_area_is_left_alone(name, city):
    assert parse_locality(name, city) is None


def test_an_override_matches_by_name_prefix_and_only_when_unique():
    overrides = [{"name": "Glasshouse 5BHK", "locality": "Koramangala"}]
    assert override_for("Glasshouse 5BHK Spacez Villa w/ Patio Koramangala", overrides) == ("Koramangala", 1)
    assert override_for("Aurora Heights", overrides) == (None, 0)

    twice = overrides + [{"name": "glasshouse", "locality": "Indiranagar"}]
    assert override_for("Glasshouse 5BHK Spacez Villa", twice) == (None, 2)

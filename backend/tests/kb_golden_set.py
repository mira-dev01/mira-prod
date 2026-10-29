"""Golden questions for tuning/regression-checking knowledge-base retrieval.

Each case is (question, expected) against GOLDEN_PROPERTY below, where expected
is a substring that MUST appear in the returned facts, or None meaning "nothing
on file -- must NOT come back as a confident match". Used by:
  - tests/test_kb_golden_set.py (lexical-only; runs everywhere, no API key)
  - scripts/tune_kb_retrieval.py (adds embeddings; run with a real
    OPENROUTER_API_KEY to choose KB_FOUND_COSINE / KB_POSSIBLE_COSINE)

Add real guest questions from your calls here -- the more realistic the set,
the better the thresholds.
"""

GOLDEN_PROPERTY = dict(
    name="Sunset Villa",
    city="Manali",
    base_price=5000,
    check_in_time="14:00",
    check_out_time="11:00",
    max_guests=6,
    minimum_nights=2,
    faq=[{"question": "Is breakfast included?", "answer": "Breakfast is not included; a cook can prepare it for 300 rupees per person."}],
    amenities=["WiFi", "Free parking", "Kitchen", "Hot water", "Bonfire pit", "Mountain view"],
    landmarks=[{"name": "Mall Road", "distance_minutes": 10, "mode": "drive"}],
    seasonal_notes=[],
    house_rules="No smoking indoors.\nPets are not allowed.\nQuiet hours after 10 PM.\nVisitors must be registered at check-in.",
    neighborhood_info=(
        "Mall Road is a 10 minute drive away. The nearest bus stand is 2 km from the villa. "
        "The closest hospital is a 15 minute drive. Cabs from the bus stand cost about 400 rupees."
    ),
    usp="A cosy wooden cottage with mountain views.",
)

# (question, expected substring in the facts | None = must be a miss)
GOLDEN_CASES = [
    # --- on file ---
    ("what time is check in", "Check-in time is 14:00"),
    ("what is the check out time", "Check-out time is 11:00"),
    ("how many people can stay", "maximum of 6 guests"),
    ("is smoking allowed", "No smoking indoors"),
    ("can I bring my dog", "Pets are not allowed"),
    ("pets allowed", "Pets are not allowed"),
    ("do you have wifi", "WiFi"),
    ("is there internet", "WiFi"),
    ("parking available", "Free parking"),
    ("is there a kitchen", "Kitchen"),
    ("is breakfast included", "Breakfast is not included"),
    ("how far is Mall Road", "10 minute drive"),
    ("how far is the bus stand", "2 km"),
    ("is there a hospital nearby", "hospital"),
    ("how much is a cab from the bus stand", "400 rupees"),
    ("quiet hours", "Quiet hours after 10 PM"),
    ("can visitors come over", "Visitors must be registered"),
    ("minimum nights", "minimum stay is 2 nights"),
    ("is there a bonfire", "Bonfire"),
    ("tell me about this place", "cosy wooden cottage"),
    # --- NOT on file: must never come back as a confident match ---
    ("how far is Delhi", None),
    ("distance from Chandigarh airport", None),
    ("is there a swimming pool", None),
    ("do you have a gym", None),
    ("is it safe for solo female travellers", None),
    ("what is the weather like in december", None),
    ("is there an ATM nearby", None),
    ("can you arrange a taxi to Delhi", None),
    ("is there a lift", None),
    ("do you allow loud music parties", None),
]

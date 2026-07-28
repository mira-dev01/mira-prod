from app.services.property_normalizer import NormalizedName, normalize_property_name


def test_empty_raw_name_returns_all_none():
    result = normalize_property_name("")
    assert result == NormalizedName()


def test_pipe_delimited_title_extracts_spoken_name_and_type():
    result = normalize_property_name("Pine - Glasshouse Suite w/bathtub | Pause Project")
    assert result.spoken_name == "Pine"
    assert result.property_type == "glasshouse"
    assert result.confidence == "high"


def test_bhk_count_extracted_from_bright_data_style_title():
    result = normalize_property_name("Nile w/pool & projector - Pause Project 1bhk")
    assert result.bedroom_count == 1
    assert result.spoken_name.startswith("Nile")


def test_star_rating_and_bedroom_bathroom_counts_stripped():
    result = normalize_property_name("Villa in Kecamatan Kuta Utara · ★5.0 · 2 bedrooms · 2 baths")
    assert result.property_type == "villa"
    assert result.bedroom_count == 2
    assert "★" not in (result.display_name or "")


def test_no_delimiter_no_type_word_is_low_confidence():
    result = normalize_property_name("random title with no delimiters at all whatsoever")
    assert result.confidence == "low"
    # Falls back to speaking the whole (unsplittable) string rather than
    # guessing a truncation.
    assert result.spoken_name == "random title with no delimiters at all whatsoever"


def test_hyphen_with_no_space_before_still_splits():
    # Real Bright Data title shape: "Name -Description" (no space after
    # the hyphen before this was fixed).
    result = normalize_property_name("Limon -Cozy forest mornings @ Pause Project 1bhk")
    assert result.spoken_name == "Limon"
    assert result.confidence == "high"


def test_pipe_in_raw_name_never_produces_a_torn_fragment():
    # Regression guard: a real name containing a literal "|" must never
    # produce a spoken_name that's an obviously broken fragment (empty,
    # or just punctuation).
    result = normalize_property_name("Azure 1bhk | 5 mins walk to beach | Pause Project")
    assert result.spoken_name
    assert result.spoken_name.strip(" -|·,")


def test_multi_word_first_segment_without_type_word_stays_whole():
    # No property-type word to anchor a safe truncation on -- must not
    # guess a shorter fragment that could read as a broken sentence.
    result = normalize_property_name("Nile w/pool & projector - Pause Project 1bhk")
    assert result.spoken_name == "Nile w/pool & projector"


def test_br_abbreviation_and_hyphen_glued_to_type_word():
    # Real title shape with no space around the hyphen at all, and a "BR"
    # abbreviation instead of "bedroom"/"bhk".
    result = normalize_property_name("Daloha 2BR luxury Villa-private pool in Canggu")
    assert result.bedroom_count == 2
    assert result.property_type == "villa"
    assert "-" not in (result.display_name or "")


def test_long_seo_stuffed_title_is_clamped_to_column_widths():
    # Regression: a real SEO-stuffed title with no delimiter this
    # normalizer recognizes as a split point (this feature's actual target
    # input) previously produced a display_name/spoken_name longer than
    # the DB columns (String(120)/String(60)), causing a
    # StringDataRightTruncation error at import time instead of a clean
    # (if imperfect) truncated name.
    title = (
        "Beautiful Spacious Sea Facing Penthouse With Amazing Panoramic View Of The "
        "Arabian Sea In Candolim Near Beach - Pause Project Collection"
    )
    result = normalize_property_name(title)
    assert len(result.display_name) <= 120
    assert len(result.spoken_name) <= 60


def test_single_unsplittable_word_longer_than_column_still_clamped():
    # No word boundary at all to truncate at -- must still hard-truncate
    # rather than exceed the column or raise.
    result = normalize_property_name("X" * 200)
    assert len(result.display_name) <= 120
    assert len(result.spoken_name) <= 60

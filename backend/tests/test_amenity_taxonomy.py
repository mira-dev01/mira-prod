from app.services.amenity_taxonomy import canonicalize_amenities, canonicalize_amenity


def test_known_synonyms_canonicalize_to_same_tag():
    assert canonicalize_amenity("Private pool") == "pool"
    assert canonicalize_amenity("Swimming Pool") == "pool"
    assert canonicalize_amenity("swimming pool") == "pool"


def test_unmapped_amenity_falls_back_to_lowercased_self():
    assert canonicalize_amenity("Rooftop Garden") == "rooftop garden"


def test_canonicalize_amenities_dedupes_and_sorts():
    result = canonicalize_amenities(["Private pool", "Swimming Pool", "Wifi", "WiFi"])
    assert result == ["pool", "wifi"]


def test_canonicalize_amenities_skips_blank_entries():
    assert canonicalize_amenities(["", "  ", "Wifi"]) == ["wifi"]


def test_rank_amenities_prefers_notable_over_generic():
    from app.services.amenity_taxonomy import rank_amenities_for_pitch

    amenities = ["Bath", "Hairdryer", "Cleaning products", "Private pool", "Projector", "Wifi"]
    assert rank_amenities_for_pitch(amenities) == ["Private pool", "Projector"]


def test_rank_amenities_falls_back_to_generic_when_no_notable_amenities():
    from app.services.amenity_taxonomy import rank_amenities_for_pitch

    amenities = ["Wifi", "Kitchen", "Parking"]
    assert rank_amenities_for_pitch(amenities) == ["Wifi", "Kitchen"]


def test_rank_amenities_respects_limit():
    from app.services.amenity_taxonomy import rank_amenities_for_pitch

    amenities = ["Private pool", "Bathtub", "Projector", "Ocean view"]
    result = rank_amenities_for_pitch(amenities, limit=3)
    assert len(result) == 3
    assert result == ["Private pool", "Bathtub", "Projector"]


def test_rank_amenities_empty_list():
    from app.services.amenity_taxonomy import rank_amenities_for_pitch

    assert rank_amenities_for_pitch([]) == []

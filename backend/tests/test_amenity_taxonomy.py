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

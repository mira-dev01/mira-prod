"""Location search by map position: resolve the area a guest names to a point
(or a state), then find the host's properties near it. All reads hit our own
database (the gazetteer tables), never an external geocoder, so a lookup adds
milliseconds to a live call and has no extra failure mode. See
app/models/place.py and scripts/load_gazetteer.py.
"""

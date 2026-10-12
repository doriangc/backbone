"""Constants shared across the pipeline: CRS, endpoints, layers and class definitions.

Endpoints, attribute names and licenses were verified on 2026-10-12 against:
- BGT OGC API landing page and OpenAPI doc: https://api.pdok.nl/kadaster/bgt/ogc/v1/api
- BGT catalogue (FunctieWeg codelist): https://docs.geostandaarden.nl/imgeo/catalogus/bgt/
- Luchtfoto WMS GetCapabilities: https://service.pdok.nl/hwh/luchtfotorgb/wms/v1_0?request=GetCapabilities&service=WMS
- Luchtfoto NGR metadata (license): https://www.nationaalgeoregister.nl/geonetwork/srv/api/records/00abe9ef-45f6-46bb-a227-2f653ae5fc6e
"""

RD_EPSG = 28992
WGS84_EPSG = 4326

# --- BGT -------------------------------------------------------------------
BGT_ITEMS_URL = "https://api.pdok.nl/kadaster/bgt/ogc/v1/collections/wegdeel/items"
BGT_LICENSE = "CC0 1.0 (http://creativecommons.org/publicdomain/zero/1.0/)"
RD_CRS_URI = "http://www.opengis.net/def/crs/EPSG/0/28992"
BGT_PAGE_LIMIT = 1000

# Values of the wegdeel `functie` attribute (BGT FunctieWeg codelist) counted as
# drivable surface. Approved by the project owner on 2026-10-12. OV-baan (bus and
# tram lanes) is included because it looks like ordinary carriageway from above.
# Speed bumps (`verkeersdrempel`) and emergency crossings (`calamiteitendoorsteek`)
# are IMGeo `plus_functie` refinements of these classes, so they come along.
BGT_DRIVABLE_FUNCTIES = frozenset(
    {
        "rijbaan autosnelweg",
        "rijbaan autoweg",
        "rijbaan regionale weg",
        "rijbaan lokale weg",
        "OV-baan",
        "overweg",
        "inrit",
        "woonerf",
    }
)

# --- Luchtfoto -------------------------------------------------------------
LUCHTFOTO_WMS_URL = "https://service.pdok.nl/hwh/luchtfotorgb/wms/v1_0"
LUCHTFOTO_LICENSE = "CC BY 4.0, attribution: Beeldmateriaal.nl"
WMS_MAX_PX = 2500  # MaxWidth/MaxHeight from GetCapabilities
WMS_TILE_PX = 2000

# Pinned to a year instead of `Actueel_*` so reruns fetch the same pixels.
# Note: the 8 cm and 25 cm products are separate flights, not resamples.
LUCHTFOTO_LAYERS = {
    "8cm": ("2025_orthoHR", 0.08),
    "25cm": ("2025_ortho25", 0.25),
}

# --- OSM -------------------------------------------------------------------
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OSM_LICENSE = "ODbL 1.0 (https://www.openstreetmap.org/copyright)"
# `track` is deliberately excluded (decision 2026-10-12).
OSM_DRIVABLE_HIGHWAYS = frozenset(
    {
        "motorway",
        "trunk",
        "primary",
        "secondary",
        "tertiary",
        "unclassified",
        "residential",
        "service",
    }
)
OSM_LINK_HIGHWAYS = frozenset({"motorway", "trunk", "primary", "secondary", "tertiary"})
# Parking is out of scope; other service roads (driveways, alleys, ...) stay in
# (decision 2026-10-12).
OSM_PARKING_SERVICE = frozenset({"parking_aisle", "parking"})

USER_AGENT = "backbone-research/0.1 (road surface polygons from aerial imagery)"

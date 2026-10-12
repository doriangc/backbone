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
BGT_API_URL = "https://api.pdok.nl/kadaster/bgt/ogc/v1"
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

# Paved classes (`fysiek_voorkomen`) of the onbegroeidterreindeel layer. They
# count as drivable only where a kept OSM service way runs over them; elsewhere
# they are plazas, schoolyards, etc. (decision 2026-10-12). `erf` is a whole
# yard (paving, gardens and trees, "not surveyed in more detail") and is never
# drivable.
BGT_PAVED_TERRAIN = frozenset({"gesloten verharding", "open verharding"})
# A service way is dropped when more than this share of its length lies on
# `erf`: private yards whose road surface BGT does not map (decision 2026-10-12).
OSM_SERVICE_MAX_ERF_SHARE = 0.5
# Minimum length (m) of a service way inside a paved terrain polygon for that
# polygon to count as drivable; avoids ways that only clip a corner or edge.
SERVICE_PAVED_MIN_OVERLAP_M = 1.0
SERVICE_PAVED_EDGE_M = 0.5

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

# --- Phase 2 baseline --------------------------------------------------------
# Default lane widths (m) per highway class (`_link` uses its base class).
# These are project assumptions, not values from a standard (approved 2026-10-12).
LANE_WIDTH_M = {
    "motorway": 3.5,
    "trunk": 3.5,
    "primary": 3.5,
    "secondary": 3.25,
    "tertiary": 3.25,
    "unclassified": 3.0,
    "residential": 3.0,
    "service": 3.0,
}
# Scoring geometry for the baseline (and later Phase 3 previews).
EVAL_MARGIN_M = 25.0  # only score inside the area box shrunk by this
CORRIDOR_HALF_WIDTH_M = 20.0  # reference surface is searched this far from the line
VORONOI_SPACING_M = 1.0  # centreline sampling for the nearest-centreline partition
GALLERY_MIN_LENGTH_M = 10.0

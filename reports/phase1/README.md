# Phase 1: Data acquisition, three pilot areas

Run on 2026-10-12. Boxes in EPSG:28992, 2 x 2 km each (`src/backbone/areas.py`):

| Area | Type | Box | Character |
|---|---|---|---|
| `utrecht_centrum` | city | `135900,454900,137900,456900` | old town, singels, station area, ring roads |
| `houten` | suburb | `139000,448100,141000,450100` | residential loops and cul-de-sacs, ring road with roundabouts, business park |
| `benschop` | rural | `126400,445400,128400,447400` | ribbon village on a dike road, polder roads, provincial road with parallel road |

Place names were checked against BGT `wijk` (Houten Noord-West) and BAG
`woonplaats` (Benschop).

```
uv sync
uv run python -m backbone.fetch --area <area>   # ~4-5 min, ~300 MB in data/<area>/
uv run python -m backbone.qa --area <area>      # ~1 min, writes reports/phase1/<area>/
uv run --group dev pytest
```

## Per-area summary

| Area | BGT wegdeel current (drivable) | OSM ways kept / excluded | Median share of OSM length on drivable label: non-service / service | 8 cm offset: median dx, dy / worst (windows used) | 25 cm offset: median dx, dy / worst |
|---|---|---|---|---|---|
| `utrecht_centrum` | 10,260 (2,861) | 1,068 / 253 | 1.00 / 0.85 | +0.00, +0.16 / 0.36 m (16/16) | +0.00, +0.12 / 2.06 m |
| `houten` | 8,189 (2,302) | 1,071 / 265 | 1.00 / 1.00 | +0.00, +0.16 / 0.23 m (16/16) | +0.00, +0.00 / 0.35 m |
| `benschop` | 1,049 (471) | 110 / 29 | 1.00 / 0.97 | +0.00, +0.16 / 1.79 m (11/16) | +0.25, -0.25 / 0.79 m |

Alignment windows with fewer than 5,000 BGT edge pixels (fields only) are
skipped. The 1.79 m Benschop window holds a single straight provincial road:
the shift is almost entirely along the road (cross-road component about
0.2 m), which a single straight road cannot constrain. An 8 cm crop shows
BGT on the visible asphalt.

The detailed numbers below are for `utrecht_centrum`.

## Sources (verified against official docs on 2026-10-12)

| Source | Endpoint | Layer / filter | License |
|---|---|---|---|
| BGT wegdeel | OGC API Features `https://api.pdok.nl/kadaster/bgt/ogc/v1/collections/wegdeel/items`, native EPSG:28992 | current versions only (see below) | CC0 1.0 |
| Luchtfoto 8 cm | WMS `https://service.pdok.nl/hwh/luchtfotorgb/wms/v1_0` | `2025_orthoHR` | CC BY 4.0, credit Beeldmateriaal.nl |
| Luchtfoto 25 cm | same | `2025_ortho25` | CC BY 4.0, credit Beeldmateriaal.nl |
| BGT onbegroeidterreindeel | same API, collection `onbegroeidterreindeel` | current, ground level; paved classes used under service ways (see below) | CC0 1.0 |
| OSM | Overpass `https://overpass-api.de/api/interpreter` | drivable `highway` values from SPEC, no `track`, no `area=yes`, no parking, no roads hidden from above, no service roads in private yards (see below) | ODbL 1.0 |

BGT `functie` values come from the BGT catalogue (FunctieWeg codelist,
https://docs.geostandaarden.nl/imgeo/catalogus/bgt/). Drivable = `rijbaan *`,
`OV-baan`, `overweg`, `inrit`, `woonerf` (`src/backbone/config.py`).

## Results

| | Count |
|---|---|
| BGT versions returned by API | 26,426 |
| BGT current objects | 10,260 (2,861 drivable) |
| OSM drivable ways kept | 1,068 (638 residential, 161 service, 136 unclassified, 93 tertiary, 40 secondary) |
| OSM ways excluded | 253 (10.5 km); reasons can combine: parking 131, not visible 64, private yard 134 (68 of them for that reason only) |
| BGT paved terrain under a service way | 11 polygons, 9,700 m² |
| Imagery 8 cm | 25,000 x 25,000 px, 159 MB, 0.07% blank |
| Imagery 25 cm | 8,000 x 8,000 px, 25 MB, 0.02% blank |

**Alignment (automated):** BGT boundaries were shifted over the image gradient in
16 windows of 500 m to find the best-fit offset (`alignment.json`, `alignment.png`).

- 8 cm: median (0.00, +0.16) m, worst window 0.36 m. That is within 1 to 2 analysis pixels.
- 25 cm: median (0.00, +0.12) m, but 2 windows reach 0.8 and 2.1 m. Crops of
  those windows show kerbs aligned by eye. The outliers sit at the edge of the
  search range and come from roof edges: the 25 cm flight leans buildings
  differently, and BGT's outer road boundaries follow the facades.

**OSM exclusions** (`fetch.exclusion_reasons`, decision 2026-10-12). Excluded
ways are kept in the `excluded` layer of `osm_drivable.gpkg` and drawn dashed
magenta in the overlays.
- `parking`: `service=parking_aisle|parking`, any `parking=*`, `amenity=parking`.
- `not_visible`: `tunnel` (incl. `building_passage`), `covered`, `indoor`,
  `location=underground|indoor`, or a negative `level`. This applies to all
  classes; 6 of the 64 are residential or unclassified, e.g. Catharijnesingel
  under Hoog Catharijne. A negative `layer` alone does not exclude: in this area
  those are open garage ramps.
- `private_yard`: service ways with more than 50% of their length on BGT
  onbegroeidterreindeel `erf` (whole yards; BGT does not map the road surface
  there). The erf share is bimodal (151 service ways near 0%, 59 above 70%),
  so the threshold is not sensitive. `access=private` was not used: 41 private
  service ways lie on mapped road surface.

**Paved terrain under service ways** (`fetch.mark_service_paved`): BGT
onbegroeidterreindeel polygons with `fysiek_voorkomen` `gesloten verharding` or
`open verharding` count as drivable (`service_paved` in
`bgt_onbegroeidterreindeel.gpkg`, green in the overlays) when a kept OSM service
way runs at least 1 m through them, measured 0.5 m inside the polygon edge so a
way along the edge does not count. Elsewhere these classes are plazas and
schoolyards and are not drivable.

**OSM vs BGT:** share of each OSM way's length that lies inside BGT drivable polygons
(median): residential, secondary, tertiary, unclassified 1.00 (mean 0.92 to 0.96);
service 0.85 against wegdeel drivable plus paved terrain (0.22 before any of
the service rules; 75% of the kept service length is covered). 21 of 161 kept
service ways are still under 20% covered; they lie mostly on `voetpad` (55%)
and `parkeervlak` (19%), e.g. brick paths used by delivery vehicles. Phase 3
should flag these as "no polygon found" rather than filter them here.

**By eye:** `overview.jpg` and the three 8 cm crops (`crop_*.jpg`) show BGT
kerbs on the visible kerbs and OSM centrelines inside the carriageways.

## Surprises and open issues

1. **The BGT OGC API returns full history.** 13k duplicate `lokaal_id`; summed area is
   4x the union. `fetch.bgt_current` keeps rows with `eind_registratie` and
   `termination_date` both null. After that: no duplicate ids, and ground-level
   (`relatieve_hoogteligging = 0`) polygons tile the plane exactly (sum of
   areas equals area of union).
2. **8 cm and 25 cm are different flights** (different season, sun and lean),
   not a resample. For the Phase 4 resolution augmentation, downsampling the
   8 cm imagery gives consistent pairs; the 25 cm product is better kept as a
   separate domain test.
3. **OSM `service` ways often have no BGT drivable surface, even after the
   exclusions.** `notebooks/service_roads_bgt.py` attributes the 9.6 km of kept
   service ways to the BGT ground layers (which together cover 100.1% of that
   length, so the layers tile the ground):

   | BGT layer / class | share of service length |
   |---|---|
   | wegdeel `rijbaan lokale weg` | 31.9% |
   | onbegroeidterreindeel `erf` | 31.4% |
   | onbegroeidterreindeel `gesloten verharding` | 11.9% |
   | wegdeel `voetpad` | 8.9% |
   | wegdeel `parkeervlak` | 5.7% |
   | wegdeel `OV-baan` | 3.7% |
   | onbegroeidterreindeel `open verharding` | 2.6% |
   | other (water banks, green, buildings, ...) | ~4% |

   By OSM type, `driveway` and `emergency_access` lie about 75% on
   onbegroeidterreindeel; untyped service ways about 30%.
   The BGT catalogue defines `erf` as the terrain belonging to a building,
   "not surveyed in more detail", a mix of vegetation, paving and water. In the
   imagery an `erf` polygon is a whole courtyard with gardens and trees, so it
   cannot be a road label. `gesloten verharding` (closed paving: asphalt,
   concrete) and `open verharding` (bricks, tiles) follow the paved surface,
   e.g. the service road along the rail yard.

   Resolution (2026-10-12): drop service ways mostly on `erf`, and use the
   paved classes only under service ways (see OSM exclusions above).
4. **BGT keeps covered roads at ground level.** The carriageway under
   Hoog Catharijne is `rijbaan lokale weg` with `relatieve_hoogteligging = 0`,
   although a building covers it. Filtering BGT by level will not remove it;
   masking with the BGT `pand` layer might.
5. **BGT calls some pedestrian squares `rijbaan lokale weg`**, e.g. the
   market square in `crop_market_square.jpg` and the square in the top left of
   `crop_pausdam.jpg`, with no OSM drivable way on them. These will show up as
   unmatched polygons in Phase 3.
6. `calamiteitendoorsteek` occurs in none of the three areas and `woonerf`
   only once (Houten), so their handling is barely tested.
7. **OSM and BGT disagree on some rural parallel roads.** Benschop's
   Ingenieur F E D Enschedeweg parallel road is OSM `unclassified`
   (`agricultural=yes`, `bicycle=yes`, 60 km/h) but BGT `fietspad`. Phase 3
   will find no carriageway there; the multi-class mask will call it cycle path.
8. **`inrit` (driveway entrance) polygons are common in suburbs** (598 in
   Houten). They count as drivable and touch the carriageway, so profiles that
   cross one will read wide; Phase 3's spread flag and medians should absorb
   this, to be checked on overlays.
9. Imagery is pinned to the 2025 layers; BGT and OSM are live snapshots (dates
   in `data/<area>/manifest.json`), so the imagery can lag recent road works.

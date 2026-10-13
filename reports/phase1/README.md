# Phase 1: Data acquisition, 20 areas

Run on 2026-10-12: three pilot areas, then 17 more after the decision to scale
up to 20 (7 city, 7 suburb, 6 rural). Boxes in EPSG:28992, 2 x 2 km each
(`src/backbone/areas.py`). Pilot characters:

| Area | Type | Character |
|---|---|---|
| `utrecht_centrum` | city | old town, singels, station area, ring roads |
| `houten` | suburb | residential loops and cul-de-sacs, ring road with roundabouts, business park |
| `benschop` | rural | ribbon village on a dike road, polder roads, provincial road with parallel road |

The 17 added areas spread over the country: city centres of Amsterdam,
Rotterdam, Den Haag, Groningen, Maastricht and Eindhoven; post-1970 suburbs in
Almere, Zoetermeer, Amersfoort, Apeldoorn, Breda and Zwolle; villages and
countryside in Friesland (Wommels), Zeeland (Kapelle), Drenthe (Westerbork),
Zuid-Limburg (Gulpen, hilly) and the Achterhoek (Aalten). Place names were
checked against BGT `wijk` and BAG `woonplaats`. Two first picks were replaced
after looking at them: a Breda box that was industrial, and an Aalten box that
was mostly town. The added areas have no hand-picked crops. `qa.auto_crops`
renders the centre plus the midpoint of the longest way of each of the two most
common road classes.

```
uv sync
uv run python -m backbone.fetch --area <area>   # ~4-9 min, 150-350 MB in data/<area>/
uv run python -m backbone.qa --area <area>      # ~1 min, writes reports/phase1/<area>/
uv run --group dev pytest
```

## Per-area summary

| Area | Type | Box | BGT wegdeel current (drivable) | OSM ways kept / excluded | 8 cm offset: median dx, dy / worst (windows) | 25 cm offset: median dx, dy / worst |
|---|---|---|---|---|---|---|
| `utrecht_centrum` | city | `135900,454900,137900,456900` | 10,260 (2,862) | 1,136 / 276 | +0.00, +0.16 / 0.36 m (16/16) | +0.00, +0.12 / 2.06 m (16/16) |
| `houten` | suburb | `139000,448100,141000,450100` | 8,189 (2,335) | 1,072 / 265 | +0.00, +0.16 / 0.23 m (16/16) | +0.00, +0.00 / 0.35 m (16/16) |
| `benschop` | rural | `126400,445400,128400,447400` | 1,049 (477) | 110 / 29 | +0.00, +0.16 / 1.79 m (11/16) | +0.25, -0.25 / 0.79 m (11/16) |
| `amsterdam_centrum` | city | `120400,486300,122400,488300` | 8,379 (1,970) | 1,400 / 44 | +0.00, +0.08 / 1.63 m (16/16) | +0.00, +0.00 / 2.02 m (16/16) |
| `rotterdam_centrum` | city | `91600,436300,93600,438300` | 12,218 (1,903) | 1,187 / 267 | +0.00, +0.00 / 0.64 m (16/16) | -0.25, +0.00 / 2.02 m (16/16) |
| `den_haag_centrum` | city | `80300,454000,82300,456000` | 10,299 (2,086) | 1,251 / 347 | +0.08, +0.00 / 1.28 m (16/16) | +0.00, +0.50 / 1.58 m (16/16) |
| `groningen_centrum` | city | `232900,581000,234900,583000` | 6,324 (1,873) | 1,180 / 236 | +0.00, +0.16 / 0.32 m (16/16) | +0.00, +0.25 / 2.06 m (16/16) |
| `maastricht_centrum` | city | `175400,317000,177400,319000` | 5,629 (1,507) | 1,065 / 185 | +0.00, +0.08 / 0.23 m (16/16) | +0.00, +0.25 / 0.56 m (16/16) |
| `eindhoven_centrum` | city | `160300,382300,162300,384300` | 8,404 (1,787) | 1,300 / 339 | +0.00, +0.08 / 0.23 m (16/16) | +0.25, +0.25 / 0.35 m (16/16) |
| `almere_buiten` | suburb | `145700,488400,147700,490400` | 10,386 (2,113) | 590 / 144 | +0.00, +0.00 / 0.23 m (16/16) | +0.00, +0.00 / 0.25 m (16/16) |
| `zoetermeer` | suburb | `92500,451500,94500,453500` | 6,666 (1,614) | 1,011 / 353 | +0.00, +0.16 / 0.23 m (16/16) | +0.25, +0.00 / 0.35 m (16/16) |
| `amersfoort_vathorst` | suburb | `156900,466400,158900,468400` | 5,563 (1,932) | 715 / 180 | +0.00, +0.00 / 0.80 m (16/16) | +0.00, +0.00 / 0.50 m (16/16) |
| `apeldoorn_zuid` | suburb | `193200,466000,195200,468000` | 6,629 (3,255) | 547 / 131 | +0.08, +0.16 / 0.36 m (16/16) | +0.00, +0.00 / 0.35 m (16/16) |
| `breda_haagse_beemden` | suburb | `108800,402400,110800,404400` | 5,819 (1,852) | 398 / 53 | +0.00, +0.00 / 0.32 m (16/16) | +0.00, -0.25 / 0.56 m (16/16) |
| `zwolle_stadshagen` | suburb | `199700,503900,201700,505900` | 4,950 (1,465) | 753 / 84 | +0.00, +0.16 / 0.32 m (16/16) | +0.00, -0.25 / 0.50 m (16/16) |
| `wommels` | rural | `167600,568300,169600,570300` | 1,311 (716) | 212 / 41 | +0.00, +0.16 / 0.23 m (13/16) | +0.00, +0.00 / 0.35 m (13/16) |
| `kapelle` | rural | `54100,386800,56100,388800` | 1,049 (466) | 221 / 29 | +0.00, +0.00 / 2.08 m (15/16) | +0.00, +0.00 / 1.03 m (15/16) |
| `westerbork` | rural | `236400,540000,238400,542000` | 1,967 (1,379) | 332 / 502 | +0.08, +0.16 / 1.82 m (16/16) | -0.25, +0.00 / 0.56 m (16/16) |
| `gulpen` | rural | `189400,312500,191400,314500` | 2,896 (1,347) | 403 / 70 | +0.00, +0.00 / 2.00 m (16/16) | +0.00, +0.00 / 1.03 m (16/16) |
| `aalten_buitengebied` | rural | `237400,440500,239400,442500` | 349 (289) | 55 / 8 | +0.00, +0.16 / 2.50 m (16/16) | +0.00, +0.00 / 2.66 m (15/16) |

Alignment windows with fewer than 5,000 BGT edge pixels (fields only) are
skipped. Blank (white or black) imagery is at most 0.2% per area.

**Worst-window offsets above 1 m** fall into two groups, checked on 8 cm crops:
- **Rural, few edges** (Benschop 1.79, Kapelle 2.08, Westerbork 1.82, Gulpen
  2.00, Aalten 2.50 m). Edge counts are 6,900-31,000 pixels against
  120,000-300,000 in cities. These windows hold one straight road or none:
  Benschop's shift is almost entirely along the road (cross-road component
  about 0.2 m), and the Aalten window is a ploughed field whose furrows
  dominate the gradient.
- **City centres, roof lean** (Amsterdam 1.63, Den Haag 1.28 m at 8 cm; most
  city windows at 25 cm). In the Amsterdam window the BGT road edges sit on the
  kerbs, but roofs are displaced 1-2 m from the BGT building footprints: the
  imagery is not a true orthophoto, and BGT's outer road boundaries follow the
  facades.

Medians are 0.00-0.16 m everywhere (one to two 8 cm pixels), so there is no
systematic offset. Building lean matters for Phase 4: near tall buildings,
image roof edges can sit 1-2 m from the BGT outlines that border the
pavement.

**Westerbork excludes more OSM ways than it keeps** (502 vs 332). 473 are
service ways on BGT `erf`, 384 of them tagged `service=driveway`: local mappers
drew every private driveway. This is the private-yard rule working as intended.

Across all 20 areas, `calamiteitendoorsteek` occurs in none, `woonerf` 180
times, `OV-baan` 637, `overweg` 119 and `inrit` 6,836 times.

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
6. `calamiteitendoorsteek` occurs in none of the 20 areas, so its handling
   is untested. `woonerf` was rare in the pilots (once, in Houten) but occurs
   180 times across the 20 areas.
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

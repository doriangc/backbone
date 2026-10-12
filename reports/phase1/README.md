# Phase 1: Data acquisition, pilot `utrecht_centrum`

Area: EPSG:28992 box `135900,454900,137900,456900` (2 x 2 km, central Utrecht).
Run on 2026-10-12.

```
uv sync
uv run python -m backbone.fetch --area utrecht_centrum   # ~4 min, ~280 MB in data/
uv run python -m backbone.qa --area utrecht_centrum      # ~1 min, writes this folder
uv run --group dev pytest
```

## Sources (verified against official docs on 2026-10-12)

| Source | Endpoint | Layer / filter | License |
|---|---|---|---|
| BGT wegdeel | OGC API Features `https://api.pdok.nl/kadaster/bgt/ogc/v1/collections/wegdeel/items`, native EPSG:28992 | current versions only (see below) | CC0 1.0 |
| Luchtfoto 8 cm | WMS `https://service.pdok.nl/hwh/luchtfotorgb/wms/v1_0` | `2025_orthoHR` | CC BY 4.0, credit Beeldmateriaal.nl |
| Luchtfoto 25 cm | same | `2025_ortho25` | CC BY 4.0, credit Beeldmateriaal.nl |
| OSM | Overpass `https://overpass-api.de/api/interpreter` | drivable `highway` values from SPEC, no `track`, no `area=yes`, no parking, no roads hidden from above (see below) | ODbL 1.0 |

BGT `functie` values come from the BGT catalogue (FunctieWeg codelist,
https://docs.geostandaarden.nl/imgeo/catalogus/bgt/). Drivable = `rijbaan *`,
`OV-baan`, `overweg`, `inrit`, `woonerf` (`src/backbone/config.py`).

## Results

| | Count |
|---|---|
| BGT versions returned by API | 26,426 |
| BGT current objects | 10,260 (2,861 drivable) |
| OSM drivable ways kept | 1,136 (638 residential, 229 service, 136 unclassified, 93 tertiary, 40 secondary) |
| OSM ways excluded | 185: 121 parking (5.1 km), 54 not visible (2.0 km), 10 both |
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

**OSM vs BGT:** share of each OSM way's length that lies inside BGT drivable polygons
(median): residential, secondary, tertiary, unclassified 1.00 (mean 0.92 to 0.96);
service 0.28 (was 0.22 before the exclusions).

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
   exclusions.** Of the 9.6 km of remaining service ways, 48% lies on no
   wegdeel at all (private yards and courtyards, presumably in other BGT
   layers), 33% on `rijbaan lokale weg`, 9% on `voetpad` and 6% on `parkeervlak`.
   Underground roads were only a small part of the mismatch.
4. **BGT keeps covered roads at ground level.** The carriageway under
   Hoog Catharijne is `rijbaan lokale weg` with `relatieve_hoogteligging = 0`,
   although a building covers it. Filtering BGT by level will not remove it;
   masking with the BGT `pand` layer might.
5. **BGT calls some pedestrian squares `rijbaan lokale weg`**, e.g. the
   market square in `crop_market_square.jpg` and the square in the top left of
   `crop_pausdam.jpg`, with no OSM drivable way on them. These will show up as
   unmatched polygons in Phase 3.
6. `calamiteitendoorsteek` and `woonerf` do not occur in this area, so their
   handling is untested.
7. Imagery is pinned to the 2025 layers; BGT and OSM are live snapshots (dates
   in `data/<area>/manifest.json`), so the imagery can lag recent road works.

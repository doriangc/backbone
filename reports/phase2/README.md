# Phase 2: Tag-based width baseline

Run on 2026-10-12 over the three pilots, then rerun over all 20 areas
(`src/backbone/areas.py`). The numbers below are for all 20.

```
uv run python -m backbone.baseline --area utrecht_centrum --area houten ...   # one --area per area, ~10 min for 20
```

Files: `metrics.md` (all tables), `segments.csv` (one row per scored segment),
`worst_0[1-5].jpg` + `worst50.csv` (gallery), `noref_0[1-2].jpg` +
`no_reference.csv` (segments without usable BGT reference).

## Method

- **Segments** (`src/backbone/segments.py`): kept OSM ways split at nodes shared
  with another kept way, at revisited nodes, and at way ends. 2,249 ways gave
  3,247 segments; 3,144 lie (partly) inside the scoring box (area box shrunk by 25 m).
- **Tag width**: `width` tag (kerb to kerb per the OSM wiki), else `lanes` x lane
  width, else class default (1 lane if oneway or service, else 2). Lane widths:
  3.0 m residential/service/unclassified, 3.25 m tertiary/secondary, 3.5 m
  primary and up. Buffered symmetrically with flat ends.
- **Reference**: BGT drivable surface (wegdeel drivable classes plus paved
  terrain under service ways) within 20 m of the segment, restricted to the
  segment's nearest-centreline cell. Excluded OSM ways (parking aisles, hidden
  roads, private yards) also claim cells, so a parking lot's aisles are not
  counted against the street beside it.
- **Rough width error**: reference area / segment length vs tag width. It
  includes junction wedges, so it is only a preview of Phase 3's profiles.
- 157 segments (5.3 km) whose cell holds less than 1.5 m of reference width are
  reported separately as "no usable reference".

## Results (19,039 segments, 1,092 km with usable reference)

| Area type | Segments | km | IoU (length-weighted) | IoU median | Share IoU < 0.5 | Width MAE (m) | Width bias (m) |
|---|---|---|---|---|---|---|---|
| city | 9,830 | 505.0 | 0.69 | 0.71 | 17% | 1.07 | -0.40 |
| suburb | 7,406 | 445.9 | 0.72 | 0.75 | 11% | 1.13 | +0.29 |
| rural | 1,803 | 141.3 | 0.69 | 0.74 | 10% | 1.09 | +0.42 |

The three pilots alone gave 0.71 / 0.72 / 0.65; the 17 added areas did not
change the picture.

| Road class | Segments | km | IoU (length-weighted) | Share IoU < 0.5 | Tag width median | Ref width median | Width bias |
|---|---|---|---|---|---|---|---|
| residential | 10,527 | 635.7 | 0.71 | 8% | 6.0 | 4.7 | +0.29 |
| unclassified | 1,549 | 101.7 | 0.66 | 13% | 6.0 | 5.2 | -0.12 |
| tertiary | 2,152 | 106.9 | 0.73 | 11% | 6.5 | 5.7 | -0.37 |
| secondary | 1,617 | 87.4 | 0.74 | 9% | 6.5 | 5.8 | -0.49 |
| primary | 563 | 38.2 | 0.79 | 5% | 7.0 | 6.6 | +0.17 |
| motorway | 22 | 8.3 | 0.62 | 0% | 7.0 | 11.5 | -3.50 |
| service | 1,839 | 75.6 | 0.48 | 53% | 3.0 | 4.6 | -1.58 |
| busway | 626 | 27.2 | 0.74 | 20% | 3.25 | 5.1 | -0.41 |

Other classes are as in `metrics.md`.

| Width source | Segments | km | IoU (length-weighted) | Width MAE | Width bias |
|---|---|---|---|---|---|
| class default | 13,721 | 812.5 | 0.68 | 1.14 | +0.06 |
| `lanes` | 4,721 | 246.3 | 0.74 | 1.03 | -0.42 |
| `width` | 597 | 33.3 | 0.77 | 0.53 | -0.10 |

Medians, except IoU (length-weighted). Bias = tag width - reference width.
Segments without usable reference: 947 (27.4 km): 554 service, 255
residential, 71 busway.

## What the gallery shows

The SPEC asks whether failures cluster at wide roads, junctions or missing tags.

- **Service roads still dominate.** 27 of the worst 50 are service roads
  (10% of all segments). Busways are the next group (9 of 50), almost all in
  Den Haag (see open issue 5).
- **Wide roads are underestimated.** IoU by reference width: 0.67 (< 4 m),
  0.72 (4-6 m), 0.73 (6-8 m), 0.64 (8-12 m), 0.51 (> 12 m), with median bias
  -2.4 m at 8-12 m and -7.6 m above 12 m. Motorways are 3.5 m too narrow on
  median: two lanes of 3.5 m against 11.5 m of BGT carriageway, which includes
  the hard shoulder.
- **Junctions matter less than expected.** Median IoU is flat across segment
  length (0.70 to 0.74 from < 10 m to > 100 m).
- **Missing tags are not what separates the worst cases.** 70% of the worst 50
  use class defaults vs 72% overall. Tags are sparse: `width` covers 3% of
  length and `lanes` 23%. Where `width` is tagged, error is lowest
  (MAE 0.53 m).
- **Default 2-lane residential width (6 m) is too wide** for Dutch residential
  streets (median reference 4.7 m). Oneway city streets go the other way
  (3 m default, bias -0.36 m for city residential).

## Surprises and open issues

1. **Resolved: bus-only roads.** BGT `OV-baan` is drivable, but OSM
   `highway=busway` was not in the drivable list, so bus-lane surface leaked
   into neighbouring segments' references. Busways are now included (68 kept
   in Utrecht; lane width 3.25 m, an assumption).
2. **Resolved: bicycle streets mapped as cycle path.** BGT maps some Dutch
   bicycle streets (fietsstraten) as `fietspad`. A `fietspad` polygon now counts
   as drivable when a kept OSM way runs along at least half its length (33
   polygons in Houten, 6 in Benschop, 1 in Utrecht). Checked on 8 cm crops:
   bicycle streets and the Benschop parallel road are picked up; separate cycle
   paths beside roads are not.
3. **The OSM `width` tag did not overestimate.** Despite meaning kerb to kerb,
   tagged widths are 0.1 m *below* the rough reference on median. Phase 3
   confirms that Dutch `width` tags match carriageway width.
4. The rough reference width counts junction wedges and side areas inside a
   segment's cell; Phase 3's profiles replace it.
5. **Den Haag's shared tram/bus lanes have no BGT road surface.** 34 of the
   38 Den Haag busway segments without a reference carry `embedded_rails=tram`,
   and BGT maps their surface as onbegroeidterreindeel `gesloten verharding`
   (1.1 km) or `open verharding` (0.7 km), not `OV-baan`. Our rule counts paved
   terrain as drivable only under service ways, so these segments get no
   reference, and their cells take surface from the parallel car lanes
   (IoU 0 in the gallery). **Decision needed:** extend the paved-terrain rule
   to busways. That is the same rule already used for service roads, and would
   be a one-line change in `fetch.mark_service_paved`.
6. **Pedestrian streets with an OSM service or residential way.** Most of
   the remaining no-reference length lies on BGT `voetpad` or
   `voetgangersgebied` (e.g. 1.8 km of service ways in Den Haag): shopping
   streets that allow delivery traffic. They correctly have no carriageway in
   BGT and stay unscored.

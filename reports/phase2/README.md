# Phase 2: Tag-based width baseline

Run on 2026-10-12 over `utrecht_centrum` (city), `houten` (suburb) and
`benschop` (rural):

```
uv run python -m backbone.baseline --area utrecht_centrum --area houten --area benschop   # ~45 s
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

## Results (3,087 segments, 162 km with usable reference)

Updated 2026-10-12 after including `highway=busway` and counting `fietspad`
under a drivable OSM way as drivable (open issues 1 and 2 below).

| Area type | Segments | km | IoU (length-weighted) | IoU median | Share IoU < 0.5 | Width MAE (m) | Width bias (m) |
|---|---|---|---|---|---|---|---|
| city | 1,419 | 73.8 | 0.71 | 0.71 | 14% | 1.07 | -0.54 |
| suburb | 1,512 | 73.5 | 0.72 | 0.75 | 7% | 1.17 | +0.73 |
| rural | 156 | 14.7 | 0.65 | 0.77 | 8% | 0.98 | +0.43 |

| Road class | Segments | km | IoU (length-weighted) | Share IoU < 0.5 | Tag width median | Ref width median | Width bias |
|---|---|---|---|---|---|---|---|
| residential | 2,063 | 110.3 | 0.73 | 5% | 6.0 | 4.7 | +0.64 |
| service | 308 | 11.5 | 0.50 | 46% | 3.0 | 4.6 | -1.55 |
| busway | 68 | 3.8 | 0.70 | 22% | 3.25 | 6.7 | -1.24 |

Other classes are as in `metrics.md`.

| Width source | Segments | km | IoU (length-weighted) | Width MAE | Width bias |
|---|---|---|---|---|---|
| class default | 2,606 | 137.7 | 0.70 | 1.15 | +0.39 |
| `lanes` | 401 | 19.8 | 0.75 | 1.03 | -0.75 |
| `width` | 80 | 4.5 | 0.73 | 0.77 | -0.29 |

Medians, except IoU (length-weighted). Bias = tag width - reference width.
Segments without usable reference: 128 (3.1 km), down from 157 (5.3 km); 88 of
them are service ways, mostly on BGT `voetpad` and `parkeervlak`.

## What the gallery shows

The SPEC asks whether failures cluster at wide roads, junctions or missing tags.

- **Service roads and open paved areas dominate.** 37 of the worst 50 are
  service roads (10% of all segments); 30% have more than 10 m of reference
  width (2% overall). By eye these are industrial yards, bus stations, squares,
  quays and parking lots, where one centreline runs through a wide paved area
  and "width" is not well defined.
- **Wide roads are underestimated.** IoU by reference width: 0.72 (< 4 m),
  0.76 (4-6 m), 0.73 (6-8 m), 0.59 (8-12 m), 0.33 (> 12 m), with bias down to
  -9 m. Tags rarely say how wide a wide road is.
- **Junctions matter less than expected.** Median IoU is flat across segment
  length (0.71 to 0.74 from < 10 m to > 100 m). Short segments are 36% of the
  worst 50 vs 30% overall. The junction failures that do appear are short
  segments crossing a wider road, whose cell includes part of that road.
- **Missing tags are not what separates the worst cases.** 88% of the worst 50
  use class defaults vs 85% overall. Tags are missing almost everywhere: `width`
  covers 2.4% of length and `lanes` 12%. Where `width` is tagged, error is lowest
  (MAE 0.77 m).
- **Default 2-lane residential width (6 m) is too wide** for Dutch residential
  streets (median reference 4.7 m, bias +0.7 m). Oneway city streets go the
  other way (3 m default).

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
   tagged widths are 0.3 m *below* the rough reference on median. The rough
   reference includes junction wedges, so Phase 3 should recheck this.
4. The rough reference width counts junction wedges and side areas inside a
   segment's cell; Phase 3's profiles replace it.

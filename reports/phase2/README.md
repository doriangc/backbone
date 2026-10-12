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

## Results (2,987 segments, 156 km with usable reference)

| Area type | Segments | km | IoU (length-weighted) | IoU median | Share IoU < 0.5 | Width MAE (m) | Width bias (m) |
|---|---|---|---|---|---|---|---|
| city | 1,361 | 70.2 | 0.70 | 0.71 | 17% | 1.12 | -0.57 |
| suburb | 1,471 | 71.2 | 0.72 | 0.75 | 7% | 1.16 | +0.71 |
| rural | 155 | 14.6 | 0.64 | 0.77 | 9% | 0.97 | +0.43 |

| Road class | Segments | km | IoU (length-weighted) | Share IoU < 0.5 | Tag width median | Ref width median | Width bias |
|---|---|---|---|---|---|---|---|
| residential | 2,024 | 108.1 | 0.73 | 6% | 6.0 | 4.7 | +0.62 |
| service | 321 | 11.7 | 0.47 | 51% | 3.0 | 4.8 | -1.77 |
| unclassified | 245 | 13.3 | 0.62 | 15% | 6.0 | 5.0 | -0.35 |
| tertiary | 223 | 13.4 | 0.77 | 9% | 6.5 | 6.2 | -0.66 |
| secondary | 167 | 7.8 | 0.73 | 8% | 3.25 | 5.8 | -0.72 |
| primary | 6 | 1.6 | 0.64 | 0% | 3.5 | 4.6 | -0.76 |

| Width source | Segments | km | IoU (length-weighted) | Width MAE | Width bias |
|---|---|---|---|---|---|
| class default | 2,553 | 134.4 | 0.70 | 1.16 | +0.36 |
| `lanes` | 354 | 17.1 | 0.74 | 1.04 | -0.79 |
| `width` | 80 | 4.5 | 0.73 | 0.77 | -0.32 |

Medians, except IoU (length-weighted). Bias = tag width - reference width.

## What the gallery shows

The SPEC asks whether failures cluster at wide roads, junctions or missing tags.

- **Service roads and open paved areas dominate.** 64% of the worst 50 are
  service roads (11% of all segments); 46% have more than 10 m of reference
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

1. **Bus-only roads are missing from the OSM set.** BGT drivable includes
   `OV-baan` (approved), but OSM bus-only roads are `highway=busway`, which is
   not in the drivable list. Utrecht has 93 busway ways and 42,000 m² of
   `OV-baan`; only 10 of its 63 `OV-baan` polygons touch a kept OSM way. The
   rest of that surface leaks into neighbouring segments' references (several
   of the worst 50). **Decision needed:** add `highway=busway` to the drivable
   OSM list (scored), or only let busways claim their surface (not scored).
2. **BGT maps some bicycle streets (fietsstraten) as `fietspad`.** In Houten,
   66% of the length of segments without reference lies on `fietspad`; 18 of
   those 59 ways are `cyclestreet=yes`, while 46 other bicycle streets in
   Houten are mapped as carriageway. These streets look like roads and carry
   cars. **Decision needed:** count `fietspad` under a drivable OSM way as
   drivable (like paved terrain under service ways), or leave these segments
   flagged as "no reference".
3. **The OSM `width` tag did not overestimate.** Despite meaning kerb to kerb,
   tagged widths are 0.3 m *below* the rough reference on median. The rough
   reference includes junction wedges, so Phase 3 should recheck this.
4. The rough reference width counts junction wedges and side areas inside a
   segment's cell; Phase 3's profiles replace it.

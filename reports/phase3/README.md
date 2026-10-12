# Phase 3: Ground-truth widths from BGT

Run on 2026-10-12 over `utrecht_centrum`, `houten` and `benschop`:

```
uv run python -m backbone.widths --area utrecht_centrum --area houten --area benschop   # ~1 min
```

Files: `metrics.md` (all tables), `segments.csv` (one row per segment, with
widths, flags and baseline errors), `random_0[1-5].jpg` (50 random scored
segments, seed 0), `spread_0[1-2].jpg` (20 random spread-flagged segments).
Per-profile data: `data/<area>/widths.gpkg`, layers `profiles` and `segments`.

## Method (`src/backbone/profiles.py`, `src/backbone/widths.py`)

- A perpendicular profile every 2 m, up to 25 m each side. A surface source
  turns each profile into runs of classes; the rules work on runs only, so the
  BGT source used here and a raster source for the Phase 4 model share all code.
  A unit test checks that both sources agree within one pixel.
- Classes: carriageway (BGT drivable, incl. paved terrain under service ways and
  cycle paths under drivable ways), `inrit` (stops profiles, decided
  2026-10-12), parking / cycle path (kerb-to-kerb only), other.
- **Carriageway width:** walk out from the centreline through carriageway and
  stop at the first other class (slivers < 5 cm ignored).
  **Kerb-to-kerb:** the same, but also through parking and cycle path.
- **Off-road OSM line:** if the line is not on carriageway, the nearest
  carriageway within 5 m is used (one side can then be negative).
- **Parallel roads:** profiles stop at the segment's nearest-centreline cell
  (Phase 2), which is the "midpoint to the nearest other centreline" rule.
- **Junctions:** profiles within (widest other road's half tag width + 3 m) of
  a junction node are excluded; 2 m where only driveways or alleys join.
- **Levels:** a segment is measured on the BGT level matching its OSM `layer`
  where BGT has surface at that level, else on ground level.
- A segment is scored with at least 3 usable profiles.

## Coverage

3,216 segments (181 km) have profiles inside the scoring box; **2,377 (170 km)
are scored**. Not scored: 553 junction-only segments (5.6 km, median 8 m long)
and 286 with fewer than 3 usable profiles (5.9 km).

| Flag | Segments | km | Meaning |
|---|---|---|---|
| spread | 626 | 49.9 | p90 - p10 of carriageway width > 1.5 m |
| cell_limited | 162 | 9.9 | > 50% of profiles stopped by a neighbouring centreline |
| offset | 45 | 4.2 | OSM line mostly off the carriageway |

Only 1.8% of usable profiles start off the carriageway, but OSM lines are
rarely centred: the median |left - right| is 0.56 m.

## True widths and baseline errors (2,377 scored segments)

Median true width: carriageway 4.75 m, kerb-to-kerb 5.80 m.

| Baseline (vs carriageway) | MAE (m) | Within 0.5 m | Bias (m) | MAE city / suburb / rural |
|---|---|---|---|---|
| B0: tags, else class defaults | 1.40 | 27% | +0.16 | 1.38 / 1.44 / 1.31 |
| B1: class median from other areas | 1.35 | 28% | -0.21 | 1.52 / 1.22 / 1.14 |
| **B2: tags, else B1** | **1.27** | **29%** | -0.30 | 1.42 / 1.15 / 1.15 |

B1 helps residential (MAE 1.22 -> 1.12) and service (3.03 -> 2.39) but hurts
classes where `lanes` tags carry information (secondary 1.21 -> 1.82).
B2 keeps both; it is the bar the imagery method has to beat in Phase 5.

Against kerb-to-kerb, all baselines are worse (MAE 2.3-2.6 m), mostly because
defaults ignore parking.

## Checked on the overlays

The 50 random segments look right: carriageway edges sit on kerbs and parking
bay lines, kerb-to-kerb continues through parallel parking, `inrit` polygons
stop profiles, curves and roundabouts measure cleanly.

The 20 spread-flagged segments, by eye: about half are real variation (bus
stops, turn lanes, medians, parking bays), a quarter are open paved areas
(squares, market places), and the rest are measurement artifacts:
- at a sharp bend, a profile on the inside of the corner runs along the other leg;
- a few profiles just outside the junction exclusion still catch the junction mouth.

Medians absorb most of these.

## Surprises and open issues

1. **The OSM `width` tag matches carriageway, not kerb-to-kerb, in these
   areas.** 66 width-tagged segments: MAE 0.77 m, bias -0.08 m against
   carriageway; MAE 1.76 m, bias -1.08 m against kerb-to-kerb. Dutch mappers
   appear to tag carriageway width, contrary to the OSM wiki definition.
2. **Perpendicular parking inflates kerb-to-kerb.** 24% of profile sides
   extend beyond the carriageway (median 2.0 m, a parallel parking lane), but
   8% extend more than 3 m (up to 5 m): perpendicular or angled bays, which the
   OSM definition of width excludes. **Decision needed:** stop kerb-to-kerb at
   parking or cycle-path runs deeper than 3 m along the profile, or keep it.
3. **Spread-flagged segments (626, 50 km).** **Decision needed (SPEC step 9):**
   split them, or keep the stored per-profile distribution and use the median.
   Recommendation: keep the distribution and median for now; splitting at width
   changes can be added later from the stored profiles without re-measuring.
4. **Parameters for approval (SPEC acceptance):** spacing 2 m, offset search
   5 m, junction exclusion = half the widest other road's tag width + 3 m (2 m
   at driveways), spread threshold 1.5 m. The overlays support keeping them.
5. Junction-only segments (553, short stubs) get no width. They are 3% of
   length; Phase 9 can give them the width of their neighbours.

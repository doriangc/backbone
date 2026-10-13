# Phase 3: Ground-truth widths from BGT

Run on 2026-10-12 over the three pilots, then rerun over all 20 areas with the
kerb-to-kerb depth cap (decision 2026-10-12, see below). Numbers are for all 20.

```
uv run python -m backbone.widths --area utrecht_centrum --area houten ...   # one --area per area, ~15 min for 20
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
  **Kerb-to-kerb:** the same, but also through parking and cycle path, and
  only through runs of those up to 3 m deep along the profile. Deeper runs are
  perpendicular or angled parking bays, so kerb-to-kerb stops at the
  carriageway edge there (decided 2026-10-12).
- **Off-road OSM line:** if the line is not on carriageway, the nearest
  carriageway within 5 m is used (one side can then be negative).
- **Parallel roads:** profiles stop at the segment's nearest-centreline cell
  (Phase 2), which is the "midpoint to the nearest other centreline" rule.
- **Junctions:** profiles within (widest other road's half tag width + 3 m) of
  a junction node are excluded; 2 m where only driveways or alleys join.
- **Levels:** a segment is measured on the BGT level matching its OSM `layer`
  where BGT has surface at that level, else on ground level.
- A segment is scored with at least 3 usable profiles. Segments whose
  carriageway width varies (spread flag) keep their per-profile distribution in
  `widths.gpkg`, and the median is their width (decided 2026-10-12).

## Coverage

19,972 segments (1,238 km) have profiles inside the scoring box; **14,188
(1,149 km) are scored**. Not scored: 3,690 junction-only segments (38 km,
median 8 m long) and 2,094 with fewer than 3 usable profiles (51 km).

| Flag | Segments | km | Meaning |
|---|---|---|---|
| spread | 3,456 | 264.3 | p90 - p10 of carriageway width > 1.5 m |
| cell_limited | 1,451 | 68.4 | > 50% of profiles stopped by a neighbouring centreline |
| offset | 364 | 25.3 | OSM line mostly off the carriageway |

Only 2.0% of usable profiles start off the carriageway, but OSM lines are
rarely centred: the median |left - right| is 0.61 m.

## True widths and baseline errors (14,188 scored segments)

Median true width: carriageway 4.99 m, kerb-to-kerb 5.61 m. 18% of profile
sides extend beyond the carriageway (median 1.9 m, a parallel parking lane or
cycle path). After the 3 m cap, 0.7% extend more than 3 m, through two shallow
runs in a row.

| Baseline (vs carriageway) | MAE (m) | Within 0.5 m | Bias (m) | MAE city / suburb / rural |
|---|---|---|---|---|
| B0: tags, else class defaults | 1.40 | 29% | -0.04 | 1.41 / 1.39 / 1.41 |
| B1: class median from other areas | 1.37 | 28% | -0.01 | 1.58 / 1.17 / 1.12 |
| **B2: tags, else B1** | **1.22** | **33%** | -0.11 | 1.37 / 1.07 / 1.07 |

B1 helps where defaults are poor: residential (MAE 1.16 -> 1.03) and service
(3.12 -> 2.22). It hurts where `lanes` tags carry information: secondary
1.30 -> 1.91, primary 1.36 -> 2.25. B2 keeps both, and it is the bar the
imagery method has to beat in Phase 5. The hardest classes for B2 are
motorway (3.42 m; hard shoulders), service (2.20 m) and busway (1.85 m).

B1 is the leave-one-area-out per-class median, so with 20 areas it is no
longer tuned on 2 neighbours. Against kerb-to-kerb, all baselines are worse
(MAE 1.6-1.8 m), mostly because defaults ignore parking.

## Checked on the overlays

Pilot run and 20-area rerun. The 50 random segments look right: carriageway edges sit on kerbs and parking
bay lines, kerb-to-kerb continues through parallel parking and stops at perpendicular
bays, `inrit` polygons
stop profiles, curves and roundabouts measure cleanly.

The 20 spread-flagged segments, by eye: about half are real variation (bus
stops, turn lanes, medians, parking bays), a quarter are open paved areas
(squares, market places), and the rest are measurement artifacts:
- at a sharp bend, a profile on the inside of the corner runs along the other leg;
- a few profiles just outside the junction exclusion still catch the junction mouth.

Medians absorb most of these.

## Surprises and open issues

1. **The OSM `width` tag matches carriageway, not kerb-to-kerb.** Over 471
   width-tagged segments in 20 areas: MAE 0.63 m, bias -0.07 m against
   carriageway; MAE 1.25 m, bias -0.52 m against kerb-to-kerb. Dutch mappers
   appear to tag carriageway width, contrary to the OSM wiki definition.
2. **Resolved: perpendicular parking.** Kerb-to-kerb now stops at parking or
   cycle-path runs deeper than 3 m (`profiles.DEFINITIONS["kerb"]`). Median
   kerb-to-kerb fell from 5.80 to 5.61 m. Unit test
   `test_kerb_stops_at_deep_parking_bays`.
3. **Resolved: spread-flagged segments** (3,456, 264 km) keep their
   distribution and use the median.
4. **Approved parameters:** spacing 2 m, offset search 5 m, junction
   exclusion = half the widest other road's tag width + 3 m (2 m at
   driveways), spread threshold 1.5 m.
5. Junction-only segments (3,690, short stubs) get no width. They are 3% of
   length; Phase 9 can give them the width of their neighbours.
6. **Motorways** (21 segments, 11 km) measure 11.5 m of carriageway against a
   7 m tag default. BGT `rijbaan autosnelweg` includes the hard shoulder,
   which is paved and drivable by the definition. Whether a width product
   should include it is open; the measurement is consistent with BGT.

# Phase 5: Segment widths from the probability map

```
uv run python -m backbone.fit_widths --run experiments/2026-10-14_full_r18 --tune                     # validation only
uv run python -m backbone.fit_widths --run experiments/2026-10-14_full_r18 --smooth 1 --min-run 1.0   # val + test
```

**Files:**
- `metrics.md` / `metrics.json`: all tables.
- `segments.csv`: one row per segment, split and source, with errors.
- `tuning_val.md`: the grid search.
- `render_sample.csv`: the render-and-score sample.
- `worst_0[1-5].jpg`: the 50 worst test segments.
- Per area: `data/<area>/ml/widths_<run>_<source>.gpkg`, with layers
  `profiles` and `segments`.

## Method (`src/backbone/fit_widths.py`)

- **Cross-section fit (main method).**
  - It uses the same profiles as the Phase 3 truth: same spacing, junction
    exclusion and cell stop. `widths.area_profiles` is now shared, and on
    Kapelle it reproduces the stored Phase 3 profiles exactly.
  - The model's probabilities are summed into the profile classes and sampled
    every 12.5 cm. The argmax class gives runs, and the Phase 3 `measure` rules
    are applied to them.
  - A foreign run shorter than the minimum run length is skipped.
  - The segment width is the median over usable profiles.
- **Tuning (validation areas only).** The grid covered averaging over 1, 3, 5
  or 9 neighbouring profiles, and minimum runs of 0.05–1 m. MAE varied only
  between 1.22 and 1.28 m across the grid, and smoothing made results slightly
  worse. The chosen setting is no smoothing and a 1.0 m minimum run. So the
  blobby look of the mask is not what drives the width error (see the
  failures below).
- **Render-and-score (validation method).** For about 300 segments per split,
  one constant half-width per side is fitted to all corridor pixels by maximum
  likelihood on P(carriageway).
- **Confidence.** Computed as share of usable profiles × median
  P(carriageway) inside the fitted width / (1 + (p90 − p10) / 2 m). It was fixed
  before looking at test errors.
- **Gated estimate.** Use the image width where confidence ≥ threshold and the
  segment has no `width` tag, else B2. The threshold (0.452) was chosen on
  validation, 8 cm source.

## Results: carriageway width, test areas (3,223 scored segments)

Errors are predicted minus true, in metres. MAE is a mean and bias a median.
"Same segments" means the 93% of segments where the image gives a width.

| | MAE | Within 0.5 m | Bias |
|---|---|---|---|
| Image (cross-section), same segments | 1.33 | 33% | −0.17 |
| B2, same segments | 1.29 | 32% | |
| Image, with B2 where it gives no width | 1.39 | | |
| **Gated image / B2** | **1.26** | **36%** | −0.23 |
| B2 (Phase 3 bar) | 1.35 | 31% | −0.39 |
| B0 (Phase 2 tag width) | 1.50 | | |

On validation the numbers are: image 1.22 against B2 1.17 on the same segments,
and gated 1.13 against B2 1.21.

**Verdict.** On its own, the image does not beat B2 on mean error. Its median
error is lower (0.85 m against 0.92 m), but large failures pull the mean up.
The image does carry information B2 lacks: gated by confidence it cuts MAE by
7% (1.35 → 1.26 m) and raises the share within 0.5 m from 31% to 36%. The
gate was chosen on validation and confirmed on test.

### Where the image wins and loses (test, 8 cm, same segments)

| | Segments | Image MAE | B2 MAE |
|---|---|---|---|
| Rural (Aalten) | 51 | **0.53** | 1.44 |
| Suburb (Zoetermeer) | 960 | **1.18** | 1.39 |
| City (Groningen, Eindhoven) | 2,212 | 1.42 | **1.24** |
| primary | 105 | **0.77** | 1.22 |
| unclassified | 280 | **1.24** | 1.52 |
| residential | 1,425 | **1.03** | 1.11 |
| secondary | 297 | 1.57 | **1.01** |
| tertiary | 611 | 1.39 | **1.33** |
| service | 360 | 2.70 | **2.28** |
| Segments with a `width` tag | 200 | 0.95 | **0.59** |

Validation shows the same pattern: rural 0.73 against 1.48, and motorways
0.52 against 3.43, because B2 misses hard shoulders.

### Confidence predicts error

Spearman correlation of confidence with |error| is −0.34 on test and −0.33 on
validation.

| Test confidence quintile | Image MAE | B2 MAE |
|---|---|---|
| q1 (low) | 2.25 | 1.67 |
| q2 | 1.58 | 1.36 |
| q3 | 1.10 | 1.19 |
| q4 | 0.95 | 1.14 |
| q5 (high) | **0.76** | 1.08 |

### Resolution and source (test)

| Source | Coverage | Image MAE | Gated MAE |
|---|---|---|---|
| 8 cm averaged to 25 cm (training domain) | 93% | 1.33 | 1.26 |
| … to 50 cm | 92% | 1.35 | 1.26 |
| … to 1 m | 92% | 1.40 | 1.29 |
| PDOK 25 cm product (another flight) | 74% | 1.63 | 1.32 |

The model runs on a 25 cm grid, so native 8 cm input is not tested.
Resolution down to 1 m costs little. The other flight costs a lot, the same
domain gap seen in the Phase 4 IoU.

### Kerb-to-kerb (secondary definition)

Test image MAE is 1.33 against 1.67 for B2. B2 estimates carriageway, so this
comparison is only indicative.

### Render-and-score against cross-section

On the sample, render-and-score correlates with the cross-section width at
0.87 (test) and 0.94 (validation). The median difference between them is 0.5 m
and 0.3 m. Its own error is larger: MAE 1.82 against 1.19 on the same 347 test
segments, biased low (−0.72 m). One constant width per segment is pulled down
by narrow sections and occlusion, where the per-profile median is robust. The
two methods agree well enough to back the cross-section fit, and the
cross-section fit stays the main method.

## The 50 worst test segments (`worst_0[1-5].jpg`), grouped by eye

| Category | Count | What happens |
|---|---|---|
| Wide paved areas counted as road | 31 | Paved yards, car parks, squares and bus stations under a service way, busway or residential way. Truth is the whole paved area (9–29 m), and the image finds a lane-like strip (2–6 m). These are 24 service, 4 busway and a few residential. Truth is wider than 9 m in 35 of the 50. |
| Adjacent carriageway merged | 14 | Short segments at junctions, bus lanes next to general lanes, roundabout approaches, splitter islands and wide crossings. BGT splits the carriageway there, but the model sees one asphalt surface, so the image is 6–11 m too wide. All are tertiary, secondary or unclassified. |
| Construction sites | 3 | BGT and imagery disagree because the area is being rebuilt. |
| Occlusion | 2 | Tree canopy over the road. |

The first group is mostly about definitions, not the model. A 25 m "width" for
a service way across a car park is not a meaningful road width. Neither B2 nor
the image gets close: on the 38 such test segments, MAE is 8.5–8.7 m for both.
Excluding service and busway segments, the image and B2 are level (MAE 1.19
against 1.20), and the image is better on median (0.80 against 0.92).

The second group is a real model weakness. It matches the Phase 4 confusion
between cycle and carriageway (19%), and it explains the positive bias on
secondary and tertiary roads (+0.3 to +0.6 m).

Parking and parallel roads, which the SPEC names as likely failure types,
hardly appear among the worst 50. The cell stop and the 3 m depth cap handle
them.

## Open questions

1. **Area-like service and busway segments.** Should segments whose truth is a
   wide paved area be flagged as "area" and left out of width scoring, or
   capped? This affects both the truth and the product.
2. **Next step to close the gap with B2 on city roads:**
   - longer Phase 4 training;
   - targeted fixes for merged adjacent carriageways, for example making a lane
     marking or bus lane a stop;
   - the Phase 6 direct regression, which can be combined with the gate.

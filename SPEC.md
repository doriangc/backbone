# OSM Road Widths from Aerial Imagery: Project Spec and Phases

## Project spec

The project measures the real-world width of OpenStreetMap (OSM) road segments from aerial imagery, using a surface-segmentation model trained on Dutch BGT data. Widths, plus fillet radii at junctions, are then assembled into road polygons.

### Goal

- **Input:** one OSM road segment plus aerial imagery around it.
- **Output:** left and right half-widths along the segment (measured separately, since OSM lines are often off-centre), a confidence value, and fillet radii at its junctions. Polygons are built from these at the end (Phase 9).
- **Target regions:** Europe, the US and Canada. The Netherlands is the proof-of-concept region because BGT gives exact road polygons there.

### Definitions (decisions the human owns)

- **Drivable OSM roads:** `highway` in `motorway`, `trunk`, `primary`, `secondary`, `tertiary`, `unclassified`, `residential`, `service`, `busway` and the `_link` variants. `track` is excluded; `busway` is included because BGT `OV-baan` is (decided 2026-10-12).
- **Excluded OSM ways** (decided 2026-10-12; rules in `src/backbone/fetch.py`):
  - cycleways, footways, pedestrian areas, paths, and `area=yes` highways;
  - parking: `service=parking_aisle|parking`, any `parking=*`, `amenity=parking`;
  - roads hidden from aerial imagery: tunnels and building passages, `covered`, `indoor`, `location=underground`, negative `level`. A negative `layer` alone does not exclude (open garage ramps);
  - service roads in private yards: more than 50% of their length on BGT onbegroeidterreindeel `erf`.
- **Drivable surface in BGT** (attribute names verified against the BGT catalogue on 2026-10-12):
  - `wegdeel` with `functie` in `rijbaan autosnelweg`, `rijbaan autoweg`, `rijbaan regionale weg`, `rijbaan lokale weg`, `OV-baan` (bus and tram lanes are included), `overweg`, `inrit`, `woonerf`;
  - plus `onbegroeidterreindeel` with `fysiek_voorkomen` `gesloten verharding` or `open verharding`, but only where a kept OSM service way runs through it;
  - plus `wegdeel` `fietspad` where a kept drivable OSM way runs along it (at least half the polygon's length), e.g. bicycle streets that BGT maps as cycle path; a road that only crosses a cycle path does not count (decided 2026-10-12);
  - only the current version of each BGT object (the PDOK API also returns history).
- **Width definitions.** Both are measured from BGT. **Carriageway width is the product; kerb-to-kerb is a secondary output** (decided 2026-10-12):
  - **carriageway width:** drivable surface as defined above, except that `inrit` (driveway entrance) polygons stop width profiles, since they lie beyond the kerb;
  - **kerb-to-kerb width:** carriageway plus `parkeervlak` and `fietspad` polygons that continue it along the profile, with no footpath, verge or other class in between, and no single parking or cycle-path run deeper than 3 m (perpendicular or angled bays are excluded, matching the OSM wiki's definition of `width`).
- **Segment:** an OSM way split at every node it shares with another *kept* drivable way (excluded ways do not split segments). Document the exact splitting rule in code.

### Conventions

- Work in a metric CRS (EPSG:28992 for Dutch data). Convert to EPSG:4326 only at the input and output edges.
- Layout: `data/` (git-ignored), `src/`, `tests/`, `notebooks/`, `experiments/`, `reports/`.
- Python with `geopandas`, `shapely`, `rasterio`, `pyproj`, `torch`, and `segmentation_models_pytorch`. Pin versions in a lockfile.
- Every script takes a bounding box or area name as an argument and runs reproducibly. Set random seeds everywhere.
- Log each experiment (config, git commit, metrics, checkpoint path) under `experiments/`.
- Tests run with `pytest`. Geometry and raster alignment must have unit tests.

### Rules for the agent

- Propose a plan and wait for approval before writing code for each phase.
- Ask before any download over 1 GB, any training run over 30 minutes, or any spend.
- Verify data endpoints, attribute names, and licenses by reading the current official documentation. Do not rely on memory.
- Produce overlay images (imagery + labels + OSM) at every stage and save them to `reports/`. Look at them before reporting success.
- Never random-split tiles. Split spatially, by blocks or municipalities.
- Commit at the end of each phase with a short summary of metrics and open issues.
- Report failures and surprising results plainly. Do not tune on the held-out test areas.

## Phase 1: Data acquisition (pilot areas only)

Phase 1 ends with a reproducible fetch script and one overlay image you have checked by eye. Do not download all of the Netherlands.

1. Pick three small pilot areas of about 2 x 2 km each: a dense city centre, a suburb, and a rural area. (Done: `utrecht_centrum`, `houten`, `benschop`; see `reports/phase1/README.md`. Scaled to 20 areas on 2026-10-12, 7 city, 7 suburb and 6 rural across the country, listed in `src/backbone/areas.py`.)
2. Download BGT road data (the `wegdeel` layer) from PDOK. The agent must verify the current endpoints, attribute names, and license itself.
3. Download the matching aerial imagery (PDOK Luchtfoto, 8 cm and 25 cm) for the same footprints.
4. Download an OSM extract (Geofabrik or Overpass) filtered to the drivable `highway` values in the spec.
5. Write one script that fetches all three sources for a given bounding box and caches them under `data/`.
6. Render a QA overlay of BGT polygons and OSM lines on the imagery for each pilot area.

**Acceptance:** the script runs from a clean checkout, and the overlays show BGT and OSM aligned with the visible roads. You review the overlays before Phase 2 starts.

## Phase 2: No-ML baseline

The baseline sets the score the model has to beat and shows where imagery matters. It needs no training.

1. Split OSM ways into segments at intersection nodes, following the definition in the spec.
2. Buffer each segment by a width derived from tags (`width`, `lanes`, `highway` class), with documented defaults when tags are missing.
3. Compute intersection-over-union (IoU) between each buffered segment and the BGT carriageway polygons it overlaps.
4. Report IoU by road class and by area type (city, suburb, rural).
5. Save a gallery of the 50 worst cases as overlay images.

**Acceptance:** a metrics table and the worst-cases gallery in `reports/`. The gallery should show whether failures cluster at wide roads, junctions, or missing tags.

## Phase 3: Ground-truth widths from BGT

Segment width is the new target quantity, so the first job is to measure true widths from BGT with a method that can later be reused on model output. The same cross-section code will score predictions in Phase 5.

1. For each OSM segment, sample perpendicular profiles along the centerline every 2 to 5 m.
2. Along each profile, measure the distance to the BGT edge on the left and on the right, separately, for both width definitions (carriageway and kerb-to-kerb). Never assume the OSM line is centered.
3. **First-edge rule:** walk outward from the centerline and stop at the first transition out of the surface being measured, not at the last matching pixel, so a profile never jumps across a kerb or verge into parking, a parallel road, or a driveway.
4. **OSM line outside the surface** (about 5% of OSM length in the pilots): search along the profile for the nearest polygon within a set distance; if none is found, mark the profile unusable and flag the segment as offset.
5. Also stop each profile at the midpoint to the nearest other drivable centerline, so parallel roads do not inflate the width.
6. Exclude profiles near intersection nodes, where the road reads artificially wide. The exclusion distance scales with the crossing road: its tag-based half-width plus a margin, with a smaller distance where only a driveway or minor service way joins.
7. **Store the full width profile** per segment (distance along the segment, left and right width for each definition, usable flag), not only a summary, so segments can later be split or summarized differently without re-measuring.
8. Summarize each segment as left half-width, right half-width, total width, and a spread measure (median plus 10th and 90th percentile across profiles).
9. Flag segments where the spread is large (turn lanes, bays, parking pockets, driveway entrances). Decided 2026-10-12: keep the stored per-profile distribution and use the median; splitting at width changes can be added later from the stored profiles.
10. Compare the true widths with the Phase 2 tag-based widths, in meters.

**Acceptance:** a table of true widths per segment (both definitions), the baseline error in meters (mean absolute error and share within 0.5 m), and overlays of the profiles on 50 random segments so you can check the method. **You approve the profile spacing, the stopping rules, the junction exclusion distances, and the width definition to use as the product before Phase 4.** (Approved 2026-10-12: 2 m spacing, 5 m offset search, junction exclusion of the widest other road's half tag width + 3 m and 2 m at driveways, 1.5 m spread threshold; carriageway is the product. The strongest no-imagery baseline, B2 = tag width where tagged else the class median from other areas, is the bar for Phase 5.)

## Phase 4: Surface-class probability map

Width fitting scores against a probability map and not against raw pixels, because shadows, trees, and concrete driveways confuse color and texture. This model is not conditioned on any segment, which makes it simpler than a segment-to-polygon model and reusable for every later phase.

The mask is **multi-class**, not binary (decided 2026-10-12). Parking bays, cycle paths and carriageway are often the same asphalt; separate classes let the width fit find the boundary it needs and keep both width definitions available.

1. Build chips from the imagery with BGT polygons rasterized as a multi-class target: carriageway (the drivable surface per the spec), parking (`parkeervlak`), cycle path (`fietspad`), footpath and pedestrian area (`voetpad`, `voetpad op trap`, `voetgangersgebied`), other paved surface (remaining wegdeel classes, and paved onbegroeidterreindeel not counted as carriageway), and background. Record the exact class mapping in the config.
2. Split spatially, by grid blocks or municipalities, into train, validation, and test. Never split by random chip. Write alignment tests for the geometry-to-raster step.
3. Add augmentations: random downsampling to 0.25 to 1 m ground resolution, color and brightness jitter, rotation and flips.
4. Train a U-Net with a pretrained encoder (via `segmentation_models_pytorch`) with cross-entropy plus Dice loss. Output: per-pixel class probabilities.
5. Run an overfit test on a few hundred chips first, then a full run in the background with logged metrics and saved checkpoints. Training runs locally on CPU (decided 2026-10-12: 16 cores, no GPU), so chip size, ground resolution, encoder size and epochs must fit CPU budgets; runs over 30 minutes still need approval.
6. Evaluate per-class pixel IoU on held-out areas, split by area type, and save a gallery of best, median, and worst predictions.

**Acceptance:** validation and test IoU per class, a checkpoint, and the gallery. Pixel IoU only needs to be good enough for width fitting; Phase 5 decides whether it is.

## Phase 5: Fit segment widths from the probability map

This is the core method: for each segment, find the width that best explains the probability map, one segment at a time. It runs on independent small jobs, so it parallelizes across millions of segments.

1. **Cross-section fit (main method).** Reuse the Phase 3 profiling code, but on the predicted class probabilities. Along each profile, find where the probability of the measured surface (carriageway, or carriageway plus parking and cycle path for kerb-to-kerb) drops on the left and on the right, then take the median across profiles.
2. **Render-and-score (validation method).** For a sample of segments, render the buffered polygon at a sweep of candidate widths (about 2 to 20 m) and score each against the probability map. Check that it agrees with the cross-section fit.
3. **Robustness:** apply the same stopping rule for parallel roads and the same junction exclusion as Phase 3, and use robust statistics against tree and shadow occlusion.
4. **Confidence:** output a confidence value per segment (profile agreement, mean probability inside the fitted width, share of profiles that were usable).
5. **Evaluation in meters:** against the Phase 3 BGT widths on held-out areas, report mean absolute error, share within 0.5 m, and bias, split by road class, area type, and **imagery resolution** (8 cm, and 8 cm downsampled to 25 cm, 50 cm, and 1 m; the PDOK 25 cm product as a separate test, since it is a different flight). Compare with the Phase 2 baseline and with B2 from Phase 3.
6. Save a gallery of the 50 worst segments and ask the agent to group failures into named categories with counts (occlusion, parallel roads, parking, junction effects).

**Acceptance:** a metrics table in meters showing whether imagery beats the tag-based baseline, where it does and does not, and how well confidence predicts error.

## Phase 6: Direct width regression (optional second experiment)

A regression model gives an independent estimate of width and a cross-check on the profiling method. Do this only if Phase 5 leaves a gap worth closing.

1. Build chips centered on each segment with channels RGB plus a rasterized target segment (and optionally neighboring segments).
2. Train a CNN with a pretrained encoder to regress left and right half-widths in meters, using the Phase 3 BGT widths as labels. Use the same spatial splits as Phase 4.
3. Augment as in Phase 4, plus small random offsets of the OSM line.
4. Evaluate in meters on held-out areas, side by side with Phase 5 and the Phase 2 baseline.
5. Test whether combining the two (for example averaging, or using regression where Phase 5 confidence is low) beats either alone.

**Acceptance:** a three-way comparison table (baseline, profile fit, regression) and a recommendation on whether to keep the regression model.

## Phase 7: Junctions and fillet radii

Once widths exist, a corner between two roads is the intersection of their edge lines, and a fillet is a tangent arc of radius r at that corner. The same fit-and-score idea applies, and BGT again supplies ground truth.

1. **Ground truth first.** Fit fillet radii to BGT polygons at junctions. Check whether radius is predictable from road class, intersection angle, and width. If it is, a lookup table may cover most junctions.
2. **Imagery fit.** For each simple junction, sweep r (about 2 to 20 m), sample the arc, and score it against the probability map near the corner. Keep the best radius and a confidence value.
3. **Classify junctions** and mark the types a single radius does not fit: roundabouts, slip lanes, channelized junctions, and junctions with more than four arms. Route those to their own handling or flag them.
4. **Resolution check:** a 6 m radius is about 24 pixels at 25 cm but only 6 to 10 pixels at 60 cm to 1 m. Report accuracy per imagery resolution so you know when estimates become too noisy.
5. Evaluate in meters against BGT radii and against the lookup-table baseline.

**Acceptance:** per-junction-type radius error in meters, a list of junction types left unhandled, and a recommendation on lookup table versus imagery fit.

## Phase 8: Generalize to the US, Canada, and the rest of Europe

The width method needs only a drivable-surface mask and some ground-truth widths for checking, which lowers the labeling burden compared with predicting polygons directly. The main risk is that the mask model learns the wrong idea of "drivable" in a new region.

1. **Research first, download later.** Ask the agent for candidate road-polygon or edge-of-pavement datasets and imagery sources (for example NAIP in the US, municipal planimetric data, and national mapping agencies in Europe), with coverage, resolution, and license for each. You review the list before anything is downloaded.
2. Repeat Phases 1 to 3 for one or two new regions to get imagery and ground-truth widths there.
3. Run the Netherlands-trained mask model **zero-shot** on those regions, then run the Phase 5 width fit, and report width error in meters. This measures the domain gap.
4. Fine-tune the mask model on the new regions mixed with Dutch data, and re-evaluate on held-out areas of each region.
5. Where ground truth is thin, check whether the Phase 5 confidence score still flags the bad fits, since that decides how far you can trust the output without labels.
6. Define "drivable" per region. The agent should note where painted bike lanes, shoulders, and parking blur the boundary, and propose a rule for each.

**Acceptance:** per-region width and radius error tables (zero-shot and fine-tuned) and a written list of the main error sources in each region.

## Phase 9: Assemble polygons, package, and working tips

The final output is a parametric polygon per segment: centerline, left and right half-widths, and fillet radii at junctions. Segments whose fit scores poorly are flagged and not forced into a shape.

1. Build each polygon from the centerline offset by the left and right half-widths, joined with fillet arcs at junctions.
2. Resolve overlaps between neighboring polygons, check validity, and export GeoJSON in EPSG:4326 with width, radius, and confidence as properties.
3. Score the assembled polygons against BGT (IoU and boundary distance) as a final end-to-end check.
4. Build an inference CLI: OSM segment IDs plus an imagery source in, GeoJSON out.
5. Write a README (install, data sources and licenses, retraining, known limits) and a small test suite that runs the full pipeline on one tiny fixture area.





# Legacy phases (for posterity)
## Phase 3: Build the segment-to-polygon labels

This is the hardest phase because it defines the 1-to-1 mapping, so spend the most review time here. One BGT polygon can span many OSM segments, and one segment can touch several polygons.

1. Select the BGT carriageway polygons that overlap each OSM segment (buffer, overlap, and class filters).
2. Partition polygons among segments. Try at least two rules and render both. Candidates: a Voronoi or nearest-segment partition clipped to the polygon, and a perpendicular-projection partition along the segment.
3. Decide how junctions are handled: assigned to one segment, shared, or given their own junction polygon.
4. Flag bad matches (no polygon found, offset beyond a set distance, area ratio far from the baseline) and exclude or log them.
5. Export the label set as segment ID, polygon geometry, and a quality flag.

**Acceptance:** a few hundred random samples rendered as overlays for you to inspect. **You approve the partition rule and the junction rule before Phase 4.**

## Phase 4: Dataset, splits, and augmentation

The dataset must be split by place, and the raster alignment must be tested, because misalignment fails silently.

1. Generate chips centered on each segment. Channels: RGB, a rasterized target segment, and optionally a second channel with neighboring segments so the model does not claim their pavement.
2. Fix chip size and ground resolution up front (for example 256 px at 25 cm) and record them in the config.
3. Split spatially, by grid blocks or municipalities, into train, validation, and test. Never split by random chip.
4. Add augmentations: random downsampling to 0.25 to 1 m ground resolution, color and brightness jitter, rotation and flips, and small random offsets of the OSM line.
5. Write unit tests that check geometry-to-raster alignment on known cases, plus an overlay check of random chips.

**Acceptance:** tests pass, a grid of sample chips with masks is saved to `reports/`, and the split map shows no spatial leakage between train and test.

## Phase 5: Model training

Start with a conditioned U-Net, prove the pipeline on a tiny subset, then scale. Decide where training runs (local GPU or cloud) and tell the agent before this phase.

1. **Architecture:** a U-Net with a pretrained encoder (via `segmentation_models_pytorch`). Input: RGB plus the segment channel. Output: the polygon mask for that segment. Loss: binary cross-entropy plus Dice.
2. **Overfit test:** train on a few hundred chips and confirm the model can nearly memorize them. If it cannot, fix the pipeline before scaling.
3. **Full run:** train on the full pilot dataset in the background, logging metrics every epoch and saving checkpoints. The agent checks logs rather than blocking on the run.
4. **Second experiment:** fine-tune a SAM-style model with the OSM segment as the prompt, and compare it with the U-Net on the same splits.
5. Log every run (config, commit, metrics, checkpoint path) under `experiments/`.

**Acceptance:** a validation-IoU curve, saved checkpoints, and a comparison of the two approaches against the Phase 2 baseline.

## Phase 6: Evaluation and error analysis

Evaluation runs on held-out test areas only, and it must show where the model beats the baseline and where it does not.

1. Report IoU and a boundary F-score against the Phase 2 baseline, broken down by road class and by area type.
2. Generate a gallery of best, median, and worst predictions.
3. Ask the agent to group failures into named categories (junctions, tree occlusion, shadows, parking lots, parallel roads) with counts, instead of listing individual cases.
4. Do not tune hyperparameters on the test areas.

**Acceptance:** a report with the metrics table, the gallery, and the categorized failure modes, plus a short list of proposed fixes ranked by expected impact.

## Phase 7: Vectorization

The final output is a vector polygon, so quality has to be measured on the polygons and not only on the masks.

1. Threshold the predicted probability map, then polygonize it (`rasterio.features.shapes`, `shapely`).
2. Remove slivers and small holes, keep the component that touches the input segment, and simplify the outline with a tolerance in meters.
3. Resolve overlaps between neighboring segments so polygons do not double-claim pavement.
4. Check validity (no self-intersections) and reproject the result to EPSG:4326 as GeoJSON.
5. Re-measure IoU and boundary accuracy on the vector output against BGT.

**Acceptance:** vector-level metrics reported next to the mask-level metrics, and a visual check of junctions and curves.

## Phase 8: Generalize to the US, Canada, and the rest of Europe

A Dutch-trained model will not transfer cleanly: imagery resolution, road context, and labeling conventions all differ. Measure the gap first, then close it with local data.

1. **Research first, download later.** Ask the agent for a list of candidate road-polygon or edge-of-pavement datasets and imagery sources (for example NAIP in the US, municipal planimetric data, and national mapping agencies in Europe), with coverage, resolution, and license for each. You review the list before anything is downloaded.
2. Repeat Phases 1 to 4 for one or two new regions using the approved sources.
3. Evaluate the Netherlands-trained model **zero-shot** on those regions to measure the domain gap.
4. Fine-tune on the new regions, mixing in Dutch data, and re-evaluate on held-out areas of each region.
5. Treat "drivable" per region. The agent should note where bike lanes, shoulders, and parking blur the boundary, and propose a rule for each.

**Acceptance:** a per-region metrics table (zero-shot and fine-tuned) and a short written list of the main sources of error in each region.

## Phase 9: Packaging, working tips, and a first prompt

Package the result as a command-line tool that anyone on the team can run.

1. Build an inference CLI: OSM segment IDs plus an imagery source in, GeoJSON polygons out.
2. Write a README covering install, data sources and licenses, how to retrain, and known limits.
3. Add a small test suite that runs the full pipeline on one tiny fixture area.

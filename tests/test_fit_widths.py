from types import SimpleNamespace

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import LineString, box

from backbone.areas import BBox
from backbone.fit_widths import (
    N_PCLS,
    PROFILE_STEP_M,
    render_and_score,
    runs_from_classes,
    sample_profiles,
    smooth_along,
)
from backbone.grid import Grid
from backbone.profiles import CARRIAGEWAY, CYCLE, DEFINITIONS, OTHER, make_profiles, measure, raster_runs
from backbone.surface_data import CLS

RES = 0.25
GRID = Grid.for_bbox(BBox(1000, 2000, 1040, 2040), RES)  # 160 x 160 px
N_CLS = 7


def _prob_raster(labels: np.ndarray) -> np.ndarray:
    """One-hot uint8 probabilities from a model-class label raster."""
    return (np.eye(N_CLS, dtype=np.uint8)[labels] * 255).astype(np.uint8)


def _road_labels(left: float, right: float, y_line: float = 2020.0) -> np.ndarray:
    """East-west road along y = y_line: carriageway from y_line - right to
    y_line + left, cycle path 1.5 m beyond the left edge, background elsewhere."""
    ys = 2040 - (np.arange(160) + 0.5) * RES
    lab = np.zeros((160, 160), np.uint8)
    lab[(ys >= y_line - right) & (ys <= y_line + left), :] = CLS["carriageway"]
    lab[(ys > y_line + left) & (ys <= y_line + left + 1.5), :] = CLS["cycle"]
    return lab


def _profiles(y_line: float = 2020.0):
    return make_profiles(gpd.GeoSeries({7: LineString([(1005, y_line), (1035, y_line)])}), 2.0, 10.0)


def test_runs_from_classes_matches_raster_runs():
    lab = _road_labels(3.3, 2.1)
    prof = _profiles()
    p = sample_profiles(prof, _prob_raster(lab), GRID.transform)
    assert p.shape[2] == N_PCLS
    ours = runs_from_classes(p.argmax(-1), prof.half_length, PROFILE_STEP_M)
    from backbone.surface_data import TO_PROFILE_CLASS

    ref = raster_runs(prof, TO_PROFILE_CLASS[lab], GRID.transform, step=PROFILE_STEP_M)
    for a, b in zip(ours, ref):
        assert [r[2] for r in a] == [r[2] for r in b]
        np.testing.assert_allclose([r[:2] for r in a], [r[:2] for r in b])
    # Left is +y for an eastbound line: cycle path lies on the left.
    assert [r[2] for r in ours[0]] == [OTHER, CARRIAGEWAY, CYCLE, OTHER]


def test_min_run_skips_blob_inside_road():
    lab = _road_labels(3.0, 3.0)
    ys = 2040 - (np.arange(160) + 0.5) * RES
    lab[(ys > 2021.0) & (ys < 2021.3), :] = CLS["footpath"]  # 25-30 cm blob 1 m left of the line
    prof = _profiles()
    p = sample_profiles(prof, _prob_raster(lab), GRID.transform)
    runs = runs_from_classes(p.argmax(-1), prof.half_length, PROFILE_STEP_M)
    strict = measure(runs[0], DEFINITIONS["carriageway"], 5.0, 0.05)
    tolerant = measure(runs[0], DEFINITIONS["carriageway"], 5.0, 0.5)
    assert strict["left"] == pytest.approx(1.0, abs=RES)
    assert tolerant["left"] == pytest.approx(3.0, abs=RES)
    assert tolerant["right"] == pytest.approx(3.0, abs=RES)


def test_smoothing_outvotes_one_profile_and_ignores_junctions():
    p = np.zeros((6, 3, N_PCLS), np.float32)
    p[..., CARRIAGEWAY] = 1.0
    p[2, 1] = 0.0
    p[2, 1, OTHER] = 1.0  # blob on one profile
    p[5, :, :] = 0.0
    p[5, :, OTHER] = 1.0  # junction profile: must not leak into profile 4
    seg = np.array([1, 1, 1, 1, 1, 1], dtype=object)
    junction = np.array([False] * 5 + [True])
    s = smooth_along(p, seg, junction, 3)
    assert s[2, 1].argmax() == CARRIAGEWAY
    np.testing.assert_allclose(s[4, :, CARRIAGEWAY], 1.0)
    # Different segments do not mix.
    s2 = smooth_along(p, np.array([1, 1, 2, 2, 2, 2], dtype=object), np.zeros(6, bool), 3)
    np.testing.assert_allclose(s2[1, 1], p[1, 1] / 1.0 * 0.5 + p[0, 1] * 0.5)


def test_render_and_score_recovers_off_centre_road():
    lab = _road_labels(4.0, 1.5)
    segs = gpd.GeoDataFrame({"segment_id": ["a"]}, geometry=[LineString([(1005, 2020), (1035, 2020)])], index=[7])
    ap = SimpleNamespace(grid=GRID, segs=segs, cells=gpd.GeoSeries({7: box(990, 1990, 1050, 2050)}))
    jd = pd.DataFrame({"start": [2.0], "end": [2.0]}, index=[7])
    out = render_and_score(ap, _prob_raster(lab), [7], jd)
    assert out.loc[7, "render_left"] == pytest.approx(4.0, abs=RES)
    assert out.loc[7, "render_right"] == pytest.approx(1.5, abs=RES)

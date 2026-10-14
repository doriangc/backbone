"""Phase 4 data: per-area 25 cm label rasters and images, and training chips.

    python -m backbone.surface_data --area houten [--area ...] [--force]   # build data/<area>/ml/
    python -m backbone.surface_data --report                               # reports/phase4/ samples + split map

Per area, data/<area>/ml/ holds memory-mappable .npy arrays on the area's
25 cm grid (Grid.for_bbox(bbox, ML_RES_M), so pixel (0, 0) is the north-west
corner of the area box):
- labels.npy (H, W) uint8, values index config.SURFACE_CLASSES;
- image_8cm.npy (H, W, 3) uint8, the 8 cm imagery averaged down to 25 cm;
- image_25cm.npy (H, W, 3) uint8, the PDOK 25 cm product (another flight).
Chips are cut from these on the fly, so nothing is held in RAM.
"""

import argparse
import json
import logging
import os
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.windows import Window

from backbone import config, profiles
from backbone.areas import AREAS
from backbone.grid import Grid

log = logging.getLogger(__name__)

DATA_DIR = Path("data")
CLS = {name: i for i, name in enumerate(config.SURFACE_CLASSES)}
# Within one BGT level, later classes are drawn over earlier ones; matches the
# Phase 3 surface, where carriageway beats inrit beats cycle beats parking.
DRAW_ORDER = ("other_paved", "footpath", "parking", "cycle", "inrit", "carriageway")
# Model class -> Phase 3 profile class, so profiles.raster_runs can read predictions.
TO_PROFILE_CLASS = np.array(
    [
        {
            "carriageway": profiles.CARRIAGEWAY,
            "inrit": profiles.INRIT,
            "parking": profiles.PARKING,
            "cycle": profiles.CYCLE,
        }.get(name, profiles.OTHER)
        for name in config.SURFACE_CLASSES
    ],
    dtype=np.uint8,
)
SOURCES = ("8cm", "25cm")  # image_<source>.npy


def ml_dir(name: str) -> Path:
    return DATA_DIR / name / "ml"


def area_grid(name: str) -> Grid:
    return Grid.for_bbox(AREAS[name].bbox, config.ML_RES_M)


def wegdeel_classes(wd: pd.DataFrame) -> np.ndarray:
    """Model class per wegdeel row (see config.SURFACE_CLASSES comments)."""
    cls = np.full(len(wd), CLS["other_paved"], dtype=np.uint8)
    functie = wd["functie"].to_numpy()
    cls[np.isin(functie, list(config.WEGDEEL_BACKGROUND))] = CLS["background"]
    cls[np.isin(functie, list(config.WEGDEEL_FOOTPATH))] = CLS["footpath"]
    cls[functie == "parkeervlak"] = CLS["parking"]
    cls[functie == "fietspad"] = CLS["cycle"]
    cls[wd["drivable"].to_numpy(bool)] = CLS["carriageway"]
    cls[functie == "inrit"] = CLS["inrit"]
    return cls


def label_polygons(area_dir: Path) -> gpd.GeoDataFrame:
    """BGT polygons with a model class and level, in drawing order."""
    wd = gpd.read_file(area_dir / "bgt_wegdeel.gpkg")
    wd = gpd.GeoDataFrame(
        {"cls": wegdeel_classes(wd), "level": wd["relatieve_hoogteligging"].to_numpy()},
        geometry=wd.geometry,
        crs=config.RD_EPSG,
    )
    terrain = gpd.read_file(area_dir / "bgt_onbegroeidterreindeel.gpkg")  # ground level only
    terrain = terrain[terrain["paved"]]
    terrain = gpd.GeoDataFrame(
        {
            "cls": np.where(terrain["service_paved"], CLS["carriageway"], CLS["other_paved"]).astype(np.uint8),
            "level": 0,
        },
        geometry=terrain.geometry.to_numpy(),
        crs=config.RD_EPSG,
    )
    polys = pd.concat([wd, terrain], ignore_index=True)
    # Below-ground objects (tunnels, underground garages) are not visible from above.
    polys = polys[polys["level"] >= 0]
    # Background polygons are still drawn: on a bridge level they hide what lies below.
    order = {CLS[name]: i for i, name in enumerate(DRAW_ORDER)}
    polys = polys.assign(_o=polys["cls"].map(order)).sort_values(["level", "_o"], kind="stable")
    return gpd.GeoDataFrame(polys.drop(columns="_o"), geometry="geometry", crs=config.RD_EPSG)


def rasterize_labels(polys: gpd.GeoDataFrame, grid: Grid) -> np.ndarray:
    """Burn polygons in order (later wins) on pixel centres. Higher BGT levels
    are drawn last, since a bridge is what the camera sees."""
    return rasterize(
        ((g, int(c)) for g, c in zip(polys.geometry, polys["cls"])),
        out_shape=(grid.height, grid.width),
        transform=grid.transform,
        fill=CLS["background"],
        dtype=np.uint8,
    )


def resample_image(src: Path, grid: Grid, out: np.ndarray, strip_px: int = 400) -> None:
    """Average-resample an RGB GeoTIFF that covers `grid` exactly onto it, in
    strips of output rows to bound memory. Overviews are disabled, so the
    average is over full-resolution pixels."""
    with rasterio.open(src, OVERVIEW_LEVEL="NONE") as ds:
        if not np.allclose(ds.bounds, grid.bbox.as_tuple()):
            raise ValueError(f"{src} bounds {ds.bounds} differ from grid {grid.bbox}")
        f = ds.height / grid.height
        for r0 in range(0, grid.height, strip_px):
            h = min(strip_px, grid.height - r0)
            win = Window(0, r0 * f, ds.width, h * f)
            arr = ds.read((1, 2, 3), window=win, out_shape=(3, h, grid.width), resampling=Resampling.average)
            out[r0 : r0 + h] = arr.transpose(1, 2, 0)


def build_area(name: str, force: bool = False) -> Path:
    out = ml_dir(name)
    out.mkdir(parents=True, exist_ok=True)
    grid = area_grid(name)
    meta_path = out / "meta.json"
    if meta_path.exists() and not force:
        log.info("%s: %s exists, skipping (use --force)", name, out)
        return out
    area_dir = DATA_DIR / name
    # Write to temporary files and rename: a training run that has these
    # arrays memory-mapped keeps reading the old files until it reopens them.
    labels = rasterize_labels(label_polygons(area_dir), grid)
    np.save(out / "labels.tmp.npy", labels)
    os.replace(out / "labels.tmp.npy", out / "labels.npy")
    for source in SOURCES:
        tmp = out / f"image_{source}.tmp.npy"
        img = np.lib.format.open_memmap(tmp, mode="w+", dtype=np.uint8, shape=(grid.height, grid.width, 3))
        resample_image(area_dir / f"luchtfoto_{source}.tif", grid, img)
        img.flush()
        del img
        os.replace(tmp, out / f"image_{source}.npy")
    counts = np.bincount(labels.ravel(), minlength=len(config.SURFACE_CLASSES))
    meta = {
        "area": name,
        "res_m": config.ML_RES_M,
        "transform": list(grid.transform)[:6],
        "shape": [grid.height, grid.width],
        "classes": list(config.SURFACE_CLASSES),
        "class_share": {c: round(float(n / labels.size), 5) for c, n in zip(config.SURFACE_CLASSES, counts)},
    }
    meta_path.write_text(json.dumps(meta, indent=1))
    log.info("%s: built %s, class share %s", name, out, meta["class_share"])
    return out


def split_of(name: str) -> str:
    if name in config.TEST_AREAS:
        return "test"
    if name in config.VAL_AREAS:
        return "val"
    return "train"


def areas_in(split: str) -> list[str]:
    return [a for a in AREAS if split_of(a) == split]


# --- chips --------------------------------------------------------------------


class AreaArrays:
    """Lazily opened memmaps of one area."""

    def __init__(self, name: str, source: str = "8cm"):
        d = ml_dir(name)
        self.name = name
        self.labels = np.load(d / "labels.npy", mmap_mode="r")
        self.image = np.load(d / f"image_{source}.npy", mmap_mode="r")


def tile_positions(shape, chip: int = config.CHIP_PX, stride: int | None = None):
    """Top-left corners of chips fully inside the raster, on a regular grid."""
    stride = stride or chip
    rows = range(0, shape[0] - chip + 1, stride)
    cols = range(0, shape[1] - chip + 1, stride)
    return [(r, c) for r in rows for c in cols]


def train_candidates(names, seed: int = 0, stride: int = 128, min_fg: float = 0.01, keep_empty: float = 0.1):
    """Chip positions for training: on a stride grid, keeping chips with at
    least `min_fg` non-background label and a random `keep_empty` share of the
    rest (fields and water teach little but must not be forgotten)."""
    rng = np.random.default_rng(seed)
    out = []
    for name in names:
        lab = np.load(ml_dir(name) / "labels.npy", mmap_mode="r")
        for r, c in tile_positions(lab.shape, stride=stride):
            fg = np.count_nonzero(lab[r : r + config.CHIP_PX, c : c + config.CHIP_PX]) / config.CHIP_PX**2
            if fg >= min_fg or rng.random() < keep_empty:
                out.append((name, r, c))
    return out


IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


def normalize(img_u8: np.ndarray) -> np.ndarray:
    """(H, W, 3) uint8 -> (3, H, W) float32, ImageNet-normalized."""
    x = (img_u8.astype(np.float32) / 255.0 - IMAGENET_MEAN) / IMAGENET_STD
    return np.ascontiguousarray(x.transpose(2, 0, 1))


def augment(img: np.ndarray, lab: np.ndarray, rng: np.random.Generator, max_down: float = 4.0):
    """Dihedral rotation/flip, colour jitter, and simulated coarser imagery.

    img (H, W, 3) uint8, lab (H, W). Resolution: with probability 0.5 the
    image is averaged down by a factor in [1, max_down] (25 cm -> up to 1 m)
    and resized back, so the model sees blurrier input on the same 25 cm
    label grid that Phase 5 measures on."""
    from PIL import Image

    k = int(rng.integers(4))
    img, lab = np.rot90(img, k), np.rot90(lab, k)
    if rng.random() < 0.5:
        img, lab = img[:, ::-1], lab[:, ::-1]
    img = np.ascontiguousarray(img)
    lab = np.ascontiguousarray(lab)
    if rng.random() < 0.5:
        f = rng.uniform(1.0, max_down)
        h, w = lab.shape
        small = Image.fromarray(img).resize((max(1, round(w / f)), max(1, round(h / f))), Image.BOX)
        img = np.asarray(small.resize((w, h), Image.BILINEAR))
    x = img.astype(np.float32)
    gain = rng.uniform(0.8, 1.2) * rng.uniform(0.93, 1.07, size=3)  # brightness and colour balance
    mean = x.mean()
    x = (x - mean) * rng.uniform(0.8, 1.2) + mean  # contrast
    img = np.clip(x * gain, 0, 255).astype(np.uint8)
    return img, lab


class ChipDataset:
    """torch-style dataset over (area, row, col) chip positions."""

    def __init__(self, positions, source: str = "8cm", augment_seed: int | None = None):
        self.positions = list(positions)
        self.source = source
        self.augment_seed = augment_seed
        self._arrays = {}
        self._epoch = 0

    def set_epoch(self, epoch: int):
        self._epoch = epoch

    def arrays(self, name):
        if name not in self._arrays:  # opened per worker process
            self._arrays[name] = AreaArrays(name, self.source)
        return self._arrays[name]

    def __len__(self):
        return len(self.positions)

    def __getitem__(self, i):
        name, r, c = self.positions[i]
        a = self.arrays(name)
        s = config.CHIP_PX
        img = np.asarray(a.image[r : r + s, c : c + s])
        lab = np.asarray(a.labels[r : r + s, c : c + s])
        if self.augment_seed is not None:
            rng = np.random.default_rng([self.augment_seed, self._epoch, i])
            img, lab = augment(img, lab, rng)
        return normalize(img), lab.astype(np.int64)


# --- report -------------------------------------------------------------------

CLASS_COLORS = {
    "background": (0, 0, 0, 0),
    "carriageway": (1.0, 0.69, 0.0, 0.55),
    "inrit": (0.6, 0.3, 0.9, 0.6),
    "parking": (0.15, 0.45, 1.0, 0.55),
    "cycle": (0.9, 0.15, 0.15, 0.55),
    "footpath": (0.85, 0.85, 0.85, 0.5),
    "other_paved": (0.2, 0.85, 0.5, 0.45),
}


def label_rgba(lab: np.ndarray) -> np.ndarray:
    lut = np.array([CLASS_COLORS[c] for c in config.SURFACE_CLASSES], dtype=np.float32)
    return lut[lab]


def class_legend(ax):
    from matplotlib.patches import Patch

    ax.legend(
        handles=[Patch(facecolor=CLASS_COLORS[c][:3], label=c) for c in config.SURFACE_CLASSES[1:]],
        loc="lower left",
        fontsize=7,
        framealpha=0.85,
    )


def render_samples(out: Path, n: int = 12, seed: int = 0):
    """Random training chips (with augmentation) next to their labels, plus
    one un-augmented chip per area with the BGT outlines drawn as vectors on
    top, to check the raster against the geometry by eye."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rng = np.random.default_rng(seed)
    cands = train_candidates(areas_in("train"), seed=seed)
    pick = [cands[i] for i in rng.choice(len(cands), n, replace=False)]
    ds = ChipDataset(pick, augment_seed=seed)
    fig, axes = plt.subplots(n // 4, 8, figsize=(24, 3.2 * n // 4), dpi=110)
    for i, ax2 in enumerate(axes.reshape(-1, 2)):
        x, y = ds[i]
        img = np.clip((x.transpose(1, 2, 0) * IMAGENET_STD + IMAGENET_MEAN), 0, 1)
        ax2[0].imshow(img)
        ax2[1].imshow(img)
        ax2[1].imshow(label_rgba(y))
        ax2[0].set_title(f"{pick[i][0]} r{pick[i][1]} c{pick[i][2]} (augmented)", fontsize=7)
        for a in ax2:
            a.set_axis_off()
    class_legend(axes[0, 1])
    fig.suptitle("Random training chips with augmentation: image | image + label", fontsize=10)
    fig.tight_layout()
    fig.savefig(out / "sample_chips.jpg", pil_kwargs={"quality": 88})
    plt.close(fig)

    # Raster vs vector: one chip per split area, BGT outlines on the label raster.
    names = [areas_in(s)[0] for s in ("train", "val", "test")] + ["amsterdam_centrum", "benschop", "westerbork"]
    fig, axes = plt.subplots(2, 3, figsize=(18, 12), dpi=120)
    for ax, name in zip(axes.ravel(), names):
        a = AreaArrays(name)
        lab = np.asarray(a.labels)
        fgr, fgc = np.nonzero(lab[:: config.CHIP_PX, :: config.CHIP_PX] == CLS["carriageway"])
        j = rng.integers(len(fgr))
        r = min(fgr[j] * config.CHIP_PX, lab.shape[0] - config.CHIP_PX)
        c = min(fgc[j] * config.CHIP_PX, lab.shape[1] - config.CHIP_PX)
        g = area_grid(name)
        x0, y1 = g.transform @ (c, r)
        x1, y0 = g.transform @ (c + config.CHIP_PX, r + config.CHIP_PX)
        ext = (x0, x1, y0, y1)
        ax.imshow(np.asarray(a.image[r : r + config.CHIP_PX, c : c + config.CHIP_PX]), extent=ext)
        ax.imshow(label_rgba(lab[r : r + config.CHIP_PX, c : c + config.CHIP_PX]), extent=ext)
        polys = label_polygons(DATA_DIR / name).cx[x0:x1, y0:y1]
        polys.boundary.plot(ax=ax, color="white", linewidth=0.6)
        ax.set_xlim(x0, x1)
        ax.set_ylim(y0, y1)
        ax.set_title(f"{name} ({split_of(name)}): label raster + BGT outlines", fontsize=9)
    class_legend(axes[0, 0])
    fig.tight_layout()
    fig.savefig(out / "raster_vs_vector.jpg", pil_kwargs={"quality": 88})
    plt.close(fig)


def render_split_map(out: Path):
    """All 20 areas on a map of the Netherlands, coloured by split."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    colors = {"train": "#1f77b4", "val": "#ff7f0e", "test": "#d62728"}
    fig, ax = plt.subplots(figsize=(8, 9), dpi=120)
    for name, a in AREAS.items():
        s = split_of(name)
        b = a.bbox
        ax.add_patch(plt.Rectangle((b.xmin, b.ymin), b.width, b.height, color=colors[s]))
        ax.plot((b.xmin + b.xmax) / 2, (b.ymin + b.ymax) / 2, "o", ms=9, mfc="none", mec=colors[s])
        ax.annotate(f"{name} ({a.area_type[0]})", ((b.xmin + b.xmax) / 2, b.ymax), fontsize=7,
                    xytext=(4, 4), textcoords="offset points", color=colors[s])  # fmt: skip
    for s, col in colors.items():
        ax.plot([], [], "s", color=col, label=f"{s}: {len(areas_in(s))} areas")
    ax.legend(loc="upper left")
    ax.set_xlim(0, 290000)
    ax.set_ylim(290000, 630000)
    ax.set_aspect("equal")
    ax.set_title("Phase 4 split by area (EPSG:28992); circles mark the 2 km boxes")
    fig.tight_layout()
    fig.savefig(out / "split_map.png")
    plt.close(fig)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--area", action="append", choices=sorted(AREAS), help="repeatable; default: all")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--report", action="store_true", help="render sample chips and the split map")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    if args.report:
        out = Path("reports/phase4")
        out.mkdir(parents=True, exist_ok=True)
        render_split_map(out)
        render_samples(out)
        return
    for name in args.area or list(AREAS):
        build_area(name, args.force)


if __name__ == "__main__":
    main()

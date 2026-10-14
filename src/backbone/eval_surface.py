"""Phase 4 evaluation: per-class pixel IoU on held-out areas, and a gallery.

    python -m backbone.eval_surface --run experiments/<run> [--split test --split val] [--degrade 2 4]

For each area, the whole 25 cm image is predicted with overlapping tiles
(`predict_area`); class probabilities are stored as uint8 (0-255) in
data/<area>/ml/prob_<run>_<source>.npy for Phase 5. Sources: `8cm` (8 cm
averaged to 25 cm, the training domain), `25cm` (the PDOK 25 cm product,
another flight), and `8cm_x<f>` (8 cm averaged to 25*f cm, then resized back to
the 25 cm grid; resolution robustness).

Writes reports/phase4/metrics.md, metrics.json and gallery_*.jpg, and copies
the metrics into the run directory.
"""

import argparse
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd
import torch

from backbone import config
from backbone.areas import AREAS
from backbone.surface_data import (
    IMAGENET_MEAN,
    IMAGENET_STD,
    class_legend,
    label_rgba,
    ml_dir,
    normalize,
    split_of,
    areas_in,
)
from backbone.train_surface import N_CLASSES, WIDTH_CLASSES, confusion, iou_from_confusion, make_model

log = logging.getLogger(__name__)
REPORT_DIR = Path("reports/phase4")


def load_model(ckpt: Path) -> torch.nn.Module:
    state = torch.load(ckpt, map_location="cpu", weights_only=False)
    if list(state["classes"]) != list(config.SURFACE_CLASSES):
        raise ValueError(f"checkpoint classes {state['classes']} differ from config")
    model = make_model(state["encoder"], weights=None)
    model.load_state_dict(state["model"])
    model.eval()
    return model


def _blend_window(chip: int, margin: int) -> np.ndarray:
    """Weights that fade tile borders, so overlapping tiles blend smoothly."""
    ramp = np.minimum(np.arange(chip) + 0.5, chip - np.arange(chip) - 0.5)
    w1 = np.clip(ramp / margin, 0.05, 1.0)
    return np.outer(w1, w1).astype(np.float32)


@torch.no_grad()
def predict_area(model, image: np.ndarray, chip: int = config.CHIP_PX, overlap: int = 64, batch: int = 16) -> np.ndarray:
    """Class probabilities for a whole (H, W, 3) uint8 image, as (H, W, C)
    uint8 (probability x 255). Tiles overlap by `overlap` px and are blended
    with weights that fade towards tile borders (float32 accumulators: about
    2 GB for an 8000 x 8000 area)."""
    h, w = image.shape[:2]
    stride = chip - overlap

    def starts(n):
        s = list(range(0, n - chip + 1, stride))
        return s if s[-1] == n - chip else s + [n - chip]

    pos = [(r, c) for r in starts(h) for c in starts(w)]
    weight = _blend_window(chip, overlap)
    acc = np.zeros((h, w, N_CLASSES), np.float32)
    wsum = np.zeros((h, w), np.float32)
    for i in range(0, len(pos), batch):
        part = pos[i : i + batch]
        x = torch.from_numpy(np.stack([normalize(np.asarray(image[r : r + chip, c : c + chip])) for r, c in part]))
        p = torch.softmax(model(x), 1).numpy().transpose(0, 2, 3, 1)
        for j, (r, c) in enumerate(part):
            acc[r : r + chip, c : c + chip] += p[j] * weight[..., None]
            wsum[r : r + chip, c : c + chip] += weight
    acc /= wsum[..., None]
    return np.round(acc * 255).astype(np.uint8)


def degrade(image: np.ndarray, factor: float) -> np.ndarray:
    """Average down by `factor` and resize back (simulates 25*factor cm imagery)."""
    from PIL import Image

    h, w = image.shape[:2]
    small = Image.fromarray(np.asarray(image)).resize((round(w / factor), round(h / factor)), Image.BOX)
    return np.asarray(small.resize((w, h), Image.BILINEAR))


def source_image(name: str, source: str) -> np.ndarray:
    if source.startswith("8cm_x"):
        return degrade(np.load(ml_dir(name) / "image_8cm.npy"), float(source.split("_x")[1]))
    return np.load(ml_dir(name) / f"image_{source}.npy", mmap_mode="r")


def eval_mask(shape, margin: int = 32) -> np.ndarray:
    """Pixels scored: the area minus a thin border (tile edge effects)."""
    m = np.zeros(shape, bool)
    m[margin:-margin, margin:-margin] = True
    return m


def iou_row(cm: np.ndarray) -> dict:
    iou = iou_from_confusion(cm)
    row = {c: float(v) for c, v in zip(config.SURFACE_CLASSES, iou)}
    row["mIoU_width"] = float(np.nanmean(iou[WIDTH_CLASSES]))
    row["mIoU_all"] = float(np.nanmean(iou))
    row["pixel_acc"] = float(np.trace(cm) / cm.sum())
    return row


def chip_scores(pred: np.ndarray, lab: np.ndarray, chip: int = config.CHIP_PX):
    """Per non-overlapping chip: carriageway IoU and width-class mIoU, for
    chips with at least 3% carriageway in the label."""
    cw = config.SURFACE_CLASSES.index("carriageway")
    out = []
    for r in range(0, lab.shape[0] - chip + 1, chip):
        for c in range(0, lab.shape[1] - chip + 1, chip):
            l, p = lab[r : r + chip, c : c + chip], pred[r : r + chip, c : c + chip]
            if np.mean(l == cw) < 0.03:
                continue
            iou = iou_from_confusion(confusion(p, l))
            out.append({"r": r, "c": c, "iou_carriageway": iou[cw], "miou_width": np.nanmean(iou[WIDTH_CLASSES])})
    return out


def render_gallery(rows, title: str, out: Path):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    n = len(rows)
    fig, axes = plt.subplots(n, 3, figsize=(10.5, 3.6 * n), dpi=100)
    axes = np.atleast_2d(axes)
    for ax3, row in zip(axes, rows):
        s = config.CHIP_PX
        img = np.asarray(np.load(ml_dir(row["area"]) / "image_8cm.npy", mmap_mode="r")[row["r"] : row["r"] + s, row["c"] : row["c"] + s])
        lab = np.load(ml_dir(row["area"]) / "labels.npy", mmap_mode="r")[row["r"] : row["r"] + s, row["c"] : row["c"] + s]
        ax3[0].imshow(img)
        ax3[1].imshow(img)
        ax3[1].imshow(label_rgba(np.asarray(lab)))
        ax3[2].imshow(img)
        ax3[2].imshow(label_rgba(row["pred"]))
        ax3[0].set_title(f"{row['area']} r{row['r']} c{row['c']}", fontsize=8)
        ax3[1].set_title("BGT label", fontsize=8)
        ax3[2].set_title(f"prediction: carriageway IoU {row['iou_carriageway']:.2f}, width mIoU {row['miou_width']:.2f}", fontsize=8)
        for a in ax3:
            a.set_axis_off()
    class_legend(axes[0, 1])
    fig.suptitle(title, fontsize=10)
    fig.tight_layout()
    fig.savefig(out, pil_kwargs={"quality": 88})
    plt.close(fig)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--ckpt", default="best.pt")
    ap.add_argument("--split", action="append", choices=["train", "val", "test"])
    ap.add_argument("--degrade", type=float, nargs="*", default=[2.0, 4.0], help="also test 8 cm averaged to 25*f cm")
    ap.add_argument("--no-25cm", action="store_true")
    ap.add_argument("--threads", type=int, default=12)
    ap.add_argument("--out", type=Path, default=REPORT_DIR)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    torch.set_num_threads(args.threads)
    model = load_model(args.run / args.ckpt)
    splits = args.split or ["val", "test"]
    sources = ["8cm"] + ([] if args.no_25cm else ["25cm"]) + [f"8cm_x{f:g}" for f in args.degrade]
    run_tag = args.run.name

    records, chips = [], []
    for split in splits:
        for name in areas_in(split):
            lab = np.load(ml_dir(name) / "labels.npy")
            mask = eval_mask(lab.shape)
            for source in sources:
                prob = predict_area(model, source_image(name, source))
                if source in ("8cm", "25cm"):
                    np.save(ml_dir(name) / f"prob_{run_tag}_{source}.npy", prob)
                pred = prob.argmax(-1).astype(np.uint8)
                cm = confusion(pred[mask], lab[mask])
                records.append({"split": split, "area": name, "area_type": AREAS[name].area_type, "source": source,
                                "confusion": cm})  # fmt: skip
                log.info("%s %s %s: %s", split, name, source, {k: round(v, 3) for k, v in iou_row(cm).items()})
                if source == "8cm" and split == "test":
                    for ch in chip_scores(pred, lab):
                        s = config.CHIP_PX
                        chips.append({"area": name, **ch, "pred": pred[ch["r"] : ch["r"] + s, ch["c"] : ch["c"] + s]})

    def agg(df):
        return pd.Series(iou_row(np.sum(np.stack(df["confusion"].to_numpy()), axis=0)))

    df = pd.DataFrame(records)
    tables = {
        "by_split_source": df.groupby(["split", "source"]).apply(agg, include_groups=False),
        "by_split_area_type_8cm": df[df.source == "8cm"].groupby(["split", "area_type"]).apply(agg, include_groups=False),
        "by_area_8cm": df[df.source == "8cm"].groupby(["split", "area"]).apply(agg, include_groups=False),
    }
    cm_test = np.sum(np.stack(df[(df.split == "test") & (df.source == "8cm")]["confusion"].to_numpy()), axis=0) if "test" in splits else None

    args.out.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Phase 4 surface-model metrics (generated by backbone.eval_surface)",
        "",
        f"Run: `{args.run}` ({args.ckpt}). Per-class pixel IoU, whole areas minus a 32 px (8 m) border.",
        "Sources: `8cm` = 8 cm imagery averaged to 25 cm (training domain); `25cm` = PDOK 25 cm product "
        "(different flight); `8cm_x<f>` = 8 cm averaged to 25*f cm and resized back.",
        "",
    ]
    for k, t in tables.items():
        lines += [f"## {k}", "", t.round(3).to_markdown(), ""]
    if cm_test is not None:
        share = cm_test / cm_test.sum(1, keepdims=True)
        lines += ["## test confusion (8cm), row-normalized: rows = BGT label, columns = prediction", "",
                  pd.DataFrame(share, index=config.SURFACE_CLASSES, columns=config.SURFACE_CLASSES).round(3).to_markdown(), ""]  # fmt: skip
    (args.out / "metrics.md").write_text("\n".join(lines))
    js = {k: t.round(4).reset_index().to_dict(orient="records") for k, t in tables.items()}
    (args.out / "metrics.json").write_text(json.dumps(js, indent=1))
    (args.run / "eval_metrics.json").write_text(json.dumps(js, indent=1))

    if chips:
        ch = pd.DataFrame(chips).sort_values("miou_width").reset_index(drop=True)
        n = len(ch)
        picks = {
            "worst": ch.iloc[:6],
            "median": ch.iloc[max(0, n // 2 - 3) : n // 2 + 3],
            "best": ch.iloc[-6:][::-1],
        }
        for k, sub in picks.items():
            render_gallery(sub.to_dict("records"), f"Test chips, {k} by width-class mIoU (of {n} chips with >= 3% carriageway)",
                           args.out / f"gallery_{k}.jpg")  # fmt: skip
        ch.drop(columns="pred").to_csv(args.out / "test_chips.csv", index=False)
    log.info("wrote %s", args.out)


if __name__ == "__main__":
    main()

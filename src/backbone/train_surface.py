"""Phase 4: train the surface-class U-Net on CPU.

    python -m backbone.train_surface --benchmark                       # seconds per training batch
    python -m backbone.train_surface --name overfit --overfit 300 --epochs 40
    python -m backbone.train_surface --name full --epochs 8 --max-minutes 240

Each run writes experiments/<date>_<name>/: config.json (arguments, git commit,
split, classes), metrics.jsonl (one line per epoch), last.pt and best.pt
(best = highest mean validation IoU over the classes width fitting uses:
carriageway, inrit, parking, cycle).
"""

import argparse
import datetime as dt
import json
import logging
import subprocess
import time
from pathlib import Path

import numpy as np
import segmentation_models_pytorch as smp
import torch
from torch.utils.data import DataLoader

from backbone import config
from backbone.surface_data import ChipDataset, areas_in, ml_dir, tile_positions, train_candidates

log = logging.getLogger(__name__)

N_CLASSES = len(config.SURFACE_CLASSES)
WIDTH_CLASSES = [config.SURFACE_CLASSES.index(c) for c in ("carriageway", "inrit", "parking", "cycle")]


def make_model(encoder: str = "resnet18", weights: str | None = "imagenet") -> torch.nn.Module:
    return smp.Unet(encoder_name=encoder, encoder_weights=weights, classes=N_CLASSES)


def class_weights(names) -> torch.Tensor:
    """Inverse square root of each class's pixel share over the given areas,
    normalized to mean 1. Rare classes (inrit is 0.3% of pixels) otherwise
    get almost no gradient from cross-entropy."""
    share = np.zeros(N_CLASSES)
    for name in names:
        meta = json.loads((ml_dir(name) / "meta.json").read_text())
        share += np.array([meta["class_share"][c] for c in config.SURFACE_CLASSES])
    w = 1 / np.sqrt(share / share.sum())
    return torch.tensor(w / w.mean(), dtype=torch.float32)


class Loss(torch.nn.Module):
    """Cross-entropy (optionally class-weighted) plus multi-class Dice
    (SPEC Phase 4 step 4)."""

    def __init__(self, weight: torch.Tensor | None = None):
        super().__init__()
        self.ce = torch.nn.CrossEntropyLoss(weight=weight)
        self.dice = smp.losses.DiceLoss(mode="multiclass")

    def forward(self, logits, target):
        return self.ce(logits, target) + self.dice(logits, target)


def confusion(pred: np.ndarray, target: np.ndarray, n: int = N_CLASSES) -> np.ndarray:
    """n x n counts, rows = target, columns = prediction."""
    return np.bincount(target.ravel() * n + pred.ravel(), minlength=n * n).reshape(n, n)


def iou_from_confusion(cm: np.ndarray) -> np.ndarray:
    tp = np.diag(cm).astype(float)
    denom = cm.sum(0) + cm.sum(1) - tp
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(denom > 0, tp / denom, np.nan)


@torch.no_grad()
def evaluate(model, loader, loss_fn) -> dict:
    model.eval()
    cm = np.zeros((N_CLASSES, N_CLASSES), np.int64)
    losses = []
    for x, y in loader:
        logits = model(x)
        losses.append(loss_fn(logits, y).item())
        cm += confusion(logits.argmax(1).numpy(), y.numpy())
    iou = iou_from_confusion(cm)
    return {
        "loss": float(np.mean(losses)),
        "iou": {c: round(float(v), 4) for c, v in zip(config.SURFACE_CLASSES, iou)},
        "miou_width": round(float(np.nanmean(iou[WIDTH_CLASSES])), 4),
        "miou_all": round(float(np.nanmean(iou)), 4),
        "confusion": cm.tolist(),
    }


def git_commit() -> dict:
    def run(*cmd):
        return subprocess.run(cmd, capture_output=True, text=True).stdout.strip()

    return {"commit": run("git", "rev-parse", "HEAD"), "dirty": bool(run("git", "status", "--porcelain", "src"))}


def loader(ds, batch, shuffle, seed, workers):
    g = torch.Generator()
    g.manual_seed(seed)
    # Workers are re-created every epoch, so ChipDataset.set_epoch reaches them.
    return DataLoader(ds, batch_size=batch, shuffle=shuffle, generator=g, num_workers=workers, drop_last=shuffle)


def benchmark(args):
    torch.manual_seed(0)
    model = make_model(args.encoder)
    opt = torch.optim.AdamW(model.parameters(), lr=1e-3)
    loss_fn = Loss()
    x = torch.randn(args.batch, 3, config.CHIP_PX, config.CHIP_PX)
    y = torch.randint(0, N_CLASSES, (args.batch, config.CHIP_PX, config.CHIP_PX))
    for threads in args.bench_threads:
        torch.set_num_threads(threads)
        model.train()
        times = []
        for i in range(6):
            t = time.perf_counter()
            opt.zero_grad()
            loss_fn(model(x), y).backward()
            opt.step()
            if i:  # first step warms up
                times.append(time.perf_counter() - t)
        model.eval()
        with torch.no_grad():
            t = time.perf_counter()
            for _ in range(3):
                model(x)
            inf = (time.perf_counter() - t) / 3
        s = float(np.median(times))
        print(
            f"threads {threads:2d}: train {s:.2f} s/batch of {args.batch} = {args.batch / s:.1f} chips/s; "
            f"inference {args.batch / inf:.1f} chips/s"
        )


def train(args):
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    torch.set_num_threads(args.threads)
    run_dir = config.EXPERIMENTS_DIR / f"{dt.date.today().isoformat()}_{args.name}"
    run_dir.mkdir(parents=True, exist_ok=args.resume_ok)

    rng = np.random.default_rng(args.seed)
    cands = train_candidates(areas_in("train"), seed=args.seed)
    if args.overfit:
        train_pos = [cands[i] for i in rng.choice(len(cands), args.overfit, replace=False)]
        val_pos = train_pos  # memorization check: evaluate on the same chips
        train_ds = ChipDataset(train_pos, augment_seed=None)
    else:
        train_pos = cands
        val_all = [(a, r, c) for a in areas_in("val") for r, c in tile_positions(_shape(a))]
        val_pos = [val_all[i] for i in np.sort(rng.choice(len(val_all), min(args.val_chips, len(val_all)), replace=False))]
        train_ds = ChipDataset(train_pos, augment_seed=args.seed)
    val_ds = ChipDataset(val_pos)

    steps_per_epoch = (
        min(len(train_pos), args.chips_per_epoch) if args.chips_per_epoch else len(train_pos)
    ) // args.batch
    model = make_model(args.encoder)
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.OneCycleLR(
        opt, max_lr=args.lr, total_steps=max(1, args.epochs * steps_per_epoch), pct_start=0.1
    )
    weights = class_weights(areas_in("train")) if args.class_weights else None
    loss_fn = Loss(weights)
    cfg = {
        "args": vars(args),
        "git": git_commit(),
        "classes": list(config.SURFACE_CLASSES),
        "res_m": config.ML_RES_M,
        "chip_px": config.CHIP_PX,
        "train_areas": areas_in("train"),
        "val_areas": areas_in("val"),
        "test_areas": areas_in("test"),
        "n_train_candidates": len(train_pos),
        "n_val_chips": len(val_pos),
        "steps_per_epoch": steps_per_epoch,
        "class_weights": None if weights is None else [round(float(w), 3) for w in weights],
        "torch": torch.__version__,
        "smp": smp.__version__,
        "started": dt.datetime.now().isoformat(timespec="seconds"),
    }
    (run_dir / "config.json").write_text(json.dumps(cfg, indent=1))
    log.info("run %s: %d train chips, %d val chips, %d steps/epoch", run_dir, len(train_pos), len(val_pos), steps_per_epoch)

    t0 = time.time()
    best = -1.0
    deadline = t0 + args.max_minutes * 60 if args.max_minutes else None
    for epoch in range(args.epochs):
        train_ds.set_epoch(epoch)
        idx_rng = np.random.default_rng([args.seed, epoch])
        sub = idx_rng.permutation(len(train_pos))[: steps_per_epoch * args.batch]
        tl = loader(torch.utils.data.Subset(train_ds, sub), args.batch, True, args.seed + epoch, args.workers)
        model.train()
        losses, te = [], time.time()
        stopped = False
        for step, (x, y) in enumerate(tl):
            opt.zero_grad()
            loss = loss_fn(model(x), y)
            loss.backward()
            opt.step()
            sched.step()
            losses.append(loss.item())
            if step % 50 == 0:
                log.info("epoch %d step %d/%d loss %.4f (%.1f chips/s)", epoch, step, steps_per_epoch,
                         np.mean(losses[-50:]), (step + 1) * args.batch / (time.time() - te))  # fmt: skip
            if deadline and time.time() > deadline:
                log.warning("time budget of %s min reached in epoch %d, stopping", args.max_minutes, epoch)
                stopped = True
                break
        train_s = time.time() - te
        last = epoch == args.epochs - 1 or stopped
        if (epoch + 1) % args.eval_every and not last:
            log.info("epoch %d: train loss %.4f (%.0f s)", epoch, np.mean(losses), train_s)
            continue
        ev = evaluate(model, loader(val_ds, args.batch, False, 0, args.workers), loss_fn)
        rec = {
            "epoch": epoch,
            "train_loss": round(float(np.mean(losses)), 4),
            "train_steps": len(losses),
            "train_s": round(train_s, 1),
            "val_loss": round(ev["loss"], 4),
            "val_iou": ev["iou"],
            "val_miou_width": ev["miou_width"],
            "val_miou_all": ev["miou_all"],
            "elapsed_min": round((time.time() - t0) / 60, 1),
            "lr": sched.get_last_lr()[0],
        }
        with open(run_dir / "metrics.jsonl", "a") as f:
            f.write(json.dumps(rec) + "\n")
        log.info("epoch %d: %s", epoch, json.dumps({k: v for k, v in rec.items() if k != "val_iou"}))
        log.info("  val IoU %s", ev["iou"])
        state = {"model": model.state_dict(), "encoder": args.encoder, "classes": list(config.SURFACE_CLASSES),
                 "epoch": epoch, "metrics": rec, "confusion": ev["confusion"]}  # fmt: skip
        torch.save(state, run_dir / "last.pt")
        if ev["miou_width"] > best:
            best = ev["miou_width"]
            torch.save(state, run_dir / "best.pt")
        if stopped:
            break
    log.info("done in %.1f min, best val mIoU (width classes) %.4f", (time.time() - t0) / 60, best)


def _shape(name):
    return np.load(ml_dir(name) / "labels.npy", mmap_mode="r").shape


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", default="run")
    ap.add_argument("--benchmark", action="store_true")
    ap.add_argument("--bench-threads", type=int, nargs="+", default=[4, 8, 12, 16])
    ap.add_argument("--encoder", default="resnet18")
    ap.add_argument("--epochs", type=int, default=10)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--threads", type=int, default=12)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--chips-per-epoch", type=int, default=0, help="0 = all training candidates")
    ap.add_argument("--val-chips", type=int, default=600)
    ap.add_argument("--overfit", type=int, default=0, help="train and evaluate on this many fixed chips")
    ap.add_argument("--max-minutes", type=float, default=0, help="stop training after this (0 = no limit)")
    ap.add_argument("--eval-every", type=int, default=1, help="validate every n epochs (and after the last)")
    ap.add_argument("--class-weights", action="store_true", help="inverse-sqrt-frequency weights in cross-entropy")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--resume-ok", action="store_true", help="allow an existing run directory")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    if args.benchmark:
        benchmark(args)
    else:
        train(args)


if __name__ == "__main__":
    main()

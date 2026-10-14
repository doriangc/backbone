"""Fetch BGT wegdeel, PDOK Luchtfoto and OSM drivable roads for one area.

    python -m backbone.fetch --area utrecht_centrum
    python -m backbone.fetch --bbox 135900,454900,137900,456900

Everything is cached under data/<area>/; reruns skip outputs that already exist
(use --force to refetch). data/<area>/manifest.json records URLs, layers, data
timestamps, licenses, counts and file hashes.
"""

import argparse
import datetime as dt
import gzip
import hashlib
import json
import logging
import re
import shutil
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import requests
from PIL import Image
from rasterio.windows import Window
from requests.adapters import HTTPAdapter
from shapely.geometry import LineString, box
from urllib3.util.retry import Retry

from backbone import config
from backbone.areas import AREAS, BBox, bbox_name, parse_bbox
from backbone.grid import Grid

log = logging.getLogger("backbone.fetch")

DATA_DIR = Path("data")


def session() -> requests.Session:
    s = requests.Session()
    # POST is retried too: Overpass queries are read-only and often return 504 when busy.
    retry = Retry(
        total=6,
        backoff_factor=5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
    )
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers["User-Agent"] = config.USER_AGENT
    return s


def write_json_gz(obj, path: Path) -> None:
    """Atomic, byte-reproducible gzip JSON (no mtime or file name in the header)."""
    tmp = path.with_suffix(".tmp")
    with open(tmp, "wb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as gz:
        gz.write(json.dumps(obj).encode())
    tmp.rename(path)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --- BGT -------------------------------------------------------------------


def bgt_current(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Keep only the current version of each BGT object.

    The PDOK OGC API returns every historic version of an object (verified
    2026-10-12: 26k features, 13k duplicate lokaal_id, area 4x the union for
    central Utrecht). A version is current when its registration has not ended
    (`eind_registratie` null) and the object itself still exists
    (`termination_date` null).
    """
    cur = gdf[gdf["eind_registratie"].isna() & gdf["termination_date"].isna()].copy()
    dups = cur["lokaal_id"].duplicated().sum()
    if dups:
        raise ValueError(f"{dups} lokaal_id values have more than one current version")
    return cur


def fetch_bgt_collection(
    collection: str, bbox: BBox, out_dir: Path, s: requests.Session
) -> tuple[gpd.GeoDataFrame, Path]:
    """Download (or reuse) all versions of one BGT collection intersecting bbox.

    Returns every version; apply bgt_current to get the present state.
    """
    raw_path = out_dir / f"bgt_{collection}_raw.geojson.gz"
    if not raw_path.exists():
        params = {
            "f": "json",
            "limit": config.BGT_PAGE_LIMIT,
            "bbox": ",".join(str(v) for v in bbox.as_tuple()),
            "bbox-crs": config.RD_CRS_URI,
            "crs": config.RD_CRS_URI,
        }
        url, features, pages = f"{config.BGT_API_URL}/collections/{collection}/items", [], 0
        while url:
            r = s.get(url, params=params, timeout=180)
            r.raise_for_status()
            if config.RD_CRS_URI not in r.headers.get("Content-Crs", ""):
                raise ValueError(f"unexpected Content-Crs {r.headers.get('Content-Crs')}")
            page = r.json()
            features += page["features"]
            pages += 1
            params = None  # the next link carries all parameters
            url = next((l["href"] for l in page["links"] if l["rel"] == "next"), None)
            log.info("BGT %s page %d: %d features so far", collection, pages, len(features))
        write_json_gz({"type": "FeatureCollection", "features": features}, raw_path)

    with gzip.open(raw_path, "rt") as f:
        features = json.load(f)["features"]
    return gpd.GeoDataFrame.from_features(features, crs=config.RD_EPSG), raw_path


def fetch_bgt(bbox: BBox, out_dir: Path, s: requests.Session) -> tuple[gpd.GeoDataFrame, dict]:
    """Current wegdeel objects; `drivable_class` marks the drivable functie values.
    The final `drivable` column is set in run(), once OSM is known."""
    all_versions, raw_path = fetch_bgt_collection("wegdeel", bbox, out_dir, s)
    cur = bgt_current(all_versions)
    cur["drivable_class"] = cur["functie"].isin(config.BGT_DRIVABLE_FUNCTIES)

    unknown = set(cur["functie"]) - config.BGT_DRIVABLE_FUNCTIES - {
        "spoorbaan", "baan voor vliegverkeer", "fietspad", "voetpad",
        "voetpad op trap", "ruiterpad", "parkeervlak", "voetgangersgebied",
    }  # fmt: skip
    if unknown:
        log.warning("BGT functie values outside the codelist: %s", sorted(unknown))
    return cur, {
        "endpoint": f"{config.BGT_API_URL}/collections/wegdeel/items",
        "license": config.BGT_LICENSE,
        "fetched_versions": len(all_versions),
        "current_features": len(cur),
        "functie_counts": cur["functie"].value_counts().to_dict(),
        "max_lv_publicatiedatum": cur["lv_publicatiedatum"].max(),
        "raw_file": raw_path.name,
        "raw_sha256": sha256(raw_path),  # GPKGs embed write times, so only raw is hashed
    }


def fetch_bgt_terrain(bbox: BBox, out_dir: Path, s: requests.Session):
    """Current, ground-level onbegroeidterreindeel (unvegetated terrain)."""
    all_versions, raw_path = fetch_bgt_collection("onbegroeidterreindeel", bbox, out_dir, s)
    cur = bgt_current(all_versions)
    cur = cur[cur["relatieve_hoogteligging"] == 0].copy()
    cur["paved"] = cur["fysiek_voorkomen"].isin(config.BGT_PAVED_TERRAIN)
    info = {
        "endpoint": f"{config.BGT_API_URL}/collections/onbegroeidterreindeel/items",
        "license": config.BGT_LICENSE,
        "fetched_versions": len(all_versions),
        "current_ground_features": len(cur),
        "fysiek_voorkomen_counts": cur["fysiek_voorkomen"].value_counts().to_dict(),
        "raw_file": raw_path.name,
        "raw_sha256": sha256(raw_path),
    }
    return cur, info


def mark_service_paved(terrain: gpd.GeoDataFrame, ways: gpd.GeoDataFrame) -> pd.Series:
    """True for paved terrain polygons that a kept OSM service way or busway
    (config.PAVED_TERRAIN_HIGHWAYS) runs over
    for at least SERVICE_PAVED_MIN_OVERLAP_M. Overlap is measured inside the
    polygon shrunk by SERVICE_PAVED_EDGE_M, so a way running along the polygon's
    edge (e.g. on the adjacent wegdeel) does not count."""
    paved = terrain[terrain["paved"]]
    svc = ways[ways["highway"].isin(config.PAVED_TERRAIN_HIGHWAYS)]
    out = pd.Series(False, index=terrain.index)
    if paved.empty or svc.empty:
        return out
    pairs = gpd.sjoin(paved[["geometry"]], svc[["geometry"]], predicate="intersects")
    overlap = [
        paved.geometry[i].buffer(-config.SERVICE_PAVED_EDGE_M).intersection(svc.geometry[j]).length
        for i, j in zip(pairs.index, pairs["index_right"])
    ]
    pairs["overlap"] = overlap
    per_poly = pairs.groupby(level=0)["overlap"].sum()
    out[per_poly[per_poly >= config.SERVICE_PAVED_MIN_OVERLAP_M].index] = True
    return out


def mark_fietspad_under_way(wegdeel: gpd.GeoDataFrame, ways: gpd.GeoDataFrame) -> pd.Series:
    """True for `fietspad` polygons that a kept OSM drivable way runs along
    (see config.FIETSPAD_ALONG_MIN_SHARE)."""
    paths = wegdeel[wegdeel["functie"] == "fietspad"]
    out = pd.Series(False, index=wegdeel.index)
    if paths.empty or ways.empty:
        return out
    pairs = gpd.sjoin(paths[["geometry"]], ways[["geometry"]], predicate="intersects")
    pairs["overlap"] = [
        paths.geometry[i].buffer(-config.SERVICE_PAVED_EDGE_M).intersection(ways.geometry[j]).length
        for i, j in zip(pairs.index, pairs["index_right"])
    ]
    overlap = pairs.groupby(level=0)["overlap"].sum()
    length = paths.geometry[overlap.index].map(_long_side)
    hit = overlap[
        (overlap >= config.SERVICE_PAVED_MIN_OVERLAP_M)
        & (overlap >= config.FIETSPAD_ALONG_MIN_SHARE * length)
    ].index
    out[hit] = True
    return out


def _long_side(poly) -> float:
    rect = poly.minimum_rotated_rectangle
    xs, ys = rect.exterior.coords.xy
    sides = [((xs[k + 1] - xs[k]) ** 2 + (ys[k + 1] - ys[k]) ** 2) ** 0.5 for k in range(len(xs) - 1)]
    return max(sides) if sides else 0.0


# --- Luchtfoto ---------------------------------------------------------------


def fetch_luchtfoto(bbox: BBox, out_dir: Path, s: requests.Session, key: str) -> dict:
    layer, res = config.LUCHTFOTO_LAYERS[key]
    grid = Grid.for_bbox(bbox, res)
    out_path = out_dir / f"luchtfoto_{key}.tif"
    tile_dir = out_dir / "tiles" / layer
    tile_dir.mkdir(parents=True, exist_ok=True)
    tiles = grid.tiles(config.WMS_TILE_PX)
    assert config.WMS_TILE_PX <= config.WMS_MAX_PX

    for i, t in enumerate(tiles):
        p = tile_dir / f"r{t.row:03d}_c{t.col:03d}.jpg"
        if p.exists():
            continue
        params = {
            "service": "WMS",
            "version": "1.3.0",
            "request": "GetMap",
            "layers": layer,
            "styles": "",
            # EPSG:28992 axis order is easting, northing, also in WMS 1.3.0.
            "crs": f"EPSG:{config.RD_EPSG}",
            "bbox": ",".join(repr(v) for v in t.bbox.as_tuple()),
            "width": t.width,
            "height": t.height,
            "format": "image/jpeg",
        }
        r = s.get(config.LUCHTFOTO_WMS_URL, params=params, timeout=300)
        r.raise_for_status()
        if not r.headers.get("Content-Type", "").startswith("image/jpeg"):
            raise ValueError(f"WMS returned {r.headers.get('Content-Type')}: {r.text[:500]}")
        p.with_suffix(".tmp").write_bytes(r.content)
        p.with_suffix(".tmp").rename(p)
        log.info("%s tile %d/%d", layer, i + 1, len(tiles))

    if not out_path.exists():
        mosaic_tiles(grid, tiles, tile_dir, out_path)

    with rasterio.open(out_path) as ds:
        sample = ds.read(out_shape=(3, 256, 256))
    blank = float(np.mean((sample.min(axis=0) > 250) | (sample.max(axis=0) < 5)))
    if blank > 0.05:
        log.warning("%s: %.0f%% of the image looks blank (no coverage?)", layer, 100 * blank)
    return {
        "endpoint": config.LUCHTFOTO_WMS_URL,
        "layer": layer,
        "resolution_m": res,
        "license": config.LUCHTFOTO_LICENSE,
        "grid_bbox": grid.bbox.as_tuple(),
        "size_px": [grid.width, grid.height],
        "tiles": len(tiles),
        "blank_fraction": round(blank, 4),
        "file": out_path.name,
        "file_mb": round(out_path.stat().st_size / 1e6, 1),
        "sha256": sha256(out_path),
    }


def mosaic_tiles(grid: Grid, tiles, tile_dir: Path, out_path: Path) -> None:
    profile = {
        "driver": "GTiff",
        "dtype": "uint8",
        "count": 3,
        "width": grid.width,
        "height": grid.height,
        "crs": f"EPSG:{config.RD_EPSG}",
        "transform": grid.transform,
        "tiled": True,
        "blockxsize": 512,
        "blockysize": 512,
        "compress": "JPEG",
        "photometric": "YCBCR",
        "jpeg_quality": 90,
        "BIGTIFF": "IF_SAFER",
    }
    tmp = out_path.with_suffix(".tmp.tif")
    with rasterio.open(tmp, "w", **profile) as dst:
        for t in tiles:
            img = Image.open(tile_dir / f"r{t.row:03d}_c{t.col:03d}.jpg").convert("RGB")
            if img.size != (t.width, t.height):
                raise ValueError(f"tile {t.row},{t.col} is {img.size}, expected {(t.width, t.height)}")
            arr = np.asarray(img).transpose(2, 0, 1)
            dst.write(arr, window=Window(t.col_off, t.row_off, t.width, t.height))
        dst.build_overviews([2, 4, 8, 16, 32], rasterio.enums.Resampling.average)
    tmp.rename(out_path)


# --- OSM ---------------------------------------------------------------------


def highway_regex() -> str:
    names = sorted(config.OSM_DRIVABLE_HIGHWAYS)
    links = sorted(f"{h}_link" for h in config.OSM_LINK_HIGHWAYS)
    return "^(" + "|".join(names + links) + ")$"


def is_drivable_highway(value: str | None) -> bool:
    return value is not None and re.fullmatch(highway_regex(), value) is not None


def overpass_query(bbox: BBox) -> str:
    w, s_, e, n = bbox.to_wgs84()
    return (
        "[out:json][timeout:300];"
        f'way["highway"~"{highway_regex()}"]({s_:.7f},{w:.7f},{n:.7f},{e:.7f});'
        "out body;>;out skel qt;"
    )


KEEP_TAGS = [
    "highway", "name", "ref", "lanes", "width", "oneway", "service", "surface",
    "bridge", "tunnel", "layer", "access", "motor_vehicle", "area", "junction",
]  # fmt: skip


def _set(tags: dict, key: str) -> bool:
    return tags.get(key) not in (None, "no")


def _below_ground_level(level: str | None) -> bool:
    try:
        return level is not None and any(float(v) < 0 for v in level.split(";"))
    except ValueError:
        return False


def exclusion_reasons(tags: dict) -> list[str]:
    """Why a drivable-highway way is left out of the label set (empty = keep).

    - parking: parking aisles and similar (out of scope, see config).
    - not_visible: the road surface is hidden from aerial imagery: tunnels and
      building passages, covered or indoor roads, and underground garage levels.
      A bare negative `layer` is not enough; in the pilot those are open ramps.
    """
    reasons = []
    if (
        tags.get("service") in config.OSM_PARKING_SERVICE
        or tags.get("amenity") == "parking"
        or "parking" in tags
    ):
        reasons.append("parking")
    if (
        _set(tags, "tunnel")
        or _set(tags, "covered")
        or _set(tags, "indoor")
        or tags.get("location") in ("underground", "indoor")
        or _below_ground_level(tags.get("level"))
    ):
        reasons.append("not_visible")
    return reasons


def osm_to_gdf(osm: dict, bbox: BBox) -> gpd.GeoDataFrame:
    """Ways as LineStrings in RD New. Node ids are kept (JSON list) so ways can
    be split at shared nodes in Phase 2. Ways are kept whole, not clipped.
    `exclude` lists the reasons a way is left out (see exclusion_reasons)."""
    nodes = {e["id"]: (e["lon"], e["lat"]) for e in osm["elements"] if e["type"] == "node"}
    rows = []
    for e in osm["elements"]:
        if e["type"] != "way":
            continue
        tags = e.get("tags", {})
        if not is_drivable_highway(tags.get("highway")) or tags.get("area") == "yes":
            continue
        missing = [n for n in e["nodes"] if n not in nodes]
        if missing:
            raise ValueError(f"way {e['id']} references {len(missing)} missing nodes")
        row = {"osm_id": e["id"], **{k: tags.get(k) for k in KEEP_TAGS}}
        row["nodes"] = json.dumps(e["nodes"])
        row["tags"] = json.dumps(tags, ensure_ascii=False)
        row["exclude"] = ";".join(exclusion_reasons(tags)) or None
        row["geometry"] = LineString([nodes[n] for n in e["nodes"]])
        rows.append(row)
    gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs=config.WGS84_EPSG).to_crs(config.RD_EPSG)
    # The lon/lat query envelope is larger than the RD box; drop ways outside it.
    return gdf[gdf.intersects(box(*bbox.as_tuple()))].reset_index(drop=True)


def mark_private_yards(ways: gpd.GeoDataFrame, erf) -> gpd.GeoDataFrame:
    """Add `erf_share` and the `private_yard` exclusion for service ways that
    lie mostly on BGT `erf` (see config.OSM_SERVICE_MAX_ERF_SHARE)."""
    ways = ways.copy()
    ways["erf_share"] = (ways.geometry.intersection(erf).length / ways.length).round(3)
    hit = (ways["highway"] == "service") & (ways["erf_share"] > config.OSM_SERVICE_MAX_ERF_SHARE)
    ways.loc[hit, "exclude"] = ways.loc[hit, "exclude"].map(
        lambda r: "private_yard" if r is None or pd.isna(r) else f"{r};private_yard"
    )
    return ways


def fetch_osm(bbox: BBox, out_dir: Path, s: requests.Session, erf) -> tuple[gpd.GeoDataFrame, dict]:
    out_path = out_dir / "osm_drivable.gpkg"
    query = overpass_query(bbox)
    # The cache file is keyed by the query, so changing the filter refetches.
    raw_path = out_dir / f"osm_raw_{hashlib.sha256(query.encode()).hexdigest()[:10]}.json.gz"
    for stale in out_dir.glob("osm_raw*.json.gz"):
        if stale != raw_path:
            stale.unlink()
    if not raw_path.exists():
        r = s.post(config.OVERPASS_URL, data={"data": query}, timeout=360)
        r.raise_for_status()
        osm = r.json()
        if "remark" in osm:
            raise ValueError(f"Overpass remark: {osm['remark']}")
        write_json_gz(osm, raw_path)
    with gzip.open(raw_path, "rt") as f:
        osm = json.load(f)
    gdf = mark_private_yards(osm_to_gdf(osm, bbox), erf)
    kept, excluded = gdf[gdf["exclude"].isna()], gdf[gdf["exclude"].notna()]
    # Derived files are always rebuilt from the cached raw download, so rule
    # changes take effect without refetching.
    out_path.unlink(missing_ok=True)
    kept.to_file(out_path, layer="ways", driver="GPKG")
    excluded.to_file(out_path, layer="excluded", driver="GPKG")
    return kept, {
        "endpoint": config.OVERPASS_URL,
        "query": query,
        "license": config.OSM_LICENSE,
        "osm_base_timestamp": osm["osm3s"]["timestamp_osm_base"],
        "ways": len(kept),
        "highway_counts": kept["highway"].value_counts().to_dict(),
        "excluded_ways": excluded["exclude"].value_counts().to_dict(),
        "excluded_length_m": excluded.length.groupby(excluded["exclude"]).sum().round().to_dict(),
        "raw_file": raw_path.name,
        "raw_sha256": sha256(raw_path),  # GPKGs embed write times, so only raw is hashed
    }


# --- main ----------------------------------------------------------------------


def run(name: str, bbox: BBox, force: bool = False) -> Path:
    out_dir = DATA_DIR / name
    out_dir.mkdir(parents=True, exist_ok=True)
    if force:
        for p in out_dir.glob("*"):
            if p.is_file():
                p.unlink()
        shutil.rmtree(out_dir / "tiles", ignore_errors=True)
    s = session()
    manifest = {
        "area": name,
        "bbox_epsg28992": bbox.as_tuple(),
        "bbox_wgs84": bbox.to_wgs84(),
        "run_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
    }
    log.info("BGT ...")
    wegdeel, manifest["bgt"] = fetch_bgt(bbox, out_dir, s)
    log.info("BGT terrain ...")
    terrain, manifest["bgt_terrain"] = fetch_bgt_terrain(bbox, out_dir, s)
    log.info("OSM ...")
    erf = terrain[terrain["fysiek_voorkomen"] == "erf"].union_all()
    ways, manifest["osm"] = fetch_osm(bbox, out_dir, s, erf)
    terrain["service_paved"] = mark_service_paved(terrain, ways)
    manifest["bgt_terrain"]["service_paved_features"] = int(terrain["service_paved"].sum())
    terrain_path = out_dir / "bgt_onbegroeidterreindeel.gpkg"
    terrain_path.unlink(missing_ok=True)
    terrain.to_file(terrain_path, layer="onbegroeidterreindeel", driver="GPKG")
    wegdeel["fietspad_under_way"] = mark_fietspad_under_way(wegdeel, ways)
    wegdeel["drivable"] = wegdeel["drivable_class"] | wegdeel["fietspad_under_way"]
    manifest["bgt"]["drivable_features"] = int(wegdeel["drivable"].sum())
    manifest["bgt"]["fietspad_under_way_features"] = int(wegdeel["fietspad_under_way"].sum())
    wegdeel_path = out_dir / "bgt_wegdeel.gpkg"
    wegdeel_path.unlink(missing_ok=True)
    wegdeel.to_file(wegdeel_path, layer="wegdeel", driver="GPKG")
    manifest["luchtfoto"] = {}
    for key in config.LUCHTFOTO_LAYERS:
        log.info("Luchtfoto %s ...", key)
        manifest["luchtfoto"][key] = fetch_luchtfoto(bbox, out_dir, s, key)
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str))
    log.info("done: %s", out_dir / "manifest.json")
    return out_dir


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--area", choices=sorted(AREAS))
    g.add_argument("--bbox", type=parse_bbox, help="xmin,ymin,xmax,ymax in EPSG:28992")
    ap.add_argument("--force", action="store_true", help="refetch even if cached")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    if args.area:
        run(args.area, AREAS[args.area].bbox, args.force)
    else:
        run(bbox_name(args.bbox), args.bbox, args.force)


if __name__ == "__main__":
    main()

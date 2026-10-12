"""Which BGT layer lies under OSM service roads that are not on BGT wegdeel?

    uv run python notebooks/service_roads_bgt.py --area utrecht_centrum

Fetches the BGT surface layers for the area (cached in data/<area>/), keeps the
current version of each object at ground level, and attributes the length of
every kept OSM service way to the layer and class it lies on.
"""

import argparse
import logging

import geopandas as gpd
import pandas as pd

from backbone.areas import AREAS
from backbone.fetch import DATA_DIR, bgt_current, fetch_bgt_collection, session

# BGT layers that together cover the ground surface, with the attribute that
# carries their class.
SURFACE_LAYERS = {
    "wegdeel": "functie",
    "ondersteunendwegdeel": "functie",
    "onbegroeidterreindeel": "fysiek_voorkomen",
    "begroeidterreindeel": "fysiek_voorkomen",
    "pand": None,
    "overigbouwwerk": "type",
    "overbruggingsdeel": "type_overbruggingsdeel",
    "kunstwerkdeel_vlak": "type",
    "scheiding_vlak": "type",
    "waterdeel": "type",
    "ondersteunendwaterdeel": "type",
    "tunneldeel": None,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--area", default="utrecht_centrum", choices=sorted(AREAS))
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    area = AREAS[args.area]
    out_dir = DATA_DIR / area.name
    s = session()

    osm = gpd.read_file(out_dir / "osm_drivable.gpkg", layer="ways")
    svc = osm[osm["highway"] == "service"].copy()
    svc["service"] = svc["service"].fillna("(none)")
    total = svc.length.sum()
    print(f"service ways: {len(svc)}, {total / 1000:.1f} km\n")

    parts = []
    for layer, attr in SURFACE_LAYERS.items():
        g = bgt_current(fetch_bgt_collection(layer, area.bbox, out_dir, s)[0])
        if "relatieve_hoogteligging" in g:
            g = g[g["relatieve_hoogteligging"] == 0]
        g = g[g.geom_type.isin(["Polygon", "MultiPolygon"])]
        if attr and attr not in g:
            print(f"!! {layer}: no attribute {attr}; columns: {sorted(g.columns)}")
            attr = None
        g = g.assign(layer=layer, cls=g[attr].fillna("(null)") if attr else "")
        if "plus_fysiek_voorkomen" in g:
            g["plus"] = g["plus_fysiek_voorkomen"].fillna("")
        else:
            g["plus"] = ""
        parts.append(g[["layer", "cls", "plus", "geometry"]])
    surface = pd.concat(parts, ignore_index=True)
    surface = gpd.GeoDataFrame(surface, geometry="geometry", crs=28992)

    pieces = gpd.overlay(
        svc[["osm_id", "service", "geometry"]], surface, how="intersection", keep_geom_type=True
    )
    pieces["len"] = pieces.length
    covered = pieces["len"].sum()
    print(f"length covered by some ground-level BGT surface: {covered / total:.1%}")
    print(f"(over 100% means overlapping layers; under 100% means gaps)\n")

    by_layer = pieces.groupby(["layer", "cls"])["len"].sum().sort_values(ascending=False)
    print("share of service-way length by BGT layer / class:")
    print((by_layer / total).round(3).head(20).to_string(), "\n")

    print("by OSM service=* value (rows) and BGT layer (columns), share of row length:")
    tab = pieces.pivot_table(index="service", columns="layer", values="len", aggfunc="sum").fillna(0)
    print(tab.div(svc.length.groupby(svc["service"]).sum(), axis=0).round(2).to_string(), "\n")

    otd = pieces[pieces["layer"] == "onbegroeidterreindeel"]
    if len(otd):
        print("onbegroeidterreindeel fysiek_voorkomen / plus_fysiek_voorkomen:")
        print((otd.groupby(["cls", "plus"])["len"].sum() / total).round(3).sort_values(ascending=False).to_string())


if __name__ == "__main__":
    main()

import osmnx as ox

mechelen_noord_bounds = ()

livingston_bounds = (-114.08429, 51.175241, -114.047939, 51.198453)

TAGS = {"highway": True}

gdf = ox.features_from_bbox(livingston_bounds, tags=dict(TAGS))

gdf = gdf[gdf.geometry.type.isin(["LineString", "MultiLineString"])]

for (element_type, osmid), row in gdf.iterrows():
    props = row.dropna().drop(labels=["geometry"])
    for key, value in props.items():
        print(key, "|", value)
    print("===========")
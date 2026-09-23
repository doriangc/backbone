# How are we going to pull infrastructure data?
This is going to require blending multiple sources of data together.

## OpenStreetMap Data
Contains positions of roads, and sometimes certain pieces of infrastructure like sidewalks, bike paths, etc.

Source from [Geofabrik](https://download.geofabrik.de/), we can either pull those extracts into a PostGIS database, or we can query the file directly. The first is probably a better idea, I'm not sure how queryable these files are (probably not very).

## Satellite / Aerial Imagery
Theoretically, there is a lot to extract from satellite/aerial imagery, but this is a trickier computer vision problem.

In addition, this data is sometimes quite hard to obtain. There are some free sources, but most are paid.
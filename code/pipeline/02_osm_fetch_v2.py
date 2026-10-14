"""
Track B - download PRE-EVENT OpenStreetMap layers with the ohsome API v2 (v1 extraction is now refused: HTTP 403).
Runs on a laptop OR Colab. Needs a free ohsome API key: https://account.heigit.org/signup

Setup once:   pip install requests geopandas pyarrow
Run:          python osm_fetch_v2.py          (it asks for the API key; or set env var OHSOME_API_KEY)
Output:       folder osm_pre_event/ with 6 GeoJSON files (same names/format osm_damage.py expects)

Data rule: snapshot timestamp 2026-07-27 (latest pre-event date named in the rules) - never "latest".
"""
import datetime as dt
import getpass
import io
import os
import time

import geopandas as gpd
import pandas as pd
import requests

BBOX = [85.10, 27.90, 85.45, 28.32]           # W, S, E, N (same as the mapping run)
EVENT = "2026-08-26"
SNAPSHOT = "2026-07-27"                        # pre-event, as stated in the hackathon data table
OUT = "osm_pre_event"
URL = "https://api.heigit.org/ohsome-api/v2-rc/extraction/features.parquet"
assert dt.date.fromisoformat(SNAPSHOT) < dt.date.fromisoformat(EVENT), "snapshot must be BEFORE the event"

LAYERS = {   # name: (ohsome filter, tiles per side)  - server timeout is 4 min, so dense layers use more tiles
    "buildings":   ("building=* and building!=no and geometry:polygon", 2),
    "roads":       ("highway in (motorway,trunk,primary,secondary,tertiary,unclassified,residential,service,track,"
                    "motorway_link,trunk_link,primary_link,secondary_link,tertiary_link) and geometry:line", 2),
    "footpaths":   ("highway in (path,footway,steps,bridleway) and geometry:line", 2),
    "bridges":     ("bridge=* and bridge!=no and geometry:line", 1),
    "settlements": ("place in (city,town,village,hamlet,isolated_dwelling)", 1),
    "health":      ("amenity in (hospital,clinic,doctors) or healthcare=*", 1),
}


def say(msg):
    print(f"[{dt.datetime.now():%H:%M:%S}] {msg}", flush=True)


def bbox_polygon(bb):
    w, s, e, n = bb
    return {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}


def to_gdf(content):
    try:
        g = gpd.read_parquet(io.BytesIO(content), to_pandas_kwargs={"maps_as_pydicts": "strict"})
    except TypeError:                                   # older geopandas without to_pandas_kwargs
        g = gpd.read_parquet(io.BytesIO(content))
    if "tags" in g.columns:
        tags = g["tags"].apply(lambda t: dict(t) if t is not None else {})
        g = g.drop(columns="tags").join(pd.json_normalize(tags.tolist()).set_index(g.index))
    return g


PAUSE_S = 12            # polite gap between requests (free key has a rate limit)


def get_tile(name, flt, bb, key, depth=0):
    """One tile: cached on disk, polite pacing, waits on 429 (rate limit), splits only on timeouts/5xx."""
    label = f"{name} [{bb[0]:.3f},{bb[1]:.3f},{bb[2]:.3f},{bb[3]:.3f}]"
    cache = f"{OUT}/_tiles/{name}_{bb[0]:.4f}_{bb[1]:.4f}_{bb[2]:.4f}_{bb[3]:.4f}.parquet"
    if os.path.exists(cache):
        return [to_gdf(open(cache, "rb").read())]
    payload = {"aoi": bbox_polygon(bb), "filter": flt, "time": f"{SNAPSHOT}T00:00:00Z", "clip": False}
    fails = 0
    while True:
        time.sleep(PAUSE_S)
        try:
            r = requests.post(URL, json=payload, headers={"authorization": key}, timeout=300)
        except requests.exceptions.RequestException as ex:
            say(f"   {label}: {type(ex).__name__}"); fails += 1
            if fails >= 2: break
            continue
        if r.status_code == 200:
            os.makedirs(f"{OUT}/_tiles", exist_ok=True)
            with open(cache, "wb") as fh:
                fh.write(r.content)
            say(f"   {label}: ok")
            return [to_gdf(r.content)] if r.content else []
        body = r.text[:200].replace("\n", " ")
        if r.status_code == 429:                                  # rate limit: wait, do NOT split (would make it worse)
            wait = int(r.headers.get("Retry-After", "0") or 0) or 90
            say(f"   {label}: rate limit - waiting {wait}s"); time.sleep(wait); continue
        say(f"   {label}: HTTP {r.status_code} - {body}")
        if r.status_code in (400, 401, 403, 404, 422):
            raise SystemExit(f"Request refused (HTTP {r.status_code}). Check the API key / send this message to Claude.")
        fails += 1
        if fails >= 2: break
        time.sleep(30)
    if depth >= 3:
        raise SystemExit(f"Kept failing for {label} - try again later")
    say(f"   splitting {label} into 4 (server timeout)")
    mx, my = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
    out = []
    for sub in [(bb[0], bb[1], mx, my), (mx, bb[1], bb[2], my), (bb[0], my, mx, bb[3]), (mx, my, bb[2], bb[3])]:
        out += get_tile(name, flt, sub, key, depth + 1)
    return out


def main():
    key = os.environ.get("OHSOME_API_KEY") or getpass.getpass("Paste your ohsome API key (hidden) and press Enter: ").strip()
    os.makedirs(OUT, exist_ok=True)
    say(f"ohsome API v2 - snapshot {SNAPSHOT} (event {EVENT}), bbox {BBOX}")
    for name, (flt, tiles) in LAYERS.items():
        path = f"{OUT}/osm_{name}.geojson"
        if os.path.exists(path):
            say(f"cached {name}"); continue
        w, s, e, n = BBOX
        xs = [w + (e - w) * k / tiles for k in range(tiles + 1)]
        ys = [s + (n - s) * k / tiles for k in range(tiles + 1)]
        parts = []
        for i in range(tiles):
            for j in range(tiles):
                parts += get_tile(name, flt, (xs[i], ys[j], xs[i + 1], ys[j + 1]), key)
        parts = [p for p in parts if len(p)]
        if parts:
            geom_col = parts[0].geometry.name                 # v2 parquet may call it "geometry" or "geom"
            g = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), geometry=geom_col,
                                 crs=parts[0].crs or 4326)
            if geom_col != "geometry":
                g = g.drop(columns=["geometry"], errors="ignore").rename_geometry("geometry")
            id_col = next((c for c in ("@osmId", "osm_id", "id") if c in g.columns), None)
            if id_col and id_col != "@osmId":
                g["@osmId"] = g[id_col].astype(str)
            if "@osmId" in g.columns:
                g = g.drop_duplicates("@osmId")
            # keep geometry + simple columns only (GeoJSON can't hold lists/dicts)
            keep = [c for c in g.columns if c == "geometry" or g[c].map(lambda v: isinstance(v, (list, dict))).sum() == 0]
            g = g[keep].to_crs(4326)
        else:
            g = gpd.GeoDataFrame({"@osmId": []}, geometry=[], crs=4326)
        g.to_file(path, driver="GeoJSON")
        say(f"{name}: {len(g)} features -> {path}")
    with open(f"{OUT}/snapshot.txt", "w") as fh:
        fh.write(f"ohsome API v2 snapshot {SNAPSHOT}T00:00:00Z (event {EVENT}), bbox {BBOX}\n(c) OpenStreetMap contributors\n")
    say(f"Done. Upload every osm_*.geojson in '{OUT}' to Colab.")


if __name__ == "__main__":
    main()

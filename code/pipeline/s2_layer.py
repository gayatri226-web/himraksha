"""HimRaksha - Sentinel-2 optical layer ("Sentinel-2 when the sky is clear").

What it does, for the same area + flood date as the radar run:
  1. finds Sentinel-2 L2A images before / after the event (Copernicus Data Space catalogue)
  2. takes dates before / after whose tiles are not mostly cloud - clearest first, closer to the event on ties -
     adding dates until 90 % of the valley floor has a clear view (max 4 per side)
  3. downloads the raw .SAFE products and reads the bands it needs (B02 B03 B04 B08 at 10 m, B11 + SCL at 20 m)
  4. removes clouds, cloud shadows and terrain shadow with the scene classification (SCL) - only clear pixels are used;
     several dates are stacked so a cloud on one date can be filled by the next
  5. finds change on the valley floor between the before and after composites:
        new water         - water after (MNDWI > 0, NDVI < 0.1) that was not water before
        debris / bare     - vegetation stripped (NDVI drop > 0.25 and NDVI after < 0.3) or river channel filled
                            (water before -> bare ground after)
  6. fuses with the radar impact map:
        radar + optical agree         -> high confidence
        optical only (sky clear)      -> new class "debris (optical)" / "new water (optical)", medium confidence
        radar only                    -> kept as it was (optical may be cloudy, late, or blind to wet debris)

Thresholds are fixed values from the remote-sensing literature, NOT tuned on EMS (EMS is used only to check).
Inputs: Sentinel-2 L2A only (+ the radar impact map / valley mask from step 4). No EMS / UNOSAT / post-event OSM.

Outputs in the run folder:
  impact_fused_s1.tif   copy of the radar-only impact map (kept for comparison)
  impact_fused.tif      updated map: band 1 class (0 none, 1 new water, 2 debris darkened, 3 debris brightened,
                        4 debris optical-only, 5 new water optical-only, 255 outside valley), band 2 confidence (1-3),
                        band 3 source (1 radar, 2 optical, 3 both)
  s2_change.tif         band 1 optical class (0 none, 4 debris, 5 new water, 254 not clear on both dates, 255 outside valley)
  s2_before_rgb.png, s2_after_rgb.png, s2_change.png   previews
  run_log.json          gets an "s2" section (dates used, clear %, areas)
"""
import datetime as dt
import json
import os
import re
import shutil
import sys
import zipfile

import numpy as np
import rasterio
import requests
from rasterio.warp import reproject, Resampling
from scipy import ndimage

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from grd_preprocess import CATALOGUE, bbox_wkt, download_slice, say   # same login / download code as the radar step

BANDS_10 = ["B02", "B03", "B04", "B08"]
BANDS_20 = ["B11", "SCL"]
CLEAR_SCL = {4, 5, 6, 7, 11}            # vegetation, bare soil, water, unclassified, snow
PRE_DAYS, POST_DAYS = 45, 60            # search windows around the event
MAX_TILE_CLOUD = 60                     # skip dates whose tiles are on average cloudier than this
N_DATES = 4                             # at most this many dates per side are stacked into the composite
TARGET_CLEAR = 0.90                     # ...but stop adding dates once 90 % of the valley floor is clear
WATER_MNDWI, WATER_NDVI = 0.0, 0.1
VEG_DROP, VEG_AFTER_MAX, BARE_NDVI = 0.25, 0.30, 0.20
MIN_BLOB_PX = 50                        # 0.5 ha at 10 m, same as the radar step
CLOUD_BUFFER_PX = 3                     # grow clouds by 30 m (edges are often missed)


# ---------------------------------------------------------------- 1. catalogue
def search_s2(bbox, start, end):
    flt = (f"Collection/Name eq 'SENTINEL-2' and OData.CSC.Intersects(area=geography'SRID=4326;{bbox_wkt(bbox)}') "
           f"and ContentDate/Start gt {start}T00:00:00.000Z and ContentDate/Start lt {end}T23:59:59.000Z "
           "and Attributes/OData.CSC.StringAttribute/any(a:a/Name eq 'productType' "
           "and a/OData.CSC.StringAttribute/Value eq 'S2MSI2A')")
    r = requests.get(CATALOGUE, params={"$filter": flt, "$expand": "Attributes", "$top": 500,
                                        "$orderby": "ContentDate/Start asc"}, timeout=120)
    r.raise_for_status()
    out = []
    for p in r.json()["value"]:
        a = {x["Name"]: x["Value"] for x in p["Attributes"]}
        out.append({"id": p["Id"], "name": p["Name"], "date": p["ContentDate"]["Start"][:10],
                    "tile": p["Name"].split("_")[5], "cloud": float(a.get("cloudCover", 100)),
                    "baseline": p["Name"].split("_")[3]})
    return out


def pick_dates(items, event, pre_date=None, post_date=None, n=N_DATES):
    """Group by date; date cloud = mean over the area's tiles (best product per tile); missing tile counts as 100 %."""
    ev = dt.date.fromisoformat(event)
    tiles = sorted({i["tile"] for i in items})
    by_date = {}
    for i in items:
        by_date.setdefault(i["date"], []).append(i)
    scored = []
    for d, its in by_date.items():
        best = {t: min([x["cloud"] for x in its if x["tile"] == t], default=100.0) for t in tiles}
        scored.append((d, sum(best.values()) / len(tiles), its))
    pre = sorted([s for s in scored if dt.date.fromisoformat(s[0]) < ev], key=lambda s: s[0], reverse=True)
    post = sorted([s for s in scored if dt.date.fromisoformat(s[0]) > ev], key=lambda s: s[0])
    if pre_date:
        pre = [s for s in pre if s[0] == pre_date]
    if post_date:
        post = [s for s in post if s[0] == post_date]
    # clearest dates first (tile cloud %), closer to the event breaks ties. Learned on Trishuli: half-cloudy
    # dates add little in narrow valleys (clouds sit in them), so trying the clearest date first saves downloads.
    ev_days = lambda s: abs((dt.date.fromisoformat(s[0]) - ev).days)
    pre = sorted([s for s in pre if s[1] <= MAX_TILE_CLOUD], key=lambda s: (round(s[1] / 10), ev_days(s)))[:n]
    post = sorted([s for s in post if s[1] <= MAX_TILE_CLOUD], key=lambda s: (round(s[1] / 10), ev_days(s)))[:n]
    return pre, post


# ---------------------------------------------------------------- 2. read one product onto the run grid
def _offset(z, safe):
    """L2A processing baseline >= 04.00 stores reflectance with an offset (BOA_ADD_OFFSET, usually -1000)."""
    mtd = [n for n in z.namelist() if n.endswith("MTD_MSIL2A.xml")]
    if not mtd:
        return 0.0
    m = re.search(r"<BOA_ADD_OFFSET[^>]*>(-?\d+)</BOA_ADD_OFFSET>", z.read(mtd[0]).decode("utf-8", "ignore"))
    return float(m.group(1)) if m else 0.0


def read_product(zip_path, grid):
    """Returns dict band -> array on the run grid (reflectance 0-1 for bands, class codes for SCL)."""
    out = {}
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        off = _offset(z, names[0].split("/")[0])
        for b in BANDS_10 + BANDS_20:
            res = "10m" if b in BANDS_10 else "20m"
            hit = [n for n in names if n.endswith(f"_{b}_{res}.jp2") and "IMG_DATA" in n]
            if not hit:
                raise RuntimeError(f"{b} not found in {os.path.basename(zip_path)}")
            dst = np.zeros((grid["height"], grid["width"]), np.float32 if b != "SCL" else np.uint8)
            with rasterio.open(f"/vsizip/{zip_path}/{hit[0]}") as src:
                reproject(rasterio.band(src, 1), dst, dst_transform=grid["transform"], dst_crs=grid["crs"],
                          resampling=Resampling.nearest if b == "SCL" else Resampling.bilinear,
                          src_nodata=0, dst_nodata=0)
            if b == "SCL":
                out[b] = dst
            else:
                v = dst.astype(np.float32)
                valid = v > 0
                v = np.where(valid, (v + off) / 10000.0, np.nan)
                out[b] = np.clip(v, 0, 1.5)
    return out


def composite(products_by_date, grid, cache_dir, label, valley):
    """Stack dates in the given order; each pixel takes the first CLEAR observation. Returns bands + clear mask + log."""
    H, W = grid["height"], grid["width"]
    comp = {b: np.full((H, W), np.nan, np.float32) for b in BANDS_10 + ["B11"]}
    clear = np.zeros((H, W), bool)
    log = []
    for d, cloud, items in products_by_date:
        date_clear = np.zeros((H, W), bool)
        for it in sorted(items, key=lambda x: x["cloud"]):
            if it["cloud"] > 90:                     # a fully clouded product adds nothing - skip the download
                continue
            zp = download_slice(it, cache_dir)
            bands = read_product(zp, grid)
            scl = bands["SCL"]
            ok = np.isin(scl, list(CLEAR_SCL))
            cloudy = np.isin(scl, [3, 8, 9, 10])
            if cloudy.any():
                ok &= ~ndimage.binary_dilation(cloudy, iterations=CLOUD_BUFFER_PX)
            ok &= np.isfinite(bands["B08"])
            take = ok & ~clear
            for b in comp:
                comp[b][take] = bands[b][take]
            clear |= take
            date_clear |= ok
        log.append({"date": d, "tile_cloud_pct": round(cloud, 1), "products": [i["name"] for i in items],
                    "clear_pct_of_area": round(100 * date_clear.mean(), 1)})
        vc = clear[valley].mean() if valley.any() else 0.0
        log[-1]["valley_clear_after_this_date_pct"] = round(100 * float(vc), 1)
        say(f"S2 {label} {d}: tile cloud {cloud:.0f}% | clear on this date {100 * date_clear.mean():.0f}% "
            f"| valley floor clear so far {100 * vc:.0f}%")
        if vc >= TARGET_CLEAR:
            break
    return comp, clear, log


# ---------------------------------------------------------------- 3. change
def indices(c):
    with np.errstate(divide="ignore", invalid="ignore"):
        ndvi = (c["B08"] - c["B04"]) / (c["B08"] + c["B04"])
        mndwi = (c["B03"] - c["B11"]) / (c["B03"] + c["B11"])
    water = (mndwi > WATER_MNDWI) & (ndvi < WATER_NDVI)
    return ndvi, mndwi, water


def clean(mask):
    lab, n = ndimage.label(mask)
    if n == 0:
        return mask
    sizes = ndimage.sum(mask, lab, range(1, n + 1))
    keep = np.zeros(n + 1, bool); keep[1:] = sizes >= MIN_BLOB_PX
    return keep[lab]


def s2_change(pre, post, clear_both, valley):
    ndvi0, _, water0 = indices(pre)
    ndvi1, _, water1 = indices(post)
    zone = clear_both & valley
    new_water = zone & water1 & ~water0
    veg_loss = zone & ((ndvi0 - ndvi1) > VEG_DROP) & (ndvi1 < VEG_AFTER_MAX) & ~water1
    channel_filled = zone & water0 & ~water1 & (ndvi1 < BARE_NDVI)
    debris = clean(veg_loss | channel_filled)
    new_water = clean(new_water) & ~debris
    cls = np.zeros(valley.shape, np.uint8)
    cls[debris] = 4
    cls[new_water] = 5
    cls[valley & ~clear_both] = 254
    cls[~valley] = 255
    return cls


# ---------------------------------------------------------------- 4. fuse with the radar map
def fuse(s1_cls, s1_conf, s2_cls):
    cls, conf = s1_cls.copy(), s1_conf.copy()
    src = np.zeros(cls.shape, np.uint8)
    s1_hit = np.isin(s1_cls, [1, 2, 3])
    s2_hit = np.isin(s2_cls, [4, 5])
    src[s1_hit] = 1
    both = s1_hit & s2_hit
    conf[both] = 3; src[both] = 3
    only2 = s2_hit & ~s1_hit & (s1_cls != 255)
    cls[only2] = s2_cls[only2]; conf[only2] = 2; src[only2] = 2
    return cls, conf, src


def _png(path, rgb_bands, clear):
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    rgb = np.dstack([rgb_bands["B04"], rgb_bands["B03"], rgb_bands["B02"]])
    rgb = np.clip(np.nan_to_num(rgb) / 0.25, 0, 1)
    rgb[~clear] = [0.85, 0.85, 0.85]
    plt.imsave(path, rgb)


def run_s2(run_dir, event=None, bbox=None, cache_dir="s2_cache", pre_date=None, post_date=None, keep_zips=True):
    log_path = f"{run_dir}/run_log.json"
    log = json.load(open(log_path))
    event, bbox = event or log["event"], bbox or log["bbox"]
    base = f"{run_dir}/impact_fused_s1.tif"
    if not os.path.exists(base):
        shutil.copy(f"{run_dir}/impact_fused.tif", base)            # keep the radar-only map
    with rasterio.open(base) as r:
        s1_cls, s1_conf, prof = r.read(1), r.read(2), r.profile
        grid = {"transform": r.transform, "crs": r.crs, "height": r.height, "width": r.width}
    valley = s1_cls != 255

    ev = dt.date.fromisoformat(event)
    items = search_s2(bbox, str(ev - dt.timedelta(days=PRE_DAYS)), str(ev + dt.timedelta(days=POST_DAYS)))
    say(f"Sentinel-2 L2A products found: {len(items)}")
    pre_d, post_d = pick_dates(items, event, pre_date, post_date)
    info = {"pre": [], "post": [], "used": False}
    if not pre_d or not post_d:
        say("No clear-enough Sentinel-2 image before AND after the event - optical layer skipped, radar map unchanged.")
        log["s2"] = info; json.dump(log, open(log_path, "w"), indent=2)
        return info
    pre, clear0, info["pre"] = composite(pre_d, grid, cache_dir, "before", valley)
    post, clear1, info["post"] = composite(post_d, grid, cache_dir, "after", valley)
    clear_both = clear0 & clear1
    s2 = s2_change(pre, post, clear_both, valley)
    cls, conf, src = fuse(s1_cls, s1_conf, s2)

    p1 = dict(prof); p1.update(count=1, dtype="uint8", nodata=None)
    with rasterio.open(f"{run_dir}/s2_change.tif", "w", **p1) as w:
        w.write(s2, 1)
    p3 = dict(prof); p3.update(count=3, dtype="uint8")
    with rasterio.open(f"{run_dir}/impact_fused.tif", "w", **p3) as w:
        w.write(cls, 1); w.write(conf, 2); w.write(src, 3)
        w.set_band_description(1, "class"); w.set_band_description(2, "confidence"); w.set_band_description(3, "source")
    _png(f"{run_dir}/s2_before_rgb.png", pre, clear0)
    _png(f"{run_dir}/s2_after_rgb.png", post, clear1)
    import matplotlib.pyplot as plt
    view = np.zeros(s2.shape + (3,)); view[valley] = [0.92, 0.92, 0.92]; view[s2 == 254] = [0.7, 0.7, 0.75]
    view[s2 == 4] = [0.55, 0.33, 0.1]; view[s2 == 5] = [0.1, 0.4, 0.9]
    plt.imsave(f"{run_dir}/s2_change.png", view)

    km2 = lambda m: round(float(m.sum()) / 1e4, 3)
    info.update({"used": True,
                 "valley_clear_both_dates_pct": round(100 * float((clear_both & valley).sum()) / max(valley.sum(), 1), 1),
                 "s2_debris_km2": km2(s2 == 4), "s2_new_water_km2": km2(s2 == 5),
                 "radar_and_optical_agree_km2": km2(src == 3), "optical_only_added_km2": km2(src == 2),
                 "radar_only_km2": km2(src == 1),
                 "radar_only_where_optical_clear_km2": km2((src == 1) & clear_both),
                 "total_impacted_km2": km2(np.isin(cls, [1, 2, 3, 4, 5])),
                 "conf_high_km2": km2(conf == 3),
                 "thresholds": {"water": f"MNDWI>{WATER_MNDWI} & NDVI<{WATER_NDVI}",
                                "vegetation_loss": f"NDVI drop>{VEG_DROP} & NDVI after<{VEG_AFTER_MAX}",
                                "channel_filled": f"water before -> NDVI after<{BARE_NDVI}"}})
    log["s2"] = info
    log.setdefault("sources", [])
    src_txt = "Sentinel-2 L2A (Copernicus Data Space), clear-sky pixels only (SCL)"
    if src_txt not in log["sources"]:
        log["sources"].append(src_txt)
    json.dump(log, open(log_path, "w"), indent=2)
    if not keep_zips:
        shutil.rmtree(cache_dir, ignore_errors=True)
    say(f"S2 done: valley clear on both dates {info['valley_clear_both_dates_pct']}% | optical debris {info['s2_debris_km2']} km2"
        f" | new water {info['s2_new_water_km2']} km2 | agree with radar {info['radar_and_optical_agree_km2']} km2"
        f" | optical-only added {info['optical_only_added_km2']} km2")
    return info


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Add the Sentinel-2 optical layer to a HimRaksha run folder")
    ap.add_argument("run_dir")
    ap.add_argument("--pre-date"); ap.add_argument("--post-date")
    a = ap.parse_args()
    run_s2(a.run_dir, pre_date=a.pre_date, post_date=a.post_date)

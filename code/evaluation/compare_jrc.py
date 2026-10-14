"""With vs without JRC Global Surface Water (permanent-water mask) - organizers asked us to compare both.
Usage: python compare_jrc.py <run_dir> <jrc_occurrence.tif> <ems_dir> <out_dir>
For each threshold T, pixels where JRC water occurrence (1984-2021) >= T % are removed from our impact map.
Writes jrc_comparison.json (+ impact_fused_jrc50.tif so the damage / access steps can be re-run on it).
"""
import sys, os, glob, json
import numpy as np, rasterio, geopandas as gpd
from rasterio.warp import reproject, Resampling
from rasterio.features import rasterize
from shapely.geometry import box

RUN, JRC, EMS, OUT = sys.argv[1:5]; os.makedirs(OUT, exist_ok=True)
AOIS = ["EMSR927_AOI01_GRA_PRODUCT_v1", "EMSR927_AOI02_GRA_PRODUCT_v2", "EMSR927_AOI03_GRA_MONIT01_v1"]
src = rasterio.open(f"{RUN}/impact_fused.tif"); cls, conf = src.read(1), src.read(2)
occ = np.zeros(cls.shape, np.float32)
with rasterio.open(JRC) as j:
    reproject(rasterio.band(j, 1), occ, dst_transform=src.transform, dst_crs=src.crs, resampling=Resampling.nearest, dst_nodata=0)
occ[occ > 100] = 0
valley, imp = cls != 255, np.isin(cls, [1, 2, 3, 4, 5])

# EMS reference grid (validation only)
bbox = gpd.GeoSeries([box(*json.load(open(f"{RUN}/run_log.json"))["bbox"])], crs=4326).to_crs(src.crs).iloc[0]
region = np.zeros(cls.shape, bool); ref = np.zeros(cls.shape, bool)
for d in AOIS:
    lay = lambda n: gpd.read_file(glob.glob(f"{EMS}/{d}/**/*_{n}_*.shp", recursive=True)[0]).to_crs(src.crs) \
        if glob.glob(f"{EMS}/{d}/**/*_{n}_*.shp", recursive=True) else None
    aoi = lay("areaOfInterestA").union_all().intersection(bbox)
    na = lay("notAnalysedA")
    reg = aoi.difference(na.union_all()) if na is not None and len(na) else aoi
    r = rasterize([(reg, 1)], cls.shape, transform=src.transform, fill=0).astype(bool)
    region |= r
    ref |= rasterize([(lay("observedEventA").union_all().intersection(reg), 1)], cls.shape, transform=src.transform, fill=0).astype(bool) & r


def score(det):
    det = det & region; tp = (det & ref).sum(); fp = (det & ~ref).sum(); fn = (~det & ref).sum()
    p, rc = tp / max(tp + fp, 1), tp / max(tp + fn, 1)
    return {"precision": round(float(p), 3), "recall": round(float(rc), 3), "f1": round(float(2 * p * rc / max(p + rc, 1e-9)), 3)}


res = {"jrc_source": "JRC Global Surface Water v1.4 occurrence 1984-2021 (Pekel et al., 2016)",
       "unet_new_water_km2": round(float((cls == 1).sum() / 1e4), 3),
       "unet_new_water_on_jrc50_water_km2": round(float(((cls == 1) & (occ >= 50)).sum() / 1e4), 3),
       "variants": {}}
for t in [None, 25, 50, 75, 90]:
    rm = (occ >= t) & imp if t else np.zeros_like(imp)
    det = imp & ~rm
    res["variants"]["no JRC" if t is None else f"JRC >= {t}%"] = {
        "impact_km2": round(float(det.sum() / 1e4), 3), "removed_km2": round(float(rm.sum() / 1e4), 3),
        "removed_by_class_km2": {"new water": round(float((rm & (cls == 1)).sum() / 1e4), 3),
                                 "debris darkened": round(float((rm & (cls == 2)).sum() / 1e4), 3),
                                 "debris brightened": round(float((rm & (cls == 3)).sum() / 1e4), 3)},
        "ems_area_all": score(det), "ems_area_high_conf": score(det & (conf == 3))}
    if t == 50:
        c2, f2 = cls.copy(), conf.copy(); c2[rm] = 0; f2[rm] = 0
        with rasterio.open(f"{OUT}/impact_fused_jrc50.tif", "w", **src.profile) as w:
            w.write(c2, 1); w.write(f2, 2)
json.dump(res, open(f"{OUT}/jrc_comparison.json", "w"), indent=2)
print(json.dumps(res, indent=1))

"""Compare HimRaksha outputs with Copernicus EMS EMSR927 grading maps (validation only).
Usage: python eval_emsr927.py <ems_dir> <results_dir> <out_dir>
"""
import sys, glob, json, os, warnings
import numpy as np, geopandas as gpd, rasterio
from rasterio.features import rasterize
from rasterio.windows import from_bounds
from shapely.geometry import box
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap
warnings.filterwarnings("ignore")

EMS, RES, OUT = sys.argv[1:4]; os.makedirs(OUT, exist_ok=True)
UTM = 32645
AOIS = {"AOI01 Syapru Besi": "EMSR927_AOI01_GRA_PRODUCT_v1",
        "AOI02 Timure": "EMSR927_AOI02_GRA_PRODUCT_v2",
        "AOI03 Bidur": "EMSR927_AOI03_GRA_MONIT01_v1"}   # latest version of each
DAMAGED = {"Destroyed", "Damaged"}                         # 'Possibly damaged' reported separately
TOL_BLD, TOL_ROAD, TOL_BRIDGE = 20, 30, 60                 # metres (S1 is 10 m, geolocation ~1-2 px)

def layer(d, name):
    f = glob.glob(f"{EMS}/{d}/**/*_{name}_*.shp", recursive=True)
    return gpd.read_file(f[0]).to_crs(UTM) if f else gpd.GeoDataFrame(geometry=[], crs=UTM)

def scores(pred, ref):
    tp = int((pred & ref).sum()); fp = int((pred & ~ref).sum()); fn = int((~pred & ref).sum())
    p = tp/(tp+fp) if tp+fp else 0; r = tp/(tp+fn) if tp+fn else 0
    return dict(precision=round(p,3), recall=round(r,3), f1=round(2*p*r/(p+r),3) if p+r else 0,
                iou=round(tp/(tp+fp+fn),3) if tp+fp+fn else 0,
                ems_km2=round((tp+fn)/1e4,3), ours_km2=round((tp+fp)/1e4,3), overlap_km2=round(tp/1e4,3))

src = rasterio.open(f"{RES}/impact_fused.tif")
bbox = gpd.GeoSeries([box(*json.load(open(f"{RES}/run_log.json"))["bbox"])], crs=4326).to_crs(UTM).iloc[0]
blds = gpd.read_file(f"{RES}/buildings_hit.geojson").to_crs(UTM)
roads = gpd.read_file(f"{RES}/roads_hit.geojson").to_crs(UTM)
bridges = gpd.read_file(f"{RES}/bridges_hit.geojson").to_crs(UTM)

result = {"reference": "Copernicus EMS EMSR927 grading products (photo-interpretation of VHR optical). Used ONLY for validation.",
          "notes": ["EMS classifies the event as '6-Mass Movement / Landslide' (debris flow), not open water.",
                    "Our map only analyses the valley floor (HAND<50 m, slope<30 deg); 'valley' scores are restricted to that mask.",
                    f"Asset matching tolerances: buildings {TOL_BLD} m, roads {TOL_ROAD} m, bridges {TOL_BRIDGE} m.",
                    "Bridges: 'bridges' = our LIKELY DAMAGED grade; 'bridges_incl_possibly' adds POSSIBLY DAMAGED (needs ground check).",
                    "AOI04 Bharatpur and AOI05 lie outside our 26 Aug 2026 run area and were not scored."],
          "aois": {}}
panels = []
for name, d in AOIS.items():
    aoi = layer(d, "areaOfInterestA").union_all().intersection(bbox)
    na = layer(d, "notAnalysedA"); na = na.union_all() if len(na) else None
    region = aoi.difference(na) if na is not None and not na.is_empty else aoi
    ev = layer(d, "observedEventA"); ev_geom = ev.union_all().intersection(region)
    win = from_bounds(*region.bounds, transform=src.transform).round_offsets().round_lengths()
    cls, conf = src.read(1, window=win), src.read(2, window=win); tr = src.window_transform(win)
    shp = cls.shape
    inreg = rasterize([(region, 1)], shp, transform=tr, fill=0).astype(bool)
    ref = rasterize([(ev_geom, 1)], shp, transform=tr, fill=0).astype(bool) & inreg
    valley = (cls != 255) & inreg
    det_all = np.isin(cls, [1, 2, 3, 4, 5]) & inreg      # 4/5 = Sentinel-2 optical-only detections
    det_high = det_all & (conf == 3)
    det_water = (cls == 1) & inreg                          # U-Net water (radar)
    r = {"aoi_km2_scored": round(inreg.sum()/1e4, 2), "valley_km2": round(valley.sum()/1e4, 2),
         "ems_event_km2": round(ref.sum()/1e4, 3),
         "ems_event_in_valley_pct": round(100*(ref & valley).sum()/max(ref.sum(),1), 1)}
    r["area_all_detections"] = scores(det_all, ref)
    r["area_high_confidence"] = scores(det_high, ref)
    r["area_water_only_unet"] = scores(det_water, ref)
    r["area_all_detections_valley_only"] = scores(det_all & valley, ref & valley)

    # buildings: EMS graded points vs our hit footprints
    eb = layer(d, "builtUpP"); eb = eb[eb.intersects(region)]
    ours_b = blds[blds.intersects(region)]
    dmg = eb[eb.damage_gra.isin(DAMAGED)]
    near = lambda pts, polys, tol: pts.geometry.apply(lambda p: polys.sindex.query(p.buffer(tol)).size > 0) if len(polys) else pts.geometry.apply(lambda p: False)
    hit = near(dmg, ours_b, TOL_BLD)
    poss = eb[eb.damage_gra == "Possibly damaged"]
    ems_any = eb[eb.damage_gra.isin(DAMAGED | {"Possibly damaged"})]
    ours_conf = ours_b.geometry.apply(lambda g: ems_any.sindex.query(g.buffer(TOL_BLD)).size > 0) if len(ems_any) else ours_b.geometry.apply(lambda g: False)
    r["buildings"] = {"ems_damaged_or_destroyed": int(len(dmg)), "ems_possibly_damaged": int(len(poss)),
                      "ems_damaged_we_flagged": int(hit.sum()),
                      "recall": round(hit.mean(), 3) if len(dmg) else None,
                      "recall_high_conf_only": round(near(dmg, ours_b[ours_b.confidence=="high"], TOL_BLD).mean(), 3) if len(dmg) else None,
                      "ours_flagged": int(len(ours_b)), "ours_confirmed_by_ems": int(ours_conf.sum()),
                      "precision": round(ours_conf.mean(), 3) if len(ours_b) else None}
    # roads: length of EMS damaged roads covered by our affected roads
    el = layer(d, "transportationL"); el = el[el.intersects(region)]
    dl = el[el.damage_gra.isin(DAMAGED)]
    our_rbuf = roads[roads.intersects(region.buffer(200))].buffer(TOL_ROAD).union_all() if len(roads) else None
    tot = dl.length.sum()
    cov = dl.intersection(our_rbuf).length.sum() if our_rbuf is not None and not our_rbuf.is_empty else 0
    ems_lbuf = el[el.damage_gra.isin(DAMAGED | {"Possibly damaged"})].buffer(TOL_ROAD).union_all() if len(el) else None
    ours_in = roads.clip(region)
    ours_len = ours_in.length.sum()
    ours_ok = ours_in.intersection(ems_lbuf).length.sum() if ems_lbuf is not None and len(ours_in) else 0
    r["roads"] = {"ems_damaged_km": round(tot/1e3, 2), "ems_damaged_km_we_flagged": round(cov/1e3, 2),
                  "recall_by_length": round(cov/tot, 3) if tot else None,
                  "ours_flagged_km": round(ours_len/1e3, 2), "ours_km_confirmed_by_ems": round(ours_ok/1e3, 2),
                  "precision_by_length": round(ours_ok/ours_len, 3) if ours_len else None}
    # bridges
    ep = layer(d, "transportationP"); ep = ep[ep.intersects(region) & ep.damage_gra.isin(DAMAGED | {"Possibly damaged"})]
    def bridge_score(sel):
        ob = sel[sel.intersects(region.buffer(TOL_BRIDGE))]
        bh = [len(ob) > 0 and ob.sindex.query(p.buffer(TOL_BRIDGE)).size > 0 for p in ep.geometry]
        oc = [len(ep) > 0 and ep.sindex.query(g.buffer(TOL_BRIDGE)).size > 0 for g in ob.geometry]
        return {"ems_damaged": int(len(ep)), "ems_damaged_we_flagged": int(sum(bh)),
                "ours_flagged": int(len(ob)), "ours_confirmed_by_ems": int(sum(oc))}
    if "status" in bridges.columns:     # graded bridges (v2): score 'likely damaged' and 'likely + possibly'
        r["bridges"] = bridge_score(bridges[bridges.status == "likely damaged"])
        r["bridges_incl_possibly"] = bridge_score(bridges)
    else:
        r["bridges"] = bridge_score(bridges)
    result["aois"][name] = r
    panels.append((name, cls, det_all, ref, inreg, tr, region, dmg, hit))

# totals over the three AOIs
def tot(key, f): return sum(result["aois"][a][key][f] for a in AOIS)
T = {}
for k in ["area_all_detections", "area_high_confidence", "area_water_only_unet", "area_all_detections_valley_only"]:
    ov, ems, ours = tot(k, "overlap_km2"), tot(k, "ems_km2"), tot(k, "ours_km2")
    p, rr = ov/ours if ours else 0, ov/ems if ems else 0
    T[k] = dict(precision=round(p,3), recall=round(rr,3), f1=round(2*p*rr/(p+rr),3) if p+rr else 0,
                iou=round(ov/(ems+ours-ov),3), ems_km2=round(ems,3), ours_km2=round(ours,3))
bd = tot("buildings","ems_damaged_or_destroyed"); bh_ = tot("buildings","ems_damaged_we_flagged")
of = tot("buildings","ours_flagged"); oc_ = tot("buildings","ours_confirmed_by_ems")
T["buildings"] = dict(ems_damaged=bd, we_flagged=bh_, recall=round(bh_/bd,3), ours_flagged=of, ours_confirmed=oc_, precision=round(oc_/of,3) if of else None)
rk, rc = tot("roads","ems_damaged_km"), tot("roads","ems_damaged_km_we_flagged")
ok, oo = tot("roads","ours_flagged_km"), tot("roads","ours_km_confirmed_by_ems")
T["roads"] = dict(ems_damaged_km=round(rk,2), recall=round(rc/rk,3), ours_km=round(ok,2), precision=round(oo/ok,3) if ok else None)
for key in [k for k in ("bridges", "bridges_incl_possibly") if k in result["aois"][next(iter(AOIS))]]:
    T[key] = dict(ems_damaged=tot(key,"ems_damaged"), we_flagged=tot(key,"ems_damaged_we_flagged"),
                  ours_flagged=tot(key,"ours_flagged"), ours_confirmed=tot(key,"ours_confirmed_by_ems"))
    T[key]["precision"] = round(T[key]["ours_confirmed"] / T[key]["ours_flagged"], 3) if T[key]["ours_flagged"] else None
    T[key]["recall"] = round(T[key]["we_flagged"] / T[key]["ems_damaged"], 3) if T[key]["ems_damaged"] else None
result["total_3_aois"] = T
json.dump(result, open(f"{OUT}/eval_emsr927.json", "w"), indent=2)

# figure: green = agree, orange = ours only, magenta = EMS only, grey = outside valley mask
fig, axs = plt.subplots(1, 3, figsize=(18, 8))
cm = ListedColormap(["white", "#d9d9d9", "#e7298a", "#ff9f1c", "#1a9850"])
for ax, (name, cls, det, ref, inreg, tr, region, dmg, hit) in zip(axs, panels):
    v = np.zeros(cls.shape, np.uint8); v[inreg] = 0; v[inreg & (cls == 255)] = 1
    v[ref & ~det] = 2; v[det & ~ref] = 3; v[det & ref] = 4
    ext = (tr.c, tr.c + tr.a*cls.shape[1], tr.f + tr.e*cls.shape[0], tr.f)
    ax.imshow(v, cmap=cm, vmin=0, vmax=4, extent=ext, interpolation="nearest")
    gpd.GeoSeries([region], crs=UTM).boundary.plot(ax=ax, color="k", lw=1)
    if len(dmg):
        dmg[hit.values].plot(ax=ax, color="#1a9850", markersize=4, edgecolor="k", linewidth=0.2)
        dmg[~hit.values].plot(ax=ax, color="#e7298a", markersize=4, edgecolor="k", linewidth=0.2)
    s = result["aois"][name]
    ax.set_title(f"{name}\narea F1 {s['area_all_detections']['f1']}  |  buildings recall {s['buildings']['recall']}", fontsize=11)
    ax.set_xticks([]); ax.set_yticks([])
from matplotlib.patches import Patch
fig.legend(handles=[Patch(color="#1a9850", label="Both (agree)"), Patch(color="#ff9f1c", label="HimRaksha only"),
                    Patch(color="#e7298a", label="EMS only (missed)"), Patch(color="#d9d9d9", label="Outside our valley mask")],
           loc="lower center", ncol=4, fontsize=11, frameon=False)
fig.suptitle("HimRaksha (Sentinel-1, automatic) vs Copernicus EMS EMSR927 (VHR photo-interpretation) - 26 Aug 2026 Trishuli", fontsize=13)
plt.tight_layout(rect=(0, 0.05, 1, 0.95)); plt.savefig(f"{OUT}/eval_emsr927_map.png", dpi=130)
print(json.dumps(result, indent=1))

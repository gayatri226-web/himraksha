# Track B - who is cut off? Road-network reachability before vs after the flood + repair priority.
# Needs in the run folder (from osm_damage.py): osm_roads/footpaths/bridges/settlements/health/buildings.geojson,
# impact_polygons.geojson, bridges_hit.geojson.   Data: pre-event OSM only.
!pip install -q geopandas networkx shapely

import os, glob, json, datetime as dt
import numpy as np, pandas as pd, geopandas as gpd, networkx as nx, matplotlib.pyplot as plt
from shapely.geometry import LineString, box

RUN = OUT if "OUT" in globals() else sorted(glob.glob("run_*"))[-1]
log = json.load(open(f"{RUN}/run_log.json"))
BBOX = log["bbox"]
SPEED_KMH = {"motorway": 50, "trunk": 40, "primary": 35, "secondary": 30, "tertiary": 25, "unclassified": 20,
             "residential": 20, "service": 15, "track": 12, "path": 4, "footway": 4, "steps": 2, "bridleway": 4}
SNAP_M = 500            # settlement / facility must be within 500 m of a road to be connected
EXIT_M = 300            # road nodes this close to the area edge = "road out of the area"
ROAD_BUF_M, BRIDGE_BUF_M = 10, 30
DETOUR_RATIO = 1.5
SCENARIOS = {"all_detections": {"high", "medium", "low"}, "confident_only": {"high", "medium"}}


def say(msg):
    print(f"[{dt.datetime.now():%H:%M:%S}] {msg}", flush=True)


def load(name):
    p = f"{RUN}/{name}.geojson"
    return gpd.read_file(p) if os.path.exists(p) else gpd.GeoDataFrame(geometry=[], crs=4326)


roads, foot = load("osm_roads"), load("osm_footpaths")
settle, health, bld = load("osm_settlements"), load("osm_health"), load("osm_buildings")
impact, bridges_hit = load("impact_polygons"), load("bridges_hit")
CRS = roads.estimate_utm_crs()
roads, foot, settle, health, bld, impact, bridges_hit = (g.to_crs(CRS) for g in
                                                         (roads, foot, settle, health, bld, impact, bridges_hit))
area = gpd.GeoSeries([box(*BBOX)], crs=4326).to_crs(CRS).iloc[0]


# ---------- 1. graph edges: consecutive vertices of each OSM way (shared OSM nodes = junctions) ----------
def way_edges(gdf, kind):
    rows = []
    g = gdf.explode(index_parts=False)
    for _, r in g.iterrows():
        geom = r.geometry
        if geom is None or geom.geom_type != "LineString":
            continue
        hw = str(r.get("highway") or "unclassified")
        v = SPEED_KMH.get(hw.replace("_link", ""), 15) / 3.6               # m/s
        is_bridge = str(r.get("bridge") or "no") not in ("no", "None", "nan", "")
        cs = [(round(x, 2), round(y, 2)) for x, y in geom.coords]
        for a, b in zip(cs[:-1], cs[1:]):
            if a == b:
                continue
            seg = LineString([a, b])
            rows.append({"u": a, "v": b, "way": str(r.get("@osmId")), "highway": hw, "kind": kind,
                         "bridge": is_bridge, "name": r.get("name"), "len_m": seg.length,
                         "t_s": seg.length / v, "geometry": seg})
    return gpd.GeoDataFrame(rows, crs=CRS)


edges = pd.concat([way_edges(roads, "road"), way_edges(foot, "foot")], ignore_index=True)
edges = gpd.GeoDataFrame(edges, crs=CRS)
say(f"network: {len(edges)} edge segments ({(edges.kind == 'road').sum()} road, {(edges.kind == 'foot').sum()} foot)")

# ---------- 2. which segments are damaged (with the confidence of the impact they touch) ----------
CONF_RANK = {"low": 1, "medium": 2, "high": 3}
zone = impact[["confidence", "geometry"]].copy(); zone["geometry"] = zone.buffer(ROAD_BUF_M)
zone["c"] = zone["confidence"].map(CONF_RANK).fillna(1)
j = gpd.sjoin(edges[["geometry"]], zone[["c", "geometry"]], how="inner", predicate="intersects")
edges["cut_conf"] = j.groupby(level=0)["c"].max().reindex(edges.index).fillna(0).astype(int)
# whole bridge ways flagged by osm_damage.py are cut too
if len(bridges_hit):
    bconf = dict(zip(bridges_hit["@osmId"].astype(str), bridges_hit["confidence"].map(CONF_RANK).fillna(1)))
    bmask = edges["way"].isin(list(bconf))
    edges.loc[bmask, "cut_conf"] = np.maximum(edges.loc[bmask, "cut_conf"], edges.loc[bmask, "way"].map(bconf)).astype(int)
say(f"damaged segments: {(edges.cut_conf > 0).sum()} (any confidence)")


# ---------- 3. graphs + targets ----------
def build_graph(e):
    G = nx.Graph()
    for u, v, t, i in zip(e["u"], e["v"], e["t_s"], e.index):
        if not G.has_edge(u, v) or G[u][v]["t"] > t:
            G.add_edge(u, v, t=t, idx=i)
    return G


nodes_xy = {}
for u, v in zip(edges["u"], edges["v"]):
    nodes_xy[u] = u; nodes_xy[v] = v
node_list = list(nodes_xy)
node_arr = np.array(node_list)
from scipy.spatial import cKDTree
tree = cKDTree(node_arr)


ROAD_EDGES = edges[edges.kind == "road"][["u", "v", "geometry"]]


def snap(points):
    """Nearest ROAD segment within SNAP_M -> its closer end node (works even where road vertices are sparse)."""
    if len(points) == 0:
        return []
    pts = gpd.GeoDataFrame(geometry=list(points), crs=CRS)
    j = gpd.sjoin_nearest(pts, ROAD_EDGES, how="left", max_distance=SNAP_M)
    j = j[~j.index.duplicated()]
    out = []
    for i, p in enumerate(pts.geometry):
        r = j.loc[i]
        if pd.isna(r.get("index_right")):
            out.append(None); continue
        u, v = r["u"], r["v"]
        out.append(u if p.distance(gpd.points_from_xy([u[0]], [u[1]])[0]) <= p.distance(gpd.points_from_xy([v[0]], [v[1]])[0]) else v)
    return out


settle_pts = settle.geometry.representative_point()
settle["node"] = snap(settle_pts)
_blank = pd.Series(index=settle.index, dtype=object)
settle["name_"] = settle.get("name:en", _blank).fillna(settle.get("name", _blank)).fillna("(unnamed)")   # English name if mapped
settle["name_local"] = settle.get("name", _blank)
settle["place"] = settle.get("place", pd.Series(index=settle.index, dtype=object))
health_nodes = [n for n in snap(health.geometry.representative_point()) if n]
town_nodes = [n for n, pl in zip(settle["node"], settle["place"]) if n and pl in ("town", "city")]
node_pts = gpd.GeoSeries(gpd.points_from_xy(node_arr[:, 0], node_arr[:, 1]), crs=CRS)
edge_nodes = [node_list[i] for i in np.where(node_pts.distance(area.exterior).values <= EXIT_M)[0]]
TARGETS = set(health_nodes) | set(town_nodes) | set(edge_nodes)
say(f"targets: {len(set(health_nodes))} health, {len(set(town_nodes))} town nodes, {len(edge_nodes)} roads leaving the area")

# population proxy: buildings within 500 m of each settlement
bc = bld.geometry.representative_point()
btree = cKDTree(np.c_[bc.x, bc.y]) if len(bc) else None
settle["buildings_500m"] = [len(btree.query_ball_point((p.x, p.y), 500)) if btree else 0 for p in settle_pts]


def times(G):
    src = [n for n in TARGETS if n in G]
    return nx.multi_source_dijkstra_path_length(G, src, weight="t") if src else {}


def status_table(e_pre, cut_mask, label):
    G_pre = build_graph(e_pre)
    G_post = build_graph(e_pre[~cut_mask.loc[e_pre.index]])
    t_pre, t_post = times(G_pre), times(G_post)
    st, tp, tq = [], [], []
    for n in settle["node"]:
        a = t_pre.get(n) if n else None
        b = t_post.get(n) if n else None
        tp.append(round(a / 60, 1) if a is not None else None); tq.append(round(b / 60, 1) if b is not None else None)
        if n is None: st.append("no road within 500 m")
        elif a is None: st.append("road not linked pre-event")
        elif b is None: st.append("cut off")
        elif a > 0 and b / a > DETOUR_RATIO and b - a > 300: st.append("long detour")
        else: st.append("unaffected")
    return st, tp, tq, G_pre, G_post, t_post


road_e = edges[edges.kind == "road"]
results = {}
for scen, confs in SCENARIOS.items():
    ranks = {CONF_RANK[c] for c in confs}
    cut = edges["cut_conf"].isin(ranks)
    st, tp, tq, G_pre, G_post, t_post = status_table(road_e, cut, scen)
    settle[f"status_{scen}"] = st
    if scen == "all_detections":
        settle["minutes_pre"], settle["minutes_post"] = tp, tq
        G_post_main, t_post_main, cut_main = G_post, t_post, cut
    results[scen] = pd.Series(st).value_counts().to_dict()

# ---------- 4. footpath check for road-cut-off settlements ----------
all_e = edges
G_foot_post = build_graph(all_e[~cut_main])
t_foot = times(G_foot_post)
settle["reachable_on_foot"] = [bool(n and n in t_foot) for n in settle["node"]]

# ---------- 5. repair priority: reopen one damaged way at a time ----------
cut_off_idx = settle.index[settle["status_all_detections"] == "cut off"]
reached_now = set(t_post_main)
repairs = []
cut_ways = road_e[cut_main.loc[road_e.index]].groupby("way")
say(f"testing {len(cut_ways)} damaged road/bridge ways for repair priority")
for way, grp in cut_ways:
    G_try = G_post_main.copy()
    for _, r in road_e[road_e["way"] == way].iterrows():
        if not G_try.has_edge(r.u, r.v) or G_try[r.u][r.v]["t"] > r.t_s:
            G_try.add_edge(r.u, r.v, t=r.t_s)
    reach = times(G_try)
    fixed = [i for i in cut_off_idx if settle.at[i, "node"] in reach]
    if fixed:
        mid = grp.geometry.union_all().centroid if hasattr(grp.geometry, "union_all") else grp.geometry.unary_union.centroid
        ll = gpd.GeoSeries([mid], crs=CRS).to_crs(4326).iloc[0]
        repairs.append({"way_osm_id": way, "name": grp["name"].dropna().iloc[0] if grp["name"].notna().any() else "(unnamed)",
                        "highway": grp["highway"].iloc[0], "is_bridge": bool(grp["bridge"].any()),
                        "damaged_m": round(grp["len_m"].sum()), "settlements_reconnected": len(fixed),
                        "buildings_reconnected": int(settle.loc[fixed, "buildings_500m"].sum()),
                        "names": ", ".join(settle.loc[fixed, "name_"].head(6)), "lat": round(ll.y, 5), "lon": round(ll.x, 5)})
rep = pd.DataFrame(repairs)
if len(rep):
    rep = rep.sort_values(["settlements_reconnected", "buildings_reconnected"], ascending=False).reset_index(drop=True)
    rep.index += 1
rep.to_csv(f"{RUN}/repairs.csv", index_label="rank")

# ---------- 6. save + summary ----------
out = settle.copy(); out["geometry"] = settle_pts
keep = ["name_", "name_local", "place", "status_all_detections", "status_confident_only", "minutes_pre", "minutes_post",
        "reachable_on_foot", "buildings_500m", "geometry"]
out[keep].rename(columns={"name_": "name"}).to_crs(4326).to_file(f"{RUN}/settlements_status.geojson", driver="GeoJSON")
stats = {"settlements": int(len(settle)), "scenarios": results,
         "cut_off_but_reachable_on_foot": int(((settle.status_all_detections == "cut off") & settle.reachable_on_foot).sum()),
         "buildings_near_cut_off_settlements": int(settle.loc[settle.status_all_detections == "cut off", "buildings_500m"].sum()),
         "repair_items_that_reconnect_someone": int(len(rep))}
json.dump(stats, open(f"{RUN}/access_stats.json", "w"), indent=2)
log["access"] = stats; json.dump(log, open(f"{RUN}/run_log.json", "w"), indent=2)

print("\n================ ACCESS SUMMARY ================")
for scen, counts in results.items():
    print(f"{scen:<16} {counts}")
print(f"cut off by road but reachable on foot: {stats['cut_off_but_reachable_on_foot']}")
print(f"buildings within 500 m of cut-off settlements (population proxy): {stats['buildings_near_cut_off_settlements']}")
co = settle[settle.status_all_detections.isin(["cut off", "long detour"])].sort_values("buildings_500m", ascending=False)
print("\nMost affected settlements (all detections):")
for _, r in co.head(15).iterrows():
    print(f"  {r['name_']:<28} {r['place'] or '':<9} {r['status_all_detections']:<12} "
          f"before {r['minutes_pre']} min -> after {r['minutes_post']} min | foot: {'yes' if r['reachable_on_foot'] else 'no'} "
          f"| ~{r['buildings_500m']} buildings")
print("\nTOP REPAIRS (reopen this -> reconnects):")
print(rep.head(10)[["name", "highway", "is_bridge", "settlements_reconnected", "buildings_reconnected", "names"]].to_string()
      if len(rep) else "  none - no settlement is fully cut off in this scenario")

# ---------- 7. map ----------
colors = {"cut off": "#d00000", "long detour": "#f08000", "unaffected": "#2a9d3a",
          "road not linked pre-event": "#777777", "no road within 500 m": "#bbbbbb"}
import warnings; warnings.filterwarnings("ignore", message=".*Glyph.*"); warnings.filterwarnings("ignore", message=".*Devanagari.*")
fig, ax = plt.subplots(figsize=(13, 14))
road_e.plot(ax=ax, color="#c8c8c8", linewidth=0.6)
road_e[cut_main.loc[road_e.index]].plot(ax=ax, color="black", linewidth=2.5)
impact.plot(ax=ax, color="#8b4513", alpha=0.5)
for s, c in colors.items():
    sub = settle[settle.status_all_detections == s]
    if len(sub):
        gpd.GeoSeries(sub.geometry.representative_point(), crs=CRS).plot(ax=ax, color=c, markersize=60, edgecolor="white",
                                                                          label=f"{s} ({len(sub)})")
for _, r in co.head(12).iterrows():
    p = r.geometry.representative_point(); ax.annotate(r["name_"], (p.x, p.y), fontsize=8, xytext=(4, 4), textcoords="offset points")
if len(health):
    health.geometry.representative_point().plot(ax=ax, color="blue", marker="P", markersize=90, label="health facility")
ax.legend(loc="lower right"); ax.set_title("Settlement access after the flood (all detections)")
plt.tight_layout(); plt.savefig(f"{RUN}/access_map.png", dpi=110); plt.show()
print("© OpenStreetMap contributors | Contains modified Copernicus Sentinel data 2026.")

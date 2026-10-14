"""
Track B - Trishuli data check using ONLY the official sources named in the rules:
  Copernicus Data Space catalogue (Sentinel-1 / Sentinel-2) + ohsome (pre-event OSM).
No login needed for searching. Run in Colab or locally:  python cdse_data_check.py
"""
import datetime as dt
from collections import defaultdict

import requests

# ---------------- CONFIG ----------------
BBOX = [85.10, 27.90, 85.45, 28.32]   # W,S,E,N - approx Rasuwagadhi -> Betrawati corridor; refine later
EVENT = "2026-08-26"
WINDOW_DAYS = 30
ODATA = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
OHSOME = "https://api.ohsome.org/v1"
# ----------------------------------------

event = dt.date.fromisoformat(EVENT)
start = (event - dt.timedelta(days=WINDOW_DAYS)).isoformat()
end = (event + dt.timedelta(days=WINDOW_DAYS)).isoformat()
w, s, e, n = BBOX
wkt = f"POLYGON(({w} {s},{e} {s},{e} {n},{w} {n},{w} {s}))"


def search(collection, name_parts):
    """Query the CDSE OData catalogue; follows paging. Returns list of products with attributes."""
    flt = (f"Collection/Name eq '{collection}' "
           + "".join(f"and contains(Name,'{p}') " for p in name_parts)
           + f"and OData.CSC.Intersects(area=geography'SRID=4326;{wkt}') "
           + f"and ContentDate/Start gt {start}T00:00:00.000Z "
           + f"and ContentDate/Start lt {end}T23:59:59.000Z")
    params = {"$filter": flt, "$expand": "Attributes", "$top": 200, "$orderby": "ContentDate/Start asc"}
    out, url = [], ODATA
    while url:
        r = requests.get(url, params=params, timeout=120)
        r.raise_for_status()
        js = r.json()
        out += js.get("value", [])
        url, params = js.get("@odata.nextLink"), None
    return out


def attrs(p):
    return {a["Name"]: a.get("Value") for a in p.get("Attributes", [])}


# ---------- 1. SENTINEL-1 GRD ----------
print("=== SENTINEL-1 GRD (IW, dual-pol VV+VH) ===")
s1 = search("SENTINEL-1", ["_IW_GRDH_1SDV_"])
groups = defaultdict(set)
for p in s1:
    if p["Name"].endswith("_COG.SAFE"):
        continue                                   # same scene in another format
    a = attrs(p)
    d = dt.date.fromisoformat(p["ContentDate"]["Start"][:10])
    key = (a.get("relativeOrbitNumber"), a.get("orbitDirection"), p["Name"][:3])  # orbit, pass, S1A/S1C
    groups[key].add(d)

if not groups:
    print("No IW GRDH dual-pol scenes found - check BBOX/dates.")
by_orbit = defaultdict(set)
for (orbit, direction, sat), dates in groups.items():
    by_orbit[(orbit, direction)] |= {(d, sat) for d in dates}
for (orbit, direction), items in sorted(by_orbit.items(), key=lambda x: (str(x[0][0]), str(x[0][1]))):
    dates = sorted(items)
    pre = [x for x in dates if x[0] < event]
    post = [x for x in dates if x[0] >= event]
    ok = "PAIR OK" if pre and post else "no pair"
    gap = (post[0][0] - pre[-1][0]).days if pre and post else None
    print(f"orbit {orbit!s:>4} {direction!s:<10} {ok:<8} "
          f"pre={pre[-1][0] if pre else '-'}({pre[-1][1] if pre else ''}) "
          f"post={post[0][0] if post else '-'}({post[0][1] if post else ''}) gap={gap}d")
    print(f"      all: {[f'{d}({sat})' for d, sat in dates]}")

# ---------- 2. SENTINEL-2 L2A ----------
print("\n=== SENTINEL-2 L2A ===")
s2 = search("SENTINEL-2", ["MSIL2A"])
by_date = defaultdict(list)
for p in s2:
    a = attrs(p)
    cc = a.get("cloudCover")
    by_date[dt.date.fromisoformat(p["ContentDate"]["Start"][:10])].append(round(cc) if cc is not None else None)
for d in sorted(by_date):
    print(f"{'PRE ' if d < event else 'POST'} {d}  tile cloud % = {by_date[d]}")
print("(Tile cloud % covers the whole 100 km tile, not just your valley.)")

# ---------- 3. OPENSTREETMAP (pre-event, ohsome) ----------
print("\n=== OPENSTREETMAP via ohsome ===")
meta = requests.get(f"{OHSOME}/metadata", timeout=60).json()
latest = meta["extractRegion"]["temporalExtent"]["toTimestamp"][:10]
snapshot = min(dt.date.fromisoformat(latest), event - dt.timedelta(days=1))
assert snapshot < event, "OSM snapshot must be BEFORE the event (data rule)"
print(f"ohsome latest data: {latest} -> using snapshot {snapshot}")
filters = {
    "buildings":   "building=* and geometry:polygon",
    "roads":       "highway in (motorway,trunk,primary,secondary,tertiary,unclassified,residential,service,track) and geometry:line",
    "footpaths":   "highway in (path,footway) and geometry:line",
    "bridges":     "bridge=* and geometry:line",
    "settlements": "place in (city,town,village,hamlet)",
    "health":      "amenity in (hospital,clinic) or healthcare=*",
}
for name, flt in filters.items():
    r = requests.post(f"{OHSOME}/elements/count",
                      data={"bboxes": ",".join(map(str, BBOX)), "time": str(snapshot), "filter": flt},
                      timeout=120)
    r.raise_for_status()
    print(f"{name:<12} {int(r.json()['result'][0]['value'])}")

print("\nDone. Want: at least one 'PAIR OK' orbit, a usable post-event S2 date, non-zero OSM counts.")
print("Copernicus DEM (GLO-30) is static and covers Nepal - it is fetched later in the pipeline.")

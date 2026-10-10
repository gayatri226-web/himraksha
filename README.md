# HimRaksha — flood damage mapping from space

*Him (हिम, snow / Himalaya) + Raksha (रक्षा, protection).* Track B, Multimodal AI Hackathon 2026.

Area + flood date in → where it flooded / what was damaged / who is cut off out.
Case study: 26 Aug 2026 Bhote Koshi–Trishuli flood, Nepal.

## Status (11 Oct 2026)
| Part | Status | File |
|---|---|---|
| Training data (22,972 Kuro Siwo chips) | done | `training/01_kurosiwo_extract.py` |
| U-Net (flood F1 0.819 vs 0.588 baseline, held-out events) | done | `training/02_*.ipynb`, `models/` |
| S1 pair search (same orbit, AOI coverage-aware) | done | `pipeline/01_mapping.py` |
| openEO S1 processing, asc + desc | done | `pipeline/01_mapping.py` |
| Terrain masks (slope + HAND from Copernicus DEM) | done | `pipeline/01_mapping.py` |
| Debris (adaptive change) + water (U-Net), observability-aware fusion | done | `pipeline/01_mapping.py` |
| Pre-event OSM via ohsome **API v2** (needs free key) | done | `pipeline/02_osm_fetch_v2.py` |
| Damage overlay (buildings/roads/bridges + confidence) | done | `pipeline/03_damage.py` |
| Cut-off analysis + footpath check + repair priority | done | `pipeline/04_access_cutoff.py` |
| One-click Colab run of everything | done | `notebooks/trackb_full_run.ipynb` |
| Final Trishuli run with orbit 85 asc + orbit 19 desc | **TODO** | notebook |
| EMSR927 comparison (check only, `eval/`) - REQUIRED in submission checklist | **TODO** | |
| AI on unseen Himalayan scenes: score U-Net vs EMSR927 (+ 1 more Himalayan event) | **TODO** | |
| Cut-off targets = hospital/town per brief; "road out of area" reported separately | **TODO** | `04_access_cutoff.py` |
| Sentinel-2 optical layer (REQUIRED by brief: "S2 when the sky is clear") | **TODO** | |
| Stricter bridge rule (high/medium conf only) | **TODO** | `03_damage.py` |
| Streamlit dashboard + sitrep PDF | **TODO** | `app/` |
| Robustness tests (no-flood area, plains flood, no-pair date) | **TODO** | |
| Report (≤6 p) + 3-min video | **TODO** | `docs/` |

## Run
Easiest: open `notebooks/trackb_full_run.ipynb` in Colab, run top to bottom. Change only the CONFIG cell.
Needs: Copernicus Data Space account (openEO login), free ohsome API key (https://account.heigit.org/signup),
`models/flood_unet.onnx` + `models/norm.json`.

## Method (short)
1. **Pairs:** CDSE catalogue → same relative orbit, pre/post around event, ≥60 % AOI coverage; best ascending + best descending.
2. **Radar:** openEO `sar_backscatter` (sigma0), dB, 10 m, UTM.
3. **Terrain:** Copernicus DEM GLO-30 → slope, HAND (pysheds). Valley floor = HAND < 50 m & slope < 30°.
4. **Detection per orbit:** U-Net → new water; despeckled VV/VH change with adaptive threshold (max(2 dB, 2.5×robust σ of valley)) → debris darkened / brightened; blobs < 0.5 ha removed.
5. **Fusion:** only orbits that can *see* a pixel vote. Confidence: high (all observing orbits agree) / medium (single view) / low (disagree) / unobserved.
6. **Damage:** pre-event OSM; buildings within 10 m, roads within 10 m, bridges within 30 m of impact = "likely hit".
7. **Access:** OSM road graph (travel time by road class); remove damaged segments/bridges; settlement → nearest health facility / town / road leaving the area; cut off / long detour / unaffected; footpath check; repair priority = reopen one damaged way at a time, count villages reconnected.

## Key findings so far (Trishuli)
- Damage is mostly **debris/sediment**, not open water: U-Net finds little new water; change detection finds the corridor. U-Net still correct on river channels (4.5 % permanent water in valley).
- **Orbit choice matters:** orbit 121 desc covered only ~19–34 % of the AOI → pair selection now checks coverage; orbit 19 desc covers 100 %.
- Orbits 85 asc + 19 desc independently agree along the main Trishuli corridor (Betrawati upstream).
- ohsome API v1 `/elements/geometry` now returns 403 (v1 shuts 30 Nov 2026) → we use v2 with the **same 27 Jul 2026 pre-event snapshot**; counts identical to v1 (82,033 buildings, 2,013 roads, 179 bridges).

## Data rules
Inputs: Sentinel-1/2 + Copernicus DEM (CDSE), pre-event OSM (ohsome, snapshot < event), Kuro Siwo (training).
**EMSR927 / UNOSAT / post-event OSM are never inputs** — only `eval/` may read them.

## Attribution (must appear in README, report, dashboard, sitrep)
- "Contains modified Copernicus Sentinel data 2026."
- "Produced using Copernicus WorldDEM-30 © DLR e.V. 2010–2014 and © Airbus Defence and Space GmbH 2014–2018 provided under COPERNICUS by the European Union and ESA; all rights reserved."
- "© OpenStreetMap contributors."
- Bountos et al., 2024. Kuro Siwo: 33 billion m² under the water. NeurIPS 2024 Datasets and Benchmarks.
- EMSR927 (comparison only): "European Union, Copernicus Emergency Management Service data".

## Secrets
Never commit the ohsome API key or Copernicus credentials. Use env vars (`OHSOME_API_KEY`) or Colab getpass.

## Responsibility & licences
- Educational prototype, **not an operational tool**. No images of victims in demos or reports.
- OSM-derived outputs are ODbL: run outputs (GeoJSON) are not committed; if published they must stay under ODbL.
- The brief lists ohsome API v1; v1 extraction is now refused (403) ahead of its 30 Nov 2026 shutdown, so we use ohsome API v2 with the same pre-event snapshot (27 Jul 2026).

# HimRaksha — flood damage mapping from space

*Him (हिम, snow / Himalaya) + Raksha (रक्षा, protection).* Track B "Mapping Flood Damage from Space",
Multimodal AI Hackathon 2026.
Team: **Anurag Kasi** and **Gayatri Markale** (K. J. Somaiya School of Engineering, Mumbai).

Give it an **area and a flood date**. From raw satellite data it answers the three questions rescue teams ask:
**where did the flood hit, what was damaged, and who is cut off**. Then it shows the answers in a map dashboard and a
one-page situation report.

Case study: the 26 August 2026 Bhote Koshi–Trishuli flood, Nepal, compared with Copernicus EMS activation EMSR927.
Rehearsal on a second unseen Himalayan event: the 7 February 2021 Chamoli debris flood, India.

| | |
|---|---|
| **Demo video** | [Watch on Google Drive](https://drive.google.com/file/d/1PaJrX4UZVGP9plKon9hde6lwzckDmmWi/view?usp=sharing) |
| **Run it** | [`docs/RUN_GUIDE.md`](docs/RUN_GUIDE.md): accounts, every notebook cell, timings, troubleshooting |
| **Report** (6 pages) | [`docs/report/HimRaksha_report.pdf`](docs/report/HimRaksha_report.pdf) |
| **The model** | [`model/README.md`](model/README.md): model card, scores, how to load it |
| **Dashboard** | `code/dashboard/web/index.html`: double-click, works offline |

## Results at a glance — Trishuli, 26 Aug 2026
| Question | Answer from HimRaksha |
|---|---|
| Where did it flood? | **7.68 km²** of the valley floor impacted (6.74 km² from radar, 0.94 km² added by Sentinel-2); 2.58 km² high confidence |
| What was damaged? | **2,263** buildings likely hit (1,887 high confidence) of 82,033; **43.1 km** of road; **27 bridges likely damaged** (16 vehicle, 8 foot, 3 other) + 17 possibly damaged, of 179 |
| Who is cut off? | **37** of 172 settlements cut off from the nearest health facility (27 using confident detections only), 18 more with long detours; 25 of the cut-off ones still reachable on foot; ~3,300 buildings near cut-off settlements |
| What to fix first? | 5 repairs reconnect someone; the top one, an unnamed road near Chipleti (534 m damaged), reconnects 4 settlements (~215 buildings) |

**Checked against Copernicus EMS EMSR927** (used only for checking, never as an input):

| | Precision | Recall |
|---|---|---|
| Affected area (pixels, AOI01–03) | 0.92 | 0.54 (F1 0.68; Bidur F1 0.80) |
| Damaged buildings (2,964 in EMS) | 0.93 | 0.81 |
| Damaged roads (47 km in EMS) | 0.92 | 0.70 |
| Bridges, likely damaged (28 in EMS) | 0.81 | 0.68 |

The main miss is the narrow upstream gorges (Syapru Besi F1 0.32, Timure 0.27). There, about 40 % of the EMS debris
lies outside our valley mask, and steep slopes are poorly seen by radar. Full numbers: `validation/eval_emsr927.json`.

## How it works
1. **Find radar images.** Search the Copernicus catalogue for before/after Sentinel-1 images from the **same orbit**
   (12 days apart), the best ascending and the best descending pair covering the area.
2. **Process raw radar ourselves.** Download raw Sentinel-1 IW GRD and run ESA SNAP: orbit file → thermal and border
   noise removal → calibration (sigma0) → terrain correction on the Copernicus DEM → 10 m, dB
   (`code/pipeline/grd_preprocess.py`).
3. **Valley floor.** From the Copernicus DEM GLO-30: height above the nearest river (HAND) < 50 m and slope < 30°.
4. **AI model.** A U-Net (ResNet-34, trained on Kuro Siwo) finds **new water** in each before/after pair.
5. **Debris.** Despeckled VV/VH change with a threshold that adapts to each area (max(2 dB, 2.5 × robust σ)) finds
   ground that **darkened** or **brightened**; patches < 0.5 ha are removed.
6. **Combine orbits.** Only orbits that can see a pixel (no radar shadow or layover) vote. Confidence: high (they agree),
   medium (only one sees it), low (they disagree).
7. **Sentinel-2 when the sky is clear.** Cloud-free pixels (SCL mask) from the clearest before/after dates: new water,
   vegetation stripped, river channel filled. Agreement with radar raises confidence; optical-only finds are added.
8. **Damage.** Pre-event OpenStreetMap (ohsome API, snapshot 27 Jul 2026): buildings and roads within 10 m of the impact.
   Bridges are graded on the deck itself: *likely damaged* if ≥ 50 % of the deck is impacted **and** debris is at both
   ends, *possibly damaged* if impact is within 30 m.
9. **Cut-off.** Road graph with travel time by road class; damaged segments removed. Each settlement is routed to the
   nearest **health facility or town**: cut off / long detour / unaffected, plus a footpath check and a repair priority
   list (reopen one damaged road at a time and count who is reconnected).
10. **Bonus, flood path.** From any upstream point, follow steepest descent on the DEM (D8) and list the settlements whose
    buildings lie within 1 km of the channel and ≤ 30 m above it (high exposure if ≤ 10 m). A quick version runs live in the
    dashboard (click ⤵; it lists settlements by location); `code/pipeline/flow_path.py` checks every building.
11. **Dashboard and sitrep.** `code/dashboard/export_dashboard.py` turns each run into map layers, base maps, a
    flood-path grid and a rule-based one-page situation report. Every number comes from the run's own files.

## The AI component
Flood segmentation U-Net, ResNet-34 encoder (ImageNet start), 6 inputs (pre/post VV and VH, relative DEM, slope),
3 classes (dry / permanent water / flood water), trained on 19,071 Kuro Siwo chips.

| Test | Result |
|---|---|
| 7 Kuro Siwo flood events never seen in training | Flood **F1 0.819** (precision 0.874, recall 0.771) vs 0.588 for a threshold baseline |
| Trishuli 2026 (unseen, Himalayan): U-Net water inside the EMS affected area | **99.4 %** |
| Trishuli 2026: whole system with / without the U-Net | Area F1 **0.678 / 0.642** |
| Chamoli 2021 (unseen, Himalayan) | Little water found (0.07 km², a winter debris flood), not confirmed by Sentinel-2 |

Debris, the main damage in both events, is mapped by change detection, not by the U-Net. Details:
`model/README.md`, `validation/model_only_check.json`.

## JRC Global Surface Water: compared, not used
The organizers allowed the JRC permanent-water layer if we compared with and without it. Removing JRC water
(occurrence ≥ 25 / 50 / 75 %) lowers the area F1 at every threshold (0.655 → 0.628 / 0.639 / 0.650, radar-only run).
What it removes is mostly debris in the normal river channel, which EMS counts as damage. Building, road, bridge and
cut-off results do not change. The U-Net already tells permanent from new water: only 0.012 of its 0.612 km² lies on
JRC water. Details: `validation/jrc_comparison.json`.

## Folder layout
```
code/
  pipeline/      grd_preprocess.py   raw Sentinel-1 GRD -> calibrated, terrain-corrected stack (ESA SNAP)
                 00_data_check.py    checks inputs before a run
                 01_mapping.py       pair search, DEM masks, U-Net water + debris change, fused impact map
                 s2_layer.py         Sentinel-2 clear-sky layer, fused with radar
                 02_osm_fetch_v2.py  pre-event OpenStreetMap layers (ohsome API v2)
                 03_damage.py        buildings / roads / bridges hit (bridges.py: bridge grading)
                 04_access_cutoff.py who is cut off from the nearest health facility / town + repair priority
                 flow_path.py        bonus: flood path from any upstream point + settlements along it
  notebooks/     himraksha_full_run.ipynb    the full run in Google Colab (USE THIS)
                 grd_preprocess_colab.ipynb  raw-GRD processing on its own
                 s2_layer_colab.ipynb        Sentinel-2 layer for an existing run
                 old_openeo_version/         first version (openEO pre-processed backscatter), kept for comparison
  evaluation/    eval_emsr927.py     comparison with Copernicus EMS EMSR927 (validation only)
                 compare_jrc.py      results with vs without the JRC permanent-water mask
                 model_only_check.py contribution of the U-Net on unseen Himalayan scenes
  dashboard/     export_dashboard.py run folder -> dashboard data
                 web/index.html      THE DASHBOARD (double-click, works offline)
                 web/sitrep.html     one-page situation report (print / save as PDF)
training/        Kuro Siwo chip extraction, U-Net training notebook, ONNX export
model/           model card, norm.json, metrics, training curves (flood_unet.onnx in the GitHub Release)
results/         small outputs of the Trishuli 2026 and Chamoli 2021 runs (rasters and big layers are not in git)
validation/      eval_emsr927.json + map, jrc_comparison.json, model_only_check.json
docs/            RUN_GUIDE.md, report/
```

## Run it
Full instructions: [`docs/RUN_GUIDE.md`](docs/RUN_GUIDE.md). In short:
1. Free accounts: **Copernicus Data Space** (https://dataspace.copernicus.eu) and an **ohsome API key**
   (https://account.heigit.org/signup).
2. Open `code/notebooks/himraksha_full_run.ipynb` in Google Colab, set the area, flood date and OSM snapshot in cell 1,
   and run top to bottom. It asks for the logins (typed, never saved) and for the code and model files to upload.
3. About 1 hour per area. Cell 9 exports the run for the dashboard: unzip it into `code/dashboard/web/data/`, then run
   `python code/dashboard/export_dashboard.py --manifest code/dashboard/web` and refresh the dashboard.

## Dashboard
Open `code/dashboard/web/index.html` in any browser. No install, no server, works offline.
- **Map page** (for rescuers): full-screen map with a side list of **cut-off villages, repairs and bridges**; one click
  flies the map there. Map / Satellite / Terrain backgrounds; every element has a tooltip and details; legend and scale bar;
  village search.
- **⚠ Critical areas**: picked by fixed rules from the run (cut off with no road and no footpath first, then the most-used
  damaged bridge, the biggest villages reachable only on foot, the most useful repair). Red rings + a Next ▶ tour.
- **☰ menu**: Map / Analysis pages, event, map layers (context layers off by default to reduce clutter),
  "All detections / Confident only" switch (numbers, map and cut-off status change together), satellite data used.
- **Analysis page**: headline numbers and full tables: Cut off · Repairs · Bridges · Flood path (click any point upstream,
  traced live on the DEM) · Evidence (Sentinel-2 before/after) · Validation. Only "Show on map" buttons move the map.
- **Situation report**: one A4 page generated from the run's numbers (print or save as PDF).
- **New analysis**: type any area and date; the page checks them and writes the notebook settings for you.
- Satellite view: where no clear Sentinel-2 image exists, the background shows grey hatching (cloud), never a colour that could
  be mistaken for damage.

## Data rules
- **Inputs:** Sentinel-1 and Sentinel-2 and the Copernicus DEM (Copernicus Data Space), OpenStreetMap as it was before
  the event (ohsome, snapshot checked to be before the flood date), Kuro Siwo (training only).
- **Never inputs:** Copernicus EMS, UNOSAT or other damage maps, post-event OSM. Only `code/evaluation/` reads EMS.
- The brief names ohsome API v1. Its extraction endpoints now refuse requests (HTTP 403) ahead of the 30 Nov 2026
  shutdown, so we use ohsome API v2 with the same pre-event snapshot (27 Jul 2026).

## Limitations (short; full list in the report)
- Sentinel-1 passes every 6–12 days, so it maps what is left after a flood. It cannot warn of a sudden glacier-lake
  burst or landslide minutes ahead.
- Narrow gorges: radar shadow and layover hide steep slopes; the valley mask leaves out debris high on the slopes.
- Damage and cut-off results are only as complete as OpenStreetMap (Chamoli 2021 had 219 mapped buildings; Trishuli
  has no OSM "town" nodes, so routes go to health facilities).
- Sentinel-2 is often cloudy in the monsoon: 40 % of the Trishuli valley was clear on both dates.
- The model was not tested on Sen1Floods11, which is optional in the brief.
- Educational prototype, not an operational tool.

## Attribution
- "Contains modified Copernicus Sentinel data 2026." (Chamoli rehearsal: Copernicus Sentinel data 2021.)
- "Produced using Copernicus WorldDEM-30 © DLR e.V. 2010–2014 and © Airbus Defence and Space GmbH 2014–2018 provided
  under COPERNICUS by the European Union and ESA; all rights reserved."
- "© OpenStreetMap contributors." OSM-derived outputs are under the ODbL.
- Kuro Siwo (training data): Bountos, N. I., et al. (2024). Kuro Siwo: 33 billion m² under the water. A global
  multi-temporal satellite dataset for rapid flood mapping. NeurIPS 2024 Datasets and Benchmarks.
- EMSR927 (checking only): "European Union, Copernicus Emergency Management Service data".
- JRC Global Surface Water (comparison only): Pekel, J.-F., Cottam, A., Gorelick, N., Belward, A. S. (2016).
  High-resolution mapping of global surface water and its long-term changes. Nature 540, 418–422.
- Processed with ESA SNAP. OSM history via the ohsome API (HeiGIT). Maps drawn with Leaflet.

## Secrets
Never commit the ohsome API key or Copernicus credentials. The notebook reads them with `getpass` or environment
variables (`OHSOME_API_KEY`, `CDSE_USER`, `CDSE_PASSWORD`). Clear notebook outputs before committing.

## Responsibility
This disaster is recent and many families are still affected. No images of victims are used anywhere in this project.
HimRaksha is an educational prototype, not an operational tool.

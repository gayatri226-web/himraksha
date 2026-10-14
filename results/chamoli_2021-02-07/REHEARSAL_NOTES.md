# Rehearsal: Chamoli rock-ice avalanche / debris flood, 7 Feb 2021 (Uttarakhand, India)

Purpose: prove the pipeline runs end-to-end on a new area + date (judging is live, any area/date), and test
the model on a second unseen Himalayan scene. Run with the unchanged notebook; only CONFIG changed.

CONFIG: BBOX [79.50, 30.40, 79.80, 30.60], EVENT 2021-02-07, OSM snapshot 2021-02-06.

## Ran end-to-end with no crash
- Sentinel-1 raw GRD: orbit 56 ascending (29 Jan -> 10 Feb) + orbit 63 descending (30 Jan -> 11 Feb), both 100 % coverage.
- Different UTM zone (44N) handled automatically.
- Sentinel-2: clear winter skies; before 16-31 Jan, after 20 Feb - 2 Mar; valley clear on both dates 73 %.
- OSM (ohsome history, 6 Feb 2021), damage, cut-off all ran.

## What it found (checked against the before/after Sentinel-2 images)
- Debris corridor traced from the avalanche source in Ronti Gad (~3,400-3,650 m, deposits visible on Sentinel-2)
  down the Rishiganga to the Raini confluence and along the Dhauliganga to the Tapovan barrage (sediment fill clearly
  visible on Sentinel-2). High elevation detections were real deposits, not snow false alarms.
- Impact 1.54 km2 (radar 1.18 + optical-only 0.36); radar + optical agree on 0.21 km2.
- Bridge near the Tapovan barrage: likely damaged (high confidence). Bridge at the Raini confluence: only "possibly
  damaged" (deck barely inside the mapped impact) - in reality it was destroyed.
- Joshimath-Malari road affected; villages upstream (Markura, Panwarsa, Tamak) cut off from health facilities by road,
  Rini (Raini) long detour - consistent with reports that the Malari valley villages were cut off.

## Problems found
1. FIXED - damage map zoom panel was hard-coded to the Trishuli corridor. Now centres on this run's largest impact clusters.
2. Limitation - OSM in Feb 2021 had only 219 buildings mapped in the area, 35 of 66 settlements had no mapped road
   within 500 m -> building counts / cut-off results depend on OSM completeness (report limitation).
3. Limitation - narrow gorges: the two orbits mostly disagree (low confidence 0.98 of 1.18 km2 radar impact);
   Sentinel-2 raised high-confidence area from 0.20 to 0.36 km2.
4. Limitation - bridge grading misses a bridge whose deck lies just outside the mapped impact (Raini).

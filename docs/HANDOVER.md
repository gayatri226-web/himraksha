# Handover notes

## Where things stand
Everything in README "Status" marked done runs on the Trishuli case. Last full outputs: `trishuli_run.zip` (orbits 85+121, older),
next run should use 85 asc + 19 desc (the notebook picks this automatically now).

## Known weaknesses (good first tasks for a GIS person)
1. **Bridges over-flagged**: any change within 30 m of a bridge flags it; channel always changes. Idea: require high/medium
   confidence, or require impact on the bridge approaches / both banks, or use change magnitude.
2. **"No road link pre-event" = 62/172 settlements**: split into no road within 500 m vs disconnected road network
   (now reported separately). Check OSM topology (unconnected ways, missing nodes at junctions), maybe snap ways within 5 m.
3. **Exits**: any road node within 300 m of the AOI edge counts as "way out". Better: only primary/secondary/trunk exits,
   or expand the network buffer beyond the AOI.
4. **HAND threshold** (50 m) and slope (30°) tuned by eye on Trishuli — validate vs EMSR927 later.
5. **Sentinel-2** layer not built yet (clear dates: 12 Aug pre, 27 Aug post near Betrawati).
6. **Flow-path bonus**: D8 trace from a clicked point (pysheds flowdir already computed).

## Gotchas
- Colab disk is wiped on disconnect; college Google accounts block Drive mounting → use a personal Gmail or download zips.
- openEO free tier: 1 batch job at a time (429 otherwise) – code already sequential + retries.
- ohsome v2 free key is rate-limited – code paces requests (12 s) and caches tiles.

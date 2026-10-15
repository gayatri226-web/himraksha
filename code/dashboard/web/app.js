/* HimRaksha dashboard v2. Reads data/events.js + data/<id>/data.js written by export_dashboard.py. No server needed.
   Two pages share one state: Map (full screen, for rescuers) and Analysis (numbers + lists). ☰ opens the menu. */
(function () {
  'use strict';
  var EVENTS = window.HR_EVENTS || [];
  window.HR_DATA = window.HR_DATA || {};
  var $ = function (id) { return document.getElementById(id); };
  var esc = function (s) { return String(s === null || s === undefined ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); };
  var fmt = function (n, d) { if (n === null || n === undefined || isNaN(n)) return '—'; return Number(n).toLocaleString('en-US', { minimumFractionDigits: d || 0, maximumFractionDigits: d || 0 }); };
  var plural = function (n, one, many) { return fmt(n) + ' ' + (Number(n) === 1 ? one : many); };
  var STATUS = { cut: 'Cut off', detour: 'Long detour', ok: 'Reachable', na: 'No mapped road nearby' };
  var HW = { primary: 'main road', trunk: 'highway', secondary: 'road', tertiary: 'road', unclassified: 'local road', residential: 'village road', track: 'track', service: 'access road' };
  var MAIN = { primary: 1, trunk: 1, secondary: 1, tertiary: 1 };

  var S = { ev: null, page: 'map', conf: 'all', base: 'map', tab: 'cut', sel: null, tracing: false, trace: null, crit: false, critI: 0, q: 'cut', qSel: null,
            layers: { impact: true, optical: true, hit: true, roads: true, minor: false, settle: true, others: false, labels: true,
                      health: true, bridges: true, bridgesP: false, repairs: true, flow: true } };
  var LAYERS = [['impact', 'Damage seen by radar', 'var(--debris-hi)'], ['optical', 'Damage seen only by Sentinel-2', 'var(--optical)'],
                ['hit', 'Affected roads', 'var(--road-hit)'], ['settle', 'Cut-off & detour villages', 'var(--cut)'], ['labels', 'Village names', '#fff'],
                ['health', 'Health facilities', 'var(--health)'], ['bridges', 'Bridges likely damaged', 'var(--bridge)'], ['repairs', 'Repair priority', 'var(--ink)'],
                ['flow', 'Flood path', '#0B6BFF'], ['roads', 'Main roads', '#E8C96A'], ['minor', 'Minor roads & tracks', '#ffffff', 'context'],
                ['others', 'Reachable / unlinked villages', 'var(--ok)', 'context'], ['bridgesP', 'Bridges possibly damaged', '#fff', 'context']];

  // ------------------------------------------------------------------ map
  var map = L.map('map', { crs: L.CRS.Simple, minZoom: -3, maxZoom: 3, zoomSnap: 0.25, zoomDelta: 0.5, wheelPxPerZoomLevel: 160, wheelDebounceTime: 60,
                           maxBoundsViscosity: 1.0, bounceAtZoomLimits: false, attributionControl: false, zoomControl: false, preferCanvas: true });
  window.__hrmap = map;
  var canvas = L.canvas({ padding: 0.5, tolerance: 4 });
  var G = {};
  ['base', 'minor', 'roads', 'glow', 'impactLo', 'impactOpt', 'impactHi', 'hit', 'flow', 'others', 'settle', 'labels', 'health', 'bridgesP', 'bridges', 'repairs', 'start', 'crit']
    .forEach(function (k) { G[k] = L.layerGroup(); });
  var baseOverlay = null, markersById = {};
  var ll = function (p) { return [-p[1], p[0]]; };
  var D = function () { return window.HR_DATA[S.ev]; };

  var ISZ = { 'm-cut': 20, 'm-detour': 20, 'm-ok': 15, 'm-na': 11, 'm-hl': 21, 'm-bl': 24, 'm-bp': 20, 'm-rp': 28, 'm-start': 24 };
  function icon(cls, html) { var z = ISZ[cls.split(' ')[0]] || 22; return L.divIcon({ className: 'mk ' + cls, html: '<i>' + (html || '') + '</i>', iconSize: [z, z], iconAnchor: [z / 2, z / 2] }); }
  function popup(title, rows, extra) {
    return '<div class="pop"><h4>' + esc(title) + '</h4><table>' + rows.map(function (r) { return '<tr><td>' + esc(r[0]) + '</td><td>' + esc(r[1]) + '</td></tr>'; }).join('') + '</table>' + (extra || '') + '</div>';
  }
  function statusOf(s) { return S.conf === 'hi' ? s.sc : s.s; }
  function confOk(c) { return S.conf !== 'hi' || c === 'high' || c === 'medium'; }
  function sname(s) { return s.n || 'Unnamed ' + (s.pl || 'settlement'); }
  function minutes(s) {
    if (s.pre === null || s.pre === undefined) return 'no road link before the flood';
    var st = statusOf(s);
    if (st === 'cut') return fmt(s.pre) + ' min → no road';
    if (s.post === null || s.post === undefined) return fmt(s.pre) + ' min';
    return fmt(s.pre) + ' → ' + fmt(s.post) + ' min';
  }

  // ------------------------------------------------------------------ build layers for the current event
  function buildStatic() {
    var d = D();
    Object.keys(G).forEach(function (k) { G[k].clearLayers(); });
    markersById = {};
    baseOverlay = L.imageOverlay(baseUrl(), [[-d.H, 0], [0, d.W]], { alt: 'Base map' });
    G.base.addLayer(baseOverlay);
    d.roads.forEach(function (r) {
      var lines = r.p.map(function (line) { return line.map(ll); });
      var tip = popup(r.n || ('Unnamed ' + (HW[r.hw] || r.hw)), [['Type', r.hw], ['Affected', r.k > 0 ? fmt(r.k * 1000) + ' m' : 'not affected']]);
      if (r.M) G.roads.addLayer(L.polyline(lines, { renderer: canvas, color: '#E8C96A', weight: 2, opacity: 0.95 }).bindPopup(tip));
      else G.minor.addLayer(L.polyline(lines, { renderer: canvas, color: '#FFFFFF', weight: 1, opacity: 0.75 }).bindPopup(tip));
    });
    d.roads_hit.forEach(function (r) {
      var lines = r.p.map(function (line) { return line.map(ll); });
      G.hit.addLayer(L.polyline(lines, { renderer: canvas, color: '#fff', weight: 7, opacity: 0.9, interactive: false }));
      G.hit.addLayer(L.polyline(lines, { renderer: canvas, color: '#E0157A', weight: 4, opacity: 1, lineCap: 'round' })
        .bindPopup(popup(r.n || ('Unnamed ' + (HW[r.hw] || r.hw)), [['Type', r.hw], ['Length affected', fmt(r.k * 1000) + ' m'], ['Confidence', r.c]])));
    });
    (d.health || []).forEach(function (h) {
      var near = d.settlements.filter(function (s) { return statusOf(s) === 'cut' && Math.hypot(s.x - h.x, s.y - h.y) * d.m_per_unit < 10000; }).length;
      G.health.addLayer(L.marker(ll([h.x, h.y]), { icon: icon('m-hl'), title: h.n, keyboard: true, riseOnHover: true, zIndexOffset: 1000 })
        .bindTooltip(esc(h.n || 'Health facility'), { className: 'tip', direction: 'top', offset: [0, -10] })
        .bindPopup(popup(h.n, [['Type', h.k], ['Cut-off settlements within 10 km', near]])));
    });
    (d.bridges || []).forEach(function (b) {
      var likely = b.s === 'likely damaged';
      (likely ? G.bridges : G.bridgesP).addLayer(L.marker(ll([b.x, b.y]), { icon: icon(likely ? 'm-bl' : 'm-bp'), title: (b.n || 'Bridge') + ': ' + b.s, keyboard: true, riseOnHover: true, zIndexOffset: 1100 })
        .bindTooltip(esc((b.n || 'Bridge') + ' · ' + b.s), { className: 'tip', direction: 'top', offset: [0, -10] })
        .bindPopup(popup(b.n || 'Unnamed bridge', [['Grade', b.s], ['Confidence', b.c], ['Use', b.k || b.hw], ['Deck impacted', fmt(b.deck) + ' %'],
          ['Debris at ends (px)', b.ea + ' / ' + b.eb]], '<div class="small muted" style="margin-top:6px">' +
          (likely ? 'Deck mostly impacted and debris at both ends.' : 'Impact nearby only — check on the ground.') + '</div>')));
    });
    (d.repairs || []).forEach(function (r) {
      G.repairs.addLayer(L.marker(ll([r.x, r.y]), { icon: icon('m-rp', String(r.rank)), title: 'Repair priority ' + r.rank, keyboard: true, zIndexOffset: 1200 })
        .bindTooltip('Repair #' + r.rank + ' · reconnects ' + plural(r.s, 'village', 'villages'), { className: 'tip', direction: 'top', offset: [0, -12] })
        .bindPopup(popup('Repair #' + r.rank + ': ' + (r.name === '(unnamed)' ? 'unnamed ' + (HW[r.hw] || r.hw) : r.name),
          [['Damaged length', fmt(r.m) + ' m'], ['Reconnects', plural(r.s, 'settlement', 'settlements')], ['Buildings reconnected', fmt(r.b)]],
          '<div class="small" style="margin-top:6px">' + esc(r.names) + '</div>')));
    });
    lockToMap(true);
  }

  function buildDynamic() {   // layers that change with the confidence filter / selection
    var d = D();
    ['glow', 'impactLo', 'impactOpt', 'impactHi', 'settle', 'others'].forEach(function (k) { G[k].clearLayers(); });
    var style = { debris: { high: '#6B2F0C', medium: '#6B2F0C', low: '#C9874E' }, water: { high: '#1D4FC4', medium: '#1D4FC4', low: '#7EA6F0' }, optical: { medium: '#E5A800', high: '#E5A800', low: '#E5A800' } };
    d.impact.forEach(function (f) {
      if (!confOk(f.c)) return;
      var col = style[f.g][f.c] || '#C9874E';
      var rings = f.p.map(function (part) { return part.map(function (ring) { return ring.map(ll); }); });
      G.glow.addLayer(L.polygon(rings, { renderer: canvas, color: '#FFD400', weight: 5, opacity: 0.55, fill: false, interactive: false }));
      var poly = L.polygon(rings, { renderer: canvas, color: col, weight: 1, fillColor: col, fillOpacity: f.c === 'low' ? 0.65 : 0.88 });
      poly.bindPopup(popup(f.t.charAt(0).toUpperCase() + f.t.slice(1), [['Confidence', f.c], ['Area', fmt(f.a, 2) + ' ha'],
        ['Seen by', f.g === 'optical' ? 'Sentinel-2 only' : (f.c === 'high' ? 'both radar orbits / radar + optical' : (f.c === 'medium' ? 'one radar orbit' : 'orbits disagree'))]]));
      (f.g === 'optical' ? G.impactOpt : (f.c === 'low' ? G.impactLo : G.impactHi)).addLayer(poly);
    });
    var order = { na: 0, ok: 1, detour: 2, cut: 3 };
    d.settlements.map(function (s, i) { return [s, i]; }).sort(function (a, b) { return order[statusOf(a[0])] - order[statusOf(b[0])]; }).forEach(function (si) {
      var s = si[0], i = si[1], st = statusOf(s), need = st === 'cut' || st === 'detour';
      var m = L.marker(ll([s.x, s.y]), { icon: icon('m-' + st + (S.sel === i ? ' m-sel' : '')), title: sname(s) + ': ' + STATUS[st], keyboard: true, riseOnHover: true,
                                          zIndexOffset: order[st] * 100 + (S.sel === i ? 2000 : 0) });
      m.bindTooltip(esc(sname(s)) + ' · ' + STATUS[st], { className: 'tip', direction: 'top', offset: [0, -10] });
      m.on('click', function () { select(i, false); });
      markersById[i] = m;
      (need || S.sel === i ? G.settle : G.others).addLayer(m);
    });
    buildLabels();
  }

  function buildLabels() {   // only the most important names, more as you zoom in; never piled on top of each other
    var d = D(); if (!d) return;
    G.labels.clearLayers();
    var z = map.getZoom(), minZ = map.getMinZoom(), n = z < minZ + 0.6 ? 5 : (z < minZ + 1.6 ? 10 : 22);
    var cand = d.settlements.map(function (s, i) { return [s, i]; }).filter(function (si) { var st = statusOf(si[0]); return (st === 'cut' || st === 'detour') && si[0].n; })
      .sort(function (a, b) { return b[0].b - a[0].b; });
    if (S.sel !== null && d.settlements[S.sel]) cand.unshift([d.settlements[S.sel], S.sel]);
    var placed = [], k = 0;
    cand.forEach(function (si) {
      if (k >= n && si[1] !== S.sel) return;
      var p = map.latLngToContainerPoint(ll([si[0].x, si[0].y]));
      var w = 8 + 7 * (si[0].n || '').length;
      if (placed.some(function (q) { return Math.abs(q.x - p.x) < (q.w + w) / 2 + 10 && Math.abs(q.y - p.y) < 22; })) return;
      placed.push({ x: p.x + w / 2, y: p.y, w: w }); k++;
      G.labels.addLayer(L.tooltip({ permanent: true, direction: 'right', offset: [9, 0], className: 'nm', interactive: false }).setLatLng(ll([si[0].x, si[0].y])).setContent(esc(si[0].n)));
    });
  }
  map.on('zoomend', function () { if (D()) { buildLabels(); updateScale(); } });

  function applyLayers() {
    var on = function (k, v) { if (v) { if (!map.hasLayer(G[k])) G[k].addTo(map); } else if (map.hasLayer(G[k])) map.removeLayer(G[k]); };
    on('base', true);
    on('minor', S.layers.minor); on('roads', S.layers.roads);
    on('glow', S.layers.impact || S.layers.optical);
    on('impactHi', S.layers.impact); on('impactLo', S.layers.impact); on('impactOpt', S.layers.optical);
    on('hit', S.layers.hit); on('flow', S.layers.flow); on('start', S.layers.flow);
    on('others', S.layers.others); on('settle', S.layers.settle); on('labels', S.layers.labels && S.layers.settle); on('health', S.layers.health);
    on('bridgesP', S.layers.bridgesP); on('bridges', S.layers.bridges); on('repairs', S.layers.repairs); on('crit', S.crit);
  }
  function baseUrl() { var d = D(); var b = (S.base === 'sat' && !d.sat) ? 'map' : S.base; return 'data/' + S.ev + '/' + b + '.jpg'; }

  // ------------------------------------------------------------------ scale bar
  function updateScale() {
    var d = D(); if (!d) return;
    var mpp = d.m_per_unit / Math.pow(2, map.getZoom());          // CRS.Simple: 1 map unit = 1 px at zoom 0
    var target = 110 * mpp, steps = [50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000], v = steps[0];
    steps.forEach(function (s) { if (s <= target) v = s; });
    var px = Math.round(v / mpp);
    $('scale').querySelector('b').style.width = px + 'px';
    $('scale').querySelector('span').textContent = v >= 1000 ? (v / 1000) + ' km' : v + ' m';
  }

  // ------------------------------------------------------------------ critical areas (rule-based, from the run's own numbers)
  function criticals() {
    var d = D(), out = [];
    var need = d.settlements.map(function (s, i) { return [s, i]; }).filter(function (si) { return statusOf(si[0]) === 'cut'; });
    need.filter(function (si) { return !si[0].foot; }).sort(function (a, b) { return b[0].b - a[0].b; }).slice(0, 3).forEach(function (si) {
      out.push({ kind: 'set', i: si[1], x: si[0].x, y: si[0].y, name: sname(si[0]),
                 why: 'Cut off, no road and no footpath · ~' + plural(si[0].b, 'building', 'buildings') + ' → needs air or river access' });
    });
    (d.bridges || []).filter(function (b) { return b.s === 'likely damaged' && b.k === 'vehicle' && MAIN[b.hw]; })
      .sort(function (a, b) { return (b.c === 'high') - (a.c === 'high') || b.deck - a.deck; }).slice(0, 1).forEach(function (b) {
        out.push({ kind: 'br', x: b.x, y: b.y, name: b.n || 'Bridge on ' + (HW[b.hw] || b.hw), why: 'Vehicle bridge on a ' + (HW[b.hw] || b.hw) + ' likely damaged (' + fmt(b.deck) + '% of deck impacted)' });
      });
    need.filter(function (si) { return si[0].foot; }).sort(function (a, b) { return b[0].b - a[0].b; }).slice(0, Math.max(0, 6 - out.length - 1)).forEach(function (si) {
      out.push({ kind: 'set', i: si[1], x: si[0].x, y: si[0].y, name: sname(si[0]), why: 'Cut off by road · ~' + plural(si[0].b, 'building', 'buildings') + ' · reachable on foot only' });
    });
    var r = (d.repairs || [])[0];
    if (r && out.length < 6) out.push({ kind: 'rep', x: r.x, y: r.y, name: 'Repair #1 · ' + (r.name === '(unnamed)' ? 'unnamed ' + (HW[r.hw] || r.hw) : r.name),
                                        why: 'Fix first: reconnects ' + plural(r.s, 'village', 'villages') + ' · ~' + fmt(r.b) + ' buildings' });
    return out;
  }
  function buildCrit() {
    G.crit.clearLayers();
    var list = criticals();
    $('critN').textContent = list.length;
    list.forEach(function (c, k) {
      G.crit.addLayer(L.marker(ll([c.x, c.y]), { icon: L.divIcon({ className: 'critring' + (S.crit && k === S.critI ? ' on' : ''), html: '<i></i>', iconSize: [54, 54], iconAnchor: [27, 27] }),
                                               interactive: false, zIndexOffset: 3000 }));
    });
    renderCritList(list);
    return list;
  }
  function renderCritList(list) {
    $('critList').innerHTML = '<div class="small muted" style="margin-bottom:6px">Picked by fixed rules from this run: cut off with no road and no footpath first, then the most-used damaged bridge, then the biggest villages reachable only on foot, then the most useful repair.</div>' +
      (list.length ? list.map(function (c, k) {
        return '<div class="crow"><span class="no">' + (k + 1) + '</span><div style="flex:1;min-width:0"><div style="font-weight:600">' + esc(c.name) + '</div><div class="small muted">' + esc(c.why) + '</div></div>' +
          '<button class="showbtn" type="button" data-crit="' + k + '">Show on map ↗</button></div>';
      }).join('') : '<div class="empty">Nothing critical with this confidence setting.</div>');
  }
  function critGo(k) {
    var list = buildCrit(); if (!list.length) return;
    S.critI = (k + list.length) % list.length; var c = list[S.critI];
    buildCrit();
    $('tour').innerHTML = '<span class="mono small" style="white-space:nowrap">' + (S.critI + 1) + ' / ' + list.length + '</span><span class="why"><b>' + esc(c.name) + '</b> — ' + esc(c.why) + '</span>' +
      '<button type="button" id="tPrev" aria-label="Previous critical area">◀</button><button type="button" id="tNext" aria-label="Next critical area">Next ▶</button><button type="button" id="tEnd" aria-label="Close critical areas">✕</button>';
    $('tour').classList.add('show');
    $('tPrev').onclick = function () { critGo(S.critI - 1); }; $('tNext').onclick = function () { critGo(S.critI + 1); }; $('tEnd').onclick = function () { setCrit(false); };
    map.flyTo(ll([c.x, c.y]), Math.max(map.getMinZoom() + 1.75, Math.min(map.getZoom(), 2)), { duration: 0.6 });
    if (c.kind === 'set') select(c.i, false, true);
  }
  function setCrit(on) {
    S.crit = on; $('btnCrit').setAttribute('aria-pressed', on);
    if (!on) { $('tour').classList.remove('show'); buildCrit(); applyLayers(); return; }
    showPage('map'); applyLayers(); critGo(0);
  }

  // ------------------------------------------------------------------ analysis page
  function renderKpis() {
    var k = D().kpi, hi = S.conf === 'hi';
    var tiles = [
      ['lav', 'Area hit', fmt(hi ? k.area_hi : k.area, 2) + ' km²', hi ? 'high confidence only · ' + fmt(k.area, 2) + ' km² all' : fmt(k.area_hi, 2) + ' km² high confidence', 'var(--debris-hi)'],
      ['lav', 'Buildings likely hit', fmt(hi ? k.bld_hi : k.bld), (hi ? fmt(k.bld) + ' with all detections' : fmt(k.bld_hi) + ' high confidence') + ' · of ' + fmt(k.osm_buildings) + ' mapped', 'var(--ink)'],
      ['lav', 'Roads affected', fmt(k.road_km, 1) + ' km', 'of ' + fmt(k.road_total) + ' km mapped', 'var(--road-hit)'],
      ['pnk', 'Bridges', fmt(k.br_likely) + ' likely', fmt(k.br_possibly) + ' possibly damaged · of ' + fmt(k.br_total), 'var(--bridge)'],
      ['pnk', 'Settlements cut off', fmt(hi ? k.cut_hi : k.cut), plural(hi ? k.detour_hi : k.detour, 'long detour', 'long detours') + ' · ' + (hi ? 'confident only' : 'all detections'), 'var(--cut)'],
      ['pnk', 'Buildings near cut-off', fmt(k.people), 'population proxy, not people · ' + fmt(k.foot) + ' reachable on foot', '#3B0F86']
    ];
    $('kpis').innerHTML = tiles.map(function (t) { return '<div class="bx kpi ' + t[0] + '" style="border-left-color:' + t[4] + '"><div class="lbl">' + t[1] + '</div><div class="kv" style="color:' + t[4] + '">' + t[2] + '</div><div class="ks">' + esc(t[3]) + '</div></div>'; }).join('');
  }
  function renderMenu() {
    var d = D();
    $('evName').textContent = d.name; $('evKind').textContent = d.kind + ' · ' + d.event;
    $('evBox').textContent = 'bbox ' + d.bbox.map(function (v) { return Number(v).toFixed(2); }).join(', ');
    $('mapTitle').textContent = d.name.split(',')[0] + ' · ' + d.event;
    $('attYear').textContent = 'Contains modified Copernicus Sentinel data ' + String(d.event).slice(0, 4) + '.';
    var tl = d.orbits.map(function (o) { return ['S1', 'p-hi', 'Radar orbit ' + o.orbit + ' ' + String(o.direction).toLowerCase(), o.pre + ' → ' + o.post + ' (+' + o.days_after_event + ' d)']; });
    if (d.s2 && d.s2.used) {
      var p0 = d.s2_dates.pre.slice().sort(), p1 = d.s2_dates.post.slice().sort();
      tl.push(['S2', 'p-md', 'Optical, clear pixels only (' + fmt(d.s2.valley_clear_both_dates_pct) + '% of valley)', p0[0] + '…' + p0[p0.length - 1] + ' → ' + p1[0] + '…' + p1[p1.length - 1]]);
    } else tl.push(['S2', 'p-lo', 'Optical: no clear image', 'radar only']);
    tl.push(['OSM', 'p-lo', 'OpenStreetMap before the event', 'snapshot ' + (d.osm_snapshot || '—')]);
    $('timeline').innerHTML = tl.map(function (t) { return '<div class="tl"><span class="pill ' + t[1] + '" style="height:20px">' + t[0] + '</span><div style="min-width:0"><div style="font-weight:600;font-size:13px">' + esc(t[2]) + '</div><div class="mono small muted">' + esc(t[3]) + '</div></div></div>'; }).join('');
    var ctx = false;
    $('layers').innerHTML = LAYERS.map(function (l) {
      var head = (l[3] === 'context' && !ctx) ? (ctx = true, '<div class="small muted" style="margin:6px 0 2px">Context (off by default, less clutter)</div>') : '';
      return head + '<button class="lay" type="button" data-layer="' + l[0] + '" aria-pressed="' + (S.layers[l[0]] ? 'true' : 'false') + '"><span class="tog"><i></i></span><span class="sw" style="background:' + l[2] + '"></span><span style="flex:1">' + l[1] + '</span></button>';
    }).join('');
    document.querySelectorAll('#events .btn').forEach(function (b) { b.classList.toggle('on', b.dataset.ev === S.ev); b.setAttribute('aria-pressed', b.dataset.ev === S.ev); });
    $('btnSitrep').href = $('btnSitrep2').href = 'sitrep.html?e=' + encodeURIComponent(S.ev);
    $('placeList').innerHTML = d.settlements.filter(function (s) { return s.n; }).map(function (s) { return '<option value="' + esc(s.n) + '">'; }).join('');
  }
  function renderQuick() {
    var d = D(), html = '', hint = '';
    document.querySelectorAll('.qtab').forEach(function (t) { t.setAttribute('aria-selected', t.dataset.q === S.q); });
    if (S.q === 'cut') {
      var list = d.settlements.map(function (s, i) { return [s, i]; }).filter(function (si) { var st = statusOf(si[0]); return st === 'cut' || st === 'detour'; })
        .sort(function (a, b) { return (statusOf(a[0]) === 'cut' ? 0 : 1) - (statusOf(b[0]) === 'cut' ? 0 : 1) || b[0].b - a[0].b; });
      hint = plural(list.filter(function (si) { return statusOf(si[0]) === 'cut'; }).length, 'village', 'villages') + ' cut off, ' + plural(list.filter(function (si) { return statusOf(si[0]) === 'detour'; }).length, 'detour', 'detours') + ' · click to go there';
      html = list.map(function (si) {
        var s = si[0], st = statusOf(s);
        return '<button class="qrow' + (S.sel === si[1] ? ' on' : '') + '" type="button" data-qs="' + si[1] + '"><div style="flex:1;min-width:0"><div class="nm2">' + esc(sname(s)) + '</div><div class="sub">' +
          (s.b ? '~' + fmt(s.b) + ' bldgs · ' : '') + 'on foot: ' + (s.foot ? 'yes' : 'no') + '</div></div><span class="pill p-' + st + '">' + (st === 'cut' ? 'Cut off' : 'Detour') + '</span></button>';
      }).join('') || '<div class="empty" style="margin:8px">Nobody cut off with this setting.</div>';
    } else if (S.q === 'rep') {
      hint = 'Fix these first · click to go there';
      html = d.repairs.map(function (r) {
        return '<button class="qrow' + (S.qSel === 'r' + r.rank ? ' on' : '') + '" type="button" data-qr="' + r.rank + '"><span class="rk">' + r.rank + '</span><div style="flex:1;min-width:0"><div class="nm2">' + esc(r.name === '(unnamed)' ? 'Unnamed ' + (HW[r.hw] || r.hw) : r.name) + '</div><div class="sub">+' + plural(r.s, 'village', 'villages') + ' · ~' + fmt(r.b) + ' bldgs · ' + fmt(r.m) + ' m</div></div></button>';
      }).join('') || '<div class="empty" style="margin:8px">No single repair reconnects anyone.</div>';
    } else {
      var brs = d.bridges.map(function (b, i) { return [b, i]; }).filter(function (x) { return x[0].s === 'likely damaged' || S.layers.bridgesP; })
        .sort(function (a, b) { return (a[0].s === b[0].s ? 0 : (a[0].s === 'likely damaged' ? -1 : 1)) || (b[0].k === 'vehicle') - (a[0].k === 'vehicle') || b[0].deck - a[0].deck; });
      hint = plural(brs.filter(function (x) { return x[0].s === 'likely damaged'; }).length, 'bridge', 'bridges') + ' likely damaged · vehicle bridges first';
      html = brs.map(function (x) {
        var b = x[0], lk = b.s === 'likely damaged';
        return '<button class="qrow' + (S.qSel === 'b' + x[1] ? ' on' : '') + '" type="button" data-qb="' + x[1] + '"><div style="flex:1;min-width:0"><div class="nm2">' + esc(b.n || 'Unnamed bridge') + '</div><div class="sub">' + esc(b.k) + ' · ' + esc(HW[b.hw] || b.hw || '') + ' · deck ' + fmt(b.deck) + '%</div></div><span class="pill ' + (lk ? 'p-hi' : 'p-lo') + '">' + (lk ? 'likely' : 'possibly') + '</span></button>';
      }).join('');
    }
    $('qHint').textContent = hint; $('qList').innerHTML = html;
  }
  var showBtn = function (attr, v) { return '<button class="showbtn" type="button" ' + attr + '="' + v + '">Show on map ↗</button>'; };
  function renderPane() {
    var d = D(), html = '';
    document.querySelectorAll('#tabs .tab').forEach(function (t) { t.setAttribute('aria-selected', t.dataset.tab === S.tab); });
    if (S.tab === 'cut') {
      var list = d.settlements.map(function (s, i) { return [s, i]; }).filter(function (si) { var st = statusOf(si[0]); return st === 'cut' || st === 'detour'; })
        .sort(function (a, b) { return b[0].b - a[0].b; });
      html = '<div style="display:flex;justify-content:space-between;align-items:baseline"><div class="h" style="font-size:16px">Settlements needing help</div><div class="mono small">' + list.length + ' listed</div></div>' +
        '<div class="small muted" style="margin:4px 0 6px">Cut off = no road to the nearest health facility or town after the flood; long detour = still reachable, but much slower. Sorted by buildings within 500 m (population proxy).</div>' +
        (list.length ? '<table class="t"><tr><th>Settlement</th><th>Status</th><th>Hospital: before → after</th><th>On foot</th><th class="num">Bldgs</th><th></th></tr>' + list.map(function (si) {
          var s = si[0], st = statusOf(s);
          return '<tr><td><b>' + esc(sname(s)) + '</b></td><td><span class="pill p-' + st + '">' + STATUS[st] + '</span></td><td class="mono small">' + esc(minutes(s)) + '</td><td>' + (s.foot ? 'yes' : 'no') +
            '</td><td class="num">' + (s.b ? fmt(s.b) : '—') + '</td><td>' + showBtn('data-sel', si[1]) + '</td></tr>';
        }).join('') + '</table>' : '<div class="empty">No settlement lost its road connection with this confidence setting.</div>');
    } else if (S.tab === 'rep') {
      var mx = Math.max.apply(null, [1].concat(d.repairs.map(function (r) { return r.s; })));
      html = '<div class="h" style="font-size:16px">Repair priority</div><div class="small muted" style="margin:4px 0 8px">Reopen one damaged road or bridge at a time: how many cut-off settlements get their road back?</div>' +
        (d.repairs.length ? d.repairs.map(function (r) {
          return '<div class="row"><span class="mono" style="width:30px;height:30px;border-radius:50%;background:var(--ink);color:#fff;display:inline-flex;align-items:center;justify-content:center;flex:none;font-weight:700">' + r.rank + '</span>' +
            '<div style="flex:1;min-width:0"><div style="font-weight:600;font-size:14px">' + esc(r.name === '(unnamed)' ? 'Unnamed ' + (HW[r.hw] || r.hw) : r.name) + (r.bridge ? ' (bridge)' : '') + ' · ' + fmt(r.m) + ' m</div><div class="small muted">' + esc(r.names) + '</div><div class="bar"><i style="width:' + Math.round(r.s / mx * 100) + '%"></i></div></div>' +
            '<div style="text-align:right;flex:none"><div class="h" style="font-size:18px">+' + r.s + '</div><div class="small muted">' + fmt(r.b) + ' bldgs</div></div>' + showBtn('data-rep', r.rank) + '</div>';
        }).join('') : '<div class="empty">No single repair reconnects a cut-off settlement.</div>');
    } else if (S.tab === 'br') {
      var brs = d.bridges.map(function (b, i) { return [b, i]; }).sort(function (a, b) { return (a[0].s === b[0].s) ? b[0].deck - a[0].deck : (a[0].s === 'likely damaged' ? -1 : 1); });
      var nl = brs.filter(function (x) { return x[0].s === 'likely damaged'; }).length;
      html = '<div class="h" style="font-size:16px">Bridges</div><div class="small muted" style="margin:4px 0 8px">Likely damaged = ≥ 50 % of the deck impacted and debris at both ends. Possibly = impact nearby only — check on the ground.</div>' +
        '<div class="seg" style="margin-bottom:8px"><span class="pill p-hi">' + nl + ' likely</span><span class="pill p-lo">' + (brs.length - nl) + ' possibly</span></div>' +
        '<table class="t"><tr><th>Bridge</th><th>Use</th><th>Grade</th><th class="num">Deck</th><th></th></tr>' + brs.map(function (x) {
          var b = x[0], lk = b.s === 'likely damaged';
          return '<tr><td>' + esc(b.n || '(unnamed)') + '</td><td>' + esc(b.k) + '</td><td><span class="pill ' + (lk ? (b.c === 'high' ? 'p-hi' : 'p-md') : 'p-lo') + '">' + (lk ? 'likely' : 'possibly') + '</span></td><td class="num">' + fmt(b.deck) + '%</td><td>' + showBtn('data-br', x[1]) + '</td></tr>';
        }).join('') + '</table>';
    } else if (S.tab === 'flow') {
      html = '<div class="h" style="font-size:16px">Flood path</div><div class="small muted" style="margin:4px 0 10px">Pick any point upstream: the path follows the steepest descent on the Copernicus DEM and lists settlements low beside the channel (≤ 1 km away, ≤ 50 m above it). This quick view uses settlement locations; the building-based list (code/pipeline/flow_path.py) is in the report.</div>' +
        '<button class="btn acc" type="button" id="traceBtn">Trace from a point on the map ↗</button>';
      var t = S.trace;
      if (t) {
        html += '<div class="stat3" style="margin:12px 0"><div><div class="lbl">Length</div><div class="h" style="font-size:19px">' + fmt(t.km, 1) + ' km</div></div><div><div class="lbl">Drop</div><div class="h" style="font-size:19px">' + fmt(t.drop) + ' m</div></div><div><div class="lbl">On damage</div><div class="h" style="font-size:19px">' + (t.pct === null ? '—' : fmt(t.pct) + '%') + '</div></div></div>' +
          '<div class="small" style="margin-bottom:6px">' + (t.pct === null ? '' : fmt(t.pct) + '% of the path runs within 100 m of mapped damage. ') + 'Snapped ' + fmt(t.snap) + ' m to the valley bottom.</div>' + profile(t) +
          (t.list.length ? '<table class="t" style="margin-top:8px"><tr><th>Settlement</th><th class="num">km</th><th class="num">m from river</th><th class="num">m above</th><th></th></tr>' + t.list.map(function (s) {
            return '<tr><td>' + (s.high ? '<span class="pill p-cut" style="margin-right:4px">high</span>' : '') + esc(s.n) + '</td><td class="num">' + fmt(s.km, 1) + '</td><td class="num">' + fmt(s.dist) + '</td><td class="num">' + fmt(s.h) + '</td><td>' + showBtn('data-sel', s.i) + '</td></tr>';
          }).join('') + '</table>' : '<div class="empty" style="margin-top:8px">No mapped settlement sits low beside this path.</div>');
      }
    } else if (S.tab === 'ev') {
      if (d.evidence) {
        var p0 = d.s2_dates.pre.slice().sort(), p1 = d.s2_dates.post.slice().sort();
        html = '<div class="h" style="font-size:16px">Sentinel-2 evidence</div><div class="small muted" style="margin:4px 0 10px">The place where radar and optical agree most strongly, before and after. Grey patches are cloud.</div>' +
          '<div class="ev"><figure><img src="data/' + S.ev + '/ev_before.jpg" alt="Sentinel-2 before the event"><figcaption class="mono">Before · ' + p0[0] + '…' + p0[p0.length - 1] + '</figcaption></figure>' +
          '<figure><img src="data/' + S.ev + '/ev_after.jpg" alt="Sentinel-2 after the event"><figcaption class="mono">After · ' + p1[0] + '…' + p1[p1.length - 1] + '</figcaption></figure>' +
          '<figure style="grid-column:1/-1"><img src="data/' + S.ev + '/ev_detected.jpg" alt="After image with detected damage outlined"><figcaption class="mono">Our detection outlined in magenta</figcaption></figure></div>' +
          '<div class="small" style="margin-top:8px">Radar + optical agree on ' + fmt(d.s2.radar_and_optical_agree_km2, 2) + ' km²; optical added ' + fmt(d.s2.optical_only_added_km2, 2) + ' km² radar missed.</div>';
      } else html = '<div class="empty">No clear Sentinel-2 image before and after this event — the map is radar only.</div>';
    } else if (S.tab === 'val') {
      var v = d.validation;
      if (v) {
        html = '<div class="h" style="font-size:16px">How good is it?</div><div class="small muted" style="margin:4px 0 10px">Scored against ' + esc(v.ref) + '. Used only for checking — never as an input.</div>' +
          '<table class="t"><tr><th>Check</th><th class="num">Precision</th><th class="num">Recall</th></tr>' + v.rows.map(function (r) { return '<tr><td>' + esc(r[0]) + '</td><td class="num">' + fmt(r[1] * 100) + '%</td><td class="num">' + fmt(r[2] * 100) + '%</td></tr>'; }).join('') + '</table>' +
          '<div class="lbl" style="margin-top:12px">By EMS area</div><table class="t"><tr><th>Area</th><th class="num">Area F1</th><th class="num">Buildings found</th></tr>' + v.aois.map(function (a) { return '<tr><td>' + esc(a[0]) + '</td><td class="num">' + fmt(a[1], 2) + '</td><td class="num">' + fmt(a[2] * 100) + '%</td></tr>'; }).join('') + '</table>' +
          '<div class="small" style="margin-top:10px">Precision = how much of what we flag is real. Recall = how much of the real damage we find. Narrow gorges are the weak spot; the wide valley (Bidur) scores best.</div>' +
          '<div class="small muted" style="margin-top:8px">Reference: European Union, Copernicus Emergency Management Service data.</div>';
      } else {
        html = '<div class="h" style="font-size:16px">How good is it?</div><div class="small" style="margin-top:6px">No official grading map is available for this area. Each detection was checked by eye against the Sentinel-2 before/after images (Evidence tab).</div>' +
          '<div class="small muted" style="margin-top:8px">Sentinel-2 saw ' + fmt(d.s2.valley_clear_both_dates_pct) + '% of the valley; radar and optical agree on ' + fmt(d.s2.radar_and_optical_agree_km2, 2) + ' km².</div>';
      }
    }
    $('pane').innerHTML = html;
  }

  function profile(t) {
    var w = 360, h = 90, p = t.prof, n = p.length;
    if (n < 2) return '';
    var zmin = Math.min.apply(null, p.map(function (q) { return q[1]; })), zmax = Math.max.apply(null, p.map(function (q) { return q[1]; }));
    var X = function (km) { return 4 + km / t.km * (w - 8); }, Y = function (z) { return h - 14 - (z - zmin) / Math.max(1, zmax - zmin) * (h - 24); };
    var path = p.map(function (q, i) { return (i ? 'L' : 'M') + X(q[0]).toFixed(1) + ' ' + Y(q[1]).toFixed(1); }).join('');
    var dots = t.list.map(function (s) { return '<circle cx="' + X(s.km).toFixed(1) + '" cy="' + Y(s.ch).toFixed(1) + '" r="3.5" fill="' + (s.high ? '#C8102E' : '#E07B00') + '"/>'; }).join('');
    return '<svg viewBox="0 0 ' + w + ' ' + h + '" width="100%" style="max-width:520px" role="img" aria-label="Elevation profile along the flood path"><path d="' + path + '" fill="none" stroke="#0B6BFF" stroke-width="2"/>' + dots +
      '<text x="4" y="10" font-size="10" fill="#4A3F63">' + fmt(zmax) + ' m</text><text x="4" y="' + (h - 2) + '" font-size="10" fill="#4A3F63">' + fmt(zmin) + ' m · ' + fmt(t.km, 1) + ' km</text></svg>';
  }

  // ------------------------------------------------------------------ selection: compact card in the map corner
  function select(i, fly, keepTour) {
    var d = D(), s = d.settlements[i];
    if (!s) return;
    S.sel = i; S.qSel = null; buildDynamic(); applyLayers(); renderQuick();
    if (!keepTour && S.crit) { /* keep the tour bar; selection is independent */ }
    var st = statusOf(s), fix = '';
    if (st === 'cut') {
      var r = d.repairs.filter(function (r) { return String(r.names).indexOf(s.n || '§') >= 0; })[0];
      fix = r ? 'Repair #' + r.rank + ' (' + (r.name === '(unnamed)' ? 'unnamed ' + (HW[r.hw] || r.hw) : r.name) + ', ' + fmt(r.m) + ' m) reconnects it.' :
                (s.foot ? 'No single road repair reconnects it — reachable on foot.' : 'No road repair reconnects it and no footpath — needs air or river access.');
    }
    var near = d.health.map(function (h) { return [h, Math.hypot(h.x - s.x, h.y - s.y) * d.m_per_unit]; }).sort(function (a, b) { return a[1] - b[1]; })[0];
    var kv = function (a, b) { return '<div class="kv2"><span>' + esc(a) + '</span><b>' + esc(b) + '</b></div>'; };
    var c = $('selcard');
    c.classList.remove('exp');
    c.innerHTML = '<div class="hd"><div style="min-width:0"><div class="h" style="font-size:17px">' + esc(sname(s)) + '</div><div class="small muted">' + esc((s.loc && s.loc !== s.n ? s.loc + ' · ' : '') + (s.pl || 'settlement')) + '</div></div>' +
      '<button class="x" type="button" id="selClose" aria-label="Close details">×</button></div>' +
      '<div style="margin:5px 0"><span class="pill p-' + st + '">' + STATUS[st] + '</span></div>' +
      kv('Hospital / town', minutes(s)) + kv('On foot', s.foot ? 'yes' : 'no') + kv('Buildings within 500 m', s.b ? fmt(s.b) : '—') +
      '<div class="more">' + kv('Road out of the area', s.out || '—') + (near ? kv('Nearest health facility', near[0].n + ' · ' + fmt(near[1] / 1000, 1) + ' km') : '') +
      (fix ? '<div class="small" style="margin-top:6px;color:#3B0F86;font-weight:600">' + esc(fix) + '</div>' : '') + '</div>' +
      '<div class="acts"><button type="button" id="selMore">More ▾</button><button type="button" id="selAna">Full list →</button></div>';
    c.classList.add('show');
    $('selClose').onclick = function () { S.sel = null; c.classList.remove('show'); buildDynamic(); applyLayers(); renderQuick(); };
    $('selMore').onclick = function () { var e = c.classList.toggle('exp'); this.textContent = e ? 'Less ▴' : 'More ▾'; };
    $('selAna').onclick = function () { S.tab = 'cut'; renderPane(); showPage('ana'); };
    if (fly) map.flyTo(ll([s.x, s.y]), Math.max(map.getZoom(), map.getMinZoom() + 1.75), { duration: 0.6 });
  }

  // ------------------------------------------------------------------ flood path (runs in the browser on the exported D8 grid)
  var FLOW = null;
  function flowGrid() {
    var d = D();
    if (FLOW && FLOW.ev === S.ev) return FLOW;
    var dec = function (b64) { var s = atob(b64), u = new Uint8Array(s.length); for (var i = 0; i < s.length; i++) u[i] = s.charCodeAt(i); return u; };
    var dir = dec(d.flow.dir), eb = dec(d.flow.elev), elev = new Int16Array(eb.buffer, 0, eb.length / 2);
    var w = d.flow.w, h = d.flow.h, cell = d.flow.cell, imp = new Uint8Array(w * h);
    d.impact.forEach(function (f) { f.p.forEach(function (part) { part.forEach(function (ring) { ring.forEach(function (q) {
      var c = Math.floor(q[0] / cell), r = Math.floor(q[1] / cell); if (c >= 0 && c < w && r >= 0 && r < h) imp[r * w + c] = 1; }); }); }); });
    FLOW = { ev: S.ev, dir: dir, elev: elev, w: w, h: h, cell: cell, imp: imp };
    return FLOW;
  }
  var STEP = [null, [-1, 0], [-1, 1], [0, 1], [1, 1], [1, 0], [1, -1], [0, -1], [-1, -1]];
  function trace(x, y) {
    var d = D(), F = flowGrid(), cm = d.m_per_unit * F.cell;
    var c0 = Math.floor(x / F.cell), r0 = Math.floor(y / F.cell);
    if (c0 < 0 || r0 < 0 || c0 >= F.w || r0 >= F.h) return null;
    var k = Math.ceil(600 / cm), best = [r0, c0], bz = Infinity;
    for (var r = Math.max(0, r0 - k); r <= Math.min(F.h - 1, r0 + k); r++) for (var c = Math.max(0, c0 - k); c <= Math.min(F.w - 1, c0 + k); c++) {
      if ((r - r0) * (r - r0) + (c - c0) * (c - c0) > k * k) continue;
      var z = F.elev[r * F.w + c]; if (z < bz) { bz = z; best = [r, c]; }
    }
    var snap = Math.hypot(best[0] - r0, best[1] - c0) * cm;
    var pts = [], seen = {}, rr = best[0], cc = best[1], steps = 0;
    while (rr >= 0 && cc >= 0 && rr < F.h && cc < F.w && !seen[rr * F.w + cc] && steps < 20000) {
      seen[rr * F.w + cc] = 1; pts.push([rr, cc]); steps++;
      var dcode = F.dir[rr * F.w + cc]; if (!dcode) break;
      rr += STEP[dcode][0]; cc += STEP[dcode][1];
    }
    var km = 0, prof = [], xy = [];
    pts.forEach(function (p, i) {
      if (i) km += Math.hypot(p[0] - pts[i - 1][0], p[1] - pts[i - 1][1]) * cm / 1000;
      prof.push([km, F.elev[p[0] * F.w + p[1]]]); xy.push([(p[1] + 0.5) * F.cell, (p[0] + 0.5) * F.cell]);
    });
    var nb = Math.max(1, Math.round(100 / cm)), onImp = 0;
    pts.forEach(function (p) { var hit = false; for (var a = -nb; a <= nb && !hit; a++) for (var b = -nb; b <= nb && !hit; b++) { var q = (p[0] + a) * F.w + (p[1] + b); if (q >= 0 && q < F.imp.length && F.imp[q]) hit = true; } if (hit) onImp++; });
    var list = [];
    d.settlements.forEach(function (s, i) {
      var bi = -1, bd = Infinity;
      xy.forEach(function (q, j) { var dd = Math.hypot(q[0] - s.x, q[1] - s.y); if (dd < bd) { bd = dd; bi = j; } });
      if (bi < 0) return;
      var dist = bd * d.m_per_unit, sc = Math.min(F.w - 1, Math.floor(s.x / F.cell)), sr = Math.min(F.h - 1, Math.floor(s.y / F.cell));
      var hgt = F.elev[sr * F.w + sc] - prof[bi][1];
      if (dist <= 1000 && hgt <= 50) list.push({ i: i, n: sname(s), km: prof[bi][0], dist: dist, h: Math.max(0, hgt), ch: prof[bi][1], high: dist <= 300 && hgt <= 15 });
    });
    list.sort(function (a, b) { return a.km - b.km; });
    return { xy: xy, km: km, drop: prof[0][1] - prof[prof.length - 1][1], prof: prof, list: list, snap: snap, pct: pts.length ? onImp / pts.length * 100 : null };
  }
  function showTrace(t) {
    G.flow.clearLayers(); G.start.clearLayers();
    if (!t) return;
    G.flow.addLayer(L.polyline(t.xy.map(ll), { color: '#ffffff', weight: 7, opacity: 0.9, interactive: false }));
    G.flow.addLayer(L.polyline(t.xy.map(ll), { color: '#0B6BFF', weight: 4, dashArray: '10 6', lineCap: 'round' })
      .bindPopup(popup('Flood path', [['Length', fmt(t.km, 1) + ' km'], ['Drop', fmt(t.drop) + ' m'], ['Settlements in corridor', t.list.length]])));
    G.start.addLayer(L.marker(ll(t.xy[0]), { icon: icon('m-start'), title: 'Flood path start' }));
    t.list.forEach(function (s) {
      var st = D().settlements[s.i];
      G.start.addLayer(L.circleMarker(ll([st.x, st.y]), { radius: 9, color: s.high ? '#C8102E' : '#E07B00', weight: 3, fill: false, interactive: false }));
    });
    S.layers.flow = true; applyLayers();
  }
  function flowCard(t) {
    var c = $('selcard'); c.classList.remove('exp');
    c.innerHTML = '<div class="hd"><div class="h" style="font-size:17px">Flood path</div><button class="x" type="button" id="selClose" aria-label="Close">×</button></div>' +
      '<div class="kv2"><span>Length</span><b>' + fmt(t.km, 1) + ' km</b></div><div class="kv2"><span>Drop</span><b>' + fmt(t.drop) + ' m</b></div>' +
      '<div class="kv2"><span>Villages low beside it</span><b>' + t.list.length + '</b></div><div class="kv2"><span>Runs over mapped damage</span><b>' + (t.pct === null ? '—' : fmt(t.pct) + '%') + '</b></div>' +
      '<div class="small muted" style="margin-top:4px">' + esc(t.list.slice(0, 6).map(function (s) { return s.n; }).join(' → ')) + (t.list.length > 6 ? ' …' : '') + '</div>' +
      '<div class="acts"><button type="button" id="selAna">Full list →</button></div>';
    c.classList.add('show');
    $('selClose').onclick = function () { c.classList.remove('show'); };
    $('selAna').onclick = function () { S.tab = 'flow'; renderPane(); showPage('ana'); };
  }
  function setTracing(on) {
    S.tracing = on; $('tracePick').setAttribute('aria-pressed', on); $('traceHint').classList.toggle('show', on);
    map.getContainer().style.cursor = on ? 'crosshair' : '';
  }
  map.on('click', function (e) {
    if (!S.tracing) return;
    var t = trace(e.latlng.lng, -e.latlng.lat);
    setTracing(false);
    if (!t) return;
    S.trace = t; showTrace(t); flowCard(t); if (S.tab === 'flow') renderPane();
  });

  // ------------------------------------------------------------------ pages, menu, sizing
  function showPage(p) {
    var was = S.page, fresh = S.fit;
    S.page = p;
    $('viewMap').classList.toggle('show', p === 'map'); $('viewAna').classList.toggle('show', p === 'ana'); $('viewNew').classList.toggle('show', p === 'new');
    document.querySelectorAll('.nav').forEach(function (b) { b.classList.toggle('on', b.dataset.page === p); });
    try { history.replaceState(null, '', '#e=' + S.ev + (p === 'map' ? '' : '&p=' + p)); } catch (e) {}
    if (p === 'map' && (was !== 'map' || fresh)) { map.invalidateSize(); lockToMap(fresh); S.fit = false; buildLabels(); updateScale(); }
    menu(false);
  }
  function menu(open) {
    $('drawer').classList.toggle('open', open); $('scrim').classList.toggle('show', open);
    $('burger').setAttribute('aria-expanded', open); $('drawer').setAttribute('aria-hidden', !open);
  }
  function lockToMap(refit) {   // zoom out only until the whole area fits; never pan off the map
    var d = D(); if (!d || !$('map').clientHeight) return;
    var b = L.latLngBounds([[-d.H, 0], [0, d.W]]);
    map.setMaxBounds(null); map.setMinZoom(-10);
    var z = map.getBoundsZoom(b, false, L.point(12, 12));
    map.setMinZoom(z);
    if (refit) {                                                       // open filled edge to edge (cover), snapped UP so no empty band
      var sz = map.getSize(), cov = Math.log(Math.max(sz.x / d.W, sz.y / d.H)) / Math.LN2;
      map.setView(b.getCenter(), Math.min(Math.ceil(cov * 4 - 1e-6) / 4, z + 2.5), { animate: false });
    }
    else if (map.getZoom() < z) map.fitBounds(b, { padding: [6, 6], animate: false });
    map.setMaxBounds(b);
    updateScale();
  }
  window.addEventListener('resize', function () { if (S.page === 'map') { map.invalidateSize(); lockToMap(false); } });

  function loadEvent(id, cb) {
    if (window.HR_DATA[id]) return cb();
    var sc = document.createElement('script'); sc.src = 'data/' + id + '/data.js';
    sc.onload = cb; sc.onerror = function () { $('kpis').innerHTML = '<div class="bx kpi"><div class="kv">Could not load ' + esc(id) + '</div><div class="ks">Re-run export_dashboard.py for this event.</div></div>'; };
    document.body.appendChild(sc);
  }
  function show(id, page) {
    loadEvent(id, function () {
      S.ev = id; S.sel = null; S.trace = null; S.crit = false; $('selcard').classList.remove('show'); $('tour').classList.remove('show'); $('btnCrit').setAttribute('aria-pressed', false);
      FLOW = null; S.fit = true;
      $('viewMap').classList.add('show');                       // the map must be visible once to size itself
      map.invalidateSize();
      buildStatic(); buildDynamic(); applyLayers(); buildCrit(); renderKpis(); renderMenu(); renderPane(); renderQuick();
      showPage(page || 'map');
    });
  }

  // ------------------------------------------------------------------ wiring
  $('events').innerHTML = EVENTS.map(function (e) { return '<button class="btn" type="button" data-ev="' + esc(e.id) + '"><span class="mono small" style="opacity:.75">' + esc(e.event) + '</span> ' + esc(e.short || e.name.split(',')[0]) + '</button>'; }).join('');
  $('events').addEventListener('click', function (e) { var b = e.target.closest('[data-ev]'); if (b && b.dataset.ev !== S.ev) show(b.dataset.ev, S.page === 'new' ? 'map' : S.page); });
  $('burger').onclick = function () { menu(!$('drawer').classList.contains('open')); };
  $('scrim').onclick = function () { menu(false); };
  document.querySelectorAll('.nav').forEach(function (b) { b.onclick = function () { showPage(b.dataset.page); }; });
  $('layers').addEventListener('click', function (e) { var b = e.target.closest('[data-layer]'); if (!b) return; S.layers[b.dataset.layer] = !S.layers[b.dataset.layer]; b.setAttribute('aria-pressed', S.layers[b.dataset.layer]); applyLayers(); renderQuick(); });
  var setConf = function (c) { S.conf = c; $('confAll').classList.toggle('on', c === 'all'); $('confHi').classList.toggle('on', c === 'hi'); $('confAll').setAttribute('aria-pressed', c === 'all'); $('confHi').setAttribute('aria-pressed', c === 'hi');
    var ce = map.getCenter(), z = map.getZoom();
    buildStatic(); map.setView(ce, z, { animate: false }); buildDynamic(); applyLayers(); buildCrit(); renderKpis(); renderPane(); renderQuick(); if (S.trace) showTrace(S.trace); if (S.sel !== null) select(S.sel, false); if (S.crit) critGo(S.critI); };
  $('confAll').onclick = function () { setConf('all'); }; $('confHi').onclick = function () { setConf('hi'); };
  document.querySelectorAll('.bm button').forEach(function (b) { b.onclick = function () {
    S.base = b.dataset.base; document.querySelectorAll('.bm button').forEach(function (x) { x.setAttribute('aria-pressed', x === b); });
    if (baseOverlay) baseOverlay.setUrl(baseUrl()); }; });
  $('zIn').onclick = function () { map.zoomIn(); }; $('zOut').onclick = function () { map.zoomOut(); };
  $('zFit').onclick = function () { var d = D(); map.flyToBounds([[-d.H, 0], [0, d.W]], { duration: 0.5 }); };
  $('tracePick').onclick = function () { setTracing(!S.tracing); };
  $('legBtn').onclick = function () { var o = $('legend').classList.toggle('show'); $('legBtn').setAttribute('aria-expanded', o); };
  $('legClose').onclick = function () { $('legend').classList.remove('show'); $('legBtn').setAttribute('aria-expanded', false); };
  $('btnCrit').onclick = function () { setCrit(!S.crit); };
  $('search').addEventListener('change', function () {
    var q = this.value.trim().toLowerCase(); if (!q) return;
    var d = D(), hit = -1;
    d.settlements.forEach(function (s, i) { if (hit < 0 && (s.n || '').toLowerCase() === q) hit = i; });
    if (hit < 0) d.settlements.forEach(function (s, i) { if (hit < 0 && (s.n || '').toLowerCase().indexOf(q) >= 0) hit = i; });
    if (hit >= 0) { showPage('map'); select(hit, true); this.blur(); } else { this.setCustomValidity('No village with that name in this area'); this.reportValidity(); var me = this; setTimeout(function () { me.setCustomValidity(''); }, 1500); }
  });
  document.querySelectorAll('.qtab').forEach(function (t) { t.onclick = function () { S.q = t.dataset.q; renderQuick(); }; });
  $('qList').addEventListener('click', function (e) {
    var d = D(), z = Math.max(map.getZoom(), map.getMinZoom() + 1.75);
    var t = e.target.closest('[data-qs]'); if (t) { select(+t.dataset.qs, true); return; }
    if (S.sel !== null) { S.sel = null; $('selcard').classList.remove('show'); buildDynamic(); applyLayers(); }
    var r = e.target.closest('[data-qr]'); if (r) { var rp = d.repairs.filter(function (x) { return x.rank === +r.dataset.qr; })[0]; S.qSel = 'r' + rp.rank; renderQuick();
      map.flyTo(ll([rp.x, rp.y]), z, { duration: 0.6 }); map.once('moveend', function () { G.repairs.eachLayer(function (m) { if (m.options.title === 'Repair priority ' + rp.rank) m.openPopup(); }); }); return; }
    var b = e.target.closest('[data-qb]'); if (b) { var br = d.bridges[+b.dataset.qb]; S.qSel = 'b' + b.dataset.qb; renderQuick();
      if (br.s !== 'likely damaged' && !S.layers.bridgesP) { S.layers.bridgesP = true; applyLayers(); renderMenu(); }
      map.flyTo(ll([br.x, br.y]), Math.max(z, map.getMinZoom() + 2), { duration: 0.6 });
      map.once('moveend', function () { [G.bridges, G.bridgesP].forEach(function (g) { g.eachLayer(function (m) { var p = m.getLatLng(); if (Math.abs(p.lat + br.y) < 0.01 && Math.abs(p.lng - br.x) < 0.01) m.openPopup(); }); }); }); return; }
  });
  $('qMore').onclick = function () { S.tab = S.q; renderPane(); showPage('ana'); };
  $('qToggle').onclick = function () { var off = $('viewMap').classList.toggle('noq'); this.textContent = off ? '▶' : '◀'; this.setAttribute('aria-label', off ? 'Show side list' : 'Hide side list'); map.invalidateSize(); };
  $('tabs').addEventListener('click', function (e) { var t = e.target.closest('[data-tab]'); if (t) { S.tab = t.dataset.tab; renderPane(); } });
  $('tabs').addEventListener('keydown', function (e) {
    if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
    var tabs = Array.prototype.slice.call(document.querySelectorAll('#tabs .tab')), i = tabs.indexOf(document.activeElement); if (i < 0) return;
    var n = tabs[(i + (e.key === 'ArrowRight' ? 1 : tabs.length - 1)) % tabs.length]; n.focus(); n.click();
  });
  // "Show on map" is the ONLY thing in the Analysis page that moves the map
  $('viewAna').addEventListener('click', function (e) {
    var t = e.target.closest('[data-sel]'); if (t) { showPage('map'); select(+t.dataset.sel, true); return; }
    var r = e.target.closest('[data-rep]'); if (r) { var rp = D().repairs.filter(function (x) { return x.rank === +r.dataset.rep; })[0]; if (rp) { showPage('map'); map.flyTo(ll([rp.x, rp.y]), Math.max(map.getZoom(), map.getMinZoom() + 1.75), { duration: 0.6 }); } return; }
    var b = e.target.closest('[data-br]'); if (b) { var br = D().bridges[+b.dataset.br]; showPage('map'); map.flyTo(ll([br.x, br.y]), Math.max(map.getZoom(), map.getMinZoom() + 2), { duration: 0.6 }); return; }
    var c = e.target.closest('[data-crit]'); if (c) { S.crit = true; $('btnCrit').setAttribute('aria-pressed', true); showPage('map'); applyLayers(); critGo(+c.dataset.crit); return; }
    if (e.target.closest('#traceBtn')) { showPage('map'); setTracing(true); }
  });
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape') return;
    if (S.tracing) setTracing(false); else if ($('drawer').classList.contains('open')) menu(false); else if (S.crit) setCrit(false);
  });

  // ------------------------------------------------------------------ new-analysis view
  var STEPS = [['Find image pairs', 'Sentinel-1 before/after from the same orbit, ascending + descending, coverage checked', '~1 min'],
    ['Process raw radar (ESA SNAP)', 'orbit file · noise removal · calibration · terrain correction · 10 m', '20–30 min'],
    ['Terrain masks', 'Copernicus DEM → slope + height above river (valley floor)', '2–4 min'],
    ['Detect water + debris', 'U-Net (Kuro Siwo) for water, adaptive change detection for debris, orbit fusion', '5–15 min'],
    ['Sentinel-2 (clear sky)', 'clearest dates, cloud-masked, added as extra evidence', '5–15 min'],
    ['Pre-event OpenStreetMap', 'buildings, roads, bridges, settlements, health facilities (ohsome)', '3–5 min'],
    ['Damage + bridges', 'what was hit, graded with confidence', '1–3 min'],
    ['Who is cut off', 'road network to the nearest hospital / town, footpaths, repair priority', '1–5 min'],
    ['Export to this dashboard', 'map layers, base maps, flood-path grid, situation report', '1 min']];
  $('steps').innerHTML = STEPS.map(function (s, i) { return '<div class="step"><div class="dot">' + (i + 1) + '</div><div style="flex:1;min-width:0"><div style="font-weight:600;font-size:14px">' + s[0] + '</div><div class="small muted">' + s[1] + '</div></div><div class="mono small muted" style="white-space:nowrap">' + s[2] + '</div></div>'; }).join('');
  var presets = EVENTS.map(function (e) { return { id: e.id, label: (e.short || e.name.split(',')[0]) + ' ' + String(e.event).slice(0, 4) }; }).concat([{ id: 'custom', label: 'Custom area' }]);
  $('presets').innerHTML = presets.map(function (p) { return '<button class="btn" type="button" data-preset="' + esc(p.id) + '">' + esc(p.label) + '</button>'; }).join('');
  function fillPreset(id) {
    document.querySelectorAll('#presets .btn').forEach(function (b) { b.classList.toggle('on', b.dataset.preset === id); });
    var set = function (b, ev) { $('fW').value = b[0]; $('fS').value = b[1]; $('fE').value = b[2]; $('fN').value = b[3]; $('fD').value = ev; };
    if (id === 'custom') { ['fW', 'fE', 'fS', 'fN', 'fD', 'fO'].forEach(function (k) { $(k).value = ''; }); $('checks').innerHTML = ''; $('cfg').textContent = '# fill the form and press "Check & create run settings"'; return; }
    loadEvent(id, function () { var d = window.HR_DATA[id]; set(d.bbox, d.event); $('fO').value = d.osm_snapshot || ''; check(); });
  }
  $('presets').addEventListener('click', function (e) { var b = e.target.closest('[data-preset]'); if (b) fillPreset(b.dataset.preset); });
  $('fD').addEventListener('change', function () { if ($('fD').value) { var t = new Date($('fD').value + 'T00:00:00Z'); t.setUTCDate(t.getUTCDate() - 1); $('fO').value = t.toISOString().slice(0, 10); } });
  function check() {
    var v = function (id) { return parseFloat(String($(id).value).replace(',', '.')); };
    var w = v('fW'), e = v('fE'), s = v('fS'), n = v('fN'), date = $('fD').value, osm = $('fO').value, errs = [], warns = [];
    ['fW', 'fE', 'fS', 'fN', 'fD', 'fO'].forEach(function (k) { $(k).setAttribute('aria-invalid', 'false'); });
    var bad = function (id, msg) { $(id).setAttribute('aria-invalid', 'true'); errs.push(msg); };
    if (isNaN(w) || w < -180 || w > 180) bad('fW', 'West must be a longitude between −180 and 180.');
    if (isNaN(e) || e < -180 || e > 180) bad('fE', 'East must be a longitude between −180 and 180.');
    if (isNaN(s) || s < -85 || s > 85) bad('fS', 'South must be a latitude between −85 and 85.');
    if (isNaN(n) || n < -85 || n > 85) bad('fN', 'North must be a latitude between −85 and 85.');
    if (!errs.length && w >= e) bad('fE', 'East must be greater than West.');
    if (!errs.length && s >= n) bad('fN', 'North must be greater than South.');
    if (!date) bad('fD', 'Enter the flood date.');
    else {
      if (date < '2014-10-03') bad('fD', 'Sentinel-1 data starts in October 2014 — pick a later flood.');
      var today = new Date().toISOString().slice(0, 10);
      if (date > today) bad('fD', 'The flood date is in the future.');
      else { var age = (Date.parse(today) - Date.parse(date)) / 864e5; if (age < 13) warns.push('The flood was ' + Math.round(age) + ' days ago: the after-image pair may not exist yet (Sentinel-1 repeats every 12 days).'); }
    }
    if (!osm) bad('fO', 'Enter the OSM snapshot date.'); else if (date && osm >= date) bad('fO', 'The OSM snapshot must be BEFORE the flood (data rules).');
    var km = 0;
    if (!errs.length) {
      var lat = (s + n) / 2, wkm = (e - w) * 111.32 * Math.cos(lat * Math.PI / 180), hkm = (n - s) * 110.57; km = wkm * hkm;
      if (wkm > 80 || hkm > 80) warns.push('Large area (' + fmt(wkm) + ' × ' + fmt(hkm) + ' km): expect several hours. Up to ~50 × 50 km is ideal.');
      if (wkm < 2 || hkm < 2) warns.push('Very small area: include the nearest hospital or town, or the cut-off check has no destination.');
      if (Math.floor((w + 180) / 6) !== Math.floor((e + 180) / 6)) warns.push('The area crosses a UTM zone border — supported, the map uses the zone of its centre.');
    }
    var html = errs.map(function (m) { return '<div class="err">✕ ' + esc(m) + '</div>'; }).join('') + warns.map(function (m) { return '<div class="small" style="color:#7A3E00;font-weight:600">! ' + esc(m) + '</div>'; }).join('');
    if (!errs.length) {
      var mins = Math.round(45 + km / 1500 * 20);
      html += '<div class="okmsg">✓ Ready — about ' + fmt(km) + ' km², roughly ' + fmt(mins) + ' min on Colab.</div>';
      $('cfg').textContent = 'BBOX = [' + [w, s, e, n].map(function (x) { return x.toFixed(4); }).join(', ') + ']   # W, S, E, N\nEVENT = "' + date + '"\nOSM_SNAPSHOT = "' + osm + '"   # must be before EVENT';
    } else $('cfg').textContent = '# fix the red fields first';
    $('checks').innerHTML = html;
    return !errs.length;
  }
  $('newForm').addEventListener('submit', function (e) { e.preventDefault(); check(); });
  $('copyCfg').onclick = function () {
    var txt = $('cfg').textContent;
    var done = function () { $('copied').textContent = 'Copied — paste into cell 1 of himraksha_full_run.ipynb'; };
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(txt).then(done, function () { $('copied').textContent = 'Select the text and copy it with Ctrl+C'; });
    else $('copied').textContent = 'Select the text and copy it with Ctrl+C';
  };
  $('btnNew').onclick = function () { showPage('new'); if (!$('fW').value) fillPreset(S.ev || 'custom'); };
  $('backDash').onclick = function () { showPage('map'); };

  // ------------------------------------------------------------------ start
  var hashEv = function () { return (location.hash.match(/e=([\w-]+)/) || [])[1]; };
  var hashPage = function () { return (location.hash.match(/p=(ana|new)/) || [])[1] || 'map'; };
  window.addEventListener('hashchange', function () { var h = hashEv(); if (h && h !== S.ev && EVENTS.some(function (e) { return e.id === h; })) show(h, hashPage()); });
  if (!EVENTS.length) { $('kpis').innerHTML = '<div class="bx kpi"><div class="kv">No runs exported yet</div><div class="ks">Run export_dashboard.py on a run folder.</div></div>'; return; }
  var start = hashEv();
  show(EVENTS.some(function (e) { return e.id === start; }) ? start : EVENTS[0].id, hashPage());
})();

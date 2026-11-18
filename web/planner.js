/* ================= ROUTE PLANNER =================
   Builds weekly beats per salesperson from shop locations, the org's visit time and
   working hours, and a visit frequency per revenue tier. Flags days that don't fit,
   auto-adjusts, sizes the team, and publishes beats as the Take order routes. */
const PLAN_DEFAULT = { visitMin:20, hours:8, breakMin:60, days:6, aMin:40000, bMin:20000, freqA:2, freqB:1, freqC:.5 };
const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const FREQ_LABEL = { 3:'3× a week', 2:'2× a week', 1:'Weekly', .5:'Every 2 weeks', .25:'Monthly' };
const REP_COLOR = ['#0E6B5C', '#D98E1F', '#3B5BA9', '#C2412D', '#8C6BB1', '#7A8F3A'];
S.planSet = { ...PLAN_DEFAULT };

const tierOf = (c, st) => c.aov >= st.aMin ? 'A' : c.aov >= st.bMin ? 'B' : 'C';
const freqOf = (t, st) => ({ A:st.freqA, B:st.freqB, C:st.freqC })[t];
const kmBetween = (a, b) => {
  const r = Math.PI / 180, dLat = (b.lat - a.lat) * r, dLng = (b.lng - a.lng) * r;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * r) * Math.cos(b.lat * r) * Math.sin(dLng / 2) ** 2;
  return 2 * 6371 * Math.asin(Math.sqrt(h)) * 1.3; // ×1.3 for roads vs straight line
};
const driveMin = km => (km <= 5 ? km * 3 : 15 + (km - 5) * 1.5) + (km > .05 ? 2 : 0); // 20 km/h in town, 40 km/h beyond, +2 min parking
const hm = m => { const t = Math.round(m); return `${Math.floor(t / 60)}h ${String(t % 60).padStart(2, '0')}m`; };
const planReps = () => [...REPS, ...(S.newReps || [])];
const repOf = c => (S.repMove || {})[c.id] || c.rep;

/* nearest-neighbour order starting from the godown */
function sequence(stops) {
  const left = stops.slice(), out = []; let cur = DEPOT;
  while (left.length) {
    let bi = 0, bd = Infinity;
    left.forEach((s, i) => { const d = kmBetween(cur, s.c); if (d < bd) { bd = d; bi = i; } });
    const s = left.splice(bi, 1)[0]; out.push({ ...s, km:bd, drive:driveMin(bd) }); cur = s.c;
  }
  return { stops:out, backKm: out.length ? kmBetween(cur, DEPOT) : 0 };
}

function buildPlan(st = S.planSet) {
  const cap = st.hours * 60 - st.breakMin, D = st.days;
  const reps = planReps().map(r => {
    const shops = CUSTOMERS.filter(c => repOf(c) === r.id && c.lat);
    if (!shops.length) return { r, days:[], weekMins:0, shops:0, util:0, visits:0 };
    const cLat = shops.reduce((a, c) => a + c.lat, 0) / shops.length, cLng = shops.reduce((a, c) => a + c.lng, 0) / shops.length;
    let list = shops.map(c => { const t = tierOf(c, st); return { c, t, f:freqOf(t, st), ang:Math.atan2(c.lat - cLat, c.lng - cLng) }; }).sort((a, b) => a.ang - b.ang);
    // start the sweep after the widest empty arc so each beat stays in one area
    let gi = 0, gw = -1;
    list.forEach((x, i) => { const g = ((list[(i + 1) % list.length].ang - x.ang) + 2 * Math.PI) % (2 * Math.PI); if (g > gw) { gw = g; gi = (i + 1) % list.length; } });
    list = list.slice(gi).concat(list.slice(0, gi));
    const total = list.reduce((a, x) => a + x.f, 0); let acc = 0;
    list.forEach((x, i) => { x.beat = Math.min(D - 1, Math.floor(acc / total * D)); acc += x.f; x.idx = i; const m = (S.beatMove || {})[x.c.id]; if (m !== undefined && m < D) x.beat = m; });
    const used = {};
    const days = Array.from({ length:D }, (_, d) => {
      const members = list.filter(x => x.beat === d), todays = [];
      list.forEach(x => {
        const every = x.f >= 1 ? 1 : Math.round(1 / x.f); // weeks between visits
        if (x.beat === d && (every === 1 || x.idx % every === 0)) todays.push({ ...x, extra:false });
        const n = Math.round(x.f);
        if (x.f >= 2) for (let k = 1; k < n; k++) if ((x.beat + Math.round(k * D / n)) % D === d) todays.push({ ...x, extra:true });
      });
      const seq = sequence(todays);
      const mins = seq.stops.reduce((a, s) => a + s.drive + st.visitMin, 0) + (seq.stops.length ? driveMin(seq.backKm) : 0);
      const cnt = {}; members.forEach(x => { cnt[x.c.place] = (cnt[x.c.place] || 0) + 1; });
      let name = Object.entries(cnt).sort((a, b) => b[1] - a[1]).slice(0, 2).map(e => e[0]).join(' – ') || 'No shops';
      used[name] = (used[name] || 0) + 1; if (used[name] > 1) name += ' ' + used[name];
      return { d, day:WEEKDAYS[d], name, members:members.map(x => x.c.id), seq, mins, cap, over:mins > cap,
        alt:members.filter(x => x.f < 1 && !todays.some(t => t.c.id === x.c.id)).length };
    });
    const weekMins = days.reduce((a, x) => a + x.mins, 0);
    return { r, days, weekMins, shops:shops.length, util:weekMins / (cap * D), visits:days.reduce((a, x) => a + x.seq.stops.length, 0) };
  });
  const weekMins = reps.reduce((a, x) => a + x.weekMins, 0);
  return { st, cap, reps, weekMins, need:weekMins / (cap * D), have:planReps().length, overDays:reps.flatMap(x => x.days.filter(d => d.over)),
    visits:reps.reduce((a, x) => a + x.visits, 0), shops:reps.reduce((a, x) => a + x.shops, 0) };
}

/* move shops off over-time days onto the same salesperson's lightest day, lowest tier first */
function autoAdjust() {
  S.beatMove = S.beatMove || {}; let moved = 0;
  for (let i = 0; i < 80; i++) {
    const plan = buildPlan(), over = plan.overDays.sort((a, b) => b.mins - a.mins)[0]; if (!over) break;
    const rp = plan.reps.find(x => x.days.includes(over));
    const light = rp.days.filter(d => d !== over).sort((a, b) => a.mins - b.mins)[0];
    if (!light || light.mins + plan.st.visitMin + 15 > plan.cap) break;
    const ls = light.seq.stops, lc = ls.length ? { lat:ls.reduce((a, s) => a + s.c.lat, 0) / ls.length, lng:ls.reduce((a, s) => a + s.c.lng, 0) / ls.length } : DEPOT;
    const cand = over.seq.stops.filter(s => !s.extra).sort((a, b) => 'CBA'.indexOf(a.t) - 'CBA'.indexOf(b.t) || kmBetween(a.c, lc) - kmBetween(b.c, lc))[0];
    if (!cand) break;
    S.beatMove[cand.c.id] = light.d; moved++;
  }
  return moved;
}

/* routes shown as chips on Take order = the signed-in salesperson's published beats */
function myRoutes() {
  if (typeof LIVE !== 'undefined' && LIVE.on) {
    const groups = new Map();
    CUSTOMERS.forEach(c => { const name = c.territory || 'Uncategorised';
      if (!groups.has(name)) groups.set(name, []); groups.get(name).push(c.id); });
    return [...groups].sort(([a], [b]) => a.localeCompare(b))
      .map(([name, cids]) => ({id:'group:' + name, name, cids}));
  }
  const plan = S.pubPlan || (S.pubPlan = buildPlan());
  const rp = plan.reps.find(x => x.r.id === ME) || plan.reps[0];
  return rp.days.map(d => ({ id:'r' + d.d, name:d.name, cids:[...new Set([...d.seq.stops.map(s => s.c.id), ...d.members])] })); // visit order first, then alternate-week shops
}
/* a party's route (beat) name from the published plan — shown instead of the street address */
function routeNameOf(cid) {
  if (typeof LIVE !== 'undefined' && LIVE.on) return C(cid)?.territory || 'Uncategorised';
  const plan = S.pubPlan || (S.pubPlan = buildPlan());
  if (!plan.rn) { plan.rn = {}; plan.reps.forEach(rp => rp.days.forEach(d => d.members.forEach(id => { plan.rn[id] = d.name; }))); }
  /* The planner's own beats need coordinates, which do not exist. Every
     shop does have a Tally route group, so fall back to that rather than
     claiming it has no route. */
  return plan.rn[cid] || (C(cid) || {}).territory || 'No route yet';
}
const todayBeat = () => 'r' + Math.min(4, S.planSet.days - 1); // Friday in the demo

/* ---------- map (inline SVG, projected from lat/lng) ---------- */
function mapSVG(pts, { w = 360, h = 230, path = null, depot = true } = {}) {
  const all = depot ? [...pts, { ...DEPOT, depot:true }] : pts;
  const la = all.map(p => p.lat), lo = all.map(p => p.lng), k = Math.cos(22.7 * Math.PI / 180);
  const minLa = Math.min(...la), maxLa = Math.max(...la), minLo = Math.min(...lo), maxLo = Math.max(...lo), pad = 18;
  const s = Math.min((w - 2 * pad) / (((maxLo - minLo) * k) || .01), (h - 2 * pad) / ((maxLa - minLa) || .01));
  const ox = (w - (maxLo - minLo) * k * s) / 2, oy = (h - (maxLa - minLa) * s) / 2;
  const X = p => (ox + (p.lng - minLo) * k * s).toFixed(1), Y = p => (oy + (maxLa - p.lat) * s).toFixed(1);
  const line = path ? `<polyline points="${[DEPOT, ...path, DEPOT].map(p => X(p) + ',' + Y(p)).join(' ')}" fill="none" stroke="var(--accent)" stroke-width="1.6" stroke-dasharray="4 3" opacity=".8"/>` : '';
  const dots = pts.map((p, i) => path ? `<circle cx="${X(p)}" cy="${Y(p)}" r="8" fill="var(--accent)"/><text x="${X(p)}" y="${+Y(p) + 3.5}" font-size="9" font-weight="700" text-anchor="middle" fill="var(--accent-ink)">${i + 1}</text>`
    : `<circle cx="${X(p)}" cy="${Y(p)}" r="3.2" fill="${p.color || 'var(--accent)'}" opacity=".85"/>`).join('');
  const dep = depot ? `<rect x="${+X(DEPOT) - 6}" y="${+Y(DEPOT) - 6}" width="12" height="12" rx="2" fill="var(--ink)"/>` : '';
  return `<svg class="pmap" viewBox="0 0 ${w} ${h}" role="img" aria-label="Map of shops">${line}${dots}${dep}</svg>`;
}

/* ---------- planner screen ---------- */
VIEWS.plan = {};
VIEWS.plan.home = () => {
  const st = S.planSet, plan = buildPlan(), short = Math.ceil(plan.need - .05) - plan.have;
  const tiers = { A:0, B:0, C:0 }; CUSTOMERS.forEach(c => { if (c.lat) tiers[tierOf(c, st)]++; });
  const overVisits = plan.overDays.reduce((a, d) => a + Math.ceil((d.mins - d.cap) / (st.visitMin + 6)), 0);
  const num = (k, label, unit) => `<label class="field"><span>${label}</span><input type="number" inputmode="numeric" value="${st[k]}" data-ch="pset" data-k="${k}" aria-label="${label}">${unit ? `<i class="unit">${unit}</i>` : ''}</label>`;
  const freq = (k, label) => `<label class="field"><span>${label}</span><select class="sel" data-ch="pset" data-k="${k}">${[3, 2, 1, .5, .25].map(f => `<option value="${f}" ${st[k] === f ? 'selected' : ''}>${FREQ_LABEL[f]}</option>`).join('')}</select></label>`;
  return `<header class="topbar"><div><h1>Route planner</h1><p class="sub">Beats built from shop locations, your visit time and how often each shop should be visited.</p></div>
    <div class="row"><button class="btn" data-a="preset">Reset</button><button class="btn primary" data-a="ppublish">${icon('send', 15)} Publish routes</button></div></header>
  <section class="kpi4">
    <div class="card kpi"><span class="k">Shops</span><span class="v num">${plan.shops}</span><span class="d">${tiers.A} A · ${tiers.B} B · ${tiers.C} C</span></div>
    <div class="card kpi"><span class="k">Visits a week</span><span class="v num">${plan.visits}</span><span class="d">${Math.round(plan.weekMins / 60)} hours of work</span></div>
    <div class="card kpi"><span class="k">Salespeople needed</span><span class="v num ${short > 0 ? 'crit' : ''}">${plan.need.toFixed(1)}</span><span class="d">You have ${plan.have}</span></div>
    <div class="card kpi"><span class="k">Days over time</span><span class="v num ${plan.overDays.length ? 'crit' : 'good'}">${plan.overDays.length}</span><span class="d">${hm(plan.cap)} working day</span></div>
  </section>
  ${plan.overDays.length || short > 0 ? `<div class="alert warn"><span class="ai">${icon('alert')}</span><div class="stack" style="gap:8px">
      <span>${plan.overDays.length ? `<b>${plan.overDays.length === 1 ? '1 day runs' : plan.overDays.length + ' days run'} past ${hm(plan.cap)}</b>, so about ${overVisits} ${overVisits === 1 ? 'visit' : 'visits'} won't happen. ` : ''}${short > 0 ? `The plan needs <b>${plan.need.toFixed(1)} salespeople</b>; you have ${plan.have}.` : ''}</span>
      <div class="row wrap">${plan.overDays.length ? `<button class="btn sm primary" data-a="padjust">Auto-adjust days</button>` : ''}
        ${st.freqC > .25 ? `<button class="btn sm" data-a="pcmonthly">Visit C shops monthly</button>` : ''}<button class="btn sm" data-a="paddrep">${icon('plus', 13)} Add a salesperson</button></div></div></div>`
    : `<div class="alert good"><span class="ai">${icon('check')}</span><span>Every shop fits into the week. Team is ${Math.round(plan.need / plan.have * 100)}% loaded.</span></div>`}
  <section class="card pad stack">
    <div class="section-h"><h2>Rules set by the business</h2></div>
    <div class="pset">${num('visitMin', 'Avg time per visit', 'min')}${num('hours', 'Working hours a day', 'hrs')}${num('breakMin', 'Break', 'min')}
      <label class="field"><span>Working days</span><select class="sel" data-ch="pset" data-k="days">${[5, 6].map(n => `<option value="${n}" ${st.days === n ? 'selected' : ''}>${n} days a week</option>`).join('')}</select></label>
      ${num('aMin', 'A shop: sales above', '₹/mo')}${num('bMin', 'B shop: sales above', '₹/mo')}
      ${freq('freqA', 'Visit A shops')}${freq('freqB', 'Visit B shops')}${freq('freqC', 'Visit C shops')}</div>
  </section>
  <section class="card pad stack"><div class="section-h"><h2>Shop map</h2><span class="aside">Coloured by salesperson · ■ godown</span></div>
    ${mapSVG(CUSTOMERS.filter(c => c.lat).map(c => ({ lat:c.lat, lng:c.lng, color:REP_COLOR[planReps().findIndex(r => r.id === repOf(c))] })))}
    <div class="row wrap" style="gap:12px;font-size:12px">${planReps().map((r, i) => `<span class="row" style="gap:6px"><i class="sw" style="background:${REP_COLOR[i]}"></i>${r.name}</span>`).join('')}</div></section>
  ${plan.reps.map((rp, i) => `<section class="card pad stack prep">
    <div class="row between"><div class="row" style="gap:10px"><span class="av" style="background:${REP_COLOR[i]};color:#fff">${rp.r.ini}</span>
      <div><b>${rp.r.name}</b><div class="faint" style="font-size:12px">${rp.r.territory} · ${rp.shops} shops · ${rp.visits} visits a week</div></div></div>
      <span class="chip ${rp.util > 1 ? 'crit' : rp.util > .9 ? 'warn' : 'good'}">${Math.round(rp.util * 100)}% loaded</span></div>
    <div class="pdays">${rp.days.map(d => `<button class="pday ${d.over ? 'over' : ''}" data-a="pday" data-r="${rp.r.id}" data-d="${d.d}">
      <span class="pd-d">${d.day}</span><span class="pd-n">${d.name}</span><span class="pd-t ${d.over ? 'crit' : ''}">${hm(d.mins)}</span>
      <span class="pd-s">${d.seq.stops.length} shops${d.alt ? ` · +${d.alt} alternate week` : ''}</span>
      <span class="pd-bar"><i style="width:${Math.min(d.mins / d.cap, 1.3) / 1.3 * 100}%"></i><em style="left:${100 / 1.3}%"></em></span></button>`).join('')}</div>
  </section>`).join('')}`;
};

/* one day's beat in a bottom sheet: map, stops in order with ETAs, move a shop to another day */
A.pday = d => {
  const plan = buildPlan(), rp = plan.reps.find(x => x.r.id === d.r), day = rp.days[+d.d];
  let t = 9 * 60 + 30;
  const rows = day.seq.stops.map((s, i) => { t += s.drive; const eta = `${Math.floor(t / 60)}:${String(Math.round(t % 60)).padStart(2, '0')}`; t += plan.st.visitMin;
    return `<div class="stoprow"><span class="sn">${i + 1}</span><div style="min-width:0"><b style="font-size:13.5px">${s.c.name}</b>
      <div class="faint" style="font-size:11.5px">${s.c.place} · ${s.km.toFixed(1)} km · ETA ${eta}${s.extra ? ' · 2nd visit' : ''}</div></div>
      <div class="row" style="gap:6px"><span class="tier ${s.t}">${s.t}</span>${s.extra ? '' : `<select class="sel sm" data-ch="pmove" data-cid="${s.c.id}" aria-label="Move ${esc(s.c.name)} to another day">${rp.days.map(o => `<option value="${o.d}" ${o.d === day.d ? 'selected' : ''}>${o.day}</option>`).join('')}</select>`}</div></div>`; }).join('');
  drawer(`<div class="drawer-h"><div><div class="eyebrow">${rp.r.name} · ${day.day}</div><h2 style="font-size:19px;margin-top:4px">${day.name}</h2>
      <p class="faint" style="font-size:12.5px">${day.seq.stops.length} shops · ${hm(day.mins)} of ${hm(day.cap)}${day.over ? ' · over time' : ''}</p></div>
      <button class="btn ghost sm" data-a="close" aria-label="Close">${icon('x')}</button></div>
    <div class="drawer-b">${mapSVG(day.seq.stops.map(s => s.c), { path:day.seq.stops.map(s => s.c) })}<div>${rows || '<p class="faint">No shops on this day.</p>'}</div></div>`);
};
A.pmove = (d, el) => { S.beatMove = S.beatMove || {}; S.beatMove[d.cid] = +el.value; closeOverlay(); render(); toast(`${C(d.cid).name} moved to ${WEEKDAYS[+el.value]}`); };
A.pset = (d, el) => { const v = parseFloat(el.value); if (!(v > 0)) { toast('Enter a number above zero', 'warn'); render(); return; } S.planSet[d.k] = v; S.beatMove = {}; render(); };
A.padjust = () => { const n = autoAdjust(), left = buildPlan().overDays.length; render(); toast(n ? `Moved ${n} shops to lighter days${left ? ` · ${left} days still over` : ' · every day fits'}` : 'No lighter day has room; add a salesperson or visit C shops less often', n && !left ? '' : 'warn'); };
A.pcmonthly = () => { S.planSet.freqC = .25; S.beatMove = {}; render(); toast('C shops now visited monthly'); };
A.paddrep = () => {
  const plan = buildPlan(), worst = plan.reps.slice().sort((a, b) => b.util - a.util)[0], n = (S.newReps || []).length + 1;
  const id = 's' + (REPS.length + n);
  S.newReps = [...(S.newReps || []), { id, name:`New salesperson ${n}`, ini:'N' + n, territory:worst.r.territory }];
  const shops = CUSTOMERS.filter(c => repOf(c) === worst.r.id).sort((a, b) => kmBetween(b, DEPOT) - kmBetween(a, DEPOT));
  S.repMove = S.repMove || {}; shops.slice(0, Math.floor(shops.length / 2)).forEach(c => { S.repMove[c.id] = id; }); S.beatMove = {};
  render(); toast(`Added a salesperson for ${worst.r.territory} · took ${Math.floor(shops.length / 2)} shops from ${worst.r.name}`);
};
A.preset = () => { S.planSet = { ...PLAN_DEFAULT }; S.beatMove = {}; S.repMove = {}; S.newReps = []; render(); toast('Plan reset to defaults'); };
A.ppublish = () => { S.pubPlan = buildPlan(); toast(`Routes published to ${planReps().length} salespeople · Take order now shows the new beats`); };

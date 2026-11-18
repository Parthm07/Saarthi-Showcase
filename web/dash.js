/* ================= SALES DASHBOARD =================
   Built on the app's own orders (last 3 months): date filters, sales by route and product,
   week on week, and a party-wise analysis. Daily charts show 7 days at a time. */
VIEWS.dash = {};
const DRANGES = [['today', 'Today'], ['7', 'Last 7 days'], ['month', 'This month'], ['30', 'Last 30 days'], ['90', 'Last 3 months'], ['custom', 'Custom']];
const isoOf = n => { const d = dAgo(n); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; };
const agoOf = iso => Math.max(0, Math.min(MAXDAY, Math.round((TODAY - new Date(iso + 'T00:00:00')) / 864e5)));
/* selected range as [newest, oldest] in days-ago */
function dRange() {
  const k = S.drange || 'month';
  if (k === 'today') return [0, 0]; if (k === '7') return [0, 6]; if (k === 'month') return [0, TODAY.getDate() - 1];
  if (k === '30') return [0, 29]; if (k === '90') return [0, MAXDAY];
  const a = S.dfrom ?? 13, b = S.dto ?? 0; return [Math.min(a, b), Math.max(a, b)];
}
const within = ([lo, hi]) => x => x.days >= lo && x.days <= hi;
const prevRange = ([lo, hi]) => [hi + 1, hi + 1 + (hi - lo)];
function tally(list) {
  const orders = new Set(), parties = new Set(); let value = 0, qty = 0;
  list.forEach(x => { value += x.value; qty += x.qty; orders.add(x.invoiceGuid || x.cid + '|' + x.days); parties.add(x.cid); });
  return { value, qty, orders:orders.size, parties:parties.size };
}
const groupBy = (list, key) => { const g = {}; list.forEach(x => { const k = key(x); (g[k] = g[k] || []).push(x); }); return g; };
const pct = (a, b) => b ? (a - b) / b * 100 : null;
const chg = v => v === null ? '<span class="faint">—</span>' : `<span class="${v < 0 ? 'crit' : 'good'}">${v < 0 ? '▼' : '▲'} ${Math.abs(v).toFixed(0)}%</span>`;
const rangeLabel = ([lo, hi]) => lo === hi ? dstr(dAgo(lo)) : `${dayNum(hi)} – ${dayNum(lo)}`;
const dtile = (k, v, d) => `<div class="card kpi"><span class="k">${k}</span><span class="v num">${v}</span><span class="d">${d}</span></div>`;

VIEWS.dash.home = () => {
  const r = dRange(), cur = SALES.filter(within(r)), prv = SALES.filter(within(prevRange(r))), a = tally(cur), b = tally(prv), tab = S.dtab || 'overview';
  return `<header class="topbar"><div><h1>Sales dashboard</h1><p class="sub">${rangeLabel(r)} · from your orders</p></div></header>
  ${typeof LIVE !== 'undefined' && LIVE.on && LIVE.status?.detail_retention_days
    ? `<p class="faint">Detailed charts cover retained invoice details (normally ${LIVE.status.detail_retention_days} days). Older daily totals are retained separately.</p>` : ''}
  <div class="catchips" role="tablist" aria-label="Date range">${DRANGES.map(([k, l]) => `<button class="${(S.drange || 'month') === k ? 'on' : ''}" data-a="drange" data-k="${k}">${l}</button>`).join('')}</div>
  ${S.drange === 'custom' ? `<div class="drow2"><label class="field"><span>From</span><input type="date" min="${isoOf(MAXDAY)}" max="${isoOf(0)}" value="${isoOf(r[1])}" data-ch="dfrom"></label>
    <label class="field"><span>To</span><input type="date" min="${isoOf(MAXDAY)}" max="${isoOf(0)}" value="${isoOf(r[0])}" data-ch="dto"></label></div>` : ''}
  <div class="kpi4">${dtile('Sales', inrS(a.value), `${chg(pct(a.value, b.value))} vs previous`)}${dtile('Orders', a.orders, `${chg(pct(a.orders, b.orders))} vs previous`)}
    ${dtile('Avg order', a.orders ? inr(a.value / a.orders) : '—', 'per order')}${dtile('Parties billed', a.parties, `of ${CUSTOMERS.length} parties`)}</div>
  <div class="tabs2" role="tablist">${[['overview', 'Overview'], ['routes', 'Routes'], ['products', 'Products'], ['parties', 'Parties']].map(([k, l]) => `<button class="${tab === k ? 'on' : ''}" data-a="dtab" data-k="${k}" role="tab" aria-selected="${tab === k}">${l}</button>`).join('')}</div>
  ${({ overview:dOverview, routes:dRoutes, products:dProducts, parties:dParties })[tab](r, cur, prv)}`;
};

/* ---------- overview ---------- */
function dOverview(r, cur, prv) {
  const end = r[0];
  const daily = weekRange('dsales', end).map(d => ({ day:d, label:dayLabel(d), sub:dayNum(d), a:tally(SALES.filter(x => x.days === d)).value }));
  const weeks = Array.from({ length:6 }, (_, k) => { const lo = end + (5 - k) * 7; return { lo, hi:lo + 6, v:tally(SALES.filter(within([lo, lo + 6]))).value }; });
  const wk = weeks.map((w, i) => { const p = i ? pct(w.v, weeks[i - 1].v) : null;
    return { a:w.v, w, label:dayNum(w.hi), sub:p === null ? '' : `${p < 0 ? '▼' : '▲'}${Math.abs(p).toFixed(0)}%`, subColor:p < 0 ? 'var(--crit)' : 'var(--good)' }; });
  return `<div class="card pad stack"><div class="section-h"><h2 style="font-size:14.5px">Daily sales</h2>${weekNav('dsales', end)}</div>
      ${selBars('dsales', daily, { fmt:inrS, cap:x => `${dstr(dAgo(x.day))} · <b>${inr(x.a)}</b>` })}</div>
    <div class="card pad stack"><div class="section-h"><h2 style="font-size:14.5px">Week on week</h2><span class="aside">6 weeks · change vs week before</span></div>
      ${selBars('wow', wk, { color:'var(--gold-strong)', fmt:inrS, cap:x => `Week of ${dayNum(x.w.hi)} – ${dayNum(x.w.lo)} · <b>${inr(x.a)}</b>${x.sub ? ` · ${x.sub} on the week before` : ''}` })}</div>
    <div class="card pad stack"><div class="section-h"><h2 style="font-size:14.5px">Sales by route</h2><button class="link" data-a="dtab" data-k="routes">All routes</button></div>${routeBars(routeRows(cur, prv).slice(0, 6))}</div>
    <div class="card pad stack"><div class="section-h"><h2 style="font-size:14.5px">Top products</h2><button class="link" data-a="dtab" data-k="products">All products</button></div>
      <div class="plist">${productRows(cur, prv).slice(0, 5).map((x, i) => prodRow(x, i)).join('') || '<p class="faint">No sales in this period.</p>'}</div></div>`;
}

/* ---------- routes ---------- */
function routeRows(cur, prv) {
  const tot = tally(cur).value || 1, g = groupBy(cur, x => routeNameOf(x.cid)), gp = groupBy(prv, x => routeNameOf(x.cid));
  return Object.entries(g).map(([k, l]) => { const s = tally(l); return { k, ...s, share:s.value / tot, ch:pct(s.value, tally(gp[k] || []).value) }; }).sort((a, b) => b.value - a.value);
}
function routeBars(rows) {
  const max = rows[0] ? rows[0].value : 1;
  return `<div class="hbars">${rows.map(x => `<div class="hb"><div class="row between" style="gap:10px"><span class="hl">${x.k}</span><span class="num"><b>${inrS(x.value)}</b> <span class="faint">${Math.round(x.share * 100)}%</span></span></div>
    <div class="hbar"><i style="width:${x.value / max * 100}%"></i></div></div>`).join('') || '<p class="faint">No sales in this period.</p>'}</div>`;
}
function dRoutes(r, cur, prv) {
  const rows = routeRows(cur, prv);
  return `<div class="card pad stack"><h2 style="font-size:14.5px">Sales split by route</h2>${routeBars(rows)}</div>
  <div class="card" style="overflow:hidden"><div class="table-wrap"><table class="t dt"><thead><tr><th>Route</th><th class="r">Sales</th><th class="r">Orders</th><th class="r">Parties</th><th class="r">Change</th></tr></thead><tbody>
    ${rows.map(x => `<tr><td>${x.k}</td><td class="r num">${inrS(x.value)}</td><td class="r num">${x.orders}</td><td class="r num">${x.parties}</td><td class="r num">${chg(x.ch)}</td></tr>`).join('')}</tbody></table></div></div>`;
}

/* ---------- products ---------- */
function productRows(cur, prv) {
  const tot = tally(cur).value || 1, g = groupBy(cur, x => x.p), gp = groupBy(prv, x => x.p);
  return Object.entries(g).map(([p, l]) => { const s = tally(l); return { p, ...s, share:s.value / tot, ch:pct(s.value, tally(gp[p] || []).value) }; }).sort((a, b) => b.value - a.value);
}
function prodRow(x, i, forParty = false) {
  const p = P(x.p);
  return `<div class="prow2"><span class="rank num">${i + 1}</span><div class="pimg xs">${art(p.id)}</div>
    <div class="pr-b"><div class="row between" style="gap:8px"><b>${p.name}</b><b class="num">${inrS(x.value)}</b></div>
      <div class="hbar"><i style="width:${Math.max(x.share * 100, 2)}%"></i></div>
      <div class="row between faint" style="font-size:11.5px;gap:8px"><span>${x.qty} ${(P(x.p) || {}).unit || 'qty'}${forParty ? ` · ${x.orders} orders` : ` · ${x.parties} parties`} · ${Math.round(x.share * 100)}% of sales</span>${forParty ? '' : chg(x.ch)}</div></div></div>`;
}
function dProducts(r, cur, prv) {
  const cat = S.dcat || 'all', f = x => cat === 'all' || PCAT[x.p] === cat, rows = productRows(cur.filter(f), prv.filter(f));
  return `<div class="catchips">${CATS.map(([k, l]) => `<button class="${cat === k ? 'on' : ''}" data-a="dcat" data-k="${k}">${l}</button>`).join('')}</div>
  <div class="card pad"><div class="plist">${rows.map((x, i) => prodRow(x, i)).join('') || '<p class="faint">No sales in this period.</p>'}</div></div>`;
}

/* ---------- parties ---------- */
function partyListD(r) {
  const q = (S.dpq || '').toLowerCase().trim(), cur = SALES.filter(within(r)), g = groupBy(cur, x => x.cid), gp = groupBy(SALES.filter(within(prevRange(r))), x => x.cid);
  const rows = Object.entries(g).map(([cid, l]) => { const s = tally(l); return { c:C(cid), ...s, last:Math.min(...l.map(x => x.days)), ch:pct(s.value, tally(gp[cid] || []).value) }; })
    .filter(x => !q || x.c.name.toLowerCase().includes(q)).sort((a, b) => b.value - a.value);
  return rows.length ? rows.map(x => `<button class="party" data-a="dpty" data-cid="${x.c.id}"><span class="av">${ini(x.c.name)}</span>
    <span class="pinfo"><b>${x.c.name}</b><span class="faint">${routeNameOf(x.c.id)} · ${x.orders} ${x.orders === 1 ? 'order' : 'orders'} · last ${x.last ? x.last + 'd ago' : 'today'}</span></span>
    <span class="due"><b class="num">${inrS(x.value)}</b><i>${chg(x.ch)}</i></span><span class="faint">${icon('arrow', 15)}</span></button>`).join('')
    : '<div class="empty">No party bought in this period.</div>';
}
function dParties(r) {
  return `<div class="search"><span class="ic">${icon('search')}</span><input placeholder="Search party" value="${esc(S.dpq || '')}" data-in="dpq" aria-label="Search party"></div>
  <p class="faint" style="font-size:12.5px">Ranked by sales in this period · tap a party for its 3-month analysis</p>
  <div class="card list" id="dplist">${partyListD(r)}</div>`;
}
VIEWS.dash.party = ({ cid }) => {
  const c = C(cid); if (!c) return VIEWS.dash.home();
  const n = S.pmon || 3, span = Math.min(MAXDAY, n * 30), list = SALES.filter(x => x.cid === cid && x.days <= span), a = tally(list);
  const vis = VISITS.filter(v => v.cid === cid && v.days <= span), weeks = Math.ceil((span + 1) / 7);
  const wk = Array.from({ length:weeks }, (_, k) => { const lo = (weeks - 1 - k) * 7, s = tally(list.filter(within([lo, lo + 6])));
    return { a:s.value, orders:s.orders, w:{ lo, hi:lo + 6 }, label:`${dAgo(lo + 6).getDate()}/${dAgo(lo + 6).getMonth() + 1}` }; });
  const orders = Object.entries(groupBy(list, x => x.days)).sort((x, y) => x[0] - y[0]).slice(0, 8);
  return `${ohead('home', c.name, `${routeNameOf(cid)} · ${R(c.rep).name}`)}
  <div class="catchips">${[1, 2, 3].map(m => `<button class="${n === m ? 'on' : ''}" data-a="pmon" data-k="${m}">Last ${m} month${m > 1 ? 's' : ''}</button>`).join('')}</div>
  <div class="kpi4">${dtile('Sales', inrS(a.value), `${inr(a.value / n)} a month`)}${dtile('Orders', a.orders, a.orders ? `${Math.round(span / a.orders)} days apart` : 'none')}
    ${dtile('Avg order', a.orders ? inr(a.value / a.orders) : '—', 'per order')}${dtile('Visits', vis.length, `${vis.filter(v => v.outcome === 'order').length} gave an order`)}</div>
  <div class="card pad stack"><h2 style="font-size:14.5px">Weekly purchases</h2>
    ${selBars('pweek', wk, { fmt:inrS, cap:x => `Week of ${dayNum(x.w.hi)} – ${dayNum(x.w.lo)} · <b>${inr(x.a)}</b> · ${x.orders} ${x.orders === 1 ? 'order' : 'orders'}` })}</div>
  <div class="card pad stack"><h2 style="font-size:14.5px">What they buy</h2><div class="plist">${productRows(list, []).map((x, i) => prodRow(x, i, true)).join('') || '<p class="faint">No orders in this period.</p>'}</div></div>
  <div class="card" style="overflow:hidden"><div class="pad" style="padding-bottom:4px"><h2 style="font-size:14.5px">Recent orders</h2></div>
    ${orders.map(([d, l]) => `<div class="payrow"><span class="num">${dstr(dAgo(+d))} · ${l.length} items</span><b class="num">${inr(tally(l).value)}</b></div>`).join('') || '<div class="empty">No orders in this period.</div>'}</div>`;
};

A.drange = d => { S.drange = d.k; if (d.k === 'custom' && S.dfrom == null) { S.dfrom = 13; S.dto = 0; } S.cw.dsales = 0; S.cs = {}; render(); };
A.dfrom = (d, el) => { S.dfrom = agoOf(el.value); S.cw.dsales = 0; render(); };
A.dto = (d, el) => { S.dto = agoOf(el.value); S.cw.dsales = 0; render(); };
A.dtab = d => { S.dtab = d.k; render(); };
A.dcat = d => { S.dcat = d.k; render(); };
A.dpq = (d, el) => { S.dpq = el.value; $('#dplist').innerHTML = partyListD(dRange()); };
A.dpty = d => { S.cs.pweek = null; go('party', { cid:d.cid }); };
A.pmon = d => { S.pmon = +d.k; S.cs.pweek = null; render(); };

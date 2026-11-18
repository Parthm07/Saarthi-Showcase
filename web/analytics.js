/* ================= ANALYTICS: order lines, 7-day bar charts =================
   Every order visit (last 3 months) is split into order lines across the party's usual
   products. The Sales dashboard and party analysis read from SALES; live orders are added
   by recordSale(). Charts show 7 bars at a time with ‹ › to move through history and a
   tap to read the numbers. */
const MAXDAY = 90;
S.cw = {}; S.cs = {}; // chart window offset (in weeks) and selected bar, per chart key

const SALES = [];
(function buildSales() {
  let s = 4242; const r = () => (s = (s * 16807) % 2147483647) / 2147483647;
  VISITS.filter(v => v.outcome === 'order' && v.amt && !v.agg).forEach(v => {
    const c = C(v.cid), pool = [...c.buys.map(b => b.p), ...(c.buys.length ? extrasFor(c).slice(0, 4).map(x => x.p) : [])];
    const picks = pool.filter(() => r() < .6).filter(P); if (!picks.length && pool.filter(P).length) picks.push(pool.filter(P)[0]);
    if (!picks.length) return;   // party with no purchase history: 315 of 605
    const w = picks.map(() => .5 + r()), W = w.reduce((a, b) => a + b, 0);
    picks.forEach((p, i) => { const value = Math.round(v.amt * w[i] / W); SALES.push({ days:v.days, cid:v.cid, rep:v.rep, p, value, qty:Math.max(1, Math.round(value / P(p).price)) }); });
  });
})();
/* Real invoice lines with their own dates -- a genuine time series.
   Built from party_buy instead, every line would sit at the date it was
   LAST bought, so a period-over-period comparison would be comparing
   "things last bought in window A" against "things last bought in window
   B". The totals would look right and the percentages would be nonsense. */
function rebuildSalesFromTally() {
  SALES.length = 0;
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const rep = {}; CUSTOMERS.forEach(c => (rep[c.id] = c.rep));
  (typeof TALLY_LINES !== 'undefined' ? TALLY_LINES : []).forEach(([ymd, cid, pid, qty, value, invoiceGuid, salesperson]) => {
    const dt = new Date(+ymd.slice(0, 4), +ymd.slice(4, 6) - 1, +ymd.slice(6, 8));
    SALES.push({ days: Math.round((today - dt) / 864e5), cid,
                 rep:invoiceGuid ? (salesperson || null) : rep[cid],
                 invoiceGuid, p: pid, qty, value });
  });
}
if (typeof TALLY_LINES !== 'undefined') rebuildSalesFromTally();
function recordSale(cid, rep, lines) { lines.forEach(([p, q, rate]) => SALES.push({ days:0, cid, rep, p, qty:q, value:Math.round(q * rate) })); }

const dayLabel = n => dAgo(n).toLocaleDateString('en-IN', { weekday:'short' });
const dayNum = n => `${dAgo(n).getDate()} ${dAgo(n).toLocaleDateString('en-IN', { month:'short' })}`;
/* the 7 days (as days-ago, oldest first) a chart currently shows; end = most recent day allowed */
function weekRange(key, end = 0) { const hi = end + (S.cw[key] || 0) * 7; return Array.from({ length:7 }, (_, i) => hi + 6 - i); }
function weekNav(key, end = 0) {
  const d = weekRange(key, end), off = S.cw[key] || 0;
  return `<div class="wnav"><button class="btn sm ghost" data-a="cprev" data-key="${key}" ${d[0] + 7 > MAXDAY ? 'disabled' : ''} aria-label="Previous 7 days">${icon('back', 15)}</button>
    <span class="num">${dayNum(d[0])} – ${dayNum(d[6])}</span>
    <button class="btn sm ghost" data-a="cnext" data-key="${key}" ${off === 0 ? 'disabled' : ''} aria-label="Next 7 days">${icon('arrow', 15)}</button></div>`;
}
A.cprev = d => { S.cw[d.key] = (S.cw[d.key] || 0) + 1; S.cs[d.key] = null; render(); };
A.cnext = d => { S.cw[d.key] = Math.max(0, (S.cw[d.key] || 0) - 1); S.cs[d.key] = null; render(); };
A.cbar = d => { S.cs[d.key] = S.cs[d.key] === +d.i ? null : +d.i; render(); };

/* short money labels that fit above a bar: ₹2.9L, ₹76K, ₹8.4K */
const kInr = n => n >= 1e5 ? '₹' + (n / 1e5).toFixed(1).replace(/\.0$/, '') + 'L' : n >= 1e3 ? '₹' + (n / 1e3).toFixed(n >= 1e4 ? 0 : 1).replace(/\.0$/, '') + 'K' : '₹' + Math.round(n);

/* bar chart with every value shown; tap a bar for the exact figure. items: [{ label, sub, subColor, [a], [b] }] — b is drawn inside a */
function selBars(key, items, { a = 'a', b, color = 'var(--accent)', soft = 'var(--accent-soft)', fmt = x => x, cap = it => fmt(it[a]), lab } = {}) {
  const L = lab || (fmt === inrS ? kInr : fmt);
  const W = 460, H = 200, pl = 6, pb = 38, pt = b ? 42 : 28, n = items.length, bw = (W - 2 * pl) / n, top = Math.max(1, ...items.map(it => it[a]));
  const y = v => pt + (H - pt - pb) * (1 - v / top), sel = S.cs[key];
  const rect = (x, w, v, fill) => `<rect x="${x.toFixed(1)}" y="${y(v).toFixed(1)}" width="${w.toFixed(1)}" height="${Math.max(H - pb - y(v), 0).toFixed(1)}" rx="4" fill="${fill}"/>`;
  const bars = items.map((it, i) => {
    const x = pl + i * bw + bw * .2, w = bw * .6, on = sel === i;
    return `<g${sel != null && !on ? ' opacity=".55"' : ''}>${rect(x, w, it[a], b ? soft : color)}${b ? rect(x, w, it[b], color) : ''}
      ${it[a] ? `<text x="${x + w / 2}" y="${(y(it[a]) - (b ? 21 : 7)).toFixed(1)}" text-anchor="middle" font-size="12.5" font-weight="700" fill="var(--ink)">${L(it[a])}</text>` : ''}
      ${b && it[a] ? `<text x="${x + w / 2}" y="${(y(it[a]) - 7).toFixed(1)}" text-anchor="middle" font-size="11.5" font-weight="700" fill="${color}">${it[b]}</text>` : ''}
      <text x="${x + w / 2}" y="${H - 21}" text-anchor="middle" font-size="12" font-weight="${on ? 700 : 500}" fill="var(--ink-2)">${it.label}</text>
      ${it.sub ? `<text x="${x + w / 2}" y="${H - 6}" text-anchor="middle" font-size="11" fill="${it.subColor || 'var(--ink-3)'}">${it.sub}</text>` : ''}
      <rect x="${pl + i * bw}" y="0" width="${bw}" height="${H}" fill="transparent" data-a="cbar" data-key="${key}" data-i="${i}" style="cursor:pointer"/></g>`;
  }).join('');
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Bar chart"><line x1="${pl}" x2="${W - pl}" y1="${H - pb}" y2="${H - pb}" stroke="var(--line)"/>${bars}</svg>
    <div class="ccap ${sel == null ? 'faint' : ''}">${sel == null ? 'Tap a bar for the exact figure' : cap(items[sel])}</div>`;
}

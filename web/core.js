/* ---------- lookups & formatting ---------- */
const P = id => PRODUCTS.find(p => p.id === id);
const C = id => CUSTOMERS.find(c => c.id === id);
const R = id => REPS.find(r => r.id === id);
const inr = n => '₹' + Math.round(n).toLocaleString('en-IN');
const inrS = n => {
  const a = Math.abs(n);
  if (a >= 1e7) return '₹' + (n / 1e7).toFixed(2).replace(/\.?0+$/, '') + ' Cr';
  if (a >= 1e5) return '₹' + (n / 1e5).toFixed(1).replace(/\.0$/, '') + 'L';
  return inr(n);
};
const esc = s => String(s).replace(/[&<>"]/g, ch => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;' }[ch]));
const ini = name => name.split(/\s+/).filter(w => /[A-Za-z]/.test(w[0])).slice(0, 2).map(w => w[0]).join('').toUpperCase();
const cover = p => Math.round(p.stock / p.daily); // days of stock on hand
const $ = sel => document.querySelector(sel);
/* Join only the parts that exist. Half the customer card's fields have no
   Tally source (address, contact, phone), and printing "null · null" is
   worse than printing nothing. */
const bits = (xs, sep = ' · ') => xs.filter(x => x != null && x !== '').join(sep);

/* ---------- icons (stroke, 24 grid) ---------- */
const ICONS = {
  spark:'<path d="M12 3l1.8 4.9L19 9.7l-4.9 1.8L12 16.5l-1.8-5L5 9.7l5.2-1.8z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/>',
  plus:'<path d="M12 5v14M5 12h14"/>', minus:'<path d="M5 12h14"/>', x:'<path d="M6 6l12 12M18 6L6 18"/>',
  check:'<path d="M5 12.5l4.5 4.5L19 7.5"/>', arrow:'<path d="M5 12h14M13 6l6 6-6 6"/>', back:'<path d="M19 12H5M11 6l-6 6 6 6"/>',
  phone:'<path d="M5 4h4l2 5-2.5 1.5a11 11 0 005 5L15 13l5 2v4a2 2 0 01-2 2A16 16 0 013 6a2 2 0 012-2"/>',
  nav:'<path d="M3 11l18-8-8 18-2-8z"/>', search:'<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>',
  repeat:'<path d="M17 2l4 4-4 4"/><path d="M3 11V9a3 3 0 013-3h15M7 22l-4-4 4-4"/><path d="M21 13v2a3 3 0 01-3 3H3"/>',
  alert:'<path d="M12 3l10 18H2z"/><path d="M12 10v5M12 18v.01"/>', box:'<path d="M21 8l-9-5-9 5v8l9 5 9-5z"/><path d="M3 8l9 5 9-5M12 13v8"/>',
  truck:'<path d="M1 5h14v11H1zM15 9h4l3 3v4h-7"/><circle cx="6" cy="18" r="2"/><circle cx="18" cy="18" r="2"/>',
  trend:'<path d="M3 17l6-6 4 4 8-8"/><path d="M15 7h6v6"/>', down:'<path d="M3 7l6 6 4-4 8 8"/><path d="M15 17h6v-6"/>',
  cart:'<circle cx="9" cy="20" r="1.5"/><circle cx="18" cy="20" r="1.5"/><path d="M2 3h3l2.7 12.4a1 1 0 001 .8h9.7a1 1 0 001-.8L21 7H6"/>',
  users:'<circle cx="9" cy="8" r="4"/><path d="M2 21a7 7 0 0114 0M16 4a4 4 0 010 8M22 21a7 7 0 00-4-6.3"/>',
  home:'<path d="M3 11l9-7 9 7v9a1 1 0 01-1 1h-5v-6H9v6H4a1 1 0 01-1-1z"/>', clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  rupee:'<path d="M6 4h12M6 9h12M14 20L7 13h3a4.5 4.5 0 000-9"/>', camera:'<path d="M3 8h4l2-3h6l2 3h4v12H3z"/><circle cx="12" cy="13" r="4"/>',
  pen:'<path d="M4 20l4-1L19 8l-3-3L5 16z"/>', key:'<circle cx="8" cy="15" r="4"/><path d="M11 12l9-9M17 6l3 3"/>',
  tag:'<path d="M3 12V3h9l9 9-9 9z"/><circle cx="8" cy="8" r="1.5"/>', layers:'<path d="M12 3l9 5-9 5-9-5z"/><path d="M3 13l9 5 9-5"/>',
  chart:'<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>', send:'<path d="M22 2L11 13M22 2l-7 20-4-9-9-4z"/>',
  grid:'<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
  shield:'<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/>', pause:'<path d="M9 5v14M15 5v14"/>', edit:'<path d="M4 20h4L20 8l-4-4L4 16z"/>',
  print:'<path d="M7 9V3h10v6"/><rect x="3" y="9" width="18" height="7" rx="2"/><path d="M7 14h10v7H7z"/>',
  split:'<path d="M4 6h6l4 6-4 6H4M14 12h6"/>', wa:'<path d="M4 20l1.3-4A8 8 0 1112 20a8 8 0 01-4-1z"/>', map:'<path d="M9 4L3 6v14l6-2 6 2 6-2V4l-6 2z"/><path d="M9 4v14M15 6v14"/>',
};
const icon = (n, s = 16, w = 1.8) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${w}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[n] || ''}</svg>`;

/* ---------- small charts ---------- */
function spark(vals, { w = 120, h = 34, color = 'var(--accent)' } = {}) {
  const max = Math.max(...vals), min = Math.min(...vals), rng = max - min || 1;
  const pts = vals.map((v, i) => [2 + i * (w - 4) / (vals.length - 1), h - 3 - (v - min) / rng * (h - 8)]);
  const line = pts.map(p => p.map(n => n.toFixed(1)).join(',')).join(' ');
  const last = pts[pts.length - 1];
  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true">
    <polygon points="2,${h} ${line} ${w - 2},${h}" fill="${color}" opacity=".12"/>
    <polyline points="${line}" fill="none" stroke="${color}" stroke-width="1.8" stroke-linejoin="round"/>
    <circle cx="${last[0]}" cy="${last[1]}" r="3" fill="${color}"/></svg>`;
}
function bars(vals, labels, { h = 150, highlightLast = true, avg = true } = {}) {
  const W = 440, padL = 44, padB = 22, top = 10, max = Math.max(...vals) * 1.15 || 1;
  const bw = (W - padL - 10) / vals.length;
  const y = v => top + (h - top - padB) * (1 - v / max);
  const mean = vals.slice(0, -1).reduce((a, b) => a + b, 0) / (vals.length - 1);
  const ticks = [0, max / 2, max].map(t => `<line x1="${padL}" x2="${W - 6}" y1="${y(t)}" y2="${y(t)}" stroke="var(--line)"/>
    <text x="${padL - 8}" y="${y(t) + 4}" text-anchor="end" font-size="10" fill="var(--ink-3)" font-family="var(--f-mono)">${t ? (t / 1000).toFixed(0) + 'k' : '0'}</text>`).join('');
  const b = vals.map((v, i) => {
    const x = padL + i * bw + bw * .22, bh = h - padB - y(v), last = i === vals.length - 1;
    return `<rect x="${x}" y="${y(v)}" width="${bw * .56}" height="${Math.max(bh, 1)}" rx="4" fill="${last && highlightLast ? 'var(--gold-strong)' : 'var(--accent)'}" opacity="${last ? 1 : .8}"/>
      <text x="${x + bw * .28}" y="${h - 6}" text-anchor="middle" font-size="11" fill="var(--ink-3)">${labels[i]}</text>`;
  }).join('');
  const avgLine = avg ? `<line x1="${padL}" x2="${W - 6}" y1="${y(mean)}" y2="${y(mean)}" stroke="var(--ink-2)" stroke-dasharray="4 4"/>
    <text x="${W - 8}" y="${y(mean) - 5}" text-anchor="end" font-size="10" fill="var(--ink-2)">avg ${(mean / 1000).toFixed(1)}k</text>` : '';
  return `<svg viewBox="0 0 ${W} ${h}" width="100%" style="max-width:${W}px" role="img" aria-label="Monthly purchases">${ticks}${b}${avgLine}</svg>`;
}
function ring(pct, size = 118, stroke = 11) {
  const r = (size - stroke) / 2, c = 2 * Math.PI * r;
  return `<svg width="${size}" height="${size}"><circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="var(--surface-3)" stroke-width="${stroke}"/>
    <circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="var(--accent)" stroke-width="${stroke}" stroke-linecap="round" stroke-dasharray="${c}" stroke-dashoffset="${c * (1 - Math.min(pct, 1))}"/></svg>`;
}

/* ---------- app state ---------- */
const ROLES = [
  { id:'sales',   label:'Salesperson',          person:'Rahul Verma',    ini:'RV' },
  { id:'desk',    label:'Accounts / Order Desk', person:'Neha Kulkarni',  ini:'NK' },
  { id:'manager', label:'Sales Manager',        person:'Vikram Singh',   ini:'VS' },
  { id:'owner',   label:'Business Owner',       person:'Anil Mahajan',   ini:'AM' },
  { id:'driver',  label:'Driver',               person:'Ramesh Yadav',   ini:'RY' },
];
const S = {
  role:'sales', view:'home', params:{},
  carts:{},          // cid -> { pid: qty }
  placed:{},         // cid -> order value placed this session
  dismissed:{},      // cid:pid -> true
  sessionAdded:0,    // value of recommended items added (feeds manager funnel)
  rates:{},          // cid -> { pid: rate } changed by the salesperson
};
const VIEWS = {};    // role -> { view: fn(params) -> html }
const NAV = {};      // role -> [{view, label, icon, count?}]
const A = {};        // click actions: data-a="name"
const AFTER = [];    // post-render hooks

function go(view, params = {}) { S.view = view; S.params = params; render(); scrollTop(); }
function setRole(role) { S.role = role; S.view = 'home'; S.params = {}; closeOverlay(); render(); window.scrollTo({ top:0 }); }
function render() {
  renderChrome();
  const { key, html } = resolveView();
  const main = $('#main');
  const sameView = key === S._lastKey;
  // Filters and live refresh replace the view's HTML. Keep the scroll position
  // when staying on the same screen, including on phones where #main scrolls.
  const top = sameView ? main.scrollTop : 0;
  const filterOffsets = new Map();
  if (sameView) {
    for (const row of main.querySelectorAll('.catchips')) {
      const action = row.querySelector('[data-a]')?.dataset.a;
      if (action) filterOffsets.set(action, row.scrollLeft);
    }
  }
  main.innerHTML = `<div class="view ${sameView ? 'still' : ''}" data-view="${key}">${html}</div>`;
  S._lastKey = key;
  AFTER.splice(0).forEach(f => f());
  if (filterOffsets.size) {
    for (const row of main.querySelectorAll('.catchips')) {
      const action = row.querySelector('[data-a]')?.dataset.a;
      if (filterOffsets.has(action)) row.scrollLeft = filterOffsets.get(action);
    }
  }
  if (sameView && main.scrollTop !== top) main.scrollTop = top;
}
const after = f => AFTER.push(f);

function renderSide() {
  const nav = (NAV[S.role] || []).map(n => `<button class="${S.view === n.view || (n.also || []).includes(S.view) ? 'on' : ''}" data-a="nav" data-v="${n.view}">${icon(n.icon)} ${n.label}${n.count ? `<span class="count">${n.count()}</span>` : ''}</button>`).join('');
  return `
    <div class="brand"><div class="brand-mark">${icon('spark', 18, 2)}</div>
      <div><div class="brand-name">Saarthi</div><div class="brand-sub">${COMPANY.name}</div></div></div>
    <div class="stack" style="gap:8px"><div class="eyebrow" style="padding:0 8px">Viewing as</div>
      <div class="personas">${ROLES.map(r => `<button class="persona ${S.role === r.id ? 'on' : ''}" data-a="role" data-r="${r.id}">
        <span class="av">${r.ini}</span><span><div class="who">${r.person}</div><div class="what">${r.label}</div></span></button>`).join('')}</div></div>
    ${nav ? `<nav class="nav">${nav}</nav>` : ''}
    <div class="sync"><div class="row" style="gap:8px"><span class="dot"></span><b>Synced with Tally</b></div>
      <span>${PRODUCTS.length} items · ${CUSTOMERS.length} parties · ${CUSTOMERS.reduce((a, c) => a + (c.buys || []).length, 0)} buying patterns</span></div>`;
}

/* ---------- recommendations engine (simulated) ---------- */
function recsFor(cid) {
  const c = C(cid); if (!c || S.placed[cid]) return [];
  const out = [];
  c.buys.forEach(b => {
    /* cyc and freq are null when the shop has bought this too few times
       to infer a cycle. It is still something they buy, but there is no
       "due" or "usually taken" to claim about it. */
    const p = P(b.p), due = b.cyc && b.cyc[0] != null && b.last >= b.cyc[0];
    if (!b.freq && !due) return;
    const qty = Math.min(b.qty, p.stock);
    if (due) out.push({ type:'reorder', p:b.p, qty, b,
      title:'Reorder due', lines:[`Normally ordered every <b>${b.cyc[0]}–${b.cyc[1]} days</b>`, `Last ordered <b>${b.last} days ago</b>`, `Previous qty <b>${b.qty}</b>`, `Stock available <b>${p.stock}</b>`] });
    else if (b.core && b.freq) out.push({ type:'missing', p:b.p, qty, b,
      title:'Usually taken', lines:[`Taken in <b>${b.freq[0]} of the last ${b.freq[1]} bills</b>`, `Missing from today's order`] });
  });
  c.cross.forEach(x => {
    const p = P(x.p);
    const label = { cross:'Cross-sell', upsell:'Upsell', slow:'Push slow stock' }[x.type];
    const lines = x.type === 'slow'
      ? [x.peer, `${p.stock} in stock · <b>${cover(p)} days</b> of cover`]
      : [x.why, x.peer];
    out.push({ type:x.type, p:x.p, qty:x.qty, title:label, lines });
  });
  return out.filter(r => !S.dismissed[cid + ':' + r.p]).map(r => ({ ...r, value: r.qty * P(r.p).price }));
}
const cartOf = cid => (S.carts[cid] = S.carts[cid] || {});
const inCart = (cid, pid) => !!cartOf(cid)[pid];
const openRecs = cid => recsFor(cid).filter(r => !inCart(cid, r.p));
const oppFor = cid => openRecs(cid).reduce((a, r) => a + r.value, 0);
/* Keep manually entered and saved draft quotes ahead of the current catalogue rate. */
const rateOf = (cid, pid) => (S.rates[cid] || {})[pid] ?? P(pid)?.price ?? null;
const cartTotal = cid => Object.entries(cartOf(cid)).reduce((a, [p, q]) => a + rateOf(cid, p) * q, 0);
const cartCount = cid => Object.keys(cartOf(cid)).length;

function whyFor(c) {
  const recs = recsFor(c.id), w = [];
  const due = recs.filter(r => r.type === 'reorder').length, miss = recs.filter(r => r.type === 'missing').length;
  const cross = recs.filter(r => ['cross', 'upsell'].includes(r.type)), slow = recs.filter(r => r.type === 'slow');
  if (c.churn) w.push({ t:`Buying ${c.churn}% less than usual — churn risk`, k:'c' });
  if (due) w.push({ t:`${due} product${due > 1 ? 's' : ''} due for reorder`, k:'a' });
  if (miss) w.push({ t:`${miss} commonly purchased product${miss > 1 ? 's' : ''} missing`, k:'w' });
  if (cross.length) w.push({ t:`${cross[0].type === 'upsell' ? 'Upsell' : 'Cross-sell'}: ${P(cross[0].p).name} (${inr(cross[0].value)})`, k:'' });
  if (slow.length) w.push({ t:`Good fit for excess ${P(slow[0].p).name} stock`, k:'' });
  const avgCycle = Math.round(c.buys.reduce((a, b) => a + b.cyc[0], 0) / c.buys.length);
  w.push({ t:`Last order ${c.lastDays} days ago${c.lastDays > avgCycle ? ` (usually every ~${avgCycle})` : ''}`, k: c.lastDays > avgCycle + 10 ? 'c' : '' });
  if (c.overdue) w.push({ t:`Collect ${inr(c.overdue)} overdue (${c.overdueDays} days)`, k:'c' });
  return w;
}

/* ---------- overlay & toast ---------- */
function drawer(html) { $('#overlay').innerHTML = `<div class="scrim" data-a="scrim"><div class="drawer" role="dialog" aria-modal="true">${html}</div></div>`; }
function closeOverlay() { $('#overlay').innerHTML = ''; }
function toast(msg, kind = '') {
  const t = document.createElement('div');
  t.className = 'toast ' + kind;
  t.innerHTML = `<span class="ti">${icon(kind === 'warn' ? 'alert' : 'check', 16, 2.2)}</span><span>${msg}</span>`;
  $('#toasts').appendChild(t);
  setTimeout(() => { t.style.transition = 'opacity .3s'; t.style.opacity = '0'; setTimeout(() => t.remove(), 300); }, 3200);
}
const bump = sel => { const el = $(sel); if (el) { el.classList.remove('bump'); void el.offsetWidth; el.classList.add('bump'); } };

/* ---------- global actions ---------- */
A.role = d => setRole(d.r);
A.nav = d => go(d.v);
A.go = d => go(d.v, d.cid ? { cid:d.cid, ...(d.rep ? { rep:d.rep } : {}) } : d.rep ? { rep:d.rep } : {});
A.scrim = (d, el, e) => { if (e.target === el) closeOverlay(); };
A.close = () => closeOverlay();
A.toast = d => toast(d.msg, d.kind || '');

document.addEventListener('click', e => {
  const el = e.target.closest('[data-a]'); if (!el) return;
  const fn = A[el.dataset.a]; if (!fn) return;
  if (el.dataset.a !== 'scrim') e.preventDefault();
  fn(el.dataset, el, e);
});
document.addEventListener('input', e => { const el = e.target.closest('[data-in]'); if (el && A[el.dataset.in]) A[el.dataset.in](el.dataset, el, e); });
document.addEventListener('change', e => { const el = e.target.closest('[data-ch]'); if (el && A[el.dataset.ch]) A[el.dataset.ch](el.dataset, el, e); });
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') closeOverlay();
  const el = e.target.closest('[data-enter]'); if (el && e.key === 'Enter' && A[el.dataset.enter]) { e.preventDefault(); A[el.dataset.enter](el.dataset, el, e); }
});

function boot() { render(); }

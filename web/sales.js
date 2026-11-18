/* ================= SALESPERSON ================= */
let ME = 's1'; // set from the signed-in user
const myCustomers = () => CUSTOMERS.filter(c => c.rep === ME);
/* The demo's canned data is written relative to this date, so the
   offline prototype keeps it. Live it is replaced with the real
   today in live.js -- left fixed, every 'today' figure reads zero
   and every period lands five weeks in the past. `let`, not
   `const`, so it can be. */
let TODAY = new Date(2026, 8, 11);
const dAgo = n => { const d = new Date(TODAY); d.setDate(d.getDate() - n); return d; };
const dstr = d => d.toLocaleDateString('en-IN', { day:'numeric', month:'short', year:'numeric' });

NAV.sales = [
  { view:'home', label:'Today', icon:'home' },
  { view:'customers', label:'My customers', icon:'users' },
  { view:'order', label:'New order', icon:'cart' },
];

/* occasional items a party buys now and then (deterministic demo) */
function extrasFor(c) {
  const seed = parseInt(c.id.slice(1)) || 1, key = p => (parseInt(p.id.slice(1)) * 7 + seed * 13) % 17;
  return PRODUCTS.filter(p => !c.buys.some(b => b.p === p.id)).sort((a, b) => key(a) - key(b)).slice(0, 10)
    .map((p, j) => ({ p:p.id, f:1 + (j % 3), q: p.price < 1500 ? 4 + (j % 3) * 2 : p.price < 4000 ? 2 + (j % 3) : 1 + (j % 2) }));
}

/* invoices reconstructed from purchase patterns (deterministic) */
function invoicesFor(c) {
  if (c._inv) return c._inv;
  const gap = Math.round(c.buys.reduce((a, b) => a + (b.cyc[0] + b.cyc[1]) / 2, 0) / c.buys.length);
  const n0 = 4800 - parseInt(c.id.slice(1)) * 23;
  c._inv = Array.from({ length:6 }, (_, k) => {
    const days = c.lastDays + k * gap + (k ? (k * 7 + c.id.length) % 5 : 0);
    let lines = c.buys.filter((b, i) => (k + i) % 6 < (b.freq ? b.freq[0] : 1)).map((b, i) => {
      const f = 0.8 + ((k * 7 + i * 3) % 5) * 0.1;
      const q = Math.max(1, Math.round(b.qty * f / (b.qty >= 50 ? 10 : 1)) * (b.qty >= 50 ? 10 : 1));
      return [b.p, k === 0 ? b.qty : q];
    });
    if (!lines.length) lines = [[c.buys[0].p, c.buys[0].qty]];
    extrasFor(c).forEach((x, j) => { if ((k * 5 + j * 3) % 6 < x.f) lines.push([x.p, x.q]); });
    const value = Math.round(lines.reduce((a, [p, q]) => a + P(p).price * q, 0));
    const status = k === 0 && c.overdue ? 'overdue' : k === 0 && c.outstanding ? 'due' : 'paid';
    return { no:`MIS/${days > 163 ? '25-26' : '26-27'}/0${n0 - k * 131}`, date:dAgo(days), days, lines, value, status };
  });
  return c._inv;
}

function schemeDiscount(cid) {
  return Object.entries(cartOf(cid)).reduce((a, [pid, q]) => {
    const p = P(pid); if (!p.scheme || q < p.scheme.min) return a;
    if (pid === 'P1') return a + Math.floor(q / 25) * p.price;
    if (pid === 'P3') return a + q * p.price * 0.05;
    if (pid === 'P7') return a + q * 8;
    return a;
  }, 0);
}

/* ---------- home ---------- */
VIEWS.sales = {};
VIEWS.sales.home = () => {
  const rep = R(ME), mine = myCustomers();
  const achieved = rep.achieved + mine.reduce((a, c) => a + (S.placed[c.id] || 0), 0);
  const pct = achieved / rep.target, remaining = Math.max(rep.target - achieved, 0);
  const all = mine.flatMap(c => openRecs(c.id));
  const total = all.reduce((a, r) => a + r.value, 0);
  const by = types => all.filter(r => types.includes(r.type)).reduce((a, r) => a + r.value, 0);
  const list = mine.map(c => ({ c, opp:oppFor(c.id) }))
    .sort((a, b) => (S.placed[a.c.id] ? 1 : 0) - (S.placed[b.c.id] ? 1 : 0) || b.opp - a.opp);
  const active = list.filter(x => x.opp > 0).length;
  const collect = mine.filter(c => c.overdue && !S.reminded?.[c.id]);
  const slow = mine.flatMap(c => openRecs(c.id).filter(r => r.type === 'slow').map(r => ({ c, r })));

  return `
  <header class="topbar"><div><div class="eyebrow">${COMPANY.today}</div>
    <h1 style="margin-top:6px">Good morning, ${rep.name.split(' ')[0]}</h1>
    <p class="sub">${active} customers to act on today. Start at the top — they're ranked by the value you can close.</p></div>
    <button class="btn primary" data-a="nav" data-v="order">${icon('plus')} New order</button></header>

  <section class="hero-strip">
    <div class="card target-card">
      <div class="ring">${ring(pct)}<div class="c"><div><b class="num">${Math.round(pct * 100)}%</b><br><span>of target</span></div></div></div>
      <div class="stack" style="gap:14px">
        <div class="eyebrow">September target · ${rep.territory}</div>
        <div class="tstats">
          <div><div class="faint" style="font-size:12px">Monthly target</div><div class="v num">${inrS(rep.target)}</div></div>
          <div><div class="faint" style="font-size:12px">Achieved</div><div class="v num">${inrS(achieved)}</div></div>
          <div><div class="faint" style="font-size:12px">Remaining</div><div class="v num">${inrS(remaining)}</div></div>
        </div>
        <p class="muted" style="font-size:13px">19 days left · you need <b style="color:var(--ink)">${inr(remaining / 16)}</b> per working day</p>
      </div>
    </div>
    <div class="card opp-hero">
      <div class="row between"><span class="eyebrow" style="color:var(--gold)">Today's sales opportunity</span><span class="chip gold">${active} customers</span></div>
      <div class="big num" id="opp-total">${inr(total)}</div>
      <div class="bd"><span>Reorders due <b class="num">${inrS(by(['reorder']))}</b></span><span>Missing items <b class="num">${inrS(by(['missing']))}</b></span>
        <span>Cross & upsell <b class="num">${inrS(by(['cross', 'upsell']))}</b></span><span>Slow stock <b class="num">${inrS(by(['slow']))}</b></span></div>
      <p style="font-size:12.5px;color:var(--ink-2)">Closing all of it takes you to <b style="color:var(--ink)">${Math.round((achieved + total) / rep.target * 100)}%</b> of target.</p>
    </div>
  </section>

  <section class="split">
    <div class="stack" style="gap:14px">
      <div class="section-h"><h2>Who to contact today</h2><span class="aside">Ranked by opportunity value</span></div>
      <div class="opp-list">${list.map((x, i) => oppCard(x.c, x.opp, i)).join('')}</div>
    </div>
    <aside class="side-cards">
      ${collect.length ? `<div class="card mini"><div class="row between"><h4>Collect while you're there</h4><span class="chip crit">${inrS(collect.reduce((a, c) => a + c.overdue, 0))}</span></div>
        ${collect.map(c => `<div class="item"><div><b>${c.name}</b><div class="faint" style="font-size:12px">${inr(c.overdue)} · ${c.overdueDays} days overdue</div></div>
          <button class="btn sm" data-a="remind" data-cid="${c.id}">${icon('wa', 14)} Remind</button></div>`).join('')}</div>` : ''}
      ${slow.length ? `<div class="card mini"><h4>Move slow stock</h4><p class="faint" style="font-size:12.5px;margin-top:-4px">Excess inventory matched to the customers most likely to buy it.</p>
        ${slow.map(({ c, r }) => `<div class="item"><div><b>${P(r.p).name}</b><div class="faint" style="font-size:12px">${cover(P(r.p))} days of cover → ${c.name}</div></div>
          <button class="btn sm" data-a="add" data-cid="${c.id}" data-p="${r.p}" data-q="${r.qty}">${icon('plus', 14)} ${r.qty}</button></div>`).join('')}</div>` : ''}
      <div class="card mini"><h4>Stock watch</h4>
        ${['P11', 'P6'].map(id => { const p = P(id); return `<div class="item"><div><b>${p.name}</b><div class="faint" style="font-size:12px">${p.stock} left · runs out in ~${cover(p)} days (restock takes ${p.lead})</div></div><span class="chip crit">${cover(p)}d</span></div>`; }).join('')}
        <p class="faint" style="font-size:12px">Book these for your regulars before they run out.</p></div>
    </aside>
  </section>`;
};

function oppCard(c, opp, i) {
  const done = S.placed[c.id];
  return `<article class="card opp ${done ? 'done' : ''}" data-a="go" data-v="customer" data-cid="${c.id}" tabindex="0">
    <div class="rank">${done ? icon('check', 18) : '#' + (i + 1)}</div>
    <div class="stack" style="gap:10px;min-width:0">
      <div><div class="row wrap" style="gap:8px"><h3>${c.name}</h3>${c.churn ? '<span class="chip churn">Churn risk</span>' : ''}${c.outstanding > c.limit ? '<span class="chip credit">Over credit</span>' : ''}${c.growth ? `<span class="chip good">+${c.growth}% growing</span>` : ''}</div>
      <div class="meta">${bits([c.area, c.contact, c.lastVisit != null ? `last visit ${c.lastVisit}d ago` : null])}</div></div>
      ${done ? `<p class="muted">Order placed today · <b class="num">${inr(done)}</b></p>` : `<ul class="why">${whyFor(c).slice(0, 4).map(w => `<li class="${w.k}">${w.t}</li>`).join('')}</ul>`}
    </div>
    <div class="right">
      <div><div class="lbl">Opportunity</div><div class="value num">${inr(opp)}</div></div>
      <span class="btn ${done ? '' : 'primary'} sm">${done ? 'View' : 'View opportunity'} ${icon('arrow', 14)}</span>
    </div></article>`;
}

/* ---------- customers list ---------- */
VIEWS.sales.customers = () => customerTable(myCustomers(), 'My customers', `${R(ME).territory} · sorted by open opportunity`);
function customerTable(list, title, sub) {
  const rows = list.map(c => ({ c, opp:oppFor(c.id) })).sort((a, b) => b.opp - a.opp);
  return `<header class="topbar"><div><h1>${title}</h1><p class="sub">${sub}</p></div></header>
  <div class="card table-wrap"><table class="t"><thead><tr><th>Customer</th><th>Last order</th><th>6-month trend</th><th class="r">Outstanding</th><th class="r">Credit left</th><th class="r">Opportunity</th></tr></thead><tbody>
  ${rows.map(({ c, opp }) => `<tr class="click" data-a="go" data-v="customer" data-cid="${c.id}">
    <td><b>${c.name}</b><div class="faint" style="font-size:12px">${c.territory} · ${R(c.rep).name}</div></td>
    <td class="num ${c.lastDays > 35 ? 'crit' : ''}">${c.lastDays} days ago</td>
    <td>${spark(c.trend.slice(0, 5), { w:90, h:26, color: c.churn ? 'var(--crit)' : 'var(--accent)' })}</td>
    <td class="r num">${inr(c.outstanding)}${c.overdue ? `<div class="crit" style="font-size:11.5px">${inr(c.overdue)} overdue</div>` : ''}</td>
    <td class="r num ${c.limit - c.outstanding < 0 ? 'crit' : ''}">${inr(c.limit - c.outstanding)}</td>
    <td class="r"><span class="value num">${inr(opp)}</span></td></tr>`).join('')}
  </tbody></table></div>`;
}

/* ---------- customer detail ---------- */
VIEWS.sales.customer = ({ cid }) => customerView(cid);
function customerView(cid) {
  const c = C(cid), recs = recsFor(cid), opp = oppFor(cid), inv = invoicesFor(c);
  const avail = c.limit - c.outstanding, usedPct = c.outstanding / c.limit;
  const prior = (c.trend[1] + c.trend[2] + c.trend[3]) / 3, chg = Math.round((c.trend[4] - prior) / prior * 100);
  const cart = Object.entries(cartOf(cid));
  const back = { sales:["Today's plan", 'plan'], manager:[S.params.rep ? R(S.params.rep).name : 'Attention', S.params.rep ? 'rep' : 'home'], desk:['Exceptions', 'home'], owner:['Copilot', 'home'] }[S.role] || ['Back', 'home'];
  return `
  <div><div class="crumbs"><button data-a="go" data-v="${back[1]}" data-rep="${S.params.rep || ''}">${icon('back', 14)} ${back[0]}</button></div>
  <div class="cust-head"><div><div class="row wrap" style="gap:8px"><h1>${c.name}</h1>
      ${c.churn ? `<span class="chip churn">${icon('down', 12)} Buying ${c.churn}% less</span>` : ''}${c.outstanding > c.limit ? '<span class="chip credit">Over credit limit</span>' : ''}${c.growth ? `<span class="chip good">${icon('trend', 12)} Growing ${c.growth}%</span>` : ''}</div>
      <p class="muted" style="margin-top:4px">${bits([c.area, c.contact, c.phone ? `<span class="mono">${c.phone}</span>` : null, routeNameOf(c.id), `Rep: ${R(c.rep).name}`])}</p></div>
    <div class="row"><button class="btn" ${c.phone ? `data-a="toast" data-msg="Calling ${c.contact} (${c.phone})…"` : 'disabled title="No phone number in Tally"'}>${icon('phone')} Call</button>
      <button class="btn primary" data-a="go" data-v="order" data-cid="${cid}">${icon('cart')} ${cart.length ? `Continue order (${cart.length})` : 'Start order'}</button></div></div></div>

  <section class="card facts">
    <div><span class="k">Last order</span><span class="v num ${c.lastDays > 35 ? 'crit' : ''}">${c.lastDays} days ago</span><span class="s">${dstr(inv[0].date)} · ${inr(inv[0].value)}</span></div>
    <div><span class="k">Average order value</span><span class="v num">${inr(c.aov)}</span><span class="s">last 6 invoices</span></div>
    <div><span class="k">Outstanding</span><span class="v num">${inr(c.outstanding)}</span><span class="s ${c.overdue ? 'crit' : ''}">${c.overdue ? `${inr(c.overdue)} overdue · ${c.overdueDays} days` : 'Nothing overdue'}</span></div>
    <div><span class="k">Credit limit</span><span class="v num">${inr(c.limit)}</span>
      <div class="meter ${usedPct > 1 ? 'crit' : usedPct > .75 ? 'warn' : ''}" style="margin-top:4px"><i style="width:${Math.min(usedPct, 1) * 100}%"></i></div>
      <span class="s ${avail < 0 ? 'crit' : ''}">${avail < 0 ? `Exceeded by ${inr(-avail)}` : `${inr(avail)} available`}</span></div>
    <div><span class="k">Purchase trend</span><div class="row" style="gap:8px">${spark(c.trend.slice(0, 5), { w:84, h:30, color: chg < -15 ? 'var(--crit)' : 'var(--accent)' })}
      <span class="v num ${chg < -15 ? 'crit' : chg > 5 ? 'good' : ''}" style="font-size:17px">${chg > 0 ? '+' : ''}${chg}%</span></div><span class="s">Aug vs 3-month avg</span></div>
  </section>

  <section class="split">
    <div class="stack" style="gap:18px">
      <div class="card lift" style="overflow:hidden">
        <div class="copilot-h"><span class="spark-ic">${icon('spark')}</span>
          <div><b>Saarthi recommends</b><div class="faint" style="font-size:12.5px">From ${inv.length} invoices, 14 similar customers and live stock</div></div>
          <div style="margin-left:auto;text-align:right"><div class="value num" id="cust-opp" style="font-size:22px">${inr(opp)}</div><div class="faint" style="font-size:11px">open opportunity</div></div></div>
        ${recs.length ? recs.map(r => recRow(cid, r)).join('') : `<div class="empty">${icon('check', 22)}<b>Nothing pending.</b><span>${S.placed[cid] ? 'Order placed today — recommendations refresh after the next Tally sync.' : 'This customer is fully stocked on their regulars.'}</span></div>`}
      </div>
      <div class="card pad stack">
        <div class="section-h"><h2>Purchase trend</h2><span class="aside">Monthly purchases, ₹ · Sep month-to-date</span></div>
        ${bars(c.trend, MONTHS)}
        ${c.churn ? `<div class="alert crit"><span class="ai">${icon('down')}</span><span><b>Purchase frequency is dropping.</b> ${c.name} usually orders every ~${c.buys[0].cyc[0]} days and hasn't ordered in ${c.lastDays}. A competitor may be supplying them — worth a visit, not just a call.</span></div>` : ''}
      </div>
      <div class="card" style="overflow:hidden">
        <div class="pad section-h" style="padding-bottom:6px"><h2>Previous invoices</h2><button class="btn sm" data-a="repeat" data-cid="${cid}">${icon('repeat', 14)} Repeat last order</button></div>
        <div class="table-wrap"><table class="t"><thead><tr><th>Invoice</th><th>Date</th><th>Items</th><th class="r">Value</th><th>Status</th></tr></thead><tbody>
        ${inv.map(v => `<tr class="click" data-a="inv" data-no="${v.no}"><td class="mono">${v.no}</td><td class="num">${dstr(v.date)}</td>
          <td>${v.lines.length} items</td><td class="r num">${inr(v.value)}</td>
          <td><span class="chip ${v.status === 'overdue' ? 'crit' : v.status === 'due' ? 'warn' : 'good'}">${v.status === 'overdue' ? 'Overdue' : v.status === 'due' ? 'Due' : 'Paid'}</span></td></tr>
          ${S.openInv === v.no ? `<tr><td colspan="5" style="background:var(--surface-2)">${v.lines.map(([p, q]) => `<div class="row between" style="padding:3px 0;font-size:13px"><span>${P(p).name}</span><span class="num muted">${q} × ${inr(P(p).price)}</span></div>`).join('')}</td></tr>` : ''}`).join('')}
        </tbody></table></div>
      </div>
    </div>
    <aside class="side-cards sticky">
      <div class="card mini"><div class="row between"><h4>Order in progress</h4><span class="chip">${cart.length} items</span></div>
        ${cart.length ? cart.map(([p, q]) => `<div class="item"><span>${P(p).name}</span><span class="num muted">${q} · ${inr(P(p).price * q)}</span></div>`).join('') +
          `<div class="item"><b>Subtotal</b><b class="num">${inr(cartTotal(cid))}</b></div><button class="btn primary block" data-a="go" data-v="order" data-cid="${cid}">Review order ${icon('arrow', 14)}</button>`
          : `<p class="faint" style="font-size:13px">Add recommendations here, or start an order and Saarthi will flag anything missing.</p>`}
      </div>
      ${c.overdue || avail < 0 ? `<div class="card mini"><h4>Before you take the order</h4>
        ${c.overdue ? `<div class="alert crit"><span class="ai">${icon('rupee')}</span><span><b>${inr(c.overdue)}</b> overdue for ${c.overdueDays} days. Ask for a cheque or UPI with this order.</span></div>` : ''}
        ${avail < 0 ? `<div class="alert warn"><span class="ai">${icon('shield')}</span><span>Over credit limit by <b>${inr(-avail)}</b>. New orders will need approval from accounts.</span></div>` : ''}</div>` : ''}
      <div class="card mini"><h4>Talking points</h4><ul class="why">${whyFor(c).map(w => `<li class="${w.k}">${w.t}</li>`).join('')}</ul></div>
    </aside>
  </section>`;
}

function recRow(cid, r, compact) {
  const p = P(r.p), added = inCart(cid, r.p);
  return `<div class="rec ${added ? 'in' : ''}">
    <div style="min-width:0"><div class="row wrap" style="gap:6px"><span class="chip ${r.type}">${r.title}</span>${p.scheme && !compact ? `<span class="chip scheme">${icon('tag', 11)} ${p.scheme.label}</span>` : ''}</div>
      <div class="name" style="margin-top:6px">${p.name}</div>
      ${compact ? `<div class="facts-line"><span>${r.lines[0]}</span></div>` : `<div class="facts-line">${r.lines.map(l => `<span>${l}</span>`).join('')}<span>Recommended qty <b>${r.qty}</b></span></div>`}</div>
    <div class="act"><span class="value num">${inr(r.value)}</span>
      ${added ? `<span class="btn sm added">${icon('check', 14)} Added</span>`
        : `<button class="btn sm primary" data-a="add" data-cid="${cid}" data-p="${r.p}" data-q="${r.qty}">${icon('plus', 14)} Add ${r.qty}</button>`}
      ${!compact && !added ? `<button style="font-size:12px;color:var(--ink-3)" data-a="dismiss" data-cid="${cid}" data-p="${r.p}">Not now</button>` : ''}
    </div></div>`;
}

/* ---------- order builder ---------- */
VIEWS.sales.order = ({ cid }) => cid ? orderView(cid) : pickCustomer(myCustomers());
VIEWS.sales.placed = ({ cid }) => placedView(cid);

function pickCustomer(list) {
  return `<header class="topbar"><div><h1>New order</h1><p class="sub">Pick a customer — Saarthi will load their buying pattern, credit and recommendations.</p></div></header>
  <div class="search" style="max-width:520px"><span class="ic">${icon('search')}</span><input placeholder="Search customer by name or area" data-in="custq" aria-label="Search customers"></div>
  <div class="grid g3" id="custgrid">${list.map(c => `<button class="card pad stack" style="text-align:left;gap:6px" data-a="go" data-v="order" data-cid="${c.id}" data-name="${esc((c.name + ' ' + (c.area || c.territory || '')).toLowerCase())}">
    <b style="font-size:15px">${c.name}</b><span class="faint" style="font-size:12.5px">${c.area || c.territory || ''}</span>
    <span class="row between" style="margin-top:6px"><span class="muted" style="font-size:12.5px">Credit left <b class="num ${c.limit - c.outstanding < 0 ? 'crit' : ''}">${inr(c.limit - c.outstanding)}</b></span><span class="value num">${inr(oppFor(c.id))}</span></span></button>`).join('')}</div>`;
}
A.custq = (d, el) => { const q = el.value.toLowerCase(); document.querySelectorAll('#custgrid [data-name]').forEach(b => b.hidden = !b.dataset.name.includes(q)); };

function orderView(cid) {
  const c = C(cid), cart = cartOf(cid), lines = Object.entries(cart);
  const sub = cartTotal(cid), disc = schemeDiscount(cid), net = sub - disc, gross = net;
  const open = openRecs(cid), missing = open.reduce((a, r) => a + r.value, 0);
  const avail = c.limit - c.outstanding, after = avail - gross;
  const regular = pid => c.buys.find(b => b.p === pid);
  const alerts = [];
  if (c.overdue) alerts.push(['crit', 'rupee', `<b>${inr(c.overdue)}</b> overdue for ${c.overdueDays} days — collect with this order.`]);
  lines.forEach(([pid, q]) => {
    const p = P(pid), b = regular(pid);
    if (p.scheme && q < p.scheme.min && q >= p.scheme.min * .5)
      alerts.push(['info', 'tag', `Add <b>${p.scheme.min - q} more ${p.name}</b> to unlock “${p.scheme.label}”. <button class="link" data-a="setq" data-cid="${cid}" data-p="${pid}" data-q="${p.scheme.min}">Make it ${p.scheme.min}</button>`]);
    if (p.scheme && q >= p.scheme.min) alerts.push(['good', 'tag', `${p.scheme.note} applied on ${p.name}.`]);
    if (b && q >= b.qty * 2.5) alerts.push(['warn', 'alert', `<b>${p.name}: ${q}</b> is ${(q / b.qty).toFixed(1)}× this customer's usual ${b.qty}. Confirm before placing.`]);
    if (cover(p) < p.lead) alerts.push(['warn', 'box', `${p.name}: only ${p.stock} left, runs out in ~${cover(p)} days. Good time to lock in their need.`]);
  });

  return `
  <div><div class="crumbs"><button data-a="go" data-v="customer" data-cid="${cid}">${icon('back', 14)} ${c.name}</button></div>
  <header class="topbar"><div><h1>New order · ${c.name}</h1><p class="sub">${bits([c.area || c.territory, c.contact])}</p></div>
    <div class="row wrap"><button class="btn" data-a="repeat" data-cid="${cid}">${icon('repeat')} Repeat previous order</button>
      <button class="btn ghost" data-a="nav" data-v="order">Change customer</button></div></header></div>

  <section class="split">
    <div class="card lift" style="overflow:visible">
      <div class="pad" style="padding-bottom:14px"><div class="search"><span class="ic">${icon('search')}</span>
        <input id="psearch" placeholder="Search products — try “wheel”, “gloves”, “E7018”" value="${esc(S.q || '')}" data-in="pq" data-cid="${cid}" autocomplete="off" aria-label="Search products"></div>
        <div id="pres">${S.q ? searchResults(cid, S.q) : ''}</div></div>
      ${lines.length ? `<div class="cart-line cart-head"><span>Product</span><span>Qty</span><span class="r" style="text-align:right">Rate</span><span style="text-align:right">Amount</span><span></span></div>` +
        lines.map(([pid, q]) => cartLine(cid, pid, q, regular(pid))).join('')
        : `<div class="empty" style="border-top:1px solid var(--line)">${icon('cart', 24)}<b>No items yet</b><span>Search above, repeat the previous order, or add Saarthi's recommendations.</span>
          <button class="btn" data-a="repeat" data-cid="${cid}">${icon('repeat')} Repeat previous order</button></div>`}
      ${lines.length ? `<div class="totals">
        <div class="ln"><span>Subtotal</span><span class="num">${inr(sub)}</span></div>
        ${disc ? `<div class="ln good"><span>Scheme discount</span><span class="num">− ${inr(disc)}</span></div>` : ''}
        <div class="ln" style="align-items:center;margin-top:6px"><span class="grand">Total</span><span class="grand num">${inr(gross)}</span></div>
        <div class="row" style="justify-content:flex-end;margin-top:10px;gap:8px">
          ${after < 0 ? `<span class="chip warn">Needs credit approval</span>` : ''}
          <button class="btn primary lg" data-a="place" data-cid="${cid}">${S.role === 'desk' ? 'Create order' : 'Place order'} ${icon('arrow', 16)}</button></div></div>` : ''}
    </div>

    <aside class="card sticky" style="overflow:hidden">
      <div class="copilot-h"><span class="spark-ic">${icon('spark')}</span><b>Order copilot</b></div>
      ${missing > 0 ? `<div class="missing-banner"><span class="eyebrow" style="color:var(--gold)">${lines.length ? 'Before you place it' : 'Suggested for this order'}</span>
          <div class="big">You may be missing <span class="num" id="miss-v">${inr(missing)}</span> from this order</div>
          <span class="muted" style="font-size:12.5px">${open.length} item${open.length > 1 ? 's' : ''} this customer is due for or usually buys.</span>
          <button class="btn gold sm" style="align-self:flex-start;margin-top:6px" data-a="addall" data-cid="${cid}">${icon('plus', 14)} Add all ${open.length}</button></div>`
        : `<div class="missing-banner clear"><span class="eyebrow" style="color:var(--good)">Looks complete</span><div class="big">Nothing missing</div><span class="muted" style="font-size:12.5px">All regular and due items are in the order.</span></div>`}
      ${open.map(r => recRow(cid, r, true)).join('')}
      <div class="pad stack" style="border-top:1px solid var(--line)">
        <div><div class="row between" style="font-size:13px"><b>Credit availability</b><span class="num ${after < 0 ? 'crit' : 'muted'}">${after < 0 ? `Over by ${inr(-after)}` : `${inr(after)} left after order`}</span></div>
          <div class="meter ${after < 0 ? 'crit' : (c.outstanding + gross) / c.limit > .75 ? 'warn' : ''}" style="margin-top:8px"><i style="width:${Math.min((c.outstanding + gross) / c.limit, 1) * 100}%"></i></div>
          <div class="row between faint" style="font-size:11.5px;margin-top:4px"><span>Outstanding ${inrS(c.outstanding)} + this order ${inrS(gross)}</span><span>Limit ${inrS(c.limit)}</span></div></div>
        ${alerts.map(([k, ic, t]) => `<div class="alert ${k}"><span class="ai">${icon(ic)}</span><span>${t}</span></div>`).join('')}
      </div>
    </aside>
  </section>`;
}

function cartLine(cid, pid, q, b) {
  const p = P(pid), short = q > p.stock;
  const flags = [];
  if (short) flags.push(`<span class="flag crit">${icon('alert', 12)} Only ${p.stock} in stock — ${q - p.stock} will be backordered</span>`);
  if (b && q >= b.qty * 2.5) flags.push(`<span class="flag warn">${icon('alert', 12)} Unusually large — usual qty ${b.qty}</span>`);
  if (b && q < b.qty * .5) flags.push(`<span class="flag warn">${icon('down', 12)} Lower than usual (${b.qty})</span>`);
  return `<div class="cart-line">
    <div style="min-width:0"><div class="pn">${p.name}</div>
      <div class="ps"><span class="mono">${p.id}</span><span>${p.brand}</span><span class="${short ? 'crit' : cover(p) < p.lead ? 'warn' : ''}">Stock ${p.stock}</span>${p.scheme ? `<span class="gold">${icon('tag', 11)} ${p.scheme.label}</span>` : ''}</div>${flags.join('')}</div>
    <div class="stepper"><button data-a="qty" data-cid="${cid}" data-p="${pid}" data-d="-1" aria-label="Decrease">${icon('minus', 14)}</button>
      <input value="${q}" inputmode="numeric" data-ch="qtyset" data-cid="${cid}" data-p="${pid}" aria-label="Quantity">
      <button data-a="qty" data-cid="${cid}" data-p="${pid}" data-d="1" aria-label="Increase">${icon('plus', 14)}</button></div>
    <div class="num muted" style="text-align:right">${inr(p.price)}</div>
    <div class="num" style="text-align:right;font-weight:600">${inr(p.price * q)}</div>
    <button class="btn ghost sm" data-a="rm" data-cid="${cid}" data-p="${pid}" aria-label="Remove">${icon('x', 14)}</button></div>`;
}

function searchResults(cid, q) {
  const s = q.toLowerCase().trim();
  const hits = PRODUCTS.filter(p => (p.name + ' ' + p.brand + ' ' + p.id).toLowerCase().includes(s)).slice(0, 7);
  if (!hits.length) return `<div class="results"><div class="res"><span class="faint">No products match “${esc(q)}”</span></div></div>`;
  return `<div class="results">${hits.map(p => {
    const b = C(cid).buys.find(x => x.p === p.id), qty = b ? b.qty : 10;
    return `<div class="res"><div><b>${p.name}</b><div class="faint" style="font-size:12px">${inr(p.price)}/${p.unit} · <span class="${cover(p) < p.lead ? 'crit' : ''}">Stock ${p.stock}</span>${p.scheme ? ` · <span class="gold">${p.scheme.label}</span>` : ''}${b ? ` · usually ${b.qty}` : ''}</div></div>
      ${inCart(cid, p.id) ? '<span class="chip good">In order</span>' : '<span></span>'}
      <button class="btn sm" data-a="add" data-cid="${cid}" data-p="${p.id}" data-q="${qty}" data-search="1">${icon('plus', 14)} Add ${qty}</button></div>`;
  }).join('')}</div>`;
}

/* ---------- placed ---------- */
function placedView(cid) {
  const o = S.lastPlaced || {}, c = C(cid);
  const next = (S.role === 'desk' ? [] : myCustomers()).filter(x => x.id !== cid && oppFor(x.id) > 0).sort((a, b) => oppFor(b.id) - oppFor(a.id))[0];
  return `<div class="card lift pad stack" style="max-width:640px;margin:30px auto;gap:18px;padding:32px">
    <span class="spark-ic" style="width:48px;height:48px;border-radius:14px;background:var(--good-soft);color:var(--good)">${icon('check', 26, 2.4)}</span>
    <div><div class="eyebrow">${o.id}</div><h1 style="font-size:28px;margin-top:6px">Order ${S.role === 'desk' ? 'created' : 'placed'} for ${c.name}</h1>
      <p class="muted" style="margin-top:6px">${inr(o.gross)} · ${o.over > 0 ? 'sent to accounts for credit approval' : 'queued for dispatch'}</p></div>
    ${o.captured ? `<div class="alert good"><span class="ai">${icon('spark')}</span><span><b class="num">${inr(o.captured)}</b> of this order came from Saarthi's recommendations.</span></div>` : ''}
    ${o.over > 0 ? `<div class="alert warn"><span class="ai">${icon('shield')}</span><span>This order takes ${c.name} <b>${inr(o.over)}</b> over their credit limit. Neha at the order desk has it in her approval queue.</span></div>` : ''}
    <div class="row wrap">
      ${next ? `<button class="btn primary" data-a="go" data-v="customer" data-cid="${next.id}">Next: ${next.name} · ${inr(oppFor(next.id))} ${icon('arrow', 14)}</button>` : ''}
      <button class="btn" data-a="nav" data-v="home">${S.role === 'desk' ? 'Back to exceptions' : "Back to today's list"}</button></div></div>`;
}

/* ---------- actions ---------- */
function addToCart(cid, pid, qty) {
  const wasRec = openRecs(cid).find(r => r.p === pid);
  cartOf(cid)[pid] = (cartOf(cid)[pid] || 0) + qty;
  if (wasRec) S.sessionAdded += wasRec.value;
  return wasRec;
}
A.add = d => {
  const q = +d.q, p = P(d.p), rec = addToCart(d.cid, d.p, q);
  if (d.search) S.q = '';
  render();
  const left = oppFor(d.cid);
  toast(rec ? `Added ${q} × ${p.name} · ${inr(left)} opportunity left` : `Added ${q} × ${p.name}`);
  bump('#opp-total'); bump('#cust-opp'); bump('#miss-v');
};
A.addall = d => { const recs = openRecs(d.cid); const v = recs.reduce((a, r) => a + r.value, 0); recs.forEach(r => addToCart(d.cid, r.p, r.qty)); render(); toast(`Added ${recs.length} recommended items · ${inr(v)}`); };
A.qty = d => {
  const cart = cartOf(d.cid), q = cart[d.p], step = q >= 100 ? 10 : q >= 20 ? 5 : 1;
  const n = q + step * +d.d;
  if (n <= 0) return A.rm(d);
  cart[d.p] = n; render();
};
A.qtyset = (d, el) => { const n = parseInt(el.value, 10); if (!n || n < 1) return A.rm(d); cartOf(d.cid)[d.p] = n; render(); };
A.setq = d => { cartOf(d.cid)[d.p] = +d.q; render(); toast(`Scheme unlocked on ${P(d.p).name}`); };
A.rm = d => {
  delete cartOf(d.cid)[d.p];
  const b = C(d.cid).buys.find(x => x.p === d.p);
  render();
  if (b && b.freq && (b.freq[0] >= 4 || b.core)) toast(`${P(d.p).name} is in ${b.freq[0]} of their last ${b.freq[1]} orders — flagged as missing`, 'warn');
};
A.repeat = d => {
  const c = C(d.cid), last = invoicesFor(c)[0];
  last.lines.forEach(([p, q]) => { cartOf(d.cid)[p] = q; });
  if (S.view !== 'order') go('order', { cid:d.cid }); else render();
  toast(`Loaded ${last.lines.length} items from ${last.no}`);
};
A.dismiss = d => { S.dismissed[d.cid + ':' + d.p] = true; render(); toast(`Hidden for today · ${P(d.p).name}`); };
A.inv = d => { S.openInv = S.openInv === d.no ? null : d.no; render(); };
A.pq = (d, el) => { S.q = el.value; $('#pres').innerHTML = S.q ? searchResults(d.cid, S.q) : ''; };
A.remind = d => { const c = C(d.cid); S.reminded = S.reminded || {}; S.reminded[d.cid] = true; render(); toast(c.phone ? `WhatsApp reminder sent to ${c.contact} for ${inr(c.overdue)}`
    : `No phone number for ${c.name} in Tally`, c.phone ? '' : 'warn'); };
A.place = d => {
  const c = C(d.cid), net = cartTotal(d.cid) - schemeDiscount(d.cid); if (!net) return;
  const gross = Math.round(net), over = gross - (c.limit - c.outstanding);
  const captured = recsFor(d.cid).filter(r => inCart(d.cid, r.p)).reduce((a, r) => a + r.value, 0);
  const id = 'SO-' + (2300 + ORDERS.length);
  ORDERS.unshift({ id, cid:d.cid, rep:c.rep, amount:gross, placed:'Just now', issue: over > 0 ? 'credit' : 'ready', status:'pending',
    lines:Object.entries(cartOf(d.cid)), fresh:true });
  S.placed[d.cid] = gross; S.lastPlaced = { id, gross, captured, over }; S.carts[d.cid] = {}; S.q = '';
  go('placed', { cid:d.cid });
  toast(`${id} ${S.role === 'desk' ? 'created' : 'placed'} · ${inr(gross)}`);
};

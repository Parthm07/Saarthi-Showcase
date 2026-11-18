/* ================= ORDER DESK — approve, or modify in the order taker ================= */
VIEWS.desk = {};
VIEWS.desk.menu = () => VIEWS.sales.menu();
VIEWS.desk.review = () => VIEWS.sales.review();
VIEWS.desk.customer = ({ cid }) => customerView(cid);

VIEWS.desk.home = () => {
  if (S.editing) { const o = ORDERS.find(x => x.id === S.editing); if (o) S.carts[o.cid] = {}; S.editing = null; }
  const pending = ORDERS.filter(o => o.status === 'pending'), done = ORDERS.filter(o => o.status === 'approved');
  return `<header class="topbar"><div><h1>Order desk</h1><p class="sub">${pending.length} to approve · ${inrS(pending.reduce((a, o) => a + o.amount, 0))}</p></div></header>
  <div class="card list">${pending.length ? pending.map(deskRow).join('') : `<div class="empty">${icon('check', 22)}<b>All orders approved</b></div>`}</div>
  ${done.length ? `<div class="section-h"><h2>Approved</h2><span class="aside">${done.length}</span></div><div class="card list">${done.map(deskRow).join('')}</div>` : ''}`;
};

function deskRow(o) {
  const c = C(o.cid);
  return `<div class="drow">
    <button class="drow-main" data-a="olines" data-id="${o.id}" aria-haspopup="dialog">
      <span class="pinfo"><b>${c.name}</b><span class="faint">${o.placed} · ${o.id} · ${o.lines.length} items · by ${o.by || R(o.rep).name}${o.edited ? ' · edited' : ''}</span></span>
      <span class="dr-amt"><b class="num">${inr(o.amount)}</b><span class="crit">Due ${inr(c.outstanding)}</span></span></button>
    <div class="dact"><button class="btn sm ghost" data-a="billview" data-id="${o.id}">${icon('print', 14)} Print</button>${o.status === 'pending'
      ? `<button class="btn sm" data-a="modify" data-id="${o.id}">${icon('edit', 14)} Modify</button><button class="btn sm primary" data-a="approve" data-id="${o.id}">${icon('check', 14, 2.2)} Approve</button>`
      : `<span class="status approved">${icon('check', 13, 2.2)} Approved</span><button class="btn sm" data-a="modify" data-id="${o.id}">${icon('edit', 14)} Edit</button>`}</div>
  </div>`;
}

/* order details in a bottom sheet: qty, MRP, our price, rate quoted, amount */
A.olines = d => {
  const o = ORDERS.find(x => x.id === d.id), c = C(o.cid);
  const rows = o.lines.map(([p, q, r]) => ({ pr:P(p), q, rate:r || P(p).price }));
  const sub = rows.reduce((a, x) => a + x.q * x.rate, 0);
  drawer(`<div class="drawer-h"><div><div class="eyebrow">${o.id} · ${o.placed}</div><h2 style="font-size:19px;margin-top:4px">${c.name}</h2>
      <p class="faint" style="font-size:12.5px"><span class="crit">Due ${inr(c.outstanding)}</span> · by ${o.by || R(o.rep).name}</p></div><button class="btn ghost sm" data-a="close" aria-label="Close">${icon('x')}</button></div>
    <div class="drawer-b">
      <div>${rows.map(x => `<div class="oline"><div class="pimg xs">${art(x.pr.id)}</div><div class="ol-b">
        <div class="row between"><b>${x.pr.name}</b><b class="num">${inr(x.q * x.rate)}</b></div>
        <div class="ol-q"><span class="qpill"><b class="num">${x.q}</b> qty</span><span class="ol-x">×</span>
          <span class="ol-rate">Rate quoted <b class="num ${x.rate < x.pr.price ? 'crit' : ''}">${inr(x.rate)}</b></span></div>
        <div class="ol-s">MRP ${inr(x.pr.mrp)} · Our price ${inr(x.pr.price)}</div></div></div>`).join('')}</div>
      <div class="card totals sm"><div class="ln"><span>Total qty</span><b class="num">${rows.reduce((a, x) => a + x.q, 0)}</b></div><div class="ln"><b>Total</b><b class="num">${inr(sub)}</b></div></div>
    </div>
    <div class="drawer-f">${S.role === 'desk' && o.status === 'pending'
      ? `<button class="btn" data-a="modify" data-id="${o.id}">${icon('edit', 14)} Modify</button><button class="btn primary" data-a="approve" data-id="${o.id}">${icon('check', 14, 2.2)} Approve</button>`
      : `<button class="btn" data-a="close">Close</button><button class="btn primary" data-a="modify" data-id="${o.id}">${icon('edit', 14)} Edit order</button>`}</div>`);
};
A.approve = d => { closeOverlay(); const o = ORDERS.find(x => x.id === d.id); o.status = 'approved'; render(); toast(`${o.id} approved · ${C(o.cid).name}`); };
A.modify = d => {
  closeOverlay();
  const o = ORDERS.find(x => x.id === d.id);
  S.carts[o.cid] = {}; S.rates[o.cid] = {};
  o.lines.forEach(([p, q, r]) => { S.carts[o.cid][p] = q; if (r && r !== P(p).price) S.rates[o.cid][p] = r; });
  S.fp = o.cid; S.fcat = 'all'; S.fq = ''; S.rq = '';
  go('review');
  S.editing = o.id; render();
};

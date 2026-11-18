/* ================= ORDER TAKER — party → items → review =================
   Compact rows with MRP, our rate, last rate and stock; editable rates;
   pending amount on top; add items and top-10 missing at checkout.
   Also used by the order desk to modify an order (S.editing = order id). */

const CATS = [['all', 'All items'], ['bis', 'Biscuits & snacks'], ['bev', 'Beverages'], ['choc', 'Chocolates'], ['pc', 'Personal care'], ['hc', 'Home care'], ['stp', 'Staples']];
const PCAT = { P1:'bis', P2:'bis', P3:'bis', P4:'bis', P5:'bev', P6:'bev', P7:'bev', P8:'choc', P9:'pc', P10:'pc', P11:'pc', P12:'pc', P13:'hc', P14:'hc', P15:'hc', P16:'stp', P17:'stp', P18:'stp' };
ICONS.cal = '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>';
ICONS.print = '<path d="M6 9V3h12v6"/><rect x="3" y="9" width="18" height="8" rx="2"/><path d="M6 14h12v7H6z"/>';


/* ---------- product pictures (inline SVG pack shapes, no external images) ---------- */
const F = 'font-family="Arial,Helvetica,sans-serif"';
const T = (x, y, c, l, s) => `<text x="${x}" y="${y}" font-size="${s || (l.length > 8 ? 7.5 : l.length > 6 ? 9 : 10.5)}" font-weight="800" text-anchor="middle" fill="${c}" ${F}>${l}</text>`;
const PACK = {
  pouch:(c, l, tc) => `<rect x="24" y="16" width="52" height="68" rx="6" fill="${c}"/><path d="M24 22h52M24 78h52" stroke="#000" stroke-opacity=".18" stroke-width="3" stroke-dasharray="3 3"/>
    <ellipse cx="50" cy="50" rx="22" ry="13" fill="#fff"/>${T(50, 53.5, tc, l)}`,
  box:(c, l, tc) => `<rect x="24" y="20" width="52" height="64" rx="4" fill="${c}"/><rect x="24" y="20" width="52" height="10" rx="4" fill="#000" opacity=".18"/>
    <rect x="29" y="44" width="42" height="22" rx="3" fill="#fff"/>${T(50, 58.5, tc, l)}`,
  jar:(c, l, tc, cap) => `<rect x="34" y="14" width="32" height="10" rx="3" fill="${cap}"/><rect x="28" y="24" width="44" height="62" rx="10" fill="${c}"/>
    <rect x="31" y="44" width="38" height="22" rx="3" fill="#fff"/>${T(50, 58.5, tc, l)}`,
  bottle:(c, l, tc, cap) => `<rect x="43" y="10" width="14" height="10" rx="2" fill="${cap}"/><path d="M43 20h14v8c8 4 11 10 11 18v36a6 6 0 01-6 6H38a6 6 0 01-6-6V46c0-8 3-14 11-18z" fill="${c}"/>
    <rect x="34" y="50" width="32" height="20" rx="3" fill="#fff"/>${T(50, 63.5, tc, l)}`,
  bar:(c, l, tc) => `<rect x="12" y="34" width="76" height="32" rx="6" fill="${c}"/><rect x="26" y="40" width="48" height="20" rx="3" fill="#fff"/>${T(50, 53.5, tc, l)}`,
  tube:(c, l, tc) => `<path d="M16 38h54l12 6v12l-12 6H16z" fill="${c}"/><rect x="82" y="46" width="8" height="8" rx="1" fill="#333"/>
    <rect x="22" y="42" width="44" height="16" rx="3" fill="#fff"/>${T(44, 53.5, tc, l)}`,
  bag:(c, l, tc) => `<path d="M30 20l6-6h28l6 6" fill="${c}" opacity=".75"/><path d="M26 20h48l4 8v54a4 4 0 01-4 4H26a4 4 0 01-4-4V28z" fill="${c}"/>
    <rect x="27" y="40" width="46" height="28" rx="3" fill="#fff"/>${T(50, 53, tc, l)}${T(50, 63, tc, '5 KG', 7.5)}`,
};
const ARTS = {
  P1:['pouch', '#F2C230', 'PARLE-G', '#C8102E'], P2:['pouch', '#C8102E', 'GOOD DAY', '#C8102E'], P3:['pouch', '#F5C400', "LAY'S", '#D52B1E'],
  P4:['pouch', '#E4572E', 'BHUJIA', '#8A2B0E'], P5:['box', '#B11226', 'TATA TEA', '#B11226'], P6:['jar', '#6B3E26', 'NESCAFÉ', '#6B3E26', '#C9A227'],
  P7:['bottle', '#F4A300', 'FROOTI', '#1E7B34', '#1E7B34'], P8:['bar', '#4B2A7B', 'DAIRY MILK', '#4B2A7B'], P9:['tube', '#D8232A', 'COLGATE', '#D8232A'],
  P10:['bar', '#D32F2F', 'LIFEBUOY', '#D32F2F'], P11:['bottle', '#E6007E', 'CLINIC+', '#E6007E', '#F3C1DB'], P12:['bottle', '#1E88E5', 'PARACHUTE', '#1E88E5', '#1E88E5'],
  P13:['box', '#1565C0', 'SURF EXCEL', '#1565C0'], P14:['bar', '#2E7D32', 'VIM', '#2E7D32'], P15:['bottle', '#1E3A8A', 'HARPIC', '#1E3A8A', '#E53935'],
  P16:['bag', '#D4A017', 'AASHIRVAAD', '#7A4F00'], P17:['bottle', '#F6B800', 'FORTUNE', '#B45309', '#B45309'], P18:['pouch', '#F9C80E', 'MAGGI', '#D62828'],
};
/* The demo's 18 hand-drawn packets only exist for the demo's 18 products.
   Real Tally items have no artwork and no brand field, and falling through
   to ARTS.P1 drew a Parle-G packet on all 625 of them -- a brand the item
   has nothing to do with. Unknown items get a plain pack with their own
   initials instead: no picture is better than the wrong picture. */
const art = pid => {
  const image = (P(pid) || {}).image;
  if (image && /^\/api\/product-image\/[0-9a-f]{64}$/.test(image)) return `<img src="${esc(image)}" alt="${esc((P(pid)||{}).name||'Product')}" loading="lazy" class="product-photo">`;
  const a = ARTS[pid];
  const [shape, ...rest] = a
    || ['box', '#E7E9EC', ini((P(pid) || {}).name || '?'), '#79808A'];
  return `<svg viewBox="0 0 100 100" aria-hidden="true">${PACK[shape](...rest)}</svg>`;
};

/* ---------- helpers ---------- */
/* What this party was LAST actually charged, straight from their invoices.
   The list price is wrong for 76% of (party, item) pairs -- median 2.7% off,
   90th percentile 11.7% -- so this is the number to quote and the list rate
   is only the fallback. Returns null when the pair has no history, or when
   the last billed rate was an outlier (e.g. VIM 85 WALA last went out at
   Rs 75 against a Rs 1,040 median; quoting that would be worse than
   quoting nothing). */
function lastRate(c, pid) {
  const b = (c.buys || []).find(x => x.p === pid);
  if (!b || b.rate == null) return null;
  return { rate:b.rate, date:b.rateDate ? ymdToDate(b.rateDate) : null };
}
const ymdToDate = y => new Date(+y.slice(0, 4), +y.slice(4, 6) - 1, +y.slice(6, 8));
/* brand is null on every Tally item, and "name " + null searches the text
   "null" -- which matches nothing the rep typed, or everything if they
   typed it. Group stands in: it is the only other text an item carries. */
const matchQ = (p, q) => q.toLowerCase().split(/\s+/).filter(Boolean).every(t => `${p.name} ${p.brand || p.grp || ''}`.toLowerCase().includes(t.replace(/s$/, '')));
const pendingBar = c => `<div class="duebar">${icon('rupee', 13)} Pending <b class="num">${inr(c.outstanding)}</b></div>`;
const ohead = (back, title, sub) => `<div class="ohead"><button class="back" data-a="nav" data-v="${back}" aria-label="Back">${icon('back', 16)}</button>
  <div class="oh-t"><b>${title}</b><span>${sub}</span></div>${S.editing ? `<span class="chip warn">Editing ${S.editing}</span>` : ''}</div>`;

/* ---------- step 1: party ---------- */
const scrollRouteChip = () => { const on = document.querySelector('[data-a="froute"].on'); if (on) on.parentElement.scrollLeft = on.offsetLeft - 16; };
VIEWS.sales.home = () => { const mine = myOrders().length; after(scrollRouteChip); return `
  <header class="topbar"><div><h1>Take order</h1><p class="sub">Select a party, or type to search.</p></div>
    <button class="btn" data-a="nav" data-v="myorders">${icon('cart', 15)} My orders${mine ? ` (${mine})` : ''}</button></header>
  <div class="catchips" role="tablist" aria-label="Routes">${[{ id:'all', name:'All routes' }, ...myRoutes()].map(r => { const on = (S.froute || 'all') === r.id;
    return `<button class="${on ? 'on' : ''}" data-a="froute" data-r="${esc(r.id)}" role="tab" aria-selected="${on}">${esc(r.name)}</button>`; }).join('')}</div>
  <div class="search big"><span class="ic">${icon('search', 18)}</span><input id="fpq" placeholder="Type party or route name" value="${esc(S.fpq || '')}" data-in="fpq" autocomplete="off" aria-label="Search party"></div>
  <div class="card list" id="plist">${partyRows()}</div>`; };
VIEWS.sales.order = ({ cid }) => { if (cid) { S.fp = cid; return VIEWS.sales.menu(); } return VIEWS.sales.home(); };

function canCreateParty() {
  return typeof LIVE === 'undefined' || !LIVE.on || LIVE.me?.role === 'admin';
}

function partySearchRank(c, q) {
  const name = c.name.toLowerCase();
  if (name === q) return 0;
  if (name.startsWith(q)) return 1;
  const words = s => s.replace(/[^\p{L}\p{N}]+/gu, ' ').trim();
  const term = words(q);
  if (term && term.split(' ').every(t=>words(name).split(' ').some(w=>w.startsWith(t)))) return 2;
  return name.includes(q) ? 3 : 4; // Route-only matches follow name matches.
}

function partyRows() {
  const q = (S.fpq || '').trim().toLowerCase();
  const route = S.froute && S.froute !== 'all' ? myRoutes().find(r => r.id === S.froute) : null;
  const list = CUSTOMERS.filter(c => (!route || q || route.cids.includes(c.id)) &&
    (typeof LIVE !== 'undefined' && LIVE.on || myTpl().id !== 'sales' || c.rep === ME || c.isNew) &&
    (!q || (c.name + ' ' + routeNameOf(c.id)).toLowerCase().includes(q)))
    .sort((a, b) => q
      ? partySearchRank(a, q) - partySearchRank(b, q) || a.name.localeCompare(b.name)
      : route ? route.cids.indexOf(a.id) - route.cids.indexOf(b.id) : a.name.localeCompare(b.name));
  const size = 50, pages = Math.max(1, Math.ceil(list.length / size));
  S.fpage = Math.min(S.fpage || 0, pages - 1);
  let html = list.slice(S.fpage * size, (S.fpage + 1) * size).map(c => { const n = cartCount(c.id); return `<button class="party" data-a="fparty" data-cid="${esc(c.id)}">
    <span class="av">${ini(c.name)}</span><span class="pinfo"><b>${esc(c.name)}</b><span class="faint">${esc(routeNameOf(c.id))}</span></span>
    <span class="due">${n ? `<span class="chip good">${n} in cart</span>` : visitChip(c.id)}</span>
    <span class="faint">${icon('arrow', 15)}</span></button>`; }).join('');
  if (canCreateParty() && q && !list.some(c => c.name.toLowerCase() === q))
    html += `<button class="party new" data-a="fnew"><span class="av">${icon('plus', 16, 2.2)}</span><span class="pinfo"><b>Add “${esc(S.fpq.trim())}” as a new party</b><span class="faint">Start an order now, fill in details later</span></span><span></span><span></span></button>`;
  if (!list.length) html += '<div class="empty">No parties match this search.</div>';
  return html + `<div class="party-pages row between"><span class="faint sm">${list.length} parties · Page ${S.fpage + 1} of ${pages}${q ? ' · searching all routes' : ''}</span>
    <div class="row"><button class="btn sm" data-a="fpage" data-d="-1" ${S.fpage ? '' : 'disabled'}>Previous</button>
    <button class="btn sm" data-a="fpage" data-d="1" ${S.fpage + 1 < pages ? '' : 'disabled'}>Next</button></div></div>`;
}
A.fpq = (d, el) => { S.fpq = el.value; S.fpage = 0; $('#plist').innerHTML = partyRows(); };
A.froute = d => { S.froute = d.r; S.fpq = ''; S.fpage = 0; render(); };
A.fpage = d => { S.fpage = Math.max(0, (S.fpage || 0) + +d.d); render(); scrollTop(); };
A.fparty = d => { S.fp = d.cid; S.fcat = 'all'; S.fq = ''; S.rq = ''; S.fmore = 0; go('menu'); };
A.fnew = () => {
  const id = 'c' + (CUSTOMERS.length + 1);
  CUSTOMERS.push({ id, name:S.fpq.trim(), area:'New party · details pending', territory:'Indore West', rep:ME, contact:'—', phone:'—', lastDays:0, lastVisit:0,
    aov:0, outstanding:0, limit:50000, overdue:0, trend:[0, 0, 0, 0, 0, 0], buys:[], cross:[], isNew:true });
  S.fpq = ''; A.fparty({ cid:id }); toast('New party added');
};

/* ---------- step 2: items ---------- */
VIEWS.sales.menu = () => {
  const c = C(S.fp); if (!c) return VIEWS.sales.home();
  const cat = S.fcat || 'all';
  return `${ohead('home', c.name, routeNameOf(c.id))}<div class="row between wrap" style="gap:8px">${pendingBar(c)}${visitBtn(c)}</div>
  <div class="menu-tools">
    <div class="search"><span class="ic">${icon('search')}</span><input placeholder="Search items" value="${esc(S.fq || '')}" data-in="fq" autocomplete="off" aria-label="Search items"></div>
    <div class="catchips" role="tablist">${CATS.map(([k, l]) => `<button class="${cat === k ? 'on' : ''}" data-a="fcat" data-k="${k}" role="tab" aria-selected="${cat === k}">${l}</button>`).join('')}</div></div>
  <div class="ilist" id="fgrid">${gridItems()}</div>
  ${cartBar()}`;
};
/* Every item in Tally, with the ones this party already buys pinned to the
   top. Within each block, in-stock products come first. The
   pinned block is simply absent for them rather than empty-looking. */
function buyRank(c) {
  const m = new Map();
  (c.buys || []).forEach(b => m.set(b.p, b));
  return m;
}
const inStock = p => (p?.stock ?? 0) > 0;
const stockFirst = (a, b) => Number(inStock(b)) - Number(inStock(a));
/* The two blocks, already sorted. Split out so "show more" can append one
   page instead of re-rendering everything above it -- rebuilding the whole
   list each time is what made the 8th page cost 247ms. */
function splitItems() {
  const c = C(S.fp), cat = S.fcat || 'all', q = (S.fq || '').trim();
  // Searching always covers the complete synced catalogue, before paging.
  const list = PRODUCTS.filter(p => q ? matchQ(p, q) : (cat === 'all' || PCAT[p.id] === cat));
  const bought = buyRank(c);
  if(q)return {c,list,bought,mine:[],rest:list.sort((a,b)=>partySearchRank(a,q.toLowerCase())-partySearchRank(b,q.toLowerCase())||a.name.localeCompare(b.name))};
  const mine = [], rest = [];
  list.forEach(p => (bought.has(p.id) ? mine : rest).push(p));
  /* freq is null for an item the shop has bought but not often enough to
     infer a cycle from. Those still belong in the pinned block -- they are
     things this shop buys -- just below the ones with a known pattern. */
  const freqOf = b => (b && b.freq ? b.freq[0] : -1);
  mine.sort((a, b) => {
    const x = bought.get(a.id), y = bought.get(b.id);
    return stockFirst(a, b) || (freqOf(y) - freqOf(x)) || ((y.qty || 0) - (x.qty || 0))
        || a.name.localeCompare(b.name);
  });
  /* Everything this shop has not bought, ordered so the rep meets the
     sellable fast movers first instead of whatever starts with A:
       1. in stock before out of stock -- offering what cannot be shipped
          wastes the visit, and 343 of 625 items are at or below zero
       2. then by how fast it actually sells, from 90 days of invoices
          (daily is units/day; 373 items have one)
       3. then by name, so the tail is at least predictable
     Oversold items are NOT hidden -- a negative figure means we owe stock,
     which the rep may still want to promise -- just ranked below what is
     on the shelf. */
  rest.sort((a, b) =>
    stockFirst(a, b)
    || ((b.daily || 0) - (a.daily || 0))
    || a.name.localeCompare(b.name));
  return { c, list, bought, mine, rest };
}
const moreBtn = left => left > 0
  ? `<button class="btn showmore" id="fmoreb" data-a="fmore">Show ${Math.min(left, PAGE)} more <span class="faint">· ${left} not shown, or search to narrow</span></button>`
  : '';
function gridItems() {
  const { c, list, bought, mine, rest } = splitItems();
  if (!list.length) return `<div class="empty">No items match “${esc(S.fq)}”</div>`;
  const head = t => `<div class="ihead">${t}</div>`;
  /* Cards are capped because DOM cost goes superlinear, measured on the real
     catalogue: 100 cards 33ms, 400 cards 108ms, 625 cards 615ms. The
     "bought before" block is never capped -- it is the part a rep actually
     scrolls, and it is 8 items for a typical party. The long tail is paged,
     and search reaches anything beyond it. */
  const shown = Math.min(rest.length, PAGE + (S.fmore || 0) * PAGE);
  const more = rest.length - shown;
  return (mine.length ? head(`Bought before · ${mine.length}`)
            + mine.map(p => itemRow(p, c, bought.get(p.id))).join('') : '')
       + (rest.length ? head((mine.length ? `All other items · ${rest.length}` : `All items · ${rest.length}`)
            + ((S.fq||'').trim()?` · best matches first`:` · in stock, fastest moving first`))
            + rest.slice(0, shown).map(p => itemRow(p, c)).join('') : '')
       + moreBtn(more);
}
/* ---------- item card: one format for the menu, checkout and suggestions ---------- */
const PAGE = 60;   // cards rendered per page of the long tail
const vsep = '<i class="vsep"></i>';
const rateINR = n => '₹' + Number(n).toLocaleString('en-IN', {maximumFractionDigits:2});
const facet = (ic, label, val, cls = '') => `<span class="fct ${cls}">${ic ? icon(ic, 13) : ''}${label}${val !== undefined ? ` <b class="num">${val}</b>` : ''}</span>`;
function ourRateField(p) {
  if (typeof LIVE === 'undefined' || !LIVE.on || LIVE.me?.role !== 'admin')
    return facet('', 'Our rate', p.price != null ? rateINR(p.price) : '—');
  return `<form class="our-rate-form" data-our-rate="${esc(p.id)}">
    <label>Our rate <span class="our-rate-value">₹<input name="price" value="${p.price ?? ''}" placeholder="—" inputmode="decimal" enterkeyhint="done" autocomplete="off" maxlength="15" aria-label="Our rate for ${esc(p.name)}"></span></label>
    <button type="submit">Save</button><button type="reset" aria-label="Cancel rate edit">Cancel</button>
  </form>`;
}
function purchaseRateText(p) {
  const rate = p.purchaseRate == null ? 'Unavailable' : rateINR(p.purchaseRate);
  const unit = p.purchaseRate != null && p.purchaseUnit ? ` / ${esc(p.purchaseUnit)}` : '';
  return rate + unit;
}
let dismissPurchaseRate = null;
A.purchaseinfo = (d, button) => {
  const wasOpen = button.getAttribute('aria-expanded') === 'true';
  dismissPurchaseRate?.();
  if (wasOpen) return;
  const popover = button.nextElementSibling;
  popover.hidden = false;
  button.setAttribute('aria-expanded', 'true');
  const hide = () => {
    clearTimeout(timeout);
    popover.hidden = true;
    button.setAttribute('aria-expanded', 'false');
    dismissPurchaseRate = null;
  };
  const timeout = setTimeout(hide, 2000);
  dismissPurchaseRate = hide;
};
function itemCard(p, { id = '', cls = '', right = '', edit = '', meta = '', action = '', partyCatalogue = false } = {}) {
  const unavailable = p.stock == null || p.stock <= 0;
  const owner = typeof LIVE !== 'undefined' && LIVE.on && LIVE.me?.role === 'admin';
  const purchaseInfo = owner && partyCatalogue ? `<div class="purchase-rate-info">
    <button type="button" data-a="purchaseinfo" aria-expanded="false" aria-label="Purchase rate for ${esc(p.name)}"><span aria-hidden="true">i</span></button>
    <div class="purchase-rate-popover" hidden>Purchase rate: <b>${purchaseRateText(p)}</b></div>
  </div>` : '';
  return `<div class="icard ${cls}"${id ? ` id="${id}"` : ''}><div class="pimg ic">${art(p.id)}</div>
    <div class="ibody">
      <div class="itop"><b class="iname">${esc(p.name)}</b>${purchaseInfo ? `<div class="itop-actions">${right}${purchaseInfo}</div>` : right}</div>
      <div class="ifacts">${ourRateField(p)}${vsep}<span class="fct">Stock ${p.stock == null ? 'Unknown' : `<b class="num${unavailable ? ' stock-qty-low' : ''}">${p.stock}</b>`}</span></div>
      ${unavailable ? '<span class="stock-note">Stock unavailable</span>' : ''}
      ${owner && !partyCatalogue ? `<div class="faint sm">Purchase rate: ${purchaseRateText(p)}${p.purchaseRate != null && p.purchaseDate ? ` · ${esc(p.purchaseDate)}` : ''}</div>` : ''}
      ${edit}${meta || action ? `<div class="imeta"><div class="imeta-l">${meta}</div>${action}</div>` : ''}
    </div></div>`;
}
/* rate box + stepper, used on the item menu and at checkout */
function rateEdit(c, pid, q) {
  const p = P(pid), r = rateOf(c.id, pid), lr = lastRate(c, pid);
  /* 130 of 625 items have no standard rate in Tally AND have never been
     billed to anyone, so there is no price to quote. Rendering that null
     printed the literal word "null" in the box and invited the rep to tap
     Add, sending a priceless line the server then rejects. Empty box, say
     why, and no comparison flags -- there is nothing to compare against. */
  const has = r != null && r !== '' && !isNaN(r);
  return `<div class="iedit"><label class="rate${has ? '' : ' needs'}"><span>Rate ₹</span><input class="num" value="${has ? r : ''}" placeholder="set price" inputmode="decimal" data-in="fdraftrate" data-ch="frate" data-cid="${c.id}" data-p="${pid}" aria-label="Rate for ${esc(p.name)}"></label>${qstep(c.id, pid, q)}</div>
    ${!has ? `<span class="flag crit">${icon('alert', 12)} No price on record — type one to sell this</span>`
      : ''}
    ${lr ? `<span class="ihint">Last price ${rateINR(lr.rate)}${lr.date ? ` on ${shortDate(lr.date)}` : ''}</span>` : ''}`;
}
function itemRow(p, c, b) {
  const q = cartOf(c.id)[p.id] || 0;
  return itemCard(p, { id:'pc-' + p.id, cls:(q ? 'in' : '') + (b ? ' mine' : ''),
    right: q ? `<b class="num iamt">${inr(rateOf(c.id, p.id) * q)}</b>` : '',
    edit:rateEdit(c, p.id, q), partyCatalogue:true });
}
function qstep(cid, pid, q) {
  if (!q) return `<button class="qadd" data-a="fqty" data-cid="${cid}" data-p="${pid}" data-d="1">${icon('plus', 14, 2.4)} Add</button>`;
  return `<div class="qstep"><button data-a="fqty" data-cid="${cid}" data-p="${pid}" data-d="-1" aria-label="Decrease">${icon('minus', 15, 2.4)}</button>
    <input class="num" value="${q}" inputmode="numeric" data-in="fdraftqty" data-ch="fset" data-cid="${cid}" data-p="${pid}" aria-label="Quantity">
    <button data-a="fqty" data-cid="${cid}" data-p="${pid}" data-d="1" aria-label="Increase">${icon('plus', 15, 2.4)}</button></div>`;
}
function cartBar() {
  const cid = S.fp, n = cartCount(cid);
  if (!n) return '<div id="fbar" hidden></div>';
  return `<div class="cartbar" id="fbar"><div class="cb-v num">${inr(cartTotal(cid))}</div>
    <button class="btn primary" data-a="nav" data-v="review">Review ${icon('arrow', 15)}</button></div>`;
}
A.fq = (d, el) => { S.fq = el.value; if (S.fq.trim()) S.fcat = 'all'; S.fmore = 0;
  document.querySelectorAll('[data-a="fcat"]').forEach(b => { const on = b.dataset.k === (S.fcat || 'all'); b.classList.toggle('on', on); b.setAttribute('aria-selected', on); });
  $('#fgrid').innerHTML = gridItems(); };
A.fcat = d => { S.fcat = d.k; S.fq = ''; S.fmore = 0; render(); };
A.fmore = () => {
  const { c, rest } = splitItems();
  const from = PAGE + (S.fmore || 0) * PAGE;
  S.fmore = (S.fmore || 0) + 1;
  const to = Math.min(rest.length, from + PAGE);
  const btn = $('#fmoreb');
  if (btn) btn.insertAdjacentHTML('beforebegin',
    rest.slice(from, to).map(p => itemRow(p, c)).join(''));
  const left = rest.length - to;
  if (btn) btn.outerHTML = moreBtn(left);
};
A.fqty = d => {
  const r = rateOf(d.cid, d.p);
  if (+d.d > 0 && (r == null || r === '' || isNaN(r) || +r <= 0)) {
    /* No price in Tally and none ever billed. The order would be rejected
       on arrival and the cart total would read NaN on the way there. */
    toast('Type a price for this item first', 'warn');
    const box = document.querySelector(`[data-ch="frate"][data-p="${d.p}"]`);
    if (box) box.focus();
    return;
  }
  const cart = cartOf(d.cid), n = (cart[d.p] || 0) + +d.d;
  if (n <= 0) delete cart[d.p]; else cart[d.p] = n;
  refreshF(d.p);
};
A.fset = (d, el) => { const n = parseInt(el.value, 10); if (!n || n < 1) delete cartOf(d.cid)[d.p]; else cartOf(d.cid)[d.p] = n; refreshF(d.p); };
function refreshF(pid) {
  if (S.view !== 'menu') return render();
  const el = $('#pc-' + pid); if (el) el.outerHTML = itemRow(P(pid), C(S.fp));
  $('#fbar').outerHTML = cartBar();
}

/* ---------- step 3: review / checkout ---------- */
function topMissing(c) {
  if (!c.buys.length) return [];
  const agg = {};
  invoicesFor(c).forEach(v => v.lines.forEach(([p, q]) => {
    const a = agg[p] = agg[p] || { p, times:0, qty:0, lastQty:0, lastDays:null };
    a.times++; a.qty += q; if (a.lastDays === null) { a.lastDays = v.days; a.lastQty = q; }
  }));
  return Object.values(agg).filter(a => !inCart(c.id, a.p))
    .sort((a, b) => b.times - a.times || b.qty * P(b.p).price - a.qty * P(a.p).price)
    .slice(0, 10)
    .sort((a, b) => stockFirst(P(a.p), P(b.p)));
}
/* items this party has never bought, ranked: explicit cross-sell/upsell/slow-stock match,
   then schemes, then what similar parties (especially in the same area) buy */
function pitchItems(c) {
  const hist = new Set(c.buys.length ? invoicesFor(c).flatMap(v => v.lines.map(([p]) => p)) : []);
  const peers = CUSTOMERS.filter(x => x.id !== c.id && !x.isNew);
  return PRODUCTS.filter(p => !hist.has(p.id) && !inCart(c.id, p.id)).map(p => {
    const x = c.cross.find(y => y.p === p.id), buyers = peers.filter(o => o.buys.some(b => b.p === p.id));
    const near = buyers.filter(o => o.territory === c.territory).length;
    const qty = x ? x.qty : buyers.length ? Math.max(1, Math.round(buyers.reduce((a, o) => a + o.buys.find(b => b.p === p.id).qty, 0) / buyers.length / 2)) : p.price < 1500 ? 5 : 2;
    let why, score = buyers.length * 2 + near * 3;
    if (x && x.type !== 'slow') { why = x.why; score += 20; }
    else if (p.scheme) { why = `Scheme: ${p.scheme.label}`; score += 8; }
    else if (buyers.length) { why = `Bought by ${buyers.length} similar ${buyers.length === 1 ? 'party' : 'parties'}${near ? `, ${near} nearby` : ''}`; }
    else { why = `Fast seller in ${COMPANY.city}`; }
    return { p:p.id, qty, why, score };
  }).sort((a, b) => b.score - a.score).slice(0, 6)
    .sort((a, b) => stockFirst(P(a.p), P(b.p)));
}

function reviewLine(c, pid, q) {
  return itemCard(P(pid), { right:`<b class="num iamt">${inr(rateOf(c.id, pid) * q)}</b>`, edit:rateEdit(c, pid, q) });
}
const shortDate = d => d ? d.toLocaleDateString('en-IN', { day:'numeric', month:'short' }) : '';
VIEWS.sales.review = () => {
  const c = C(S.fp); if (!c) return VIEWS.sales.home();
  const lines = Object.entries(cartOf(c.id)), sub = cartTotal(c.id), top = topMissing(c), pitch = pitchItems(c);
  const addBtn = (pid, q) => `<button class="qadd" data-a="fprev" data-cid="${c.id}" data-p="${pid}" data-q="${q}">${icon('plus', 14, 2.4)} Add</button>`;
  return `${ohead('menu', S.editing ? `Modify ${S.editing}` : 'Review order', c.name)}${pendingBar(c)}
  ${typeof LIVE !== 'undefined' && LIVE.on ? `<div class="card mini"><label>Invoice date <input type="date" data-ch="invoiceDate" data-cid="${esc(c.id)}" min="${indiaDay()}" value="${esc(DRAFTS?.data.invoiceDates?.[c.id] || indiaDay())}"></label></div>` : ''}
  <div class="ilist">${lines.length ? lines.map(([p, q]) => reviewLine(c, p, q)).join('') : `<div class="card empty">${icon('cart', 22)}<b>No items yet</b></div>`}</div>
  ${lines.length ? `<div class="card totals sm"><div class="ln"><b>Order total</b><b class="num">${inr(sub)}</b></div></div>` : ''}
  <button class="btn addmore" data-a="nav" data-v="menu">${icon('plus', 16, 2.4)} Add more items</button>
  ${top.length ? `<section class="stack" style="gap:8px"><div class="section-h"><h2>${top.length === 10 ? 'Top 10 items' : 'Items'} this party buys, not in this bill</h2></div>
    <div class="ilist">${top.map(x => { const lr = lastRate(c, x.p);
      return itemCard(P(x.p), {
        meta:`<span class="fct">${icon('clock', 12)} ${shortDate(dAgo(x.lastDays))} · <b class="num">${x.lastQty}</b> @ <b class="num">${inr(lr ? lr.rate : P(x.p).price)}</b></span>`,
        action:addBtn(x.p, x.lastQty) }); }).join('')}</div></section>` : ''}
  ${pitch.length ? `<section class="stack" style="gap:8px"><div class="section-h"><h2>Items to pitch</h2><span class="aside">Not bought by this party yet</span></div>
    <div class="ilist">${pitch.map(x => itemCard(P(x.p), { meta:`<span class="fct wrap">${icon('spark', 12)} ${esc(x.why)}</span>`, action:addBtn(x.p, x.qty) })).join('')}</div></section>` : ''}
  <div class="cartbar" id="fbar"><div><span class="cb-k">Order total</span><div class="cb-v num">${inr(sub)}</div></div>
    <button class="btn primary" data-a="fplace" ${lines.length ? '' : 'disabled'}>${S.editing ? 'Save changes' : 'Place order'} ${icon('check', 15, 2.2)}</button></div>`;
};
A.frate = (d, el) => {
  const n = parseFloat(el.value); S.rates[d.cid] = S.rates[d.cid] || {};
  if (!n || n <= 0) delete S.rates[d.cid][d.p]; else S.rates[d.cid][d.p] = Math.round(n * 100) / 100;
  render();
};
A.fprev = d => { cartOf(d.cid)[d.p] = +d.q; render(); toast(`Added ${d.q} × ${P(d.p).name}`); };
A.fplace = () => {
  const c = C(S.fp), lines = Object.entries(cartOf(c.id)).map(([p, q]) => [p, q, rateOf(c.id, p)]); if (!lines.length) return;
  const total = Math.round(cartTotal(c.id));
  S.carts[c.id] = {};
  if (S.editing) {
    const o = ORDERS.find(x => x.id === S.editing), reapprove = o.status === 'approved' && myTpl().id === 'sales';
    Object.assign(o, { lines, amount:total, edited:true, editedBy:meName(true) }); if (reapprove) o.status = 'pending';
    S.editing = null; go(myTpl().id === 'sales' ? 'myorders' : 'home');
    toast(`${o.id} updated · ${inr(total)}${reapprove ? ' · sent back for approval' : ''}`); return;
  }
  const id = 'SO-' + (2300 + ORDERS.length), t = new Date().toLocaleTimeString('en-IN', { hour:'2-digit', minute:'2-digit', hour12:false });
  ORDERS.unshift({ id, cid:c.id, rep:me().rep || c.rep, by:meName(true), amount:total, placed:`Today, ${t}`, status:'pending', lines, fresh:true });
  logVisit(c.id, total); recordSale(c.id, me().rep || c.rep, lines); S.placed[c.id] = total; S.fdone = { id, total, n:lines.length, name:c.name };
  go('fdone'); toast(`${id} placed · ${inr(total)}`);
};

/* ---------- My orders (salesperson): view and edit, even after approval ---------- */
/* A function, not a const, so live.js can replace it. Live, ORDERS is
   empty -- orders live on the server -- and this returning [] meant the
   'My orders' button showed no count, so after a refresh the home screen
   looked identical whether you had placed an order or not. */
function myOrders() { return ORDERS.filter(o => o.by ? o.by === meName(true) : myTpl().id === 'sales' && o.rep === ME); }
const ostatus = o => o.status === 'approved' ? '<span class="chip good">Approved</span>' : `<span class="chip warn">${o.edited ? 'Edited · awaiting approval' : 'Awaiting approval'}</span>`;
VIEWS.sales.myorders = () => {
  const list = myOrders();
  return `${ohead('home', 'My orders', `${list.length} ${list.length === 1 ? 'order' : 'orders'} · tap one to see or edit it`)}
  <div class="card list">${list.length ? list.map(o => { const c = C(o.cid); return `<button class="drow-main myo" data-a="olines" data-id="${o.id}">
    <span class="pinfo"><b>${c.name}</b><span class="faint">${o.placed} · ${o.id} · ${o.lines.length} items</span>
      <span class="row" style="gap:6px;margin-top:4px">${ostatus(o)}</span></span>
    <span class="dr-amt"><b class="num">${inr(o.amount)}</b><span class="faint">${icon('arrow', 14)}</span></span></button>`; }).join('')
    : '<div class="empty">No orders yet.</div>'}</div>`;
};

VIEWS.sales.fdone = () => {
  const o = S.fdone || {};
  return `<div class="card lift pad stack" style="max-width:480px;margin:24px auto;gap:14px;padding:26px;text-align:center;align-items:center">
    <span class="spark-ic" style="width:48px;height:48px;border-radius:14px;background:var(--good-soft);color:var(--good)">${icon('check', 26, 2.4)}</span>
    <div><div class="eyebrow">${o.id}</div><h1 style="font-size:22px;margin-top:4px">Order placed</h1>
      <p class="muted" style="margin-top:4px">${o.name} · ${o.n} items · <b class="num" style="color:var(--ink)">${inr(o.total)}</b></p></div>
    <div class="row wrap" style="justify-content:center"><button class="btn" data-a="modify" data-id="${o.id}">${icon('edit', 15)} Edit order</button>
      <button class="btn" data-a="billview" data-id="${o.id}">${icon('print', 15)} Print bill</button>
      <button class="btn primary" data-a="fnext">Take next order ${icon('arrow', 15)}</button></div></div>`;
};

/* ---------- bill ---------- */
function invoiceHTML(o) {
  const c = C(o.cid), lines = o.lines.map(([p, q, r]) => [p, q, r || P(p).price]);
  const sub = lines.reduce((a, [, q, r]) => a + q * r, 0);
  return `<div class="invoice">
    <div class="inv-h"><div><b class="inv-co">${COMPANY.name}</b><span>Palda Industrial Area, Indore 452020</span></div><div class="inv-t">BILL</div></div>
    <div class="inv-meta"><div><span>Bill to</span><b>${c.name}</b><span>${c.area || c.territory || ''}</span></div>
      <div><span>Invoice no.</span><b>${o.id.replace('SO-', 'MIS/26-27/0')}</b><span>${o.placed.replace('Today', dstr(TODAY))}</span></div></div>
    <table class="inv-t2"><thead><tr><th>#</th><th>Item</th><th class="r">Qty</th><th class="r">Rate</th><th class="r">Amount</th></tr></thead>
      <tbody>${lines.map(([p, q, r], i) => `<tr><td>${i + 1}</td><td>${P(p).name}</td><td class="r">${q}</td><td class="r">${inr(r)}</td><td class="r">${inr(q * r)}</td></tr>`).join('')}</tbody></table>
    <div class="inv-tot"><div class="g"><span>Total</span><span>${inr(sub)}</span></div></div>
    <div class="inv-f">Pending before this bill: ${inr(c.outstanding)} · Goods once sold will not be taken back · Subject to Indore jurisdiction</div>
  </div>`;
}
A.billview = d => {
  const o = ORDERS.find(x => x.id === d.id);
  drawer(`<div class="drawer-h"><h2 style="font-size:19px">Bill · ${o.id}</h2><button class="btn ghost sm" data-a="close" aria-label="Close">${icon('x')}</button></div>
    <div class="drawer-b">${invoiceHTML(o)}</div>
    <div class="drawer-f"><button class="btn" data-a="close">Close</button><button class="btn primary" data-a="doprint" data-id="${o.id}">${icon('print', 15)} Print</button></div>`);
};
A.doprint = d => {
  let box = document.getElementById('printArea');
  if (!box) { box = document.createElement('div'); box.id = 'printArea'; document.body.appendChild(box); }
  box.innerHTML = invoiceHTML(ORDERS.find(x => x.id === d.id));
  toast('Opening print dialog…');
  setTimeout(() => { try { window.print(); } catch (e) { toast('Printing is blocked in this preview', 'warn'); } }, 150);
};
A.fnext = () => { S.fp = null; S.fpq = ''; go('home'); };

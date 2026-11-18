/* ================= PAYMENTS — pending amount and history (view only) ================= */
VIEWS.pay = {};
const payScope = () => myTpl().id === 'sales';

/* Real receipts from Tally, newest first, max 10 per party.
   mode is Cash or Bank, taken from the ledger the money landed in.
   ref (cheque no / UTR) and "against invoice" are NULL and always will be
   until bill-wise accounting is switched on -- the demo invented both. */
function payHistory(c) {
  if (c._pay) return c._pay;
  const raw = (typeof TALLY_PAYMENTS !== 'undefined' && TALLY_PAYMENTS[c.name]) || [];
  c._pay = raw.map(p => {
    const dt = new Date(+p.date.slice(0, 4), +p.date.slice(4, 6) - 1, +p.date.slice(6, 8));
    return { days: Math.round((Date.now() - dt) / 864e5), date: dt, amt: p.amt,
             mode: p.mode, into: p.into, ref: p.ref, against: p.against,
             no: p.no, by: null };
  });
  return c._pay;
}
const payParties = () => CUSTOMERS.filter(c => !c.isNew && (!payScope() || c.rep === ME));

VIEWS.pay.home = () => {
  return `<header class="topbar"><div><h1>Payments</h1><p class="sub">${payScope() ? `Your parties · ${R(ME).territory}` : 'All parties'} · from Tally receipts</p></div></header>
  <div class="search"><span class="ic">${icon('search')}</span><input placeholder="Search party" value="${esc(S.payq || '')}" data-in="payq" aria-label="Search party"></div>
  <div class="card list" id="paylist">${payRows()}</div>`;
};
function payRows() {
  const q = (S.payq || '').toLowerCase().trim();
  const list = payParties().filter(c => !q || c.name.toLowerCase().includes(q)).sort((a, b) => b.outstanding - a.outstanding);
  if (!list.length) return `<div class="empty">No party matches “${esc(S.payq)}”</div>`;
  return list.map(c => `<button class="party" data-a="go" data-v="party" data-cid="${c.id}">
    <span class="av">${ini(c.name)}</span><span class="pinfo"><b>${c.name}</b><span class="faint">${routeNameOf(c.id)}</span></span>
    <span class="due crit">${c.outstanding ? `${inr(c.outstanding)}<i>pending</i>` : '<i>No dues</i>'}</span>
    <span class="faint">${icon('arrow', 15)}</span></button>`).join('');
}
A.payq = (d, el) => { S.payq = el.value; $('#paylist').innerHTML = payRows(); };

VIEWS.pay.party = ({ cid }) => {
  const c = C(cid); if (!c) return VIEWS.pay.home();
  const h = payHistory(c);
  return `<div class="ohead"><button class="back" data-a="nav" data-v="home" aria-label="Back">${icon('back', 16)}</button>
    <div class="oh-t"><b>${c.name}</b><span>${bits([routeNameOf(c.id), c.contact])}</span></div></div>
  <div class="card pendcard"><span>Pending</span><b class="num">${inr(c.outstanding)}</b></div>
  <div class="section-h"><h2>Last 10 payments</h2></div>
  <div class="card list">${h.map(x => `<div class="payrow"><span class="num">${dstr(x.date)}</span><b class="num">${inr(x.amt)}</b></div>`).join('')}</div>`;
};

/* Purchase costs are server-authorised owner data; no mutation action exists. */
VIEWS.purchasecost={};
function purchaseCostRows(){
 if(typeof LIVE==='undefined'||!LIVE.on||LIVE.me?.role!=='admin')return '<p>Owner access only.</p>';
 const q=(S.purchaseq||'').trim().toLowerCase(),sort=S.purchasesort||'name';
 const rows=PRODUCTS.filter(p=>!q||(p.name+' '+(p.grp||'')).toLowerCase().includes(q)).sort((a,b)=>
   (q?partySearchRank(a,q)-partySearchRank(b,q):0)||(sort==='cost'?(a.purchaseRate==null?b.purchaseRate==null?0:1:b.purchaseRate==null?-1:b.purchaseRate-a.purchaseRate):0)||a.name.localeCompare(b.name));
 const shown=60+(S.purchasemore||0)*60;
 return rows.slice(0,shown).map(p=>`<div class="card mini"><b>${esc(p.name)}</b><p>Purchase rate: <b>${purchaseRateText(p)}</b></p><p class="faint sm">${p.purchaseDate?'Last purchase invoice · '+dstr(ymdToDate(p.purchaseDate)):'No synced purchase invoice rate'}</p><div class="ifacts">${ourRateField(p)}</div></div>`).join('')+(rows.length>shown?'<button class="btn block" data-a="purchasemore">Show more</button>':'');
}
VIEWS.purchasecost.home=()=>typeof LIVE==='undefined'||LIVE.me?.role!=='admin'?'<p>Owner access only.</p>':`<header class="topbar"><h1>Purchase rates</h1><p>From Tally purchase invoices · read only</p></header><div class="search"><input data-in="purchaseq" placeholder="Search all products" aria-label="Search purchase rates" value="${esc(S.purchaseq||'')}"></div><label>Sort<select data-ch="purchasesort"><option value="name" ${S.purchasesort!=='cost'?'selected':''}>Product A–Z</option><option value="cost" ${S.purchasesort==='cost'?'selected':''}>Purchase rate: high to low</option></select></label><div id="purchase-products">${purchaseCostRows()}</div>`;
A.purchaseq=(d,el)=>{S.purchaseq=el.value;S.purchasemore=0;$('#purchase-products').innerHTML=purchaseCostRows();};
A.purchasesort=(d,el)=>{S.purchasesort=el.value;S.purchasemore=0;$('#purchase-products').innerHTML=purchaseCostRows();};
A.purchasemore=()=>{S.purchasemore=(S.purchasemore||0)+1;$('#purchase-products').innerHTML=purchaseCostRows();};

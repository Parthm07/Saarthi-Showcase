/* ================= VISIT TRACKING + VISIT REPORT =================
   One tap on "Log visit" records the date and time; placing an order counts as a visit
   with its order value. The Visit report shows today and daily history for the team
   or one salesperson. Seeded with the last 3 months. */
ICONS.pin = '<path d="M12 21s-7-6.2-7-11.5a7 7 0 0114 0C19 14.8 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.5"/>';

/* Built from real Tally sales, not a simulation.
   IMPORTANT: Tally records the visits that became a SALE and nothing else.
   A rep who walked into four shops and sold to one leaves one trace. So
   this is order activity, not visit coverage -- any "visits made" or
   "coverage %" read off it would overstate conversion to 100%. Real
   coverage needs check-ins the app itself captures (logVisit does that
   from today onward). */
const VISITS = [];
/* Who the team breakdown lists. The demo's four, unless live.js
   replaces it with the people who can actually sign in. */
let teamList = () => REPS;
/* Named and callable, not an IIFE. Live, TALLY_DAILY is still empty when
   this file loads -- applyData fills it afterwards from /api/data -- so
   seeding once at load left the report reading zero for every day. live.js
   calls this again once the real data is in. */
function seedVisitsFromTally() {
  const daily = typeof TALLY_DAILY !== 'undefined' ? TALLY_DAILY : {};
  const today = new Date(); today.setHours(0, 0, 0, 0);
  // Keep anything the rep logged in this session; replace the Tally rows.
  for (let i = VISITS.length - 1; i >= 0; i--) if (VISITS[i].agg || VISITS[i].fromTally) VISITS.splice(i, 1);
  const lines = typeof TALLY_LINES !== 'undefined' ? TALLY_LINES : [];
  if (lines.some(l => l[5])) {
    const invoices = new Map();
    for (const [ymd,cid,pid,qty,amount,guid,rep,total] of lines) {
      if (!guid) continue;
      let v = invoices.get(guid);
      if (!v) {
        const date = new Date(+ymd.slice(0,4),+ymd.slice(4,6)-1,+ymd.slice(6,8));
        v = {cid,rep,days:Math.round((today-date)/864e5),time:'',outcome:'order',
          amt:0,orders:1,fromTally:true,guid,invoiceTotal:total};
        invoices.set(guid,v);
      }
      v.amt += amount || 0;
    }
    for (const v of invoices.values()) {
      if (v.invoiceTotal != null) v.amt = v.invoiceTotal;
      if (v.days >= 0 && v.days <= 90) VISITS.push(v);
    }
    return;
  }
  Object.entries(daily).forEach(([ymd, d]) => {
    const dt = new Date(+ymd.slice(0, 4), +ymd.slice(4, 6) - 1, +ymd.slice(6, 8));
    const days = Math.round((today - dt) / 864e5);
    if (days < 0 || days > 90) return;
    VISITS.push({ cid:null, rep:null, days, time:null, outcome:'order',
                  amt:d.value, orders:d.orders, parties:d.parties, agg:true });
  });
}
seedVisitsFromTally();

function seedCloudVisits(rows) {
  for (let i = VISITS.length - 1; i >= 0; i--)
    if (VISITS[i].fromCloud) VISITS.splice(i, 1);
  const today = Date.parse(indiaDay());
  const logged = new Set(VISITS.filter(v => v.logged).map(v => `${v.rep}|${v.cid}|${v.days}`));
  for (const {rep, cid, date} of rows) {
    if (!C(cid) || !/^\d{8}$/.test(date)) continue;
    const days = (today - Date.parse(`${date.slice(0,4)}-${date.slice(4,6)}-${date.slice(6)}`)) / 864e5;
    if (days >= 0 && !logged.has(`${rep}|${cid}|${days}`))
      VISITS.push({rep, cid, days, outcome:'visit', fromCloud:true});
  }
}
const nowTime = () => new Date().toLocaleTimeString('en-IN', { hour:'2-digit', minute:'2-digit', hour12:false });
const todayVisit = cid => {
  const rows = VISITS.filter(v => v.cid === cid && v.days === 0
    && (typeof LIVE === 'undefined' || !LIVE.on || v.rep === LIVE.me.email));
  return rows.find(v=>v.logged) || rows[0];
};
let visitStoreKey = null, visitStoreBlocked = false;
function restoreVisitLog() {
  if (typeof LIVE === 'undefined' || !LIVE.on) return;
  const key = 'saarthi.visits.v1:' + encodeURIComponent(LIVE.me.email.toLowerCase())
    + ':' + encodeURIComponent(LIVE.status.company || '');
  const previous = visitStoreKey === key ? VISITS.filter(v=>v.logged) : [];
  visitStoreKey = key; visitStoreBlocked = false;
  for (let i=VISITS.length-1;i>=0;i--) if (VISITS[i].logged) VISITS.splice(i,1);
  let rows = [];
  try {
    const raw = localStorage.getItem(key);
    if (raw) {
      const saved = JSON.parse(raw);
      if (saved.v !== 1 || !Array.isArray(saved.rows)) throw new Error('Invalid visits');
      rows = saved.rows;
    }
  } catch(e) { visitStoreBlocked = true; }
  const merged = new Map();
  const today = new Date();today.setHours(0,0,0,0);
  for (const row of [...rows,...previous]) {
    if (!row || typeof row !== 'object') continue;
    const at = new Date(row.at), day = new Date(row.at);
    if (!row.cid || !Number.isFinite(at.getTime()) || !C(row.cid)) continue;
    day.setHours(0,0,0,0);
    const days = Math.round((today-day)/864e5);
    if (days < 0 || days > 90) continue;
    const id = row.cid+'|'+day.toISOString();
    if (merged.has(id)) continue;
    merged.set(id,{cid:row.cid,at:at.toISOString(),rep:LIVE.me.email,by:row.by,
      days,time:at.toLocaleTimeString('en-IN',{hour:'2-digit',minute:'2-digit',hour12:false}),
      outcome:'visit',amt:0,orders:0,logged:true,synced:row.synced===true});
  }
  VISITS.push(...merged.values());
  flushVisitLog();
}
function saveVisitLog() {
  if (visitStoreBlocked) return false;
  try {
    localStorage.setItem(visitStoreKey,JSON.stringify({v:1,rows:VISITS.filter(v=>v.logged)
      .map(v=>({cid:v.cid,at:v.at,by:v.by,synced:v.synced===true}))}));
    return true;
  } catch(e) { return false; }
}
function logVisit(cid, amt = 0) {
  if (typeof LIVE !== 'undefined' && LIVE.on) {
    restoreVisitLog();
    let v = VISITS.find(v=>v.logged && v.cid===cid && v.days===0);
    if (!v) {
      v = {cid,rep:LIVE.me.email,by:LIVE.me.name || LIVE.me.email,days:0,
        at:new Date().toISOString(),time:nowTime(),outcome:'visit',amt:0,orders:0,logged:true};
      VISITS.unshift(v);
    }
    v.saved = saveVisitLog();flushVisitLog();
    return v;
  }
  const ex = todayVisit(cid);
  if (ex) { if (amt) { ex.outcome = 'order'; ex.amt = (ex.amt || 0) + amt; ex.orders = (ex.orders || 0) + 1; } return ex; }
  const v = { cid, rep:me().rep || C(cid).rep, by:meName(true), days:0, time:nowTime(), outcome: amt ? 'order' : 'visit', amt, orders: amt ? 1 : 0 };
  VISITS.unshift(v); return v;
}
let visitSyncBusy=false;
async function flushVisitLog(){
  if(visitSyncBusy||typeof LIVE==='undefined'||!LIVE.on||!LIVE.me?.email)return;
  const rows=VISITS.filter(v=>v.logged&&!v.synced&&v.rep===LIVE.me.email).slice(0,100);
  if(!rows.length)return;
  const key=visitStoreKey,email=LIVE.me.email;visitSyncBusy=true;
  try{
    await api('/api/visits',{email,company:LIVE.status.company||'',rows:rows.map(v=>({cid:v.cid,at:v.at}))});
    if(key===visitStoreKey&&email===LIVE.me.email){for(const row of rows)row.synced=true;saveVisitLog();}
  }catch(error){/* Local visit remains visible and retries on the next refresh. */}
  finally{visitSyncBusy=false;}
}
const visitChip = cid => { const v = todayVisit(cid); return v ? `<span class="chip ${v.outcome === 'order' ? 'good' : 'info'}">${icon('check', 11, 2.6)} ${v.time}</span>` : ''; };
const visitBtn = c => { const v = todayVisit(c.id);
  return v ? `<span class="chip good vchip">${icon('check', 12, 2.6)} Visited ${v.time}</span>`
    : `<button class="btn sm" data-a="vlog" data-cid="${c.id}">${icon('pin', 14)} Log visit</button>`; };
A.vlog = d => { const v = logVisit(d.cid); render(); toast(v.saved === false
  ? 'Visit is only in this open page: device storage failed. Keep it open and retry.'
  : `Visit logged · ${C(d.cid).name} · ${v.time}, ${dstr(TODAY)}`); };

/* ---------- Visit report ---------- */
VIEWS.vis = {};
/* Two kinds of row live in VISITS and they used to be counted the same
   way, which made the two halves of this screen measure different things:
   history counted ORDERS (from Tally's own per-day totals) while today
   counted SHOPS THAT ORDERED. A shop that ordered twice in a day showed
   as one order. Now both count orders, and shops separately. */
function visitStats(rep, day) {
  const vs = VISITS.filter(v => v.days === day && (rep === 'all' || v.rep === rep)
    && !(typeof LIVE !== 'undefined' && LIVE.on && v.fromTally && !v.rep));
  const seen = new Set(), bought = new Set();
  let shops = 0, orders = 0, ordered = 0, rev = 0;
  for (const v of vs) {
    if (v.checkOnly) continue;
    rev += v.amt || 0;
    if (v.agg) {                       // a day's totals out of Tally
      shops += v.parties || 0;
      orders += v.orders || 0;
      ordered += v.parties || 0;       // every party in that total bought
    } else {                           // one shop, logged or ordered today
      if (!seen.has(v.cid)) { seen.add(v.cid); shops++; }
      if (v.outcome === 'order') {
        orders += v.orders || 1;       // a shop can order twice in a day
        if (!bought.has(v.cid)) { bought.add(v.cid); ordered++; }
      }
    }
  }
  return { shops, orders, ordered, rev };
}
VIEWS.vis.home = () => {
  const self = myTpl().id === 'sales', rep = self ? ME : (S.vRep || 'all'), t = visitStats(rep, 0);
  const rows = weekRange('vis').map(d => ({ day:d, label:dayLabel(d), sub:dayNum(d), ...visitStats(rep, d) }));
  const tot = rows.reduce((a, s) => ({ shops:a.shops + s.shops, orders:a.orders + s.orders, ordered:a.ordered + s.ordered, rev:a.rev + s.rev }), { shops:0, orders:0, ordered:0, rev:0 });
  const noOrder = VISITS.filter(v => v.days === 0 && C(v.cid) && v.outcome !== 'order' && (rep === 'all' || v.rep === rep)
    && !VISITS.some(o=>o.cid===v.cid && o.rep===v.rep && o.days===0 && o.outcome==='order'));
  const tile = (k, v, d = '', cls = '') => `<div class="card kpi"><span class="k">${k}</span><span class="v num ${cls}">${v}</span>${d ? `<span class="d">${d}</span>` : ''}</div>`;
  const repWeek = id => weekRange('vis').reduce((a, d) => { const x = visitStats(id, d); return { shops:a.shops + x.shops, orders:a.orders + x.orders, ordered:a.ordered + x.ordered, rev:a.rev + x.rev }; }, { shops:0, orders:0, ordered:0, rev:0 });
  return `<header class="topbar"><div><h1>Visit report</h1><p class="sub">${self ? 'Shops you visited and the orders you took' : 'Shops visited and orders taken by your team'}</p></div></header>
  ${self ? '' : `<div class="catchips">${[['all', 'All team'], ...teamList().map(r => [r.id, r.name])].map(([k, l]) => `<button class="${rep === k ? 'on' : ''}" data-a="vrep" data-k="${esc(k)}">${esc(l)}</button>`).join('')}</div>`}
  <section class="stack" style="gap:10px"><div class="section-h"><h2>Today${rep === 'all' ? '' : ' · ' + esc(R(rep)?.name || rep)}</h2><span class="aside">${dstr(TODAY)}</span></div>
    <div class="kpi4">${tile('Shops visited', t.shops)}${tile('Orders', t.orders)}${tile('Order value', inrS(t.rev))}${tile('No order', t.shops - t.ordered, '', t.shops - t.ordered ? 'crit' : '')}</div>
    ${noOrder.length ? `<p class="faint" style="font-size:12.5px">No order today from ${noOrder.slice(0, 5).map(v => C(v.cid).name).join(', ')}${noOrder.length > 5 ? ` and ${noOrder.length - 5} more` : ''}.</p>` : ''}</section>
  ${self ? '' : `<section class="stack" style="gap:10px"><div class="section-h"><h2>History</h2>${weekNav('vis')}</div>
    <div class="kpi4 k3">${tile('Shops visited', tot.shops, `${(tot.shops / 7).toFixed(1)} a day`)}${tile('Orders', tot.orders, `${tot.shops ? Math.round(tot.ordered / tot.shops * 100) : 0}% of visits`)}${tile('Order value', inrS(tot.rev), tot.orders ? `${inr(tot.rev / tot.orders)} per order` : '')}</div>
    <div class="card pad stack"><h2 style="font-size:14.5px">Shops visited and orders</h2>
      ${selBars('vis', rows, { a:'shops', b:'orders', cap:r => `${dstr(dAgo(r.day))} · <b>${r.shops}</b> shops visited · <b>${r.orders}</b> gave an order` })}
      <div class="row" style="gap:14px;font-size:12px"><span class="row" style="gap:6px"><i class="sw" style="background:var(--accent-soft)"></i>Shops visited</span><span class="row" style="gap:6px"><i class="sw" style="background:var(--accent)"></i>Gave an order</span></div></div>
    <div class="card pad stack"><h2 style="font-size:14.5px">Order value</h2>
      ${selBars('vis', rows, { a:'rev', color:'var(--gold-strong)', fmt:inrS, cap:r => `${dstr(dAgo(r.day))} · <b>${inr(r.rev)}</b> order value` })}</div>
    ${rep === 'all' ? `<div class="card" style="overflow:hidden"><div class="table-wrap"><table class="t dt"><thead><tr><th>Salesperson</th><th class="r">Shops</th><th class="r">Orders</th><th class="r">Order %</th><th class="r">Value</th></tr></thead><tbody>
      ${teamList().map(r => { const x = repWeek(r.id); return `<tr data-a="vrep" data-k="${esc(r.id)}" tabindex="0"><td>${esc(r.name)}</td><td class="r num">${x.shops}</td><td class="r num">${x.orders}</td><td class="r num">${x.shops ? Math.round(x.orders / x.shops * 100) : 0}%</td><td class="r num">${inrS(x.rev)}</td></tr>`; }).join('')}
    </tbody></table></div></div>` : ''}
  </section>`}`;
};
A.vrep = d => { S.vRep = d.k; S.cs.vis = null; render(); };

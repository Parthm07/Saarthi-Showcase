/* Live data and real sign-in.
 *
 * Overwrites the globals with this user's scoped slice from the API.
 * Demo mode must be explicitly selected with ?demo=1; a network failure
 * never turns a production order into a browser-only demo order.
 *
 * The globals are MUTATED IN PLACE, never reassigned: data-tally.js declares
 * them with const, and other modules captured those bindings at load time.
 */
const LIVE = { on:false, me:null, status:null, orders:[] };
const indiaDay = (date = new Date()) => new Intl.DateTimeFormat('en-CA', {
  timeZone:'Asia/Kolkata', year:'numeric', month:'2-digit', day:'2-digit',
}).format(date);
const indiaHour = new Intl.DateTimeFormat('en-IN', {
  timeZone:'Asia/Kolkata', hour:'numeric', hourCycle:'h23',
});

function liveRefreshHours(date = new Date()) {
  const hour = Number(indiaHour.format(date));
  return hour >= 9 && hour < 19;
}

/* The sign-in and loading screens replace everything inside #app, which
   destroys the shell's own skeleton -- #appbar, #main, #tabbar.
   renderChrome() then throws on a null element.

   That never showed up while testing by setting a cookie and reloading,
   because a reload rebuilds the skeleton from index.html. It DOES happen
   on the real Google flow: the callback verifies the token and calls
   boot() in the same page, so the app crashed the moment someone actually
   signed in. Kept once, restored before the shell renders. */
const APP_SKELETON = document.getElementById('app').innerHTML;
function restoreSkeleton() {
  const app = document.getElementById('app');
  if (!document.getElementById('main')) app.innerHTML = APP_SKELETON;
}

/* data-tally.js is the OFFLINE snapshot, and it is deliberately NOT shipped
   to the deployed site: it holds all 605 customers with their outstanding
   balances, and static files are served before anyone has signed in -- so
   publishing it would hand the whole customer book to any visitor. Where it
   is absent these globals do not exist at all, so declare them here. A bare
   TALLY_LINES in dash.js then resolves to the window property instead of
   throwing. */
if (typeof TALLY_PAYMENTS === 'undefined') window.TALLY_PAYMENTS = {};
if (typeof TALLY_DAILY === 'undefined') window.TALLY_DAILY = {};
if (typeof TALLY_LINES === 'undefined') window.TALLY_LINES = [];

/* data-tally.js declares these with const, so they can only be filled, not
   replaced. Arrays get emptied and refilled; objects get their keys
   deleted. Same trick the generated file itself uses on PRODUCTS. */
function refill(arr, rows) { arr.length = 0; rows.forEach(r => arr.push(r)); }
function rekey(obj, src) {
  Object.keys(obj).forEach(k => delete obj[k]);
  Object.assign(obj, src);
}

async function api(path, body, timeout = 30000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const r = await fetch(path, {
      method: body === undefined ? 'GET' : 'POST',
      headers: body === undefined ? {} : { 'Content-Type':'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials:'same-origin',
      signal:controller.signal,
    });
    let data = null;
    try { data = await r.json(); } catch (e) {
      if (controller.signal.aborted) throw e;
      if (r.ok) throw new Error('Could not read the server response. Check your connection and retry.');
    }
    if (!r.ok) throw Object.assign(new Error(errText(data, r)), { status:r.status, data });
    return data;
  } catch (e) {
    if (controller.signal.aborted) throw new Error('The server took too long to respond. Retry in a moment.');
    throw e;
  } finally { clearTimeout(timer); }
}

/* `detail` is not always a string. Our own 409 for a near-duplicate shop
   sends {message, matches}, and FastAPI's validation errors send a list of
   {loc, msg, type}. Passing either to new Error() produced a toast reading
   "[object Object]", which tells the user nothing about what went wrong. */
function errText(data, r) {
  const d = data && data.detail;
  if (typeof d === 'string') return d;
  if (Array.isArray(d)) {
    // FastAPI validation: name the field, not the type machinery.
    return d.map(e => {
      const field = (e.loc || []).filter(x => x !== 'body').join(' ');
      return field ? `${field}: ${e.msg}` : e.msg;
    }).join('; ') || r.statusText;
  }
  if (d && typeof d === 'object') return d.message || JSON.stringify(d);
  return (data && data.message) || r.statusText || `Error ${r.status}`;
}

/* ---------- sign-in screen ---------- */
/* Rendered outside the app shell: there is nothing to show until we know
   who is looking, and which shops they are allowed to see. */
function signInScreen(cfg, err) {
  document.getElementById('app').innerHTML = `
    <div class="signin">
      <div class="si-card">
        <div class="si-brand">Saarthi</div>
        <p class="si-sub">Sign in with the Google account your office registered.</p>
        ${err ? `<div class="si-err">${esc(err)}</div>` : ''}
        <div id="gbtn"></div>
        ${themeControls()}
        ${cfg.dev_login ? `
          <div class="si-dev">
            <label>Development sign-in — no password</label>
            <input id="devmail" type="email" placeholder="you@example.com" autocomplete="username">
            <button class="btn primary" id="devgo">Sign in</button>
          </div>` : ''}
      </div>
    </div>`;
  if (cfg.dev_login) {
    const go = async () => {
      const email = document.getElementById('devmail').value.trim();
      if (!email) return;
      try { await api('/api/auth/dev', { email }); boot(); }
      catch (e) { signInScreen(cfg, e.message); }
    };
    document.getElementById('devgo').onclick = go;
    document.getElementById('devmail').onkeydown = e => { if (e.key === 'Enter') go(); };
  }
  if (!cfg.google_client_id) return;
  /* The Google script tag is async, so on a cold load it is usually NOT
     ready by the time this runs -- checking window.google once renders no
     button and tells the rep sign-in is broken when nothing is wrong.
     Wait for it, and only give up after a timeout that really does mean
     the script could not be fetched. */
  whenGoogleReady(ok => {
    const box = document.getElementById('gbtn');
    if (!box) return;                       // screen moved on
    if (!ok) {
      box.innerHTML = '<div class="si-err">Google sign-in could not load. '
        + 'Check the connection and reload.</div>';
      return;
    }
    google.accounts.id.initialize({
      client_id: cfg.google_client_id,
      callback: async res => {
        try { await api('/api/auth/google', { credential:res.credential }); boot(); }
        catch (e) { signInScreen(cfg, e.message); }
      },
    });
    google.accounts.id.renderButton(box,
      { theme:'outline', size:'large', width:260, text:'signin_with' });
  });
}

function whenGoogleReady(cb, waited = 0) {
  if (window.google && google.accounts && google.accounts.id) return cb(true);
  if (waited >= 10000) return cb(false);
  setTimeout(() => whenGoogleReady(cb, waited + 120), 120);
}

/* ---------- map the real user onto the app's template system ---------- */
/* The prototype decides which modules to show from a template id. Rather
   than rebuild that, the signed-in user is injected as a user row and the
   role picks the template: an admin gets the owner's view, a salesperson
   the order-taking view. */
function installUser(me) {
  const tpl = me.role === 'admin' ? 'owner' : 'sales';
  const id = 'live';
  /* Every module looks a salesperson up with R(id) against REPS -- six call
     sites, several of which dereference the result immediately
     (R(ME).territory). Live, ME is 'live' and nothing matched, so R(ME) was
     undefined and the Payments screen threw. That killed render() BEFORE it
     wrote #main, so the tab highlight moved (renderChrome runs first) while
     the previous screen stayed on display -- which reads exactly like "the
     footer doesn't work".
     One matching entry fixes every call site; guarding six of them would
     not have fixed the seventh. target/achieved are null, not invented:
     Tally holds no sales targets. */
  const scope = 'All sales parties';
  const rep = { id: me.email, name: me.name || me.email.split('@')[0],
                ini: ini(me.name || me.email), territory: scope,
                target: null, achieved: null, visitCov: null, conv: null,
                accept: null, aov: null };
  const repAt = REPS.findIndex(r => r.id === me.email);
  if (repAt >= 0) REPS[repAt] = rep; else REPS.push(rep);
  const row = { id, name:me.name || me.email.split('@')[0], phone:'',
                tpl, status:'active', seen:'Active now',
                area:(me.routes || []).join(', ') || 'All territories',
                rep:me.email, email:me.email, live:true };
  const at = store.users.findIndex(u => u.id === id);
  if (at >= 0) store.users[at] = row; else store.users.unshift(row);
  return id;
}

function applyData(d) {
  LIVE.on = true; LIVE.me = d.me; LIVE.status = d.status;
  /* The demo is pinned to 11 Sept 2026 so its canned figures line up. Live
     that makes "today" five weeks in the past: the visit report reads zero
     shops and zero orders however many were taken, and every dashboard
     period measures the wrong week. Everything date-relative reads TODAY,
     so correcting it here corrects all of them. */
  TODAY = new Date();
  TODAY.setHours(0, 0, 0, 0);
  COMPANY.today = new Date().toLocaleDateString('en-IN',
    { weekday:'long', day:'numeric', month:'long', year:'numeric' });
  refill(PRODUCTS, d.products);
  refill(CUSTOMERS, d.customers);
  refill(CATS, d.cats);
  rekey(PCAT, Object.fromEntries(d.products.map(p => [p.id, p.grp])));
  rekey(TALLY_PAYMENTS, d.payments);
  rekey(TALLY_DAILY, d.daily);
  refill(TALLY_LINES, d.lines);
  /* All sales parties are available. c.rep is a legacy UI lookup only;
     invoice/order ownership continues to come from the server. */
  CUSTOMERS.forEach(c => { c._inv = null; c._pay = null; c.rep = LIVE.me.email; });
  rebuildSalesFromTally();
  LIVE.lastRefresh = new Date();
  /* Now that TALLY_DAILY actually has something in it. */
  if (typeof seedVisitsFromTally === 'function') seedVisitsFromTally();
  restoreVisitLog();
  seedCloudVisits(d.visits || []);
  /* Category chips are derived from the live product list, so PCAT must use
     the same keys CATS does -- anything outside the top groups is "other". */
  const keep = new Set(d.cats.slice(1).map(c => c[0]));
  Object.keys(PCAT).forEach(k => { if (!keep.has(PCAT[k])) PCAT[k] = 'other'; });
  ORDERS.length = 0;
}

/* Loading takes real time -- the shops, prices, reorder cycles and six
   months of history are one request, and on a phone on mobile data that is
   several seconds. A blank screen for that long reads as a broken app, so
   say what is happening and roughly how far along it is. */
function loadingScreen(step, pct) {
  const app = document.getElementById('app');
  const bar = app.querySelector('.boot-bar i');
  if (bar) {                       // already up: just move it on
    bar.style.width = pct + '%';
    app.querySelector('.boot-step').textContent = step;
    return;
  }
  app.innerHTML = `
    <div class="booting">
      <div class="boot-card">
        <div class="brand-mark lg">${icon('spark', 24, 2)}</div>
        <div class="boot-name">Saarthi</div>
        <div class="boot-step">${esc(step)}</div>
        <div class="boot-bar"><i style="width:${pct}%"></i></div>
      </div>
    </div>`;
}

function bootFailed(msg) {
  const app = document.getElementById('app');
  app.innerHTML = `
    <div class="booting"><div class="boot-card">
      <div class="brand-mark lg">${icon('spark', 24, 2)}</div>
      <div class="boot-name">Saarthi</div>
      <div class="si-err" style="margin-top:14px">${esc(msg)}</div>
      <p class="faint sm">Saved drafts stay on this device. Reconnect and retry to continue.</p>
      <button class="btn primary block" onclick="boot()">Retry connection</button>
    </div></div>`;
}

async function boot() {
  if (new URLSearchParams(location.search).get('demo') === '1') {
    restoreSkeleton(); startApp(); return;
  }
  let cfg;
  try {
    cfg = await api('/api/config', undefined, 12000);
    if (!cfg || typeof cfg.dev_login !== 'boolean') throw new Error('Invalid server configuration response');
  } catch (e) {
    bootFailed('Cannot reach the server. Check your connection and retry.');
    return;
  }
  loadingScreen('Checking your sign-in…', 12);
  let me;
  try {
    me = await api('/api/me', undefined, 12000);
    if (!me?.email) throw new Error('Could not confirm your sign-in. Retry the connection.');
  } catch (e) {
    if (e.status === 401 || e.status === 403) {
      signInScreen(cfg, e.status === 403 ? e.message : '');
    } else {
      bootFailed(e.status === 503 ? 'Saarthi is temporarily unavailable. Retry in a moment.' : e.message);
    }
    return;
  }
  loadingScreen('Loading your shops, prices and order history…', 35);
  let d;
  try {
    d = await api('/api/data', undefined, 20000);
    if (!d?.me?.email || !Array.isArray(d.products) || !Array.isArray(d.customers)
        || !Array.isArray(d.cats)) throw new Error('Could not load your order data. Retry the connection.');
  } catch (e) {
    bootFailed(e.message);
    return;
  }
  loadingScreen(`${d.customers.length} shops · ${d.products.length} items`, 85);
  applyData(d);
  initOrderDrafts();
  loadingScreen('Getting your orders…', 94);
  try { LIVE.orders = await api(orderFeed(me)); } catch (e) {}
  seedVisitsFromOrders();
  if (me.role === 'admin') {
    await Promise.all([loadProspects(), loadPeople()]);
    seedVisitsFromOrders();   // now that every rep has a REPS entry
    // These modules postdate any template already saved in this browser,
    // so make sure the owner has them before the shell builds the tab bar.
    // 'users' is dropped: it edits a store in this browser, which live is
    // a screen that looks like it grants access and does not.
    const owner = TPL('owner');
    if (owner) {
      for (const m of ['newshops', 'people', 'purchasecost'])
        if (!owner.mods.includes(m)) owner.mods.push(m);
      owner.mods = owner.mods.filter(m => m !== 'users');
      persist();
    }
  }
  restoreSkeleton();        // the loading screen replaced it
  startApp(installUser(me));
  // Restore carts for recovery, but start salespeople at party selection.
  // Opening a saved party is an explicit Resume draft / Check order action.
  if (me.role === 'salesperson' && myMods().includes('orders')) {
    S.mod = 'orders'; S.fp = null; S.froute = 'all'; S.fpq = ''; S.fpage = 0;
    go('home');
  }
  startLiveRefresh();
  flushOrderAcknowledgements();
}

function liveSyncMessage() {
  return LIVE.refreshError || (LIVE.lastRefresh
    ? `Data loaded ${LIVE.lastRefresh.toLocaleTimeString('en-IN', {hour:'2-digit',minute:'2-digit'})}`
    : 'Waiting for the latest data');
}

const orderFeed = user => user.role === 'admin' ? '/api/orders?include_tally=true' : '/api/orders';

function refreshLiveData(ordersOnly = false, {automatic = false} = {}) {
  if (automatic && !liveRefreshHours()) return Promise.resolve(false);
  const running = LIVE.refreshing;
  if (running) {
    // A resume or manual catalogue refresh must not get lost behind an orders poll.
    return running.ordersOnly && !ordersOnly
      ? running.promise.then(() => refreshLiveData(false, {automatic})) : running.promise;
  }
  if (!LIVE.on || document.hidden || DRAFT_SENDING.size || document.querySelector('[data-our-rate][data-dirty]') || (S.mod === 'people' && S.person))
    return Promise.resolve(false);
  const promise = loadLiveData(ordersOnly).finally(() => { LIVE.refreshing = null; });
  LIVE.refreshing = { ordersOnly, promise };
  return promise;
}

async function loadLiveData(ordersOnly) {
  try {
    const [data, orders] = await Promise.all([ordersOnly ? null : api('/api/data'), api(orderFeed(LIVE.me))]);
    if (!Array.isArray(orders) || (!ordersOnly &&
        (!data?.me?.email || ![data.lines,data.products,data.customers,data.cats].every(Array.isArray))))
      throw new Error('Could not load current data. Retry the connection.');
    if (data && data.me.email !== LIVE.me.email) { location.reload(); return false; }
    if (document.hidden || DRAFT_SENDING.size || document.querySelector('[data-our-rate][data-dirty]') || (S.mod === 'people' && S.person)) return false;
    // Do not replace a focused quantity/rate field or an unsaved memory-only cart.
    const active = document.activeElement;
    const searchResults = active?.dataset?.in === 'fq' ? $('#fgrid')
      : active?.dataset?.in === 'fpq' ? $('#plist') : null;
    if (!searchResults && active?.matches('input,textarea,select,[contenteditable="true"]')) return false;
    if (DRAFTS && !saveOrderDrafts()) return false;
    const openOrder = document.querySelector('[data-live-order]')?.dataset.liveOrder;
    if (data) {
      applyData(data);
      installUser(data.me);
      initOrderDrafts();
    }
    LIVE.orders = orders; LIVE.refreshError = '';
    seedVisitsFromOrders();
    if (searchResults && (active.dataset.in !== 'fq' || C(S.fp))) {
      if (data) searchResults.innerHTML = active.dataset.in === 'fq' ? gridItems() : partyRows();
    } else {
      render();
      if (openOrder && liveOrder(openOrder)) A.oview({no:openOrder});
    }
    document.querySelectorAll('[data-live-sync]').forEach(el => { el.textContent = liveSyncMessage(); });
    flushOrderAcknowledgements();
    return true;
  } catch (e) {
    console.warn('Live data refresh failed', e);
    LIVE.refreshError = 'Could not refresh. Showing the last loaded data.';
    const el = document.querySelector('[data-live-sync]');
    if (el) el.textContent = LIVE.refreshError;
    return false;
  }
}

function startLiveRefresh() {
  if (LIVE.refreshStarted) return;
  LIVE.refreshStarted = true;
  const refresh = (ordersOnly = false) => refreshLiveData(ordersOnly, {automatic:true});
  let ticks = 0;
  setInterval(() => {
    if (++ticks % 2 === 0) refresh();
    else if (S.view === 'myorders' || S.mod === 'approvals' || document.querySelector('[data-live-order]'))
      refresh(true);
  }, 7500);
  window.addEventListener('online', () => refresh());
  window.addEventListener('focus', () => refresh());
  window.addEventListener('pageshow', event => { if (event.persisted) refresh(); });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
}
A.refreshlive = async () => {
  if (await refreshLiveData()) toast('Data refreshed');
  else toast(LIVE.refreshError || 'Finish editing the current field, then refresh.', 'warn');
};

document.addEventListener('input', event => {
  const form = event.target.form;
  if (form?.dataset?.ourRate) form.dataset.dirty = '';
});
document.addEventListener('reset', event => {
  const form = event.target;
  if (form.dataset?.ourRate) delete form.dataset.dirty;
});
document.addEventListener('submit', event => {
  if (!event.target.dataset?.ourRate) return;
  event.preventDefault();
  saveWebsiteRate(event.target);
});

async function saveWebsiteRate(form) {
  if (!LIVE.on || LIVE.me?.role !== 'admin' || form.dataset.saving) return;
  const input = form.elements.price;
  const price = input.value.trim().replace(',', '.');
  if (!/^-?(?:\d{1,10}(?:\.\d{0,2})?|\.\d{1,2})$/.test(price)) {
    toast('Enter a number with up to two decimal places.', 'warn');
    return;
  }
  const guid = form.dataset.ourRate;
  form.dataset.dirty = '';
  form.dataset.saving = '1';
  form.querySelectorAll('input,button').forEach(el => { el.disabled = true; });
  try {
    const result = await api(`/api/products/${encodeURIComponent(guid)}/our-rate`, {price});
    const product = P(guid);
    if (product) product.price = result.price;
    input.defaultValue = input.value = result.price;
    delete form.dataset.dirty;
    if (form.isConnected && !document.querySelector('[data-our-rate][data-dirty]')) render();
    toast('Our rate saved. Saarthi Admin picks it up on the next sync.');
  } catch (e) {
    toast(esc(e.message), 'warn');
  } finally {
    delete form.dataset.saving;
    form.querySelectorAll('input,button').forEach(el => { el.disabled = false; });
  }
}

/* ---------- placing an order for real ---------- */
/* Replaces the prototype's in-memory queue. The server recomputes every
   amount from qty and rate, so what comes back -- not what was on screen --
   is what the shop will be billed. */
A.fplace = async () => {
  const c = C(S.fp);
  if (!c || DRAFT_SENDING.has(c.id)) return;
  const lines = Object.entries(cartOf(c.id)).map(([p, q]) => [p, q, rateOf(c.id, p)]);
  if (!lines.length && !pendingOrder(c.id)) return;
  const total = Math.round(cartTotal(c.id));
  if (!LIVE.on) return offlinePlace(c, lines, total);

  if (!pendingOrder(c.id) && c._draftUnavailable) {
    toast('This party is no longer on your current list. The draft is kept; ask the office.', 'warn'); return;
  }
  if (!pendingOrder(c.id) && lines.some(([p]) => P(p)?._draftUnavailable)) {
    toast('Remove unavailable items before sending. Your other items are kept.', 'warn'); return;
  }
  const invoiceDate = DRAFTS?.data.invoiceDates?.[c.id];
  if (!pendingOrder(c.id) && invoiceDate && invoiceDate < indiaDay()) {
    toast('Choose today or a future invoice date.', 'warn'); return;
  }
  try {
    const body = beginOrderSubmission(c.id, {
      customer_guid: c.id,
      ...(invoiceDate ? {invoice_date:invoiceDate} : {}),
      lines: lines.map(([p, q, rate]) => ({ item_guid:p, qty:q, rate })),
    });
    DRAFT_SENDING.add(c.id); render();
    const r = await api('/api/orders', body);
    if (!r?.order_no) throw new Error('The server did not confirm the order.');
    ORDER_CONFIRMATION_ERRORS.delete(body.request_id);
    const showingOrder = S.fp === c.id;
    (DRAFTS.data.acks ||= {})[r.order_no] = body.request_id;
    finishOrderDraft(c.id);
    flushOrderAcknowledgements();
    if (r.archived) {
      if (showingOrder) go('myorders');
      toast(`${r.order_no} was already saved and archived. No new order was created.`);
      return;
    }
    S.placed[c.id] = r.total;
    /* r.status, never a hardcoded 'placed'. An order for a shop the office
       has not confirmed yet comes back as pending_party, and telling the
       rep it is placed would send them off expecting a bill that cannot
       be raised. r.note carries the explanation in the server's words. */
    S.fdone = { id:r.order_no, total:r.total, n:r.lines, name:r.customer,
                status:r.status, note:r.note };
    logVisit(c.id, r.total);
    if (!LIVE.orders.some(o=>o.order_no===r.order_no)) LIVE.orders.unshift({ order_no:r.order_no, at:new Date().toISOString(),
                          by:LIVE.me.email, customer:r.customer, route:c.territory,
                          status:r.status, total:r.total, vchno:null,
                          with_laptop:false, lines:body.lines.map(l => ({
                            item:P(l.item_guid)?.name || l.item_guid,
                            unit:P(l.item_guid)?.unit || '', qty:l.qty,
                            rate:l.rate, amount:Math.round(l.qty*l.rate*100)/100 })) });
    seedVisitsFromOrders();
    if (showingOrder) go('fdone');
    toast(r.note || `${r.order_no} placed · ${inr(r.total)}`);
  } catch (e) {
    // Only definitive validation failures permit editing a new submission.
    // A timeout/5xx may follow a committed order; keep its frozen ID/body.
    const pending = pendingOrder(c.id);
    const message = e.status === 500 ? 'The server encountered an error. If this keeps happening, contact the office.' : e.message;
    if ([400,404,422].includes(e.status) && DRAFTS) {
      ORDER_CONFIRMATION_ERRORS.delete(pending?.request_id);
      delete DRAFTS.data.pending[c.id]; writeOrderDrafts();
    } else if (pending) {
      ORDER_CONFIRMATION_ERRORS.set(pending.request_id, message);
      if (S.fp === c.id) return; // The confirmation panel shows the latest retry error.
    }
    toast(esc(message), 'warn');
  } finally {
    DRAFT_SENDING.delete(c.id);
    if (S.fp === c.id) render();
  }
};

async function flushOrderAcknowledgements() {
  const drafts=DRAFTS;
  if (!LIVE.on || !drafts || drafts.blocked || LIVE.ackBusy) return;
  LIVE.ackBusy=true;
  try {
    for (const [order_no,request_id] of Object.entries(drafts.data.acks || {})) {
      const r=await api('/api/orders/ack',{order_no,request_id});
      if (DRAFTS!==drafts) return;
      if (r.acknowledged) {
        delete drafts.data.acks[order_no];
        writeOrderDrafts();
      }
    }
  } catch(e) {
    // Conservatively retain the server's protection flag and retry later.
  } finally { LIVE.ackBusy=false; }
}

/* ---------- my orders, from the server ---------- */
/* Shows what Tally has said, which the prototype's local queue could not:
   an order can be placed, with the laptop, or invoiced with a bill number. */
/* Four genuinely different states, and a rep chasing a bill needs to tell
   them apart: billed, reached the laptop, sent, or blocked because the shop
   itself is not confirmed yet. */
const currentInvoice = o => o.invoice?.state === 'active' ? o.invoice : null;
// Submitted values stay in storage for recovery. Once billed, only an active
// Tally invoice may supply displayed amounts and quantities.
const hasTallyInvoice = o => !!(o.vchno || o.invoice || o.invoice_restricted);
const orderAmount = o => currentInvoice(o) ? o.invoice.total : hasTallyInvoice(o) ? null : o.total;
const orderCustomer = o => currentInvoice(o)?.customer || o.customer;
const orderReference = o => o.order_no.startsWith('TALLY-') ? (o.invoice?.number || 'Tally invoice') : o.order_no;
const orderAmountHTML = o => `<b class="num">${orderAmount(o) === null ? '—' : inr(orderAmount(o))}</b>`;
const liveStatus = o => o.invoice && o.invoice.state !== 'active'
  ? `<span class="chip warn">Invoice ${esc(o.invoice.state)}</span>` : hasTallyInvoice(o)
  ? `<span class="chip good">Invoiced${o.vchno ? ' · '+esc(o.vchno) : ''}</span>`
  : o.status === 'pending_party' ? '<span class="chip warn">New shop · not confirmed</span>'
  : o.status === 'rejected' ? '<span class="chip bad">Shop turned down</span>'
  : o.with_laptop ? '<span class="chip">At the office · awaiting invoicing</span>'
                  : '<span class="chip good">Order placed</span>';

/* The "My orders" button's count comes from myOrders(). Live, that read the
   demo's ORDERS array, which applyData empties -- so the count never showed
   and a placed order left no trace on the home screen. The order was always
   there behind the button; nothing on screen said so. */
const offlineMyOrders = myOrders;
myOrders = function () { return LIVE.on ? (LIVE.orders || []) : offlineMyOrders(); };

if (typeof VIEWS !== 'undefined' && VIEWS.sales) {
  const offlineOrders = VIEWS.sales.myorders;
  VIEWS.sales.myorders = () => {
    if (!LIVE.on) return offlineOrders();
    const list = LIVE.orders;
    return `${ohead('home', 'My orders', `${list.length} ${list.length === 1 ? 'order' : 'orders'} · tap one to see it or print`)}
      <button class="btn sm" data-a="refreshlive">Refresh</button>
      <div class="ilist">${list.length ? list.map(o => `
        <button class="card row" data-a="oview" data-no="${esc(o.order_no)}" style="width:100%;text-align:left">
          <div><b>${esc(orderCustomer(o))}</b><div class="sub">${esc(o.order_no)} · ${new Date(o.at).toLocaleString('en-IN', { day:'numeric', month:'short', hour:'2-digit', minute:'2-digit' })}</div></div>
          <div class="right">${orderAmountHTML(o)}<div>${liveStatus(o)}</div></div>
        </button>`).join('')
        : `<div class="card empty">No orders yet</div>`}</div>`;
  };
}

/* ---------- office-only new party creation ---------- */
const newPartyRoutes = () => LIVE.me?.role === 'admin'
  ? (LIVE.routes || []).map(r => r.route) : [];
const offlineNew = A.fnew;
A.fnew = () => {
  if (!LIVE.on) return offlineNew();
  if (!canCreateParty()) return toast('Only the office can create a new party.', 'warn');
  S.newParty = { name:S.fpq.trim(), route:null, phone:'', matches:null, busy:false };
  go('fnewparty');
};

A.npset = (d, el) => { S.newParty[d.k] = el.value; };
A.nproute = d => { S.newParty.route = d.r; render(); };
A.npconfirm = () => { S.newParty.confirm = true; A.npsave(); };
A.nppick = d => { S.fpq = ''; S.newParty = null; A.fparty({ cid:d.cid }); };

A.npsave = async () => {
  if (!canCreateParty()) return toast('Only the office can create a new party.', 'warn');
  const p = S.newParty;
  if (!p || p.busy) return;
  if (!p.name || p.name.length < 3) return toast('Enter the shop name', 'warn');
  const routes = newPartyRoutes();
  if (!p.route && routes.length === 1) p.route = routes[0];
  if (!p.route) return toast('Choose which route this shop is on', 'warn');
  p.busy = true; render();
  try {
    const r = await api('/api/prospects', {
      name:p.name, route:p.route, phone:p.phone || null,
      confirm_duplicate: !!p.confirm });
    /* It can be ordered against at once -- that is the whole point -- so
       drop it into CUSTOMERS and open it, exactly as a real shop. */
    CUSTOMERS.push({ id:r.id, name:r.name, territory:r.route, place:r.route,
      rep:LIVE.me.email, isNew:true, prospectStatus:'pending', phone:p.phone || null,
      outstanding:null, aov:null, lastDays:null, lastVisit:null, churn:null,
      growth:null, trend:[], overdue:null, overdueDays:null, ageingGap:null,
      gstin:null, contact:null, limit:null, lat:null, lng:null, area:null,
      buys:[], cross:[] });
    S.fpq = ''; S.newParty = null;
    A.fparty({ cid:r.id });
    toast(`${r.name} added · the office will confirm it`);
  } catch (e) {
    p.busy = false;
    const m = e.data && e.data.detail;
    if (e.status === 409 && m && m.matches) { p.matches = m.matches; render(); }
    else { toast(e.message, 'warn'); render(); }
  }
};

VIEWS.sales.fnewparty = () => {
  if (LIVE.on && !canCreateParty()) return '<div class="card mini">Only the office can create a new party. Choose an existing party or contact the office.</div>';
  const p = S.newParty || {}, routes = newPartyRoutes();
  return `${ohead('home', 'New shop', 'Tally does not know this shop yet')}
    <div class="form">
      <label>Shop name</label>
      <input class="fld" value="${esc(p.name || '')}" data-a="npset" data-k="name" placeholder="As it should appear on the bill">
      <label>Phone <span class="faint">(optional)</span></label>
      <input class="fld" value="${esc(p.phone || '')}" data-a="npset" data-k="phone" inputmode="tel">
      ${routes.length > 1 ? `<label>Which of your routes?</label>
        <div class="seg2 wrap">${routes.map(r => `<button class="${p.route === r ? 'on' : ''}" data-a="nproute" data-r="${esc(r)}">${esc(r)}</button>`).join('')}</div>`
        : `<p class="faint sm">Route: ${esc(routes[0] || '—')}</p>`}
      ${p.matches ? `<div class="warnbox">
          <b>This may already exist.</b>
          <p class="sm">Pick the right one, or confirm it really is a different shop.</p>
          ${p.matches.map(n => { const c = CUSTOMERS.find(x => x.name === n);
            return c ? `<button class="btn sm block" data-a="nppick" data-cid="${c.id}">${esc(n)}</button>`
                     : `<div class="faint sm">${esc(n)} <span class="faint">(not on your routes)</span></div>`; }).join('')}
          <button class="btn sm block" data-a="npconfirm">No, this is a different shop</button>
        </div>` : ''}
      <p class="faint sm">You can take the order now. The bill waits until the
        office confirms the shop.</p>
      <button class="btn primary block" data-a="npsave" ${p.busy ? 'disabled' : ''}>
        ${p.busy ? 'Saving…' : 'Add shop and start order'}</button>
    </div>`;
};

/* The confirmation screen must not say "Order placed" for a shop the office
   has not confirmed -- that is the one screen the rep reads before walking
   out, and a bill that cannot be raised is not a placed order. */
const dropBtn = (html, action) =>
  html.replace(new RegExp(`<button[^>]*data-a="${action}"[\\s\\S]*?</button>`, 'g'), '');

const offlineDone = VIEWS.sales.fdone;
VIEWS.sales.fdone = () => {
  let html = offlineDone();
  const d = S.fdone || {};
  if (LIVE.on) {
    /* Both read the demo's in-memory ORDERS, which is empty once orders
       live on the server -- so they open nothing. A button that does
       nothing is worse than no button, especially on the screen the rep
       reads before leaving the shop. Restore them by implementing edit
       and bill-print against /api/orders. */
    html = dropBtn(dropBtn(html, 'modify'), 'billview');
    /* Straight after placing is exactly when a rep wants to hand the shop
       a copy, so put it here rather than only behind My orders. */
    if (d.id) html += `<div style="padding:0 16px 16px">
      <button class="btn block" data-a="oprint" data-no="${esc(d.id)}">${icon('print', 15)} Print / Save PDF</button></div>`;
  }
  if (d.status !== 'pending_party') return html;
  return html.replace('Order placed', 'Order saved')
    + `<div class="warnbox" style="margin:0 16px">
         <b>Waiting on the office</b>
         <p class="sm">${esc(d.note || 'This shop is new, so no bill can be raised until it is confirmed.')}</p>
       </div>`;
};

/* ---------- is billing actually happening? ---------- */
/* A stalled invoice run has no symptom of its own: orders sit saying "At
   the office", which is exactly what they say when everything is fine.
   Without this the first sign is a shop telephoning. Owner only -- a rep
   can do nothing about it and would only worry. */
function billingAlert() {
  const s = LIVE.status || {};
  if (!LIVE.me || LIVE.me.role !== 'admin') return '';
  const n = s.orders_waiting || 0, mins = s.oldest_wait_mins;
  const aside = s.orders_set_aside || 0;
  const blocked = s.invoice_blocked_since;
  const rows = [];
  if (blocked) {
    rows.push(`<b>Tally is refusing every bill.</b>
      <p class="sm">Restart TallyPrime on the office laptop and reopen the
      company. ${n} order${n === 1 ? '' : 's'} waiting${mins != null ? `, oldest ${ago(mins)}` : ''}
      — none are lost, they bill themselves once Tally is back.</p>`);
  } else if (mins != null && mins >= 180 && n) {
    /* Not necessarily broken: orders placed during billing hours wait for
       the evening run by design. Worth saying once it is unusual. */
    rows.push(`<b>${n} order${n === 1 ? '' : 's'} not billed yet.</b>
      <p class="sm">Oldest placed ${ago(mins)}. Normal in the afternoon —
      bills are raised after 5pm. Worth a look if it is already evening.</p>
      <button class="btn sm" data-a="seeorders">See the orders</button>`);
  }
  if (aside) {
    rows.push(`<b>${aside} order${aside === 1 ? '' : 's'} could not be billed.</b>
      <p class="sm">Tally refused ${aside === 1 ? 'it' : 'them'} repeatedly —
      usually a shop or item that has since been renamed or deleted. Needs
      someone to look.</p>`);
  }
  return rows.length ? `<div class="warnbox" style="margin:0 0 14px">${rows.join('')}</div>` : '';
}

const ago = m => m < 60 ? `${m} min ago`
  : m < 1440 ? `${Math.round(m / 60)} hr ago`
  : `${Math.round(m / 1440)} day${Math.round(m / 1440) === 1 ? '' : 's'} ago`;

/* Put it at the top of the owner's home screen -- the one they land on --
   rather than behind a tab nobody opens when things look normal. */
if (typeof VIEWS !== 'undefined' && VIEWS.dash && VIEWS.dash.home) {
  const offlineDash = VIEWS.dash.home;
  VIEWS.dash.home = () => (LIVE.on ? billingAlert() : '') + offlineDash();
}

/* ---------- the office: who may sign in ---------- */
/* The demo's User management edits a store in this browser. Live, that is
   worse than useless: the owner would add a salesperson, see them appear,
   and they would not be able to sign in. So the whole screen is replaced
   by one backed by the allowlist the API actually checks. */
async function loadPeople() {
  const [people, routes] = await Promise.allSettled([api('/api/users'), api('/api/routes')]);
  if (people.status === 'fulfilled' && Array.isArray(people.value)) LIVE.people = people.value;
  if (routes.status === 'fulfilled' && Array.isArray(routes.value)) LIVE.routes = routes.value;
  /* The visit report breaks activity down per rep by looking each one up
     in REPS. Without an entry per real person it listed the demo's four
     -- Rahul Verma and colleagues who do not work here. */
  for (const p of LIVE.people || []) {
    if (!p.active) continue;
    const row = { id: p.email, name: p.name || p.email.split('@')[0],
                  ini: ini(p.name || p.email), territory: (p.routes || []).join(' · '),
                  target: null, achieved: null, visitCov: null, conv: null,
                  accept: null, aov: null };
    const at = REPS.findIndex(r => r.id === p.email);
    if (at >= 0) REPS[at] = row; else REPS.push(row);
  }
  teamList = () => REPS.filter(r => (LIVE.people || []).some(
    p => p.email === r.id && p.active));
}

A.pnew = () => { S.person = { email:'', name:'', role:'salesperson', routes:[] }; render(); };
A.pedit = d => {
  const p = (LIVE.people || []).find(x => x.email === d.e);
  S.person = { email:p.email, name:p.name || '', role:p.role,
               routes:p.routes.slice(), existing:true };
  render();
};
A.pcancel = () => { if (!S.person?.busy) { S.person = null; render(); } };
function rememberPersonFields(p, form = $('#person-form')) {
  // Autofill can change an input without firing the delegated input handler.
  if (!form) return;
  if (!p.existing) p.email = form.elements.namedItem('email').value;
  p.name = form.elements.namedItem('name').value;
}
A.personField = (d, el) => {
  if (S.person && !S.person.busy) S.person[d.k] = el.value;
};
A.prole = d => {
  if (!S.person || S.person.busy) return;
  rememberPersonFields(S.person);
  S.person.role = d.r;
  render();
};
A.proute = d => {
  if (!S.person || S.person.busy) return;
  rememberPersonFields(S.person);
  const r = S.person.routes, i = r.indexOf(d.r);
  if (i < 0) r.push(d.r); else r.splice(i, 1);
  render();
};
document.addEventListener('submit', async event => {
  const form = event.target;
  if (form.id !== 'person-form') return;
  event.preventDefault();
  const p = S.person;
  if (!p || p.busy) return;
  rememberPersonFields(p, form);
  const email = p.email.trim();
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email) || email.length < 5) {
    const input = form.elements.namedItem('email');
    input.focus();
    toast('Enter the full Google email address.', 'warn');
    return;
  }
  p.busy = true;
  const save = form.elements.namedItem('save-person');
  save.disabled = true;
  save.textContent = 'Saving…';
  form.elements.namedItem('email').readOnly = true;
  form.elements.namedItem('name').readOnly = true;
  try {
    await api('/api/users', { email, name:p.name.trim() || null, role:p.role, routes:p.routes });
    if (S.person === p) S.person = null;
    await loadPeople(); render();
    toast('Saved · they can sign in with that Google account');
  } catch (e) {
    p.busy = false;
    toast(esc(e.message), 'warn');
    if (S.person === p) render();
  }
});
A.pactive = async d => {
  try {
    await api(`/api/users/${encodeURIComponent(d.e)}/active`, { active: d.on === '1' });
    await loadPeople(); render();
    toast(d.on === '1' ? 'Access restored' : 'Access switched off');
  } catch (e) { toast(e.message, 'warn'); }
};

function personForm() {
  const p = S.person, routes = LIVE.routes || [];
  return `<form id="person-form" class="card">
    <div class="eyebrow">${p.existing ? 'Edit person' : 'Add a person'}</div>
    <label for="person-email">Google address</label>
    <input id="person-email" name="email" class="fld" value="${esc(p.email)}" data-in="personField" data-k="email"
      type="email" required maxlength="200" autocomplete="off" ${p.existing || p.busy ? 'readonly' : ''}
      placeholder="salesperson@example.com">
    <p class="faint sm">Must be the exact Google account they sign in with.</p>
    <label for="person-name">Name</label>
    <input id="person-name" name="name" class="fld" value="${esc(p.name)}" data-in="personField" data-k="name"
      maxlength="120" autocomplete="off" ${p.busy ? 'readonly' : ''} placeholder="Shown in the app">
    <label>Role</label>
    <div class="seg2">
      <button type="button" class="${p.role === 'salesperson' ? 'on' : ''}" data-a="prole" data-r="salesperson">Salesperson</button>
      <button type="button" class="${p.role === 'admin' ? 'on' : ''}" data-a="prole" data-r="admin">Owner</button>
    </div>
    ${p.role === 'admin'
      ? `<p class="faint sm">An owner sees every shop and every rep's orders, and can add people.</p>`
      : `<label>Preferred routes (optional — all sales parties are visible)</label>
         <div class="seg2 wrap">${routes.map(r => `
           <button type="button" class="${p.routes.includes(r.route) ? 'on' : ''}" data-a="proute" data-r="${esc(r.route)}">
             ${esc(r.route)} <span class="faint">${r.shops}</span></button>`).join('')}</div>
         <p class="faint sm">They see only these shops — names, balances and
           order history. Nothing outside them.</p>`}
    <button type="submit" name="save-person" class="btn primary block" ${p.busy ? 'disabled' : ''}>${p.busy ? 'Saving…' : 'Save'}</button>
    <button type="button" class="btn sm block" data-a="pcancel" ${p.busy ? 'disabled' : ''}>Cancel</button>
  </form>`;
}

VIEWS.people = {};
VIEWS.people.home = () => {
  if (S.person) return `<header class="topbar"><h1>People</h1></header><div class="form">${personForm()}</div>`;
  const list = LIVE.people || [];
  return `<header class="topbar"><div><h1>People</h1><p class="sub">${list.filter(p => p.active).length} with access</p></div></header>
    <div class="ilist">
      <button class="btn primary block" data-a="pnew" style="margin:0 16px 10px">${icon('plus', 15)} Add a person</button>
      ${list.map(p => `
        <div class="card">
          <div class="row"><div class="person-identity">
            <b>${esc(p.name || p.email.split('@')[0])}</b>${p.is_you ? ' <span class="chip">you</span>' : ''}
            <div class="sub">${esc(p.email)}</div>
            <div class="sub">${p.role === 'admin' ? 'Owner · every shop'
              : (p.routes.length ? esc(p.routes.join(' · ')) : 'no routes yet')}
              ${p.role === 'admin' ? '' : ` · <b>${p.shops}</b> shop${p.shops === 1 ? '' : 's'}`}</div>
          </div><div class="right">${p.active ? '<span class="chip good">Active</span>' : '<span class="chip bad">Off</span>'}</div></div>
          <div class="row" style="gap:8px;margin-top:10px">
            <button class="btn sm" data-a="pedit" data-e="${esc(p.email)}">Edit</button>
            ${p.is_you ? '' : `<button class="btn sm" data-a="pactive" data-e="${esc(p.email)}" data-on="${p.active ? '0' : '1'}">${p.active ? 'Switch off' : 'Switch on'}</button>`}
          </div>
        </div>`).join('')}
    </div>`;
};

MODULES.people = {
  label:'People', short:'People', icon:'users', ns:'people', home:'home',
  group:'Admin',
  desc:'Manage sign-in access and preferred routes. Every salesperson can browse all sales parties.',
  needs:() => LIVE.on,
};
if (!MOD_ORDER.includes('people')) MOD_ORDER.push('people');

/* ---------- the office: shops waiting to be confirmed ---------- */
A.apdecide = async (d) => {
  try {
    await api(`/api/prospects/${d.id}/decide`, { approve: d.ok === '1' });
    LIVE.prospects = await api('/api/prospects');
    render();
    toast(d.ok === '1' ? 'Approved · the ledger is created on the next sync'
                       : 'Turned down');
  } catch (e) { toast(e.message, 'warn'); }
};

async function loadProspects() {
  try { LIVE.prospects = await api('/api/prospects'); } catch (e) { LIVE.prospects = []; }
}

/* Registered here, not in shell.js, because it only means anything when
   there is a server to approve against -- `needs` keeps it out of the
   offline prototype entirely. Added to the owner template at boot rather
   than by bumping the stored-template version, so a browser that already
   has templates saved still gets it. */
MODULES.newshops = {
  label:'New shops', short:'New shops', icon:'plus', ns:'newshops',
  home:'home', group:'Admin',
  desc:'Shops salespeople found that Tally has no ledger for yet. Approving one creates the ledger and releases its orders for billing.',
  needs:() => LIVE.on,
  badge:() => (LIVE.prospects || []).filter(p => p.status === 'pending').length,
};
if (!MOD_ORDER.includes('newshops')) MOD_ORDER.push('newshops');

VIEWS.newshops = {};
VIEWS.newshops.home = () => {
  const list = (LIVE.prospects || []);
  const pend = list.filter(p => p.status === 'pending');
  return `<header class="topbar"><div><h1>New shops</h1><p class="sub">${pend.length} waiting</p></div></header>
    <div class="ilist">${list.length ? list.map(p => `
      <div class="card">
        <div class="row"><div><b>${esc(p.name)}</b>
          <div class="sub">${esc(p.route || 'no route')} · ${esc(p.by)}</div>
          ${p.orders ? `<div class="sub">${p.orders} order(s) waiting · ${inr(p.order_value)}</div>` : ''}</div>
          <div class="right">${p.status === 'pending'
            ? '<span class="chip warn">Waiting</span>'
            : p.status === 'approved'
              ? (p.in_tally ? '<span class="chip good">In Tally</span>'
                            : '<span class="chip">Approved · creating</span>')
              : '<span class="chip bad">Turned down</span>'}</div></div>
        ${p.status === 'pending' ? `<div class="row" style="gap:8px;margin-top:10px">
          <button class="btn primary sm" data-a="apdecide" data-id="${p.id}" data-ok="1">Approve</button>
          <button class="btn sm" data-a="apdecide" data-id="${p.id}" data-ok="0">Turn down</button>
        </div>` : ''}
      </div>`).join('') : `<div class="card empty">No new shops have been proposed</div>`}</div>`;
};

/* Today's orders, as visits. TALLY_DAILY only holds vouchers Tally has
   already raised, and today's orders are not billed until the evening --
   so without this the visit report reads "0 shops, 0 orders" on a day the
   rep took four. logVisit dedupes by shop, so an order placed in this
   session and the same order read back from the server count once. */
function seedVisitsFromOrders() {
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const byName = new Map(CUSTOMERS.map(c => [c.name, c]));
  // Rebuild rather than append, so a reload does not count twice.
  for (let i = VISITS.length - 1; i >= 0; i--)
    if (VISITS[i].fromOrder) VISITS.splice(i, 1);
  const rows = new Map();
  for (const o of LIVE.orders || []) {
    if (o.source === 'tally' || o.by === 'Tally') continue;
    if (o.invoice && VISITS.some(v=>v.fromTally && v.guid===o.invoice.guid)) continue;
    const when = new Date(o.at);
    const day = new Date(when);day.setHours(0,0,0,0);
    const days = Math.round((today-day)/864e5);
    if (isNaN(when) || days < 0 || days > 90) continue;
    const c = byName.get(orderCustomer(o));
    if (!c) continue;
    // Attributed to whoever placed it, so an owner's per-rep breakdown is
    // real rather than everything landing on the signed-in user.
    const rep = o.by || (LIVE.me && LIVE.me.email);
    const k = rep + '|' + c.id + '|' + days;
    let v = rows.get(k);
    if (!v) {
      v = { cid:c.id, rep, by:o.by, days, time:when.toLocaleTimeString('en-IN',{hour:'2-digit',minute:'2-digit',hour12:false}), outcome:'order',
            amt:0, orders:0, fromOrder:true };
      rows.set(k, v);
      VISITS.unshift(v);
    }
    if (hasTallyInvoice(o)) {
      // If details have not reached this client, preserve the checkmark but
      // never count the submitted amount as a confirmed invoice.
      v.checkOnly = v.orders === 0;
    } else {
      v.amt += o.total || 0;
      v.orders += 1;v.checkOnly = false;
    }
  }
}

/* ---------- one order, in detail, and its bill ---------- */
/* Three gaps this closes: a rep could not open an order they had placed,
   could not print or share it, and the owner could not see the orders the
   billing banner was counting. */
const liveOrder = no => (LIVE.orders || []).find(o => o.order_no === no);

function liveBillHTML(o) {
  /* The same bill layout the demo prints, from a real order. The company
     name and address still come from data.js -- Tally's own company name
     is "Demo Bill - (from 1-Apr-26)", which is not a letterhead. */
  const invoice = currentInvoice(o);
  if (!invoice && hasTallyInvoice(o)) {
    const message = o.invoice_restricted ? 'Current invoice details are outside your assigned access.'
      : o.invoice ? `Invoice ${o.invoice.state}. Current billing details are unavailable.`
      : `Invoice ${o.vchno || ''} confirmed. Details are loading.`;
    return `<div class="invoice"><p>${esc(message)}</p></div>`;
  }
  const customer = invoice?.customer || o.customer;
  const c = CUSTOMERS.find(x => x.name === customer) || {};
  const when = (invoice ? ymdToDate(invoice.date) : o.invoice_date
    ? ymdToDate(o.invoice_date.replaceAll('-','')) : new Date(o.at)).toLocaleDateString('en-IN',
    { day:'numeric', month:'short', year:'numeric' });
  return `<div class="invoice">
    <div class="inv-h"><div><b class="inv-co">${esc(COMPANY.name)}</b><span>${esc(COMPANY.city || '')}</span></div><div class="inv-t">${invoice ? 'BILL' : 'ORDER'}</div></div>
    <div class="inv-meta">
      <div><span>${invoice ? 'Bill to' : 'Ordered for'}</span><b>${esc(customer)}</b><span>${esc(c.territory || o.route || '')}</span></div>
      <div><span>${invoice ? 'Invoice no.' : 'Order no.'}</span><b>${esc(invoice?.number || o.order_no)}</b><span>${when}</span></div></div>
    <table class="inv-t2"><thead><tr><th>#</th><th>Item</th><th class="r">Qty</th><th class="r">Rate</th><th class="r">Amount</th></tr></thead>
      <tbody>${(invoice?.lines || o.lines || []).map((l, i) => `<tr><td>${i + 1}</td><td>${esc(l.item)}</td><td class="r">${l.qty}${l.unit ? ' ' + esc(l.unit) : ''}</td><td class="r">${inr(l.rate)}</td><td class="r">${inr(l.amount)}</td></tr>`).join('')}</tbody></table>
    <div class="inv-tot"><div class="g"><span>Total</span><span>${inr(orderAmount(o))}</span></div></div>
    <div class="inv-f">${invoice ? `Tally voucher ${esc(invoice.number)}` : 'Not yet billed — this is the order, not a tax invoice'}</div>
  </div>`;
}

A.oview = d => {
  const o = liveOrder(d.no);
  if (!o) return;
  drawer(`<div class="drawer-h" data-live-order="${esc(o.order_no)}"><div>
      <h2 style="font-size:19px">${esc(orderCustomer(o))}</h2>
      <p class="faint" style="font-size:12.5px">${esc(orderReference(o))}${LIVE.me.role === 'admin' ? ' · ' + esc(o.source === 'tally' ? 'Only Tally' : R(o.by)?.name || o.by) : ''}</p></div>
      <button class="btn ghost sm" data-a="close" aria-label="Close">${icon('x')}</button></div>
    <div class="drawer-b">
      <div class="row" style="margin-bottom:12px">${liveStatus(o)}${orderAmountHTML(o)}</div>
      ${deskDates(o)}
      ${liveBillHTML(o)}
    </div>
    <div class="drawer-f"><button class="btn" data-a="close">Close</button>
      <button class="btn primary" data-a="oprint" data-no="${esc(o.order_no)}">${icon('print', 15)} Print / Save PDF</button></div>`);
};

A.oprint = d => {
  const o = liveOrder(d.no);
  if (!o) return;
  let box = document.getElementById('printArea');
  if (!box) { box = document.createElement('div'); box.id = 'printArea'; document.body.appendChild(box); }
  box.innerHTML = liveBillHTML(o);
  /* The browser's own print dialog has "Save as PDF" in it, on phones too,
     so there is no PDF library here and nothing to keep up to date. */
  toast('Choose "Save as PDF" in the print dialog to keep a copy');
  setTimeout(() => { try { window.print(); } catch (e) {
    toast('Printing is blocked here', 'warn'); } }, 200);
};

/* ---------- the office: every rep's orders ---------- */
/* The demo's Order desk reads the in-memory ORDERS queue, which is empty
   live -- so the owner had no way to see the orders the billing banner was
   counting. */
VIEWS.desk = VIEWS.desk || {};
const offlineDesk = VIEWS.desk.home;
function deskDates(o) {
  const dateOptions = { day:'numeric', month:'short', year:'numeric' };
  const created = o.source !== 'tally' && o.at
    ? new Date(o.at).toLocaleString('en-IN', {
        ...dateOptions, hour:'2-digit', minute:'2-digit', timeZone:'Asia/Kolkata',
      })
    : 'Unavailable';
  const voucherDate = o.invoice?.date;
  const invoiced = voucherDate
    ? ymdToDate(voucherDate).toLocaleDateString('en-IN', dateOptions)
    : hasTallyInvoice(o) ? 'Date unavailable' : 'Not billed';
  const requested = !voucherDate && o.invoice_date
    ? ` · Requested ${ymdToDate(o.invoice_date.replaceAll('-','')).toLocaleDateString('en-IN', dateOptions)}` : '';
  return `<div class="sub">Created: ${esc(created)}</div><div class="sub">Invoice date: ${esc(invoiced + requested)}</div>`;
}
VIEWS.desk.home = () => {
  if (!LIVE.on) return offlineDesk ? offlineDesk() : '';
  const today = indiaDay();
  const dates = Array.from({length:7}, (_, i) => {
    const day = new Date(`${today}T12:00:00+05:30`);
    day.setUTCDate(day.getUTCDate()+i-3);
    return indiaDay(day);
  });
  const date = dates.includes(S.deskDate) ? S.deskDate : today;
  const rep = S.deskRep || 'all';
  const all = (LIVE.orders || []).filter(o => {
    const voucherDate = o.invoice?.date;
    const day = voucherDate ? `${voucherDate.slice(0,4)}-${voucherDate.slice(4,6)}-${voucherDate.slice(6)}`
      : o.invoice_date || o.desk_date || (o.at ? indiaDay(new Date(o.at)) : '');
    return day === date && (rep === 'all' || (rep === 'tally' ? o.source === 'tally' : o.by === rep));
  });
  const f = S.deskFilter || 'all';
  const unbilled = all.filter(o => !hasTallyInvoice(o));
  const list = f === 'unbilled' ? unbilled : all;
  return `${ohead('home', 'Orders', `${all.length} order${all.length === 1 ? '' : 's'} · ${unbilled.length} not billed yet`)}
    <div class="row wrap" style="gap:10px">
      <label>Invoice date <select data-ch="deskdate">${dates.map(d => `<option value="${d}" ${d===date?'selected':''}>${d===today?'Today · ':''}${shortDate(ymdToDate(d.replaceAll('-','')))}</option>`).join('')}</select></label>
      <label>Salesperson <select data-ch="deskrep">${[['all','All'],['tally','Only Tally'],...teamList().map(p=>[p.id,p.name])].map(([k,label])=>`<option value="${esc(k)}" ${k===rep?'selected':''}>${esc(label)}</option>`).join('')}</select></label>
    </div>
    <div class="catchips" role="tablist">
      <button class="${f === 'all' ? 'on' : ''}" data-a="deskfilter" data-f="all">All</button>
      <button class="${f === 'unbilled' ? 'on' : ''}" data-a="deskfilter" data-f="unbilled">Not billed (${unbilled.length})</button>
    </div>
    <div class="ilist">${list.length ? list.map(o => `
      <button class="card row" data-a="oview" data-no="${esc(o.order_no)}" style="width:100%;text-align:left">
        <div><b>${esc(orderCustomer(o))}</b>
          <div class="sub">${esc(orderReference(o))}</div>
          ${deskDates(o)}
          <div class="sub">${o.source === 'tally' ? 'Only Tally' : esc(R(o.by)?.name || o.by)}</div></div>
        <div class="right">${orderAmountHTML(o)}<div>${liveStatus(o)}</div></div>
      </button>`).join('') : `<div class="card empty">No orders yet</div>`}</div>`;
};
A.deskfilter = d => { S.deskFilter = d.f; render(); };
A.deskdate = (_, el) => { S.deskDate = el.value; render(); };
A.deskrep = (_, el) => { S.deskRep = el.value; render(); };
A.seeorders = () => { S.deskFilter = 'unbilled'; openModule('approvals'); };

A.logout = async () => {
  try { await api('/api/auth/logout', {}); } catch (e) {}
  /* Google remembers who was chosen last and will sign them straight back
     in on the next visit, which is exactly the "it keeps loading the wrong
     account" complaint. Clear its hint too. */
  try { if (window.google && google.accounts) google.accounts.id.disableAutoSelect(); } catch (e) {}
  location.replace(location.pathname);
};

/* The mobile account sheet owns appearance and session actions. */
const offlineSheet = A.usersheet;
function ownerAgentSyncControl() {
  return LIVE.on && LIVE.me?.role === 'admin' ? `<div class="card mini"><h4>Business laptop sync</h4>
    <p data-agent-sync-status role="status">Check the laptop status before pausing or resuming sync.</p>
    <button class="btn sm" data-a="agentsyncstatus">Check status</button>
    <button class="btn sm" data-a="agentsynctoggle" hidden></button></div>` : '';
}

async function updateAgentSync(button, paused) {
  if (!LIVE.on || LIVE.me?.role !== 'admin') return;
  const panel = button.closest('.card');
  const status = panel.querySelector('[data-agent-sync-status]');
  const toggle = panel.querySelector('[data-a="agentsynctoggle"]');
  const check = panel.querySelector('[data-a="agentsyncstatus"]');
  check.disabled = toggle.disabled = true;
  try {
    let state = await api('/api/agent-sync', paused === undefined ? undefined : {paused});
    for (let attempt = 0; ; attempt++) {
      const pending = state.status === 'Pausing' || state.status === 'Resuming';
      status.textContent = state.status;
      toggle.dataset.paused = String(!state.paused);
      toggle.textContent = state.paused ? 'Resume laptop sync' : 'Pause laptop sync';
      toggle.hidden = false;
      if (!pending) break;
      if (attempt === 15) {
        status.textContent += ' · Waiting for the laptop. Tap Check status to try again.';
        break;
      }
      await new Promise(resolve => setTimeout(resolve, 5000));
      if (!panel.isConnected || document.hidden || LIVE.me?.role !== 'admin') break;
      state = await api('/api/agent-sync');
    }
  } catch (error) {
    status.textContent = error.message;
    toggle.hidden = true;
  } finally {
    check.disabled = toggle.disabled = false;
  }
}
A.agentsyncstatus = (d, el) => updateAgentSync(el);
A.agentsynctoggle = (d, el) => updateAgentSync(el, d.paused === 'true');

A.usersheet = () => {
  if (!LIVE.on) return offlineSheet();
  const u = LIVE.me;
  const scope = 'All sales parties';
  drawer(`<div class="drawer-h"><div class="row" style="gap:12px">
      <span class="av" style="width:44px;height:44px">${ini(u.name || u.email)}</span>
      <div><h2 style="font-size:20px">${esc(u.name || u.email.split('@')[0])}</h2>
        <p class="faint" style="font-size:12.5px">${esc(u.email)}</p></div></div>
      <button class="btn ghost sm" data-a="close" aria-label="Close">${icon('x')}</button></div>
    <div class="drawer-b">
      ${ownerAgentSyncControl()}
      <div class="card mini"><h4>${u.role === 'admin' ? 'Owner' : 'Salesperson'}</h4>
        <p class="faint" style="font-size:12.5px">${esc(scope)}</p></div>
      <div class="card mini"><h4>Your modules</h4>
        <div class="modchips">${myMods().map(m => `<span class="chip">${icon(MODULES[m].icon, 12)} ${MODULES[m].short}</span>`).join('')}</div></div>
      <div class="card mini"><h4>Appearance</h4>${themeControls()}</div>
      <button class="btn block" data-a="refreshlive">${icon('repeat', 15)} Refresh data</button>
      <p class="faint sm" data-live-sync>${esc(liveSyncMessage())}</p>
      ${typeof installAppCard === 'function' ? installAppCard() : ''}
      ${LIVE.status && LIVE.status.stock_asof ? `<p class="faint" style="font-size:12px">Stock as of ${new Date(LIVE.status.stock_asof).toLocaleString('en-IN', { day:'numeric', month:'short', hour:'2-digit', minute:'2-digit' })}</p>` : ''}
      <button class="btn block" data-a="logout">${icon('repeat', 15)} Sign out</button>
    </div>`);
};

/* The snapshot path, kept so the offline prototype still demos. */
function offlinePlace(c, lines, total) {
  const id = 'SO-' + (2300 + ORDERS.length);
  const t = new Date().toLocaleTimeString('en-IN', { hour:'2-digit', minute:'2-digit', hour12:false });
  ORDERS.unshift({ id, cid:c.id, rep:me().rep || c.rep, by:meName(true), amount:total,
                   placed:`Today, ${t}`, status:'pending', lines, fresh:true });
  S.carts[c.id] = {};
  logVisit(c.id, total); recordSale(c.id, me().rep || c.rep, lines);
  S.placed[c.id] = total;
  S.fdone = { id, total, n:lines.length, name:c.name };
  go('fdone'); toast(`${id} placed · ${inr(total)}`);
}

installDraftHooks();

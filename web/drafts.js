/* Device drafts are separate from the server's saved orders. No background
   submission: an uncertain result is resolved by replaying its original ID. */
let DRAFTS = null;
let DRAFT_ERROR = '';
const DRAFT_SENDING = new Set();
const ORDER_CONFIRMATION_ERRORS = new Map();
const copyDraft = value => JSON.parse(JSON.stringify(value));
const DRAFT_SAVED_MESSAGE = 'Draft saved on this device. It is an order only after the server confirms it.';

function updateDraftMessage() {
  const el = document.querySelector('[data-draft-message]');
  if (el) el.textContent = DRAFT_ERROR || DRAFT_SAVED_MESSAGE;
}

function initOrderDrafts() {
  const key = 'saarthi.order-drafts.v1:' + encodeURIComponent(LIVE.me.email.toLowerCase())
    + ':' + encodeURIComponent(LIVE.status.company || '');
  DRAFTS = { key, data:{v:1, selected:null, carts:{}, rates:{}, items:{}, parties:{}, pending:{}}, warnings:{} };
  DRAFT_ERROR = '';
  S.carts = {}; S.rates = {}; S.fp = null;
  try {
    const raw = localStorage.getItem(key);
    if (raw) {
      const d = JSON.parse(raw);
      if (d.v !== 1 || !d.carts || !d.rates || !d.items || !d.pending)
        throw new Error('Unrecognized saved draft');
      DRAFTS.data = d;
      DRAFTS.data.parties ||= {};
    }
  } catch (e) {
    DRAFTS.blocked = true; // Do not overwrite an unreadable saved draft.
    DRAFT_ERROR = 'Cannot read saved drafts on this device. Keep this page open and contact the office.';
    return;
  }
  const d = DRAFTS.data;
  // A saved request can be resolved even if its party was removed from
  // the fresh route list. This placeholder may only retry that request.
  for (const cid of Object.keys(d.pending)) {
    if (!C(cid)) CUSTOMERS.push({id:cid, name:d.parties[cid] || 'Saved order',
      buys:[], cross:[], _draftUnavailable:true, territory:'Unavailable'});
  }
  for (const cid of new Set([...Object.keys(d.carts), ...Object.keys(d.rates)])) {
    if (!C(cid)) continue; // Retain inaccessible parties in storage, not the live catalogue.
    S.carts[cid] = copyDraft(d.carts[cid] || {});
    S.rates[cid] = copyDraft(d.rates[cid] || {});
    const warnings = [];
    for (const [pid, qty] of Object.entries(S.carts[cid])) {
      let p = P(pid);
      if (!p) {
        p = {...d.items[pid], id:pid, name:d.items[pid]?.name || 'Unavailable item',
          price:S.rates[cid][pid], stock:0, daily:0, grp:'Unavailable',
          _draftUnavailable:true};
        PRODUCTS.push(p);
      }
      if (p._draftUnavailable) warnings.push(`${p.name} is no longer available. Remove it before sending a new order.`);
      else {
        const current = p.price ?? null;
        if (current !== S.rates[cid][pid]) warnings.push(`${p.name}: current price ${current == null ? 'unavailable' : inr(current)}; your quoted price is kept.`);
        // Stock availability is shown on each product card, including review.
        // Do not repeat the same notice above the entire order after refresh.
      }
    }
    DRAFTS.warnings[cid] = warnings;
  }
  if (C(d.selected)) S.fp = d.selected;
}

function writeOrderDrafts() {
  if (!DRAFTS || DRAFTS.blocked) return false;
  try {
    localStorage.setItem(DRAFTS.key, JSON.stringify(DRAFTS.data));
    DRAFT_ERROR = '';
    updateDraftMessage();
    return true;
  } catch (e) {
    DRAFT_ERROR = 'Draft cannot be saved on this device. Keep this page open; sending is paused until device storage works.';
    updateDraftMessage();
    return false;
  }
}

function saveOrderDrafts() {
  if (!LIVE.on || !DRAFTS || DRAFTS.blocked) return false;
  const d = DRAFTS.data;
  // A second tab must not overwrite an in-flight submission with its
  // older editable draft. Replay the already persisted ID in either tab.
  try {
    const stored = JSON.parse(localStorage.getItem(DRAFTS.key) || 'null');
    Object.assign(d.pending, stored?.pending || {});
    d.acks = {...stored?.acks,...d.acks};
  } catch (e) {
    DRAFT_ERROR = 'Cannot read saved drafts. Keep this page open and retry when device storage works.';
    updateDraftMessage();
    return false;
  }
  d.selected = S.fp || null;
  for (const cid of new Set([...Object.keys(S.carts), ...Object.keys(S.rates)])) {
    if (!C(cid) || d.pending[cid]) continue;
    d.parties[cid] = C(cid).name;
    d.carts[cid] = copyDraft(S.carts[cid] || {});
    d.rates[cid] = copyDraft(S.rates[cid] || {});
    for (const pid of Object.keys(d.carts[cid])) {
      const p = P(pid);
      if (!p) continue;
      // Pin even an unedited default rate: a reload must keep the quote.
      d.rates[cid][pid] = rateOf(cid, pid);
      d.items[pid] = {id:pid, name:p.name, unit:p.unit, price:p.price, grp:p.grp};
    }
    S.rates[cid] = copyDraft(d.rates[cid]);
  }
  return writeOrderDrafts();
}

function pendingOrder(cid) { return DRAFTS?.data.pending[cid]; }
function beginOrderSubmission(cid, body) {
  if (!saveOrderDrafts()) throw new Error(DRAFT_ERROR || 'Draft could not be saved.');
  if (!pendingOrder(cid)) {
    body = {...copyDraft(body), request_id:crypto.randomUUID()};
    DRAFTS.data.pending[cid] = body;
  }
  if (!writeOrderDrafts()) throw new Error(DRAFT_ERROR);
  return copyDraft(pendingOrder(cid));
}

function finishOrderDraft(cid) {
  delete DRAFTS.data.pending[cid];
    if (DRAFTS.data.invoiceDates) delete DRAFTS.data.invoiceDates[cid];
  DRAFTS.data.carts[cid] = {}; DRAFTS.data.rates[cid] = {};
  DRAFTS.data.selected = null;
  S.carts[cid] = {}; S.rates[cid] = {};
  if (S.fp === cid) S.fp = null;
  else DRAFTS.data.selected = S.fp || null;
  // If cleanup fails, the stored old ID is safe to check again after reload.
  writeOrderDrafts();
}

function draftNotice(cid) {
  const warnings = DRAFTS?.warnings[cid] || [];
  return `<div class="card mini" role="status"><span data-draft-message>${esc(DRAFT_ERROR || DRAFT_SAVED_MESSAGE)}</span>
    ${warnings.length ? `<p class="faint sm">${warnings.map(esc).join('<br>')}</p>` : ''}</div>`;
}

function installDraftHooks() {
  A.invoiceDate = (d, el) => {
    if (!DRAFTS || pendingOrder(d.cid)) return;
    (DRAFTS.data.invoiceDates ||= {})[d.cid] = el.value > indiaDay() ? el.value : null;
    saveOrderDrafts();
  };
  for (const name of ['fparty','fnext']) {
    const action = A[name];
    A[name] = (...args) => {
      const result = action(...args);
      if (LIVE.on) saveOrderDrafts();
      return result;
    };
  }
  // Quantity/rate text is saved on input, without replacing the focused field.
  A.fdraftqty = (d, el) => {
    const n = parseInt(el.value, 10);
    if (n > 0) cartOf(d.cid)[d.p] = n; else delete cartOf(d.cid)[d.p];
  };
  A.fdraftrate = (d, el) => {
    const n = Number(el.value);
    (S.rates[d.cid] ||= {})[d.p] = n > 0 ? Math.round(n * 100) / 100 : null;
  };
  for (const name of ['fqty','fset','frate','fprev','fdraftqty','fdraftrate',
    'add','addall','qty','qtyset','repeat','rm']) {
    const action = A[name];
    if (!action) continue;
    A[name] = (...args) => {
      const cid = args[0]?.cid || S.fp;
      if (LIVE.on && pendingOrder(cid)) {
        toast('Check / Retry the pending order before changing it.', 'warn');
        return;
      }
      const result = action(...args);
      if (LIVE.on) saveOrderDrafts();
      return result;
    };
  }
  for (const event of ['click','input','change'])
    document.addEventListener(event, () => { if (LIVE.on) saveOrderDrafts(); });
  window.addEventListener('pagehide', () => { if (LIVE.on) saveOrderDrafts(); });
  for (const view of ['menu','review']) {
    const original = VIEWS.sales[view];
    VIEWS.sales[view] = () => {
      if (!LIVE.on || !DRAFTS || !C(S.fp)) return original();
      const pending = pendingOrder(S.fp);
    if (pending) return `${ohead('home','Check order confirmation',esc(C(S.fp).name))}
        <div class="warnbox" role="status"><b>${DRAFT_SENDING.has(S.fp) ? 'Sending order…' : 'Order confirmation pending'}</b>
          <p>The server did not confirm whether this order was saved. Your items and prices are kept. Check / Retry safely checks the same order.</p>
          ${!DRAFT_SENDING.has(S.fp) && ORDER_CONFIRMATION_ERRORS.has(pending.request_id) ? `<p>${esc(ORDER_CONFIRMATION_ERRORS.get(pending.request_id))}</p>` : ''}
          <button class="btn primary block" data-a="fplace" ${DRAFT_SENDING.has(S.fp) ? 'disabled' : ''}>${DRAFT_SENDING.has(S.fp) ? 'Sending…' : 'Check / Retry'}</button></div>
        ${pending.invoice_date ? `<div class="card mini">Invoice date <b>${esc(pending.invoice_date)}</b></div>` : ''}
        <div class="ilist">${pending.lines.map(l=>`<div class="card mini">${esc(P(l.item_guid)?.name || DRAFTS.data.items[l.item_guid]?.name || 'Item')}
          <span class="num"> · ${l.qty} × ${inr(l.rate)}</span></div>`).join('')}</div>
        <div class="card mini">Order total <b class="num">${inr(pending.lines.reduce((sum,l)=>sum+l.qty*l.rate,0))}</b></div>
        ${DRAFT_ERROR ? draftNotice(S.fp) : ''}`;
      const warnings = DRAFTS.warnings[S.fp] || [];
      return (DRAFT_ERROR ? draftNotice(S.fp) : warnings.length
        ? `<div class="card mini" role="status">${warnings.map(esc).join('<br>')}</div>` : '') + original();
    };
  }
  const home = VIEWS.sales.home;
  VIEWS.sales.home = () => {
    const html = home();
    if (!LIVE.on || !DRAFTS) return html;
    const ids = Object.keys(DRAFTS.data.carts).filter(cid=>C(cid) &&
      (Object.keys(DRAFTS.data.carts[cid]).length || pendingOrder(cid)));
    return (DRAFT_ERROR ? draftNotice(null) : '') + (ids.length ? `<div class="card mini saved-drafts"><b>Saved drafts on this device</b>
      ${ids.map(cid=>`<button class="btn block draft-resume" data-a="fparty" data-cid="${esc(cid)}"><span class="draft-resume-action">${pendingOrder(cid) ? 'Check order' : 'Resume draft'}</span><span class="draft-resume-name">${esc(C(cid).name)}</span></button>`).join('')}</div>` : '') + html;
  };
}

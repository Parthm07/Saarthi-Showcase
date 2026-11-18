/* ================= APP SHELL — modules, role templates, users =================
   Every feature is a module. The owner builds role templates by picking modules;
   each user gets a template, and their navigation shows only those modules. */

const MODULES = {
  purchasecost:{label:'Purchase rates',short:'Purchase rates',icon:'rupee',ns:'purchasecost',home:'home',group:'Insight',desc:'Read-only purchase invoice rates from Tally.',needs:()=>typeof LIVE!=='undefined'&&LIVE.on&&LIVE.me?.role==='admin'},
  orders:    { label:'Order taker',       short:'Orders',    icon:'cart',  ns:'sales',   home:'home', group:'Sell',
               desc:'Pick a party, tap items with + / −, review and place the order.' },
  approvals: { label:'Order desk',        short:'Desk',      icon:'alert', ns:'desk',    home:'home', group:'Operate',
               desc:'Approve orders, or modify them in the order taker.', badge:() => ORDERS.filter(o => o.status === 'pending').length },
  payments:  { label:'Payments',          short:'Payments',  icon:'rupee', ns:'pay',     home:'home', group:'Operate',
               desc:'Pending amount and last 10 payments for every party.' },
  /* Route planner needs a latitude and longitude for every shop. Tally
     stores none -- 0 of 734 ledgers have so much as an address -- so the
     module is withheld rather than shown computing on nulls. Restore it by
     deleting `needs` once coordinates exist. */
  routes:    { label:'Route planner',     short:'Routes',    icon:'map',   ns:'plan',    home:'home', group:'Operate',
               desc:'Builds beats from shop locations, visit time and visit frequency, flags gaps and sizes the team.',
               needs:() => CUSTOMERS.some(c => c.lat != null) },
  visits:    { label:'Visit report',      short:'Visits',    icon:'pin',   ns:'vis',     home:'home', group:'Insight',
               desc:'Shops visited, orders and order value today, with daily history per salesperson.' },
  dashboard: { label:'Sales dashboard',   short:'Dashboard', icon:'chart', ns:'dash',    home:'home', group:'Insight',
               desc:'Sales by date, route, product and party, with week-on-week trends.' },
  users:     { label:'User management',   short:'Users',     icon:'users', ns:'admin',   home:'home', group:'Admin',
               desc:'Add people, assign role templates and choose which modules each role sees.',
               needs:() => typeof LIVE === 'undefined' || !LIVE.on },
};
const MOD_ORDER = Object.keys(MODULES);
const GROUPS = ['Sell', 'Operate', 'Insight', 'Admin'];

const DEFAULT_TEMPLATES = [
  { id:'owner',     name:'Owner',                 desc:'Full visibility and control of the business', mods:['dashboard', 'purchasecost', 'visits', 'approvals', 'routes', 'payments', 'orders', 'users'], home:'dashboard', locked:['users'] },
  { id:'manager',   name:'Sales manager',         desc:'Runs the sales team and territories',          mods:['dashboard', 'visits', 'approvals', 'payments'], home:'dashboard' },
  { id:'sales',     name:'Salesperson',           desc:'Takes orders on the field',                    mods:['orders', 'visits', 'payments'], home:'orders' },
  { id:'accounts',  name:'Accounts / order desk', desc:'Approves and corrects orders',                 mods:['approvals', 'orders', 'payments'], home:'approvals' },
];
const DEFAULT_USERS = [
  { id:'u1',  name:'Anil Mahajan',  phone:'98260 10001', tpl:'owner',     status:'active',  seen:'Active now' },
  { id:'u2',  name:'Vikram Singh',  phone:'98260 10002', tpl:'manager',   status:'active',  seen:'12 min ago', area:'All territories' },
  { id:'u3',  name:'Rahul Verma',   phone:'98260 10003', tpl:'sales',     status:'active',  seen:'5 min ago',  area:'Indore West & Central', rep:'s1' },
  { id:'u4',  name:'Priya Nair',    phone:'98260 10004', tpl:'sales',     status:'active',  seen:'1 hr ago',   area:'Indore East', rep:'s2' },
  { id:'u5',  name:'Amit Joshi',    phone:'98260 10005', tpl:'sales',     status:'active',  seen:'Yesterday',  area:'Pithampur', rep:'s3' },
  { id:'u6',  name:'Sanjay Patel',  phone:'98260 10006', tpl:'sales',     status:'active',  seen:'3 hr ago',   area:'Dewas – Ujjain', rep:'s4' },
  { id:'u7',  name:'Neha Kulkarni', phone:'98260 10007', tpl:'accounts',  status:'active',  seen:'Active now', area:'Head office' },
  { id:'u10', name:'Deepak Soni',   phone:'98260 10010', tpl:'sales',     status:'invited', seen:'Invite sent 2 days ago', area:'Indore North' },
];
const DEFAULT_DASH = [
  { id:'d-monday', name:'Monday review', by:'Vikram Singh', shared:['owner', 'manager'],
    w:[{ t:'kpi', m:'sales' }, { t:'kpi', m:'parties' }, { t:'bar', m:'sales', d:'brand', n:6 }, { t:'table', m:'sales', d:'area', n:6 }, { t:'share', m:'sales', d:'category' }] },
];

/* ---------- demo persistence (this browser only) ---------- */
const STORE_KEY = 'saarthi-demo-v5';
const clone = o => JSON.parse(JSON.stringify(o));
const store = (() => {
  let s = null;
  try { s = JSON.parse(localStorage.getItem(STORE_KEY)); } catch (e) {}
  return s && s.v === 5 ? s : { v:5, tpl:clone(DEFAULT_TEMPLATES), users:clone(DEFAULT_USERS), dash:clone(DEFAULT_DASH) };
})();
function persist() { try { localStorage.setItem(STORE_KEY, JSON.stringify(store)); } catch (e) {} }

const TPL = id => store.tpl.find(t => t.id === id);
const USER = id => store.users.find(u => u.id === id);
const me = () => USER(S.user) || store.users[0];
const meName = full => full ? me().name : me().name.split(' ')[0];
const myTpl = () => TPL(me().tpl) || { id:'none', name:'No template', mods:[] };
const modAvailable = m => !MODULES[m].needs || MODULES[m].needs();
function modsOf(t) {
  const mods = MOD_ORDER.filter(m => t.mods.includes(m) && modAvailable(m));
  const h = mods.includes(t.home) ? t.home : mods[0];
  return h ? [h, ...mods.filter(m => m !== h)] : [];
}
const myMods = () => modsOf(myTpl());

function scrollTop() { for (const id of ['main','appInner']) { const a = document.getElementById(id); if (a) a.scrollTop = 0; } window.scrollTo(0, 0); }
function signIn(uid) {
  S.user = uid; const u = me();
  ME = u.rep || 's1';
  S.mod = myMods()[0] || null; S.view = S.mod ? MODULES[S.mod].home : null; S.params = {}; S.fp = null;
  closeOverlay(); render(); scrollTop();
}
function openModule(m) {
  if (!myMods().includes(m)) { toast(`${MODULES[m].label} isn't enabled for ${myTpl().name}`, 'warn'); return; }
  S.mod = m; S.view = MODULES[m].home; S.params = {}; closeOverlay(); render(); scrollTop();
}

/* resolves the current module + view into HTML; called by render() */
function resolveView() {
  const mods = myMods();
  if (!mods.includes(S.mod)) { S.mod = mods[0] || null; S.view = S.mod ? MODULES[S.mod].home : null; }
  if (!S.mod) return { key:'none', html:`<div class="card empty" style="margin-top:40px">${icon('shield', 26)}<b>No modules enabled</b>
    <span>${myTpl().name} has nothing switched on yet. Ask the owner to update this role template.</span></div>` };
  const m = MODULES[S.mod]; S.role = m.ns;
  const v = (m.alias && m.alias[S.view]) || S.view;
  const ns = VIEWS[m.ns] || {};
  const fn = ns[v] || ns[m.home];
  return { key:S.mod + '-' + v, html:fn(S.params || {}) };
}

/* ---------- phone app bar and tab bar ---------- */
const badgeOf = m => (MODULES[m].badge ? MODULES[m].badge() : 0);
function renderChrome() {
  const mods = myMods(), u = me(), t = myTpl();
  $('#appbar').innerHTML = `<div class="brand-mark sm">${icon('spark', 15, 2)}</div>
    <div class="ab-txt"><b>${esc(u.name)}</b><span>${esc(t.name)} · ${esc(COMPANY.name)}</span></div>
    <button class="av" data-a="usersheet" aria-label="Account">${ini(u.name)}</button>`;
  const more = mods.length > 5, tabs = more ? mods.slice(0, 4) : mods;
  const tb = $('#tabbar');
  tb.hidden = !mods.length;
  tb.style.gridTemplateColumns = `repeat(${tabs.length + (more ? 1 : 0)},minmax(0,1fr))`;
  tb.innerHTML = tabs.map(m => { const b = badgeOf(m); return `<button class="${S.mod === m ? 'on' : ''}" data-a="mod" data-m="${m}"><span class="ti">${icon(MODULES[m].icon, 20)}${b ? `<i>${b}</i>` : ''}</span><span>${MODULES[m].short}</span></button>`; }).join('')
    + (more ? `<button class="${mods.slice(4).includes(S.mod) ? 'on' : ''}" data-a="more"><span class="ti">${icon('grid', 20)}</span><span>More</span></button>` : '');
}

function themeControls() {
  const dark = document.documentElement.dataset.theme === 'dark';
  return `<div class="seg2 theme-options" role="group" aria-label="Appearance">
    <button class="${dark ? '' : 'on'}" aria-pressed="${!dark}" data-a="theme" data-theme="light">Light</button>
    <button class="${dark ? 'on' : ''}" aria-pressed="${dark}" data-a="theme" data-theme="dark">Dark</button>
  </div>`;
}

A.theme = ({theme}) => {
  if (theme !== 'light' && theme !== 'dark') return;
  document.documentElement.dataset.theme = theme;
  document.querySelector('meta[name="theme-color"]').content = theme === 'dark' ? '#0d1412' : '#faf9fd';
  try { localStorage.setItem('saarthi-theme', theme); } catch (e) {}
  document.querySelectorAll('.theme-options button').forEach(button => {
    const active = button.dataset.theme === theme;
    button.classList.toggle('on', active);
    button.setAttribute('aria-pressed', String(active));
  });
};

// Keep navigation out of the keyboard area while editing on a real phone.
let mobileFullHeight = 0, mobileWidth = 0;
function updateMobileViewport() {
  const viewport = window.visualViewport;
  const app = document.getElementById('app');
  const layoutHeight = window.innerHeight || document.documentElement.clientHeight;
  const scale = viewport?.scale || 1;
  const height = viewport ? viewport.height * scale : layoutHeight;
  if (!height || !app) return;
  const focused = document.activeElement;
  const editing = focused && /^(INPUT|TEXTAREA)$/.test(focused.tagName);
  const wasKeyboardOpen = app.classList.contains('keyboard-open');
  if (!mobileFullHeight || mobileWidth !== window.innerWidth)
    mobileFullHeight = Math.max(layoutHeight, height);
  else if (!editing && !wasKeyboardOpen && scale === 1)
    mobileFullHeight = Math.max(layoutHeight, height);
  mobileWidth = window.innerWidth;
  const reduced = mobileFullHeight - height > 140;
  const keyboard = reduced && (editing || wasKeyboardOpen);
  app.classList.toggle('keyboard-open', Boolean(keyboard));
  // While zoomed, keep the existing shell height. Pinch zoom and keyboard
  // panning should not resize the layout and move the navigation.
  if (scale === 1) {
    // The shell starts at the top of the document. iOS can pan the visual
    // viewport to the input, so size the shell to the visible bottom edge.
    const visibleTop = viewport?.pageTop ?? ((window.scrollY || 0) + (viewport?.offsetTop || 0));
    const shellHeight = keyboard ? height + Math.max(0, visibleTop) : layoutHeight || height;
    document.documentElement.style.setProperty('--mobile-height', `${shellHeight}px`);
  }
}
window.visualViewport?.addEventListener('resize', updateMobileViewport);
window.visualViewport?.addEventListener('scroll', updateMobileViewport);
window.addEventListener('resize', updateMobileViewport);
document.addEventListener('focusin', updateMobileViewport);
document.addEventListener('focusout', () => setTimeout(updateMobileViewport, 0));
window.addEventListener('DOMContentLoaded', updateMobileViewport);

/* ---------- sheets ---------- */
A.mod = d => openModule(d.m);
A.signin = d => { signIn(d.u); toast(`Signed in as ${me().name} · ${myTpl().name}`); };
A.resetdemo = () => { try { localStorage.removeItem(STORE_KEY); } catch (e) {} location.reload(); };
A.more = () => {
  const rest = myMods().slice(4);
  drawer(`<div class="drawer-h"><h2 style="font-size:20px">More</h2><button class="btn ghost sm" data-a="close" aria-label="Close">${icon('x')}</button></div>
    <div class="drawer-b">${typeof ownerAgentSyncControl === 'function' ? ownerAgentSyncControl() : ''}${rest.map(m => `<button class="modrow ${S.mod === m ? 'on' : ''}" data-a="mod" data-m="${m}"><span class="mi">${icon(MODULES[m].icon, 18)}</span>
      <span class="mt"><b>${MODULES[m].label}</b><span>${MODULES[m].desc}</span></span>${icon('arrow', 16)}</button>`).join('')}</div>`);
};
A.usersheet = () => {
  const u = me(), t = myTpl();
  drawer(`<div class="drawer-h"><div class="row" style="gap:12px"><span class="av" style="width:44px;height:44px">${ini(u.name)}</span>
      <div><h2 style="font-size:20px">${u.name}</h2><p class="faint" style="font-size:12.5px">${t.name} · ${u.area || u.phone}</p></div></div>
      <button class="btn ghost sm" data-a="close" aria-label="Close">${icon('x')}</button></div>
    <div class="drawer-b">
      <div class="card mini"><h4>Your modules</h4><div class="modchips">${myMods().map(m => `<span class="chip">${icon(MODULES[m].icon, 12)} ${MODULES[m].short}</span>`).join('') || '<span class="faint">None</span>'}</div>
        <p class="faint" style="font-size:12px">Set by the owner in the “${t.name}” role template.</p></div>
      <div class="eyebrow">Switch user (demo)</div>
      <div class="card" style="overflow:hidden">${store.users.filter(x => x.status !== 'disabled').map(x => `<button class="party" data-a="signin" data-u="${x.id}">
        <span class="av">${ini(x.name)}</span><span class="pinfo"><b>${x.name}</b><span class="faint">${TPL(x.tpl) ? TPL(x.tpl).name : '—'}</span></span><span></span>
        <span class="${x.id === S.user ? 'good' : 'faint'}">${x.id === S.user ? icon('check', 16, 2.4) : icon('arrow', 16)}</span></button>`).join('')}</div>
      ${themeControls()}
      <button class="btn" data-a="resetdemo">Reset demo data</button>
    </div>`);
};

/* legacy owner shortcuts now open modules */
A.ojump = d => openModule({ desk:'approvals', manager:'team', sales:'plan' }[d.r] || d.r);

/* uid comes from live.js once the real user is known. Without it this is
   the offline prototype, which opens as the demo owner. */
function startApp(uid) {
  signIn(uid || 'u1');
}

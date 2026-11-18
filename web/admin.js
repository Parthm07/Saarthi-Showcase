/* ================= USER MANAGEMENT + ROLE TEMPLATES ================= */
VIEWS.admin = {};

VIEWS.admin.home = () => {
  const tab = S.admTab || 'users';
  return `<header class="topbar"><div><h1>User management</h1><p class="sub">Decide who uses Saarthi and what each role sees.</p></div>
    <button class="btn primary" data-a="${tab === 'users' ? 'uadd' : 'tnew'}">${icon('plus')} ${tab === 'users' ? 'Add user' : 'New template'}</button></header>
  <div class="tabs2" role="tablist">
    <button class="${tab === 'users' ? 'on' : ''}" data-a="admtab" data-t="users" role="tab" aria-selected="${tab === 'users'}">Users <span>${store.users.length}</span></button>
    <button class="${tab === 'tpl' ? 'on' : ''}" data-a="admtab" data-t="tpl" role="tab" aria-selected="${tab === 'tpl'}">Role templates <span>${store.tpl.length}</span></button></div>
  ${tab === 'users'
    ? `<div class="search"><span class="ic">${icon('search')}</span><input placeholder="Search people" value="${esc(S.uq || '')}" data-in="uq" aria-label="Search people"></div>
       <div class="card" style="overflow:hidden" id="ulist">${userRows()}</div>`
    : `<p class="muted" style="font-size:13px">A template is a set of modules. Everyone on a template sees exactly those modules in their navigation.</p>
       <div class="grid g3">${store.tpl.map(tplCard).join('')}</div>`}`;
};

function userRows() {
  const q = (S.uq || '').toLowerCase().trim();
  const list = store.users.filter(u => !q || (u.name + ' ' + (u.area || '')).toLowerCase().includes(q));
  if (!list.length) return `<div class="empty">No one matches “${esc(S.uq)}”</div>`;
  return list.map(u => { const t = TPL(u.tpl); return `<button class="party" data-a="uopen" data-u="${u.id}">
    <span class="av">${ini(u.name)}</span>
    <span class="pinfo"><b>${u.name}${u.id === S.user ? ' <span class="faint" style="font-weight:500">(you)</span>' : ''}</b><span class="faint">${u.area || u.phone} · ${u.seen}</span>
      <span class="row" style="gap:6px;margin-top:5px"><span class="chip reorder">${t ? t.name : 'No template'}</span>${u.status !== 'active' ? `<span class="chip ${u.status === 'invited' ? 'warn' : 'crit'}">${u.status === 'invited' ? 'Invited' : 'Disabled'}</span>` : ''}</span></span>
    <span></span><span class="faint">${icon('arrow', 16)}</span></button>`; }).join('');
}

function tplCard(t) {
  const n = store.users.filter(u => u.tpl === t.id).length, mods = modsOf(t);
  return `<button class="card tpl-card" data-a="topen" data-t="${t.id}">
    <div class="row between"><b style="font-size:16px">${esc(t.name)}</b><span class="faint" style="font-size:12.5px">${n} ${n === 1 ? 'person' : 'people'}</span></div>
    <p class="faint" style="font-size:12.5px">${esc(t.desc || '')}</p>
    <div class="modchips">${mods.map((m, i) => `<span class="chip ${i ? '' : 'reorder'}">${icon(MODULES[m].icon, 12)} ${MODULES[m].short}</span>`).join('') || '<span class="chip crit">No modules</span>'}</div>
    <span class="link" style="font-size:12.5px">Edit modules ${icon('arrow', 12)}</span></button>`;
}

/* ---------- template editor ---------- */
VIEWS.admin.tpl = () => {
  const d = S.tDraft; if (!d) return VIEWS.admin.home();
  const isNew = !TPL(d.id), users = store.users.filter(u => u.tpl === d.id), on = modsOf(d);
  const home = on[0];
  const tabs = on.length > 5 ? on.slice(0, 4) : on;
  return `<div><div class="crumbs"><button data-a="tback">${icon('back', 14)} Role templates</button></div>
  <header class="topbar"><div><h1>${isNew ? 'New role template' : esc(d.name || 'Untitled')}</h1>
    <p class="sub">Switch on the modules this role should see. They appear in the navigation for everyone on this template.</p></div></header></div>
  <section class="split">
    <div class="stack" style="gap:16px">
      <div class="card pad stack">
        <label class="field"><span>Template name</span><input value="${esc(d.name)}" data-in="tname" placeholder="e.g. Van salesman"></label>
        <label class="field"><span>Who is it for?</span><input value="${esc(d.desc || '')}" data-in="tdesc" placeholder="e.g. Sells and delivers from the van"></label>
      </div>
      ${GROUPS.map(g => `<div class="card" style="overflow:hidden"><div class="grp-h">${g}</div>
        ${MOD_ORDER.filter(m => MODULES[m].group === g).map(m => { const locked = (d.locked || []).includes(m), onm = d.mods.includes(m);
          return `<label class="modrow ${onm ? 'on' : ''}"><span class="mi">${icon(MODULES[m].icon, 18)}</span>
            <span class="mt"><b>${MODULES[m].label}</b><span>${MODULES[m].desc}</span></span>
            ${locked ? `<span class="chip">${icon('shield', 12)} Always on</span>` : `<input type="checkbox" class="switch" ${onm ? 'checked' : ''} data-ch="tmod" data-m="${m}" aria-label="${MODULES[m].label}">`}</label>`; }).join('')}</div>`).join('')}
    </div>
    <aside class="side-cards sticky">
      <div class="card mini"><h4>Opens on</h4>
        ${on.length ? `<select class="sel" data-ch="thome" aria-label="First screen">${on.map(m => `<option value="${m}" ${m === home ? 'selected' : ''}>${MODULES[m].label}</option>`).join('')}</select>` : '<p class="faint" style="font-size:13px">Switch on at least one module.</p>'}</div>
      <div class="card mini"><h4>Preview</h4><p class="faint" style="font-size:12.5px;margin-top:-4px">What ${esc(d.name || 'this role')} sees at the bottom of the phone</p>
        <div class="navprev">${tabs.map((m, i) => `<span class="${i ? '' : 'on'}">${icon(MODULES[m].icon, 18)}<i>${MODULES[m].short}</i></span>`).join('')}${on.length > 5 ? `<span>${icon('grid', 18)}<i>More</i></span>` : ''}${on.length ? '' : '<span class="faint">Empty</span>'}</div></div>
      <div class="card mini"><h4>People on this template</h4>
        ${users.length ? users.map(u => `<div class="item"><span class="row" style="gap:8px"><span class="av" style="width:26px;height:26px;font-size:10.5px">${ini(u.name)}</span>${u.name}</span>
          <button class="link" style="font-size:12.5px" data-a="signin" data-u="${u.id}">View as</button></div>`).join('') : '<p class="faint" style="font-size:13px">Nobody yet. Assign it from the Users tab.</p>'}</div>
      <div class="row"><button class="btn primary lg" style="flex:1" data-a="tsave">Save template</button>
        ${!isNew && !users.length && !(d.locked || []).length ? `<button class="btn danger lg" data-a="tdel">Delete</button>` : ''}</div>
    </aside>
  </section>`;
};

A.admtab = d => { S.admTab = d.t; render(); };
A.uq = (d, el) => { S.uq = el.value; $('#ulist').innerHTML = userRows(); };
A.topen = d => { S.tDraft = clone(TPL(d.t)); go('tpl'); };
A.tnew = () => { S.tDraft = { id:'t' + Date.now().toString(36), name:'', desc:'', mods:['orders'], home:'orders' }; go('tpl'); };
A.tback = () => { S.tDraft = null; S.admTab = 'tpl'; go('home'); };
A.tname = (d, el) => { S.tDraft.name = el.value; };
A.tdesc = (d, el) => { S.tDraft.desc = el.value; };
A.tmod = (d, el) => {
  const t = S.tDraft;
  t.mods = el.checked ? [...new Set([...t.mods, d.m])] : t.mods.filter(m => m !== d.m);
  if (!t.mods.includes(t.home)) t.home = modsOf(t)[0];
  render();
};
A.thome = (d, el) => { S.tDraft.home = el.value; render(); };
A.tsave = () => {
  const t = S.tDraft;
  if (!t.name.trim()) { toast('Give the template a name', 'warn'); $('[data-in="tname"]')?.focus(); return; }
  if (!t.mods.length) { toast('Switch on at least one module', 'warn'); return; }
  t.name = t.name.trim();
  const i = store.tpl.findIndex(x => x.id === t.id);
  if (i >= 0) store.tpl[i] = clone(t); else store.tpl.push(clone(t));
  persist();
  const n = store.users.filter(u => u.tpl === t.id).length;
  S.tDraft = null; S.admTab = 'tpl'; go('home');
  toast(`${t.name} saved · ${n ? `${n} ${n === 1 ? 'person sees' : 'people see'}` : 'will show'} ${t.mods.length} modules`);
};
A.tdel = () => { const t = S.tDraft; store.tpl = store.tpl.filter(x => x.id !== t.id); persist(); S.tDraft = null; S.admTab = 'tpl'; go('home'); toast(`${t.name} deleted`); };

/* ---------- user drawer ---------- */
A.uopen = d => userDrawer(d.u);
function userDrawer(uid) {
  const u = USER(uid), t = TPL(u.tpl), self = u.id === S.user;
  drawer(`<div class="drawer-h"><div class="row" style="gap:12px"><span class="av" style="width:44px;height:44px">${ini(u.name)}</span>
      <div><h2 style="font-size:20px">${u.name}</h2><p class="faint" style="font-size:12.5px"><span class="mono">${u.phone}</span> · ${u.seen}</p></div></div>
      <button class="btn ghost sm" data-a="close" aria-label="Close">${icon('x')}</button></div>
    <div class="drawer-b">
      <label class="field"><span>Role template</span><select class="sel" data-ch="utpl" data-u="${u.id}">${store.tpl.map(x => `<option value="${x.id}" ${x.id === u.tpl ? 'selected' : ''}>${esc(x.name)}</option>`).join('')}</select></label>
      <div class="card mini"><h4>${u.name.split(' ')[0]} will see</h4>
        <div class="modchips">${t ? modsOf(t).map((m, i) => `<span class="chip ${i ? '' : 'reorder'}">${icon(MODULES[m].icon, 12)} ${MODULES[m].label}</span>`).join('') : ''}</div></div>
      <label class="field"><span>Area / details</span><input value="${esc(u.area || '')}" data-in="uarea" data-u="${u.id}" placeholder="e.g. Indore North"></label>
      <div class="card mini"><h4>Salesperson details</h4><p class="faint" style="font-size:12px;margin-top:-4px">Every order and visit is tagged to this person for reports.</p>
        <div class="pset">
          <label class="field"><span>Employee code</span><input value="${esc(u.emp || 'MD-' + u.id.slice(1).padStart(3, '0'))}" data-in="ufield" data-k="emp" data-u="${u.id}"></label>
          <label class="field"><span>Reports to</span><select class="sel" data-ch="ufield" data-k="boss" data-u="${u.id}">${store.users.filter(x => ['owner', 'manager'].includes(x.tpl) && x.id !== u.id)
            .map(x => `<option value="${x.id}" ${(u.boss || (u.tpl === 'sales' ? 'u2' : 'u1')) === x.id ? 'selected' : ''}>${esc(x.name)}</option>`).join('')}</select></label>
          <label class="field"><span>Joined on</span><input type="date" value="${u.joined || '2024-04-01'}" data-ch="ufield" data-k="joined" data-u="${u.id}"></label>
          <label class="field"><span>Monthly target ₹</span><input inputmode="numeric" value="${u.target ?? (u.rep ? R(u.rep).target : '')}" data-in="ufield" data-k="target" data-u="${u.id}"></label>
        </div></div>
      <label class="modrow on" style="border:1px solid var(--line);border-radius:12px"><span class="mi">${icon('shield', 18)}</span>
        <span class="mt"><b>Access</b><span>${u.status === 'disabled' ? 'Disabled · cannot sign in' : u.status === 'invited' ? 'Invited · can sign in once they accept' : 'Can sign in to the app'}</span></span>
        <input type="checkbox" class="switch" ${u.status !== 'disabled' ? 'checked' : ''} ${self ? 'disabled' : ''} data-ch="ustat" data-u="${u.id}" aria-label="Access"></label>
    </div>
    <div class="drawer-f"><button class="btn" data-a="close">Done</button>
      ${u.status !== 'disabled' && !self ? `<button class="btn primary" data-a="signin" data-u="${u.id}">View app as ${u.name.split(' ')[0]}</button>` : ''}</div>`);
}
A.utpl = (d, el) => {
  const u = USER(d.u), nt = TPL(el.value);
  if (u.id === S.user && !nt.mods.includes('users')) { toast('Keep yourself on a template with User management, or you’ll lock yourself out', 'warn'); userDrawer(d.u); return; }
  u.tpl = el.value; persist(); render(); userDrawer(d.u);
  toast(`${u.name} is now ${nt.name}`);
};
A.ustat = (d, el) => { const u = USER(d.u); u.status = el.checked ? 'active' : 'disabled'; persist(); render(); userDrawer(d.u); toast(`${u.name} ${el.checked ? 'can sign in' : 'disabled'}`); };
A.uarea = (d, el) => { USER(d.u).area = el.value; persist(); };
A.ufield = (d, el) => { USER(d.u)[d.k] = el.value; persist(); };

A.uadd = () => drawer(`<div class="drawer-h"><div><div class="eyebrow">User management</div><h2 style="font-size:20px;margin-top:4px">Add a user</h2></div><button class="btn ghost sm" data-a="close" aria-label="Close">${icon('x')}</button></div>
  <div class="drawer-b">
    <label class="field"><span>Full name</span><input id="nuName" placeholder="e.g. Deepak Soni"></label>
    <label class="field"><span>Mobile number</span><input id="nuPhone" inputmode="tel" placeholder="98260 12345"></label>
    <label class="field"><span>Role template</span><select class="sel" id="nuTpl">${store.tpl.map(x => `<option value="${x.id}" ${x.id === 'sales' ? 'selected' : ''}>${esc(x.name)}</option>`).join('')}</select></label>
    <label class="field"><span>Area (optional)</span><input id="nuArea" placeholder="e.g. Indore North"></label>
    <label class="field"><span>Employee code</span><input id="nuEmp" placeholder="e.g. MD-011"></label>
    <div class="alert info"><span class="ai">${icon('send')}</span><span>They get an SMS with a link to the app and sign in with an OTP on this number.</span></div>
  </div>
  <div class="drawer-f"><button class="btn" data-a="close">Cancel</button><button class="btn primary" data-a="ucreate">Send invite</button></div>`);
A.ucreate = () => {
  const name = $('#nuName').value.trim(), phone = $('#nuPhone').value.trim() || '—';
  if (!name) { toast('Enter the person’s name', 'warn'); $('#nuName').focus(); return; }
  const id = 'u' + Date.now().toString(36);
  store.users.push({ id, name, phone, tpl:$('#nuTpl').value, status:'invited', seen:'Invite sent just now', area:$('#nuArea').value.trim(), emp:$('#nuEmp').value.trim() });
  persist(); closeOverlay(); S.admTab = 'users'; render();
  toast(`Invite sent to ${name}`);
};

'use strict';
const $=s=>document.querySelector(s);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money=n=>n==null?'—':'₹'+Number(n).toLocaleString('en-IN',{maximumFractionDigits:2});
const date=s=>s&&/^\d{8}$/.test(s)?s.slice(6,8)+'/'+s.slice(4,6)+'/'+s.slice(0,4):s||'—';
const when=s=>s?new Date(s).toLocaleString('en-IN'):'Not recorded';
const badge=s=>`<span class="badge ${['cancelled','deleted','rejected'].includes(s)?'bad':s==='active'||s==='invoiced'?'':'muted'}">${esc(s||'unknown')}</span>`;
const labels={updates:'Updates & setup',analysis:'Analysis',transactions:'Transactions',overview:'Overview',orders:'Orders',vouchers:'Tally vouchers',parties:'Parties & ledgers',products:'Products & stock',website:'Website access',explorer:'Data explorer',sync:'Sync & backups'};
const S={view:'overview',q:'',category:'',page:1,table:'',start:'',end:'',generation:0};
let overview=null,searchTimer=null;
let employees=[],employee=null;
async function api(path,body){
  const res=await fetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const data=await res.json();if(!res.ok){const error=new Error(data.error||'Request failed');error.status=res.status;throw error;}return data;
}
function notify(message){$('#toast').textContent=message;$('#toast').hidden=false;setTimeout(()=>$('#toast').hidden=true,7000);}
function error(message){$('#main').innerHTML=`<div class="notice error">${esc(message)}</div><button data-retry>Retry</button>`;}
const head=(title,sub,extra='')=>`<div class="page-head"><div><div class="eyebrow">LOCAL WORKSPACE</div><h1>${esc(title)}</h1><p>${esc(sub)}</p></div>${extra}</div>`;
const panel=(title,body,note='')=>`<section class="panel"><div class="panel-head"><h2>${esc(title)}</h2><small>${esc(note)}</small></div>${body}</section>`;
function table(headers,rows,keys=null,scope=''){
  if(!keys){
    if(headers[0]==='Order'){keys=['order_no','date','source','party_name','salesperson','status','display_total','tally_vchno'];scope='listing';}
    else if(headers[0]==='Party / ledger'){keys=['name','group','balance','balance_asof'];scope='listing';}
    else if(headers[0]==='Date'&&headers[3]==='Party'&&S.view==='vouchers'){keys=['date','number','type','party','total','state'];scope='listing';}
    else if(headers[0]==='Created'&&headers[1]==='Entry'){keys=['created_at','kind','summary','total','state',null];scope='requests';}
    else if(headers[0]==='Product'&&headers[1]==='Stock'){keys=['name','stock','rate','last_sold_rate','purchase_rate','estimated_margin','minimum','target','reorder_qty',null,null];scope='inventory';}
  }
  const active=scope==='listing'||scope==='explorer'?{key:S.sort,direction:S.direction}:scope==='analysis'?window.salesAnalysis?.sorting?.():scope==='inventory'?window.stockAdmin?.sorting?.():scope==='requests'?window.adminWork?.sorting?.():null;
  return `<div class="table-wrap"><table data-sort-scope="${esc(scope)}"><thead><tr>${headers.map((h,i)=>{const key=keys?keys[i]:String(i);return `<th ${active?.key===key?`aria-sort="${active.direction==='desc'?'descending':'ascending'}"`:''}>${key==null?esc(h):`<button type="button" class="column-sort" data-table-sort="${esc(key)}" data-column="${i}" title="Sort ${esc(h)}">${esc(h)} ${active?.key===key?(active.direction==='desc'?'↓':'↑'):'↕'}</button>`}</th>`;}).join('')}</tr></thead><tbody>${rows.length?rows.join(''):`<tr><td colspan="${headers.length}"><div class="empty">No records in this selection.</div></td></tr>`}</tbody></table></div>`;
}
async function sortTable(button){
  const grid=button.closest('table'),scope=grid.dataset.sortScope,key=button.dataset.tableSort;
  if(scope==='listing'||scope==='explorer'){S.direction=S.sort===key&&S.direction==='asc'?'desc':'asc';S.sort=key;S.page=1;return scope==='explorer'?loadExplorer():loadListing();}
  if(scope==='analysis')return window.salesAnalysis.sort(key);
  if(scope==='inventory')return window.stockAdmin.sort(key);
  if(scope==='requests')return window.adminWork.sort(key);
  const column=+button.dataset.column,descending=button.dataset.direction!=='desc',rows=Array.from(grid.tBodies[0].rows);
  const value=r=>{const v=r.cells[column]?.textContent.trim()||'';if(!v||v==='—'||v==='Unavailable')return null;const dateMatch=v.match(/^(\d{2})\/(\d{2})\/(\d{4})$/);if(dateMatch)return dateMatch[3]+dateMatch[2]+dateMatch[1];const n=v.replace(/[₹,%\s,]/g,'');return /^-?\d+(\.\d+)?$/.test(n)?Number(n):v;};
  rows.sort((a,b)=>{const x=value(a),y=value(b);return x==null||y==null?window.ListTools.compare(x,y):window.ListTools.compare(x,y)*(descending?1:-1);});
  for(const row of rows)grid.tBodies[0].appendChild(row);button.dataset.direction=descending?'desc':'asc';
  for(const th of grid.querySelectorAll('th'))th.removeAttribute('aria-sort');button.closest('th').setAttribute('aria-sort',descending?'ascending':'descending');
}

const pager=d=>`<div class="pagination"><span>${d.total.toLocaleString('en-IN')} records · Page ${d.page} of ${Math.max(1,Math.ceil(d.total/d.size))}</span><div><button data-page="${d.page-1}" ${d.page<=1?'disabled':''}>Previous</button><button data-page="${d.page+1}" ${d.page*d.size>=d.total?'disabled':''}>Next</button></div></div>`;
const row=(kind,id,body)=>`<tr data-kind="${kind}" data-id="${esc(id)}" tabindex="0">${body}</tr>`;
const cells=values=>values.map(v=>`<td>${v}</td>`).join('');
function voucherTable(rows){return table(['Date','Voucher','Type','Party','Amount','State'],rows.map(v=>row('vouchers',v.guid,cells([date(v.date),esc(v.number||'—'),esc(v.type),esc(v.party),money(v.total),badge(v.state)]))));}

async function loadOverview(){
  const generation=++S.generation;
  const data=await api('/api/overview?'+new URLSearchParams({start:S.start,end:S.end}));
  if(generation!==S.generation)return;
  overview=data;$('#company').textContent=data.company;
  $('#loaded').textContent='Loaded '+new Date().toLocaleTimeString('en-IN',{hour:'2-digit',minute:'2-digit'});
  const iso=s=>s.slice(0,4)+'-'+s.slice(4,6)+'-'+s.slice(6,8);
  const dates=`<div class="period"><input type="date" id="from" aria-label="From date" value="${iso(data.start)}"><span>to</span><input type="date" id="to" aria-label="To date" value="${iso(data.end)}"><button id="overview-mtd">Month to date</button></div>`;
  const metric=(label,value,note,primary=false)=>`<div class="metric ${primary?'primary':''}"><div class="label">${label}</div><div class="value">${value}</div><small>${note}</small></div>`;
  const maximum=Math.max(1,...data.daily.map(d=>d.total));
  const chart=data.sales_count?`<div class="chart" role="img" aria-label="Daily sales in selected period">${data.daily.map(d=>`<div class="bar" tabindex="0" data-tooltip="${date(d.date)} · ${money(d.total)} · ${d.count} invoices" title="${date(d.date)} · ${money(d.total)} · ${d.count} invoices" aria-label="${date(d.date)}: ${money(d.total)}, ${d.count} invoices">${d.total?`<i style="height:${Math.max(1,d.total/maximum*100)}%"></i>`:''}</div>`).join('')}</div><div class="chart-labels"><span>${date(data.start)}</span><span>Invoice-date sales · active vouchers only</span><span>${date(data.end)}</span></div>`:'<div class="empty">No sales in this period.</div>';
  $('#main').innerHTML=head('Your business, in view.','Full local archive · '+data.company,dates)+
    `<div class="metrics">${metric('Overall sales',money(data.sales),data.sales_count+' confirmed invoice'+(data.sales_count===1?'':'s'),true)}${metric('<button id="overview-debtors">Debtors ↗</button>',money(data.debtors),data.debtor_count+' parties · current outstanding balance')}${metric('Credit notes',money(data.credits),'Shown separately from gross sales')}${metric('Orders awaiting billing',data.pending,data.failed+' with recorded billing errors')}</div>`+
    (data.failed?`<div class="notice error">${data.failed} order(s) have recorded invoice errors. Open Orders to inspect the reason.</div>`:'')+
    `<div class="grid">${panel('Sales over time',chart,date(data.start)+' – '+date(data.end))}${panel('Business by route',(data.routes||[]).length?table(['Route','Sales'],data.routes.map(r=>`<tr>${cells([esc(r.name),money(r.gross)])}</tr>`)):'<div class="empty">No sales in this period.</div>','Selected period')}</div>`+
    panel('Top 10 selling products',table(['Product','Sales','Net quantity'],(data.top_products||[]).map(p=>`<tr>${cells([esc(p.name),money(p.gross),esc(Object.entries(p.units||{}).map(([u,q])=>q+' '+u).join(' · '))])}</tr>`)),'Selected period')+
    `<p class="path">${esc(data.database)} · ${data.current_vouchers.toLocaleString()} current vouchers · ${data.versions.toLocaleString()} saved versions</p>`;
}

function listingTable(d){
  if(S.view==='vouchers')return voucherTable(d.rows);
  if(S.view==='orders')return table(['Order','Date','Source','Party','Salesperson','Status','Current value','Invoice'],d.rows.map(r=>row(r.detail_kind||'orders',r.detail_id||r.order_no,cells([esc(r.order_no),esc(r.date?date(r.date):when(r.created_at)),esc(r.source||'Website'),esc(r.invoice?.party||r.party_name),esc(r.salesperson),badge(r.status),money(r.display_total),esc(r.tally_vchno||'—')]))));
  if(S.view==='products')return table(['Product · exact Tally name','Group','Unit','Stock','Reference rate','Last sold price','Last sale date'],d.rows.map(r=>row('products',r.id,cells([esc(r.name),esc(r.group),esc(r.unit||'—'),r.stock==null?badge('unknown'):r.stock<=0?`<span class="badge bad">${r.stock}</span>`:esc(r.stock),money(r.rate),money(r.last_sold_rate),date(r.last_sold_date)]))),['name','group','unit','stock','rate','last_sold_rate','last_sold_date'],'listing');
  return table(['Party / ledger','Tally group','Balance (Tally sign)','Balance observed'],d.rows.map(r=>row('parties',r.id,cells([esc(r.name),esc(r.group),money(r.balance),esc(when(r.balance_asof))]))));
}
async function loadListing(fresh=false){
  if(S.view==='products'&&window.stockAdmin)return window.stockAdmin.load();
  const view=S.view,generation=++S.generation;
  const data=await api('/api/list?'+new URLSearchParams({kind:view,q:S.q,page:S.page,category:S.category,sort:S.sort||'',direction:S.direction||'asc'}));
  if(generation!==S.generation||S.view!==view)return;
  if(fresh){
    const subtitles={orders:'Website orders and synced Tally sales. Use the Deleted filter to review removed invoices.',vouchers:'Synced Tally vouchers. Use the Deleted filter to review removed vouchers.',parties:'All local accounting ledgers, including customers and suppliers.',products:'Names and stock come directly from the local Tally archive.'};
    const checksDeletions = view === 'orders' || view === 'vouchers';
    const actions = view === 'parties' ? '<button data-work-kind="party_create">＋ Create party</button>'
      : checksDeletions ? '<button id="check-deletions">Check Tally deletions</button>' : '';
    const categories = checksDeletions ? [...new Set([...data.categories, 'deleted'])] : data.categories;
    $('#main').innerHTML=head(labels[view],subtitles[view],actions)+`<div class="toolbar"><input type="search" id="search" aria-label="Search records" placeholder="Search the entire list…" value="${esc(S.q)}"><select id="category" aria-label="Filter records"><option value="">${checksDeletions?'Exclude deleted':'All groups'}</option>${categories.map(c=>`<option value="${esc(c)}" ${c===S.category?'selected':''}>${esc(c==='deleted'?'Deleted':c||'Uncategorised')}</option>`).join('')}</select></div><div id="listing" class="panel"></div>`;
  }
  $('#listing').innerHTML=listingTable(data)+pager(data);
}

async function loadExplorer(fresh=false){
  const generation=++S.generation;
  if(!S.table){
    const data=await api('/api/explorer');if(generation!==S.generation)return;data.sort((a,b)=>S.explorerSort==='rows'?b.rows-a.rows:a.name.localeCompare(b.name));
    $('#main').innerHTML=head('Explore the complete archive.','Every local table, including raw source records, snapshots, summaries and processing state.')+
      `<label>Sort tables<select id="explorer-sort"><option value="name" ${S.explorerSort!=='rows'?'selected':''}>Name A–Z</option><option value="rows" ${S.explorerSort==='rows'?'selected':''}>Most records</option></select></label>`+'<div class="notice">The explorer is read-only. Opening a compressed record expands its original Tally fields. CSV exports include all rows in the selected table.</div>'+
      `<div class="table-cards">${data.map(t=>`<button class="table-card" data-table="${esc(t.name)}"><b>${esc(t.name)}</b><small>${t.rows.toLocaleString('en-IN')} rows · Open table →</small></button>`).join('')}</div>`;
    return;
  }
  const data=await api('/api/explorer?'+new URLSearchParams({table:S.table,page:S.page,q:S.q,sort:S.sort||'',direction:S.direction||'desc'}));if(generation!==S.generation)return;
  if(fresh)$('#main').innerHTML=head(S.table,'Complete local table. Select a row to inspect every field.',`<div class="inline-actions"><button id="all-tables">← All tables</button><a href="/api/export?${new URLSearchParams({table:S.table})}" download>Export full CSV</a></div>`)+
    `<div class="toolbar"><input type="search" id="search" aria-label="Search records" placeholder="Search all text fields…" value="${esc(S.q)}"></div><div id="listing" class="panel"></div>`;
  $('#listing').innerHTML=table(data.columns.map(c=>c.name),data.rows.map(r=>`<tr data-rowid="${r.__rowid__}" tabindex="0">${data.columns.map(c=>`<td title="${esc(r[c.name])}">${esc(r[c.name]??'—')}</td>`).join('')}</tr>`),data.columns.map(c=>c.name),'explorer')+pager(data);
}

let lastAgentState;
async function loadSync(){
  const generation=++S.generation,[data,agent]=await Promise.all([api('/api/overview'),api('/api/agent')]);if(generation!==S.generation)return;
  overview=data;
  const jobs=Object.entries(data.sync).filter(([k])=>k.startsWith('last_run_'));
  const checkingDeletions=agent.deletion_check_pending;
  lastAgentState=agent.state;
  const canCheckDeletions=agent.managed&&['started','running'].includes(agent.state);
  $('#main').innerHTML=head('Sync & local safeguards.','Observe the existing laptop agent and create a verified copy of the archive.')+
    '<div class="notice">Use Stop agent to pause all syncing and invoicing. The agent finishes its current work and stays stopped until you select Start agent, even if you reopen Admin.</div>'+
    panel('Tally agent',`<div class="pad"><p role="status">${badge(agent.state)} ${esc(agent.message)} ${agent.pid?'· PID '+esc(agent.pid):''}</p><button id="agent-start" ${['running','started','checking','stopping','stop-pending','disabled'].includes(agent.state)?'disabled':''}>Start agent</button> <button id="agent-stop" ${['stopped','stopping','disabled','conflict'].includes(agent.state)?'disabled':''}>${agent.state==='stop-pending'?'Check stop':'Stop agent'}</button>${agent.managed?'<p>Set which jobs can run in Updates & setup.</p>':`<div class="form-grid"><label>Agent script<input id="agent-script" value="${esc(agent.script||'')}" placeholder="Full path to tally_agent.py"></label><label>Python interpreter<input id="agent-python" value="${esc(agent.python||'')}" placeholder="Full path to python.exe"></label></div><button id="agent-save">Save agent paths</button><p>Uses the agent next to the active tally_raw.db. Another source folder running an agent blocks a second worker.</p>`}</div>`)+
    `<div class="sync-grid">${panel('Archive connection',`<div class="pad"><div class="definition"><span>Company</span><b>${esc(data.company)}</b></div><div class="definition"><span>Database</span><b class="path">${esc(data.database)}</b></div><div class="definition"><span>Latest stock snapshot</span><b>${esc(when(data.stock_asof))}</b></div><div class="definition"><span>Recorded invoice error</span><b>${esc(data.sync.invoice_error||'None recorded')}</b></div><p>Data is refreshed from disk every 30 seconds while this window is open.</p></div>`)}${panel('Back up the archive',`<div class="pad"><p>Creates a consistent SQLite backup and verifies its integrity while the agent can keep running.</p><button id="backup">Create verified backup</button><p id="backup-result" class="path"></p><p>This local copy should also be copied to your separate backup drive.</p></div>`)}</div>`+
    (agent.managed?panel('Tally invoice deletions',`<div class="pad"><p>Use this after deleting an invoice in Tally. The agent checks the voucher list, which may take a few minutes on larger books. Keep only the intended company open.</p><button id="check-deletions" ${!canCheckDeletions||checkingDeletions?'disabled':''}>Check Tally deletions</button><p role="status">${esc(checkingDeletions?'Waiting for the agent to finish the deletion check…':data.sync.deletion_check_result||'')}</p></div>`):'')+
    panel('Recorded agent jobs',table(['Job','Last recorded run'],jobs.map(([k,v])=>`<tr>${cells([esc(k.replace('last_run_','')),esc(when(v))])}</tr>`)))+
    `<details><summary>All recorded sync state</summary><pre>${esc(JSON.stringify(data.sync,null,2))}</pre></details>`;
}

function employeeForm(){
  const p=employee||{email:'',name:'',role:'salesperson'};
  return `<form id="employee-form"><div class="form-grid"><label>Google login email<input id="employee-email" type="email" required value="${esc(p.email)}" ${employee?'readonly':''}></label><label>Name<input id="employee-name" required value="${esc(p.name||'')}"></label><label>Access role<select id="employee-role"><option value="salesperson" ${p.role==='salesperson'?'selected':''}>Salesperson</option><option value="admin" ${p.role==='admin'?'selected':''}>Owner</option></select></label></div><p>Employees sign in on the website with this Google account. Salespeople can browse all sales parties and see their own orders.</p><button type="submit">${employee?'Save changes':'Add website login'}</button> ${employee?'<button type="button" id="employee-cancel">Cancel edit</button>':''}</form>`;
}
async function loadWebsite(){
  const generation=++S.generation,status=await api('/api/website/status');
  let users=[];if(status.connected)users=await api('/api/website/users');
  if(generation!==S.generation)return;
  employees=users;
  $('#main').innerHTML=head('Website access','Manage employee logins with an owner-approved website connection.')+
    (status.connected?`<div class="notice">Connected as ${esc(status.email)} · connection lasts up to one hour. <button id="website-disconnect">Disconnect</button></div>`:
    panel('Connect your website',`<div class="pad"><label>Website address<input id="website-url" type="url" placeholder="https://your-website.example" value="${esc(status.url)}"></label><p>Opens your website in the browser. Sign in as an Owner and approve employee management. The website must have the desktop access update installed.</p><button id="website-connect">${status.pending?'Connect again':'Connect as owner'}</button>${status.pending?'<p>Waiting for browser approval. After approving, press Refresh here.</p>':''}</div>`))+
    (status.connected?panel(employee?'Edit employee':'Add employee',`<div class="pad" id="employee-editor">${employeeForm()}</div>`)+
    panel('Employees',table(['Name','Google login email','Role','Access','Actions'],users.map((p,i)=>`<tr>${cells([esc(p.name),esc(p.email),esc(p.role==='admin'?'Owner':'Salesperson'),badge(p.active?'active':'disabled'),`<button data-employee="${i}">Edit</button> ${p.is_you?'Your account':`<button data-toggle-employee="${i}">${p.active?'Disable':'Enable'}</button>`}`])}</tr>`))):'<div class="notice">Website access changes require internet and an Owner account. Your local reports remain available offline.</div>');
}

function definitions(data){return Object.entries(data).filter(([,v])=>v==null||typeof v!=='object').map(([k,v])=>`<div class="definition"><span>${esc(k.replaceAll('_',' '))}</span><b>${esc(v??'—')}</b></div>`).join('');}
const raw=(title,data)=>`<details><summary>${esc(title)}</summary><pre>${esc(JSON.stringify(data,null,2))}</pre></details>`;
function invoiceDetails(d){
  const rec=d.record||{},lines=rec['ALLINVENTORYENTRIES.LIST']||[];
  return `<p>${badge(d.state)} · Tally voucher ${esc(rec.VOUCHERNUMBER||'—')} · ${date(d.vdate)} · ${esc(d.party)}</p><h2 class="detail-section">${d.state==='active'?money(Math.abs(d.amount||0)):'Inactive voucher — excluded from sales totals'}</h2>`+
    table(['Product','Billed quantity','Rate','Amount (Tally)'],lines.map(l=>`<tr>${cells([esc(l.STOCKITEMNAME),esc(l.BILLEDQTY||l.ACTUALQTY),esc(l.RATE),esc(l.AMOUNT)])}</tr>`))+
    (d.versions?.length?`<label>Saved version <select class="version-select" id="version" data-guid="${esc(d.guid)}">${d.versions.map(v=>`<option value="${v.alterid}" ${v.alterid===d.alterid?'selected':''}>AlterID ${v.alterid} · ${date(v.vdate)} · ${money(Math.abs(v.amount||0))}</option>`).join('')}</select></label>`:'')+raw('All Tally fields in this saved version',rec)+raw('Version metadata',d.versions||[]);
}
async function openDetail(kind,id,version){
  $('#detail-title').textContent=kind==='vouchers'?'Tally voucher':'Record details';
  $('#detail-body').textContent='Loading local record…';if(!$('#detail').open)$('#detail').showModal();
  try{
    const data=await api('/api/detail?'+new URLSearchParams({kind,id,...(version?{version}:{})}));
    $('#detail-title').textContent=data.name||data.order_no||'Tally voucher';
    if(kind==='vouchers')$('#detail-body').innerHTML=invoiceDetails(data);
    else if(kind==='orders')$('#detail-body').innerHTML=definitions({order_no:data.order_no,party:data.party_name,status:data.status,salesperson:(data.note||'').match(/^\[([^\]]+)\]/)?.[1]||'Unattributed',invoice_error:data.invoice_error})+
      (data.invoice?.unavailable?'<div class="notice">The linked invoice has not been observed in this archive.</div>':data.invoice?'<h2 class="detail-section">Current Tally invoice</h2>'+invoiceDetails(data.invoice):'<h2 class="detail-section">Submitted order total · '+money(data.total)+'</h2>')+
      raw('Preserved original submission (internal recovery record)',data.submitted_lines)+raw('All order processing fields',data);
    else $('#detail-body').innerHTML=definitions(data)+raw('All original Tally master fields',data.record)+raw('Calculated local facts',data.facts)+(data.vouchers?.length?'<h2 class="detail-section">Party voucher history</h2>'+voucherTable(data.vouchers):'');
    if(kind==='products')$('#detail-body').insertAdjacentHTML('afterbegin',`<div class="inline-actions"><button data-stock-rate="${esc(id)}">Edit our rate</button><button data-image-product="${esc(id)}">Attach image</button></div><img class="product-preview" src="/api/work/image?guid=${encodeURIComponent(id)}" alt="No image attached">`);
  }catch(e){$('#detail-body').innerHTML=`<div class="notice error">${esc(e.message)}</div>`;}
}

async function navigate(view,reset=true){
  clearTimeout(searchTimer);S.view=view;if(reset){S.q='';S.page=1;S.category='';S.table='';S.sort='';S.direction='asc';}
  ++S.generation; // Invalidate the previous screen's in-flight response.
  $('#crumb').textContent=labels[view];$('#nav').querySelectorAll('button').forEach(b=>b.classList.toggle('active',b.dataset.view===view));
  $('#main').setAttribute('aria-busy','true');
  if(reset)$('#main').innerHTML=head(labels[view],'Loading '+labels[view]+'…')+'<div class="panel"><div class="empty">Reading local records…</div></div>';
  try{if(view==='overview')await loadOverview();else if(view==='sync')await loadSync();else if(view==='website')await loadWebsite();else if(view==='updates')await window.saarthiUpdates.load();else if(view==='analysis')await window.salesAnalysis.load();else if(view==='transactions')await window.adminWork.load();else if(view==='explorer')await loadExplorer(true);else await loadListing(true);}
  catch(e){if(S.view===view)error(e.message);}
  finally{if(S.view===view)$('#main').removeAttribute('aria-busy');}
  if(reset)window.scrollTo(0,0);
}
document.addEventListener('click',async e=>{
  const el=e.target.closest('button,a,tr[data-id],tr[data-rowid]');if(!el)return;
  try{
    if(el.dataset.tableSort!==undefined)return sortTable(el);
    if(el.dataset.view)return navigate(el.dataset.view);
    if(el.dataset.id)return openDetail(el.dataset.kind,el.dataset.id);
    if(el.dataset.rowid){
      const data=await api('/api/explorer?'+new URLSearchParams({table:S.table,rowid:el.dataset.rowid}));
      $('#detail-title').textContent=S.table+' · complete row';$('#detail-body').innerHTML='<pre>'+esc(JSON.stringify(data,null,2))+'</pre>';$('#detail').showModal();return;
    }
    if(el.dataset.table){S.sort='';S.direction='desc';S.table=el.dataset.table;S.page=1;S.q='';return loadExplorer(true);}
    if(el.dataset.page){S.page=+el.dataset.page;return S.view==='explorer'?loadExplorer():loadListing();}
    if(el.id==='all-tables'){S.table='';return loadExplorer();}
    if(el.id==='website-connect'){await api('/api/website/connect',{url:$('#website-url').value});return loadWebsite();}
    if(el.id==='website-disconnect'){await api('/api/website/disconnect',{});employee=null;return loadWebsite();}
    if(el.dataset.employee!==undefined){employee=employees[+el.dataset.employee];$('#employee-editor').innerHTML=employeeForm();$('#employee-email').focus();return;}
    if(el.id==='employee-cancel'){employee=null;$('#employee-editor').innerHTML=employeeForm();return;}
    if(el.dataset.toggleEmployee!==undefined){const p=employees[+el.dataset.toggleEmployee];await api('/api/website/active',{email:p.email,active:!p.active});return loadWebsite();}
    if(el.id==='agent-start'||el.id==='agent-stop'){
      const action=el.id==='agent-start'?'start':'stop';
      await api('/api/agent/'+action,{});
      notify(action==='start'?'Starting agent…':'Stop requested. Waiting for current work to finish.');
      await loadSync();
      setTimeout(()=>{if(S.view==='sync')loadSync().catch(e=>notify(e.message));},2000);return;
    }
    if(el.id==='check-deletions'){
      el.disabled=true;
      try {
        await api('/api/agent/check-deletions',{});
        notify('Deletion check queued. The list updates after the agent finishes. See Sync & backups for the result.');
        if(S.view==='sync')await loadSync();
      } finally {el.disabled=false;}
      return;
    }
    if(el.id==='agent-save'){await api('/api/settings',{agent_script:$('#agent-script').value,agent_python:$('#agent-python').value});notify('Agent paths saved. Use Start / check agent to apply.');return;}
    if(el.id==='close-detail')return $('#detail').close();
    if(el.id==='refresh'||el.hasAttribute('data-retry'))return navigate(S.view,false);
    if(el.id==='apply-dates'){S.start=$('#from').value.replaceAll('-','');S.end=$('#to').value.replaceAll('-','');return loadOverview();}
    if(el.id==='backup'){
      el.disabled=true;el.textContent='Creating backup…';
      try{const data=await api('/api/backup',{});$('#backup-result').textContent='Verified backup: '+data.path+(data.admin_path?' · Admin requests and images: '+data.admin_path:'');notify('Backup verified and saved.');}
      finally{el.disabled=false;el.textContent='Create verified backup';}return;
    }
    if(el.id==='exit'){await api('/api/exit',{});$('#main').innerHTML=head('Admin closed.','You can close this window. The Tally sync agent is unaffected.');return;}
  }catch(err){notify(err.message);}
});
document.addEventListener('submit',async e=>{
  if(e.target.id!=='employee-form')return;e.preventDefault();
  const button=e.target.querySelector('button[type="submit"]');button.disabled=true;
  try{await api('/api/website/users',{email:$('#employee-email').value.trim(),name:$('#employee-name').value.trim(),role:$('#employee-role').value,routes:employee?.routes||[]});employee=null;notify('Website login saved.');await loadWebsite();}
  catch(err){notify(err.message);}finally{button.disabled=false;}
});
document.addEventListener('input',e=>{
  if(e.target.id!=='search')return;S.q=e.target.value;S.page=1;clearTimeout(searchTimer);
  searchTimer=setTimeout(()=>{(S.view==='explorer'?loadExplorer():loadListing()).catch(e=>notify(e.message));},220);
});
document.addEventListener('change',e=>{
  if(['from','to'].includes(e.target.id)&&S.view==='overview'){S.start=$('#from').value.replaceAll('-','');S.end=$('#to').value.replaceAll('-','');loadOverview().catch(e=>notify(e.message));}

  if(e.target.id==='category'){S.category=e.target.value;S.page=1;loadListing().catch(e=>notify(e.message));}
  if(e.target.id==='version')openDetail('vouchers',e.target.dataset.guid,e.target.value);
});
document.addEventListener('keydown',e=>{if(e.key==='Enter'&&e.target.matches('tr[data-id],tr[data-rowid]'))e.target.click();});
let refreshTicks=0, refreshing=false;
setInterval(async()=>{
  const regular=++refreshTicks%6===0;
  const liveViews=['overview','vouchers','products','parties'];
  const live=refreshTicks%2===0&&liveViews.includes(S.view);
  const agentChanging=S.view==='sync'&&['checking','stopping','remote-resuming'].includes(lastAgentState);
  if(refreshing||(!regular&&!live&&!agentChanging&&S.view!=='orders'))return;
  refreshing=true;
  try{if(regular)await api('/api/health');if(!document.hidden&&!$('#detail').open&&$('#main').getAttribute('aria-busy')!=='true'&&!document.activeElement.matches('input,select,textarea')){
    if(S.view==='overview')await loadOverview();else if(S.view==='sync')await loadSync();else if(S.view==='updates')await window.saarthiUpdates.load();else if(S.view==='analysis')await window.salesAnalysis.load();else if(S.view==='website'||S.view==='transactions'){/* Preserve form values until explicit refresh. */}
    else if(S.view==='products'&&window.stockAdmin){await window.stockAdmin.refresh();}
    else if(S.view==='explorer'){if(S.table)await loadExplorer();}else await loadListing();
  }}catch(e){$('#loaded').textContent='Refresh unavailable · retry';}
  finally{refreshing=false;}
},5000);
navigate('overview');

document.addEventListener('click',e=>{if(e.target.closest('#overview-mtd')){S.start='';S.end='';loadOverview().catch(error=>notify(error.message));}});

document.addEventListener('click',e=>{if(e.target.closest('#overview-debtors')&&overview){$('#detail-title').textContent='Debtors · latest local balances';$('#detail-body').innerHTML=table(['Party','Route','Outstanding','Observed'],(overview.debtor_list||[]).map(p=>'<tr>'+cells([esc(p.name),esc(p.group),money(-p.balance),esc(when(p.balance_asof))])+'</tr>'));$('#detail').showModal();}});

document.addEventListener('change',e=>{if(e.target.id==='explorer-sort'){S.explorerSort=e.target.value;loadExplorer().catch(error=>notify(error.message));}});

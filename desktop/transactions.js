'use strict';
// Accounting requests are saved locally and executed only by the Tally agent.
window.adminWork=(()=>{
  const titles={purchase:'Record purchase',sale:'Create sale',party_create:'Create party',product_create:'Add product',product_update:'Edit product',image:'Product image'};
  const W={kind:'sale',context:null,fields:{},lines:[{}],pending:null,key:null,page:1,partySearch:'',partyOpen:false,partyActive:0,partyVisible:[]};
  const productStates=new WeakMap();let productSequence=0,activeProduct=null;
  function productState(row){if(!productStates.has(row))productStates.set(row,{row,key:'product-'+(++productSequence),query:'',active:0,visible:[]});return productStates.get(row);}
  function closeProducts(){
    if(!activeProduct)return;
    const key=activeProduct.key,input=$('#'+key),list=$('#'+key+'-results');
    if(input){input.setAttribute('aria-expanded','false');input.removeAttribute('aria-activedescendant');}
    if(list)list.hidden=true;activeProduct=null;
  }
  function productPicker(row,label='Product'){
    const state=productState(row),product=W.context.products.find(p=>p.id===row.item),key=state.key;
    return `<div class="party-combobox product-combobox"><label for="${key}">${esc(label)}</label><input id="${key}" data-product-key="${key}" role="combobox" aria-autocomplete="list" aria-controls="${key}-results" aria-expanded="false" autocomplete="off" required value="${esc(product?product.name:state.query)}" placeholder="Type to search all products…"><div id="${key}-results" class="party-results" role="listbox" aria-label="Matching products" hidden></div><small id="${key}-count" role="status">${product?'Selected: '+esc(product.name):'Search by product name or group, then choose a result.'}</small></div>`;
  }
  function stateForInput(input){return [W.fields,...W.lines].map(productState).find(s=>s.key===input.dataset.productKey);}
  function refreshProducts(state){
    if(activeProduct!==state)closeProducts();activeProduct=state;
    const query=searchable(state.query),tokens=query.split(' ').filter(Boolean);
    const rank=p=>{const name=searchable(p.name);return name===query?0:name.startsWith(query)?1:name.split(/[^\p{L}\p{N}]+/u).some(w=>w.startsWith(query))?2:3;};
    const matches=W.context.products.filter(p=>tokens.every(t=>searchable(p.name+' '+(p.group||'')+' '+(p.unit||'')).includes(t))).sort((a,b)=>rank(a)-rank(b)||a.name.localeCompare(b.name));
    state.visible=matches.slice(0,50);state.active=Math.max(0,Math.min(state.active,state.visible.length-1));
    const input=$('#'+state.key),list=$('#'+state.key+'-results');list.hidden=false;input.setAttribute('aria-expanded','true');
    list.innerHTML=state.visible.length?state.visible.map((p,i)=>`<button type="button" tabindex="-1" role="option" id="${state.key}-option-${i}" data-product-id="${esc(p.id)}" aria-selected="${i===state.active}" class="party-option ${i===state.active?'active':''}"><span>${esc(p.name)}</span><small>${esc(p.group||'')}${p.unit?' · '+esc(p.unit):''}</small></button>`).join(''):'<div class="party-empty">No matching products. Try another name or group.</div>';
    if(state.visible.length)input.setAttribute('aria-activedescendant',state.key+'-option-'+state.active);else input.removeAttribute('aria-activedescendant');
    $('#'+state.key+'-count').textContent=matches.length>50?'Showing 50 of '+matches.length+' matches. Keep typing to narrow results.':matches.length+' matching products';
  }
  function selectProduct(id){
    const state=activeProduct,product=W.context.products.find(p=>p.id===id);if(!state||!product)return;
    state.row.item=id;state.query=product.name;
    const input=$('#'+state.key);input.value=product.name;input.setCustomValidity('');
    $('#'+state.key+'-count').textContent='Selected: '+product.name;
    if(state.row===W.fields){
      if(W.kind==='product_update'){chooseProduct(id);$('#work-form [data-work-field="name"]').value=W.fields.name;$('#work-form [data-work-field="price"]').value='';}
      if(W.kind==='image'){const preview=$('#work-editor > #work-form fieldset > .product-preview');preview?.setAttribute('src','/api/work/image?guid='+encodeURIComponent(id));preview?.removeAttribute('hidden');}
    }else if(W.kind==='sale'&&product.rate!=null){state.row.rate=product.rate;input.closest('[data-work-line]').querySelector('[data-work-field="rate"]').value=product.rate;}
    closeProducts();total();
  }
  const field=(label,name,type='text',value='',extra='')=>`<label>${esc(label)}<input data-work-field="${name}" type="${type}" value="${esc(value)}" ${extra}></label>`;
  const select=(label,name,options,value='',extra='')=>`<label>${esc(label)}<select data-work-field="${name}" ${extra}><option value="">Choose…</option>${options.map(o=>{const id=typeof o==='string'?o:o.id,name=typeof o==='string'?o:o.name;return `<option value="${esc(id)}" ${id===value?'selected':''}>${esc(name)}</option>`;}).join('')}</select></label>`;
  const today=()=>new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Kolkata',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
  const searchable=value=>String(value||'').normalize('NFKD').replace(/[\u0300-\u036f]/g,'').toLocaleLowerCase().replace(/\s+/g,' ').trim();
  function partyMatches(){
    const rows=W.context[W.kind==='purchase'?'suppliers':'customers'];
    const query=searchable(W.partySearch),tokens=query.split(' ').filter(Boolean);
    const rank=p=>{const name=searchable(p.name);return name===query?0:name.startsWith(query)?1:name.split(/[^\p{L}\p{N}]+/u).some(w=>w.startsWith(query))?2:3;};
    return rows.filter(p=>tokens.every(t=>searchable(p.name+' '+(p.group||'')).includes(t)))
      .sort((a,b)=>rank(a)-rank(b)||a.name.localeCompare(b.name));
  }
  function closeParties(){
    W.partyOpen=false;const input=$('#work-party-combo'),list=$('#work-party-results');
    if(input){input.setAttribute('aria-expanded','false');input.removeAttribute('aria-activedescendant');}
    if(list)list.hidden=true;
  }
  function refreshPartyOptions(){
    const matches=partyMatches(),visible=matches.slice(0,50);W.partyVisible=visible;
    W.partyActive=Math.max(0,Math.min(W.partyActive||0,visible.length-1));
    const input=$('#work-party-combo'),list=$('#work-party-results');if(!input||!list)return;
    list.hidden=false;W.partyOpen=true;input.setAttribute('aria-expanded','true');
    list.innerHTML=visible.length?visible.map((p,i)=>`<button type="button" role="option" tabindex="-1" id="work-party-option-${i}" data-party-id="${esc(p.id)}" aria-selected="${i===W.partyActive}" class="party-option ${i===W.partyActive?'active':''}"><span>${esc(p.name)}</span><small>${esc(p.group||'')}</small></button>`).join(''):'<div class="party-empty">No matching parties. Try another name or group.</div>';
    if(visible.length)input.setAttribute('aria-activedescendant','work-party-option-'+W.partyActive);else input.removeAttribute('aria-activedescendant');
    $('#work-party-count').textContent=(matches.length>50?'Showing 50 of '+matches.length+' matches. Keep typing to narrow results.':matches.length+' matching '+(W.kind==='purchase'?'supplier':'customer')+(matches.length===1?'':'s'));
  }
  function chooseParty(id){
    const party=(W.context[W.kind==='purchase'?'suppliers':'customers']).find(p=>p.id===id);if(!party)return;
    if(W.kind==='sale'&&W.saleParty!==id){W.saleCarts=W.saleCarts||{};if(W.saleParty)W.saleCarts[W.saleParty]=W.lines;W.saleParty=id;W.lines=W.saleCarts[id]||[{}];}
    W.fields.party=id;W.partySearch=party.name;
    const input=$('#work-party-combo');input.value=party.name;input.setCustomValidity('');
    $('#work-party-count').textContent='Selected: '+party.name+(party.group?' · '+party.group:'');closeParties();
    if(W.kind==='sale')saleProducts();
  }
  function partyPicker(){
    const selected=(W.context[W.kind==='purchase'?'suppliers':'customers']).find(p=>p.id===W.fields.party);
    const value=selected?selected.name:W.partySearch;
    return `<div class="party-combobox"><label for="work-party-combo">${W.kind==='purchase'?'Supplier':'Sales party'}</label><input id="work-party-combo" type="text" role="combobox" aria-autocomplete="list" aria-controls="work-party-results" aria-expanded="false" autocomplete="off" required value="${esc(value)}" placeholder="Type to search all ${W.kind==='purchase'?'suppliers':'customers'}…"><div id="work-party-results" class="party-results" role="listbox" aria-label="Matching parties" hidden></div><small id="work-party-count" role="status">${selected?'Selected: '+esc(selected.name):'Search by party name or group, then choose a result.'}</small></div>`;
  }
  function defaults(){
    const c=W.context;
    if(!W.fields.date)W.fields.date=today();
    if(!W.fields.godown&&c.godowns.length===1)W.fields.godown=c.godowns[0];
    const ledgers=c[W.kind==='purchase'?'purchase_ledgers':'sales_ledgers'];
    if(!W.fields.ledger&&ledgers.length===1)W.fields.ledger=ledgers[0].id;
  }
  function saleProductCard(product){
    const line=W.lines.find(row=>row.item===product.id)||{};
    const quote=product.rate;
    const orderLoad=product.order_load??0;
    const unavailable=product.stock==null||product.stock<=orderLoad;
    const stockClass=product.stock==null?'unknown':product.stock<=0?'negative':'positive';
    const unit=esc(product.unit||''),id=esc(product.id);
    return `<article class="sale-product">
      <div class="sale-product-header">
        <div><b>${esc(product.name)}</b><small>${esc(product.group||'')}</small></div>
        <div class="sale-stock"><span>Stock</span><strong class="stock-value ${stockClass}">${product.stock==null?'Unknown':esc(product.stock)+' '+unit}</strong></div>
      </div>
      <div class="sale-product-facts">
        <div class="sale-last-price"><span>Last sold price</span><strong>${product.last_sold_rate==null?'No sale recorded':money(product.last_sold_rate)}</strong></div>
        <div class="sale-product-alerts">
          ${orderLoad>0?`<span>Order load ${esc(orderLoad)} ${unit}</span>`:''}
          ${unavailable?'<span>Stock unavailable</span>':''}
        </div>
      </div>
      <div class="sale-product-entry">
        <label>Rate ₹<input data-sale-rate="${id}" type="number" min="0.01" step="0.01" value="${esc(line.rate??quote??'')}"></label>
        <label>Qty<input data-sale-qty="${id}" type="number" min="0" step="any" value="${esc(line.qty||0)}"></label>
      </div>
    </article>`;
  }
  function saleProducts(){
    const node=$('#sale-products');if(!node||W.kind!=='sale')return;
    const rows=window.ListTools.products(W.context.products,W.saleQuery||'',W.saleSort||'name:asc'),visible=rows.slice(0,W.saleLimit||30);
    node.innerHTML=W.fields.party?`<div class="sale-catalogue">${visible.map(saleProductCard).join('')}</div><p>${rows.length} matching products ${rows.length>visible.length?'<button type="button" id="sale-more">Show more</button>':''}</p>`:'<p>Choose a party first, then add products to its order.</p>';
    total();
  }
  function saleEntry(){
    const c=W.context,f=W.fields;
    const accounting=`<details ${!f.ledger||!f.godown?'open':''}><summary>Invoice settings</summary><div class="form-grid">${field('Date','date','date',f.date,'required')}${select('Sales account','ledger',c.sales_ledgers,f.ledger,'required')}${select('Godown','godown',c.godowns,f.godown,'required')}${field('Note','note','text',f.note||'','maxlength="400"')}</div></details>`;
    if(W.review||W.pending)return accounting+`<h2>Review sale</h2><p>${esc(c.customers.find(p=>p.id===f.party)?.name||'Selected party')}</p>`+table(['Product','Quantity','Rate','Amount'],W.lines.filter(l=>l.item).map(l=>`<tr>${cells([esc(c.products.find(p=>p.id===l.item)?.name||l.item),esc(l.qty),money(l.rate),money(Number(l.qty)*Number(l.rate))])}</tr>`))+`<p class="entry-total">Order total <b id="work-total"></b></p>${!W.pending?'<button type="button" id="sale-back">← Edit products</button>':''}`;
    return partyPicker()+accounting+`<div class="sale-search"><label>Search all products<input id="sale-search" type="search" value="${esc(W.saleQuery||'')}" placeholder="Product name or group…"></label><label>Sort products<select id="sale-sort">${window.ListTools.options(W.saleSort)}</select></label></div><div id="sale-products"></div><div class="sale-summary">Order total <b id="work-total"></b><span>Review the order below before saving</span></div>`;
  }
  function entryForm(){
    const f=W.fields,c=W.context,k=W.kind;defaults();let fields='';
    if(k==='sale'){fields=saleEntry();}
    else if(k==='purchase'){
      const purchase=k==='purchase';
      fields=partyPicker()+`<div class="form-grid">${field('Invoice date','date','date',f.date,'required')}${select(purchase?'Purchase account':'Sales account','ledger',c[purchase?'purchase_ledgers':'sales_ledgers'],f.ledger,'required')}${select('Godown','godown',c.godowns,f.godown,'required')}${purchase?field('Supplier bill number','bill_no','text',f.bill_no||'','required maxlength="80"'):''}${field('Note','note','text',f.note||'','maxlength="400"')}</div>`;
      fields+='<p>No GST. Quantities change stock only after Tally confirms the voucher.</p><div class="entry-lines">'+W.lines.map((l,i)=>`<div class="entry-line" data-work-line="${i}">${productPicker(l)}${field('Quantity','qty','number',l.qty||'','required min="0.0001" max="10000000" step="any"')}${field('Rate / unit','rate','number',l.rate||'','required min="0.01" max="10000000" step="0.01"')}<button type="button" data-work-remove="${i}" aria-label="Remove line ${i+1}" ${W.lines.length===1?'disabled':''}>×</button></div>`).join('')+'</div><button type="button" id="work-add-line">＋ Add product line</button><p class="entry-total">Total <b id="work-total"></b></p>';
    }else if(k==='party_create'){
      fields=`<div class="form-grid">${field('Party name','name','text',f.name||'','required maxlength="200"')}${select('Customer route or supplier group','group',[...c.customer_groups,...c.supplier_groups],f.group,'required')}${field('Phone','phone','tel',f.phone||'','maxlength="40"')}${field('Address','address','text',f.address||'','maxlength="400"')}</div><p>Select a group under Sundry Debtors for a sales customer, or Sundry Creditors for a supplier.</p>`;
    }else if(k==='product_create'||k==='product_update'){
      fields=`<div class="form-grid">${k==='product_update'?productPicker(f,'Existing product'):''}${field(k==='product_update'?'Product name after change':'Exact product name','name','text',f.name||'','required maxlength="200"')}${k==='product_create'?select('Stock group','group',c.stock_groups,f.group,'required')+select('Base unit','unit',c.units,f.unit,'required'):''}${field('Future selling price / unit (optional)','price','number',f.price??'','min="0.01" max="10000000" step="0.01"')}</div><p>Names are changed in Tally using the existing product identity. A new selling price applies from today after confirmation; previous invoice amounts stay intact. Record a purchase to add stock.</p>`;
    }else{
      fields=`<div class="form-grid">${productPicker(f)}</div>${window.productImage.picker()}<p>Pictures are resized and saved locally. Cloud sync publishes them to the sales website.</p><img class="product-preview" ${f.item?`src="/api/work/image?guid=${encodeURIComponent(f.item)}"`:'hidden'} alt="No image attached yet">`;
    }
    return `<form id="work-form"><fieldset ${W.pending?'disabled':''}>${fields}</fieldset>${W.pending?'<div class="notice">A previous submission needs acknowledgement. Retry sends exactly that saved request. Its details cannot be changed until acknowledged.</div>':''}<button type="submit" class="work-primary">${W.pending?'Retry saved request':k==='sale'&&!W.review?'Review order':k==='image'?'Save product image':'Save request for Tally'}</button></form>`;
  }
  function total(){const el=$('#work-total');if(el)el.textContent=money(W.lines.reduce((s,l)=>s+Math.round(Number(l.qty||0)*Number(l.rate||0)*100)/100,0));}
  function render(history){
    closeProducts();
    const ctx=W.context;
    $('#main').innerHTML=head('Take an order.','Sales and party creation · '+ctx.company)+
      (!ctx.enabled?'<div class="notice error">Archive preview: choose the active tally_raw.db to save Tally requests.</div>':'')+
      `<div class="work-tabs">${Object.entries(titles).filter(([k])=>['sale','party_create'].includes(k)).map(([k,v])=>`<button data-work-kind="${k}" ${W.pending&&W.pending.kind!==k?'disabled':''} class="${k===W.kind?'selected':''}">${esc(v)}</button>`).join('')}</div>`+
      panel(titles[W.kind],'<div class="pad" id="work-editor">'+entryForm()+'</div>')+
      panel('Saved requests',table(['Created','Entry','Party / product','Total','State','Action'],history.rows.map(r=>`<tr>${cells([esc(when(r.created_at)),esc(titles[r.kind]),esc(r.summary),money(r.total),badge(r.state)+(r.error?`<small class="work-error">${esc(r.error)}</small>`:''),`<button data-work-detail="${r.id}">Details</button> ${['failed','uncertain'].includes(r.state)?`<button data-work-retry="${r.id}">${r.state==='uncertain'?'Recheck only':'Retry'}</button>`:''}`])}</tr>`))+`<div class="pagination"><span>${history.total} requests · Page ${history.page} of ${Math.max(1,Math.ceil(history.total/history.size))} · ${W.historySort?'Sorted by '+esc(W.historySort):'Unresolved first'}</span><div><button data-work-page="${history.page-1}" ${history.page<=1?'disabled':''}>Previous</button><button data-work-page="${history.page+1}" ${history.page*history.size>=history.total?'disabled':''}>Next</button></div></div>`,'Refresh to see the agent result. Queued is not invoiced.');
    saleProducts();total();
  }
  async function load(){
    const generation=++S.generation;
    const [context,history]=await Promise.all([api('/api/work/context'),api('/api/work/history?'+new URLSearchParams({page:W.page,sort:W.historySort||'',direction:W.historyDirection||'asc'}))]);
    if(generation!==S.generation||S.view!=='transactions')return;
    W.context=context;W.page=history.page;const key='saarthi-admin-pending:'+context.database;
    if(W.key!==key){W.key=key;W.pending=JSON.parse(localStorage.getItem(key)||'null');if(W.pending){W.kind=W.pending.kind;W.fields={...W.pending.body};W.lines=W.pending.body.lines||[{}];}}
    render(history);
  }
  async function open(kind,item){
    if(kind==='product_create'){notify('Create products in Tally. Saarthi only displays the synced catalogue.');return;}
    if(W.pending){notify('A previous request needs acknowledgement first.');return;}
    closeProducts();closeParties();W.kind=kind;W.fields={};W.lines=[{}];W.review=false;W.saleQuery='';W.saleParty='';W.saleCarts={};W.saleLimit=30;W.page=1;W.partySearch='';await navigate('transactions');
    if(item&&W.context){W.fields.item=item;chooseProduct(item);$('#work-editor').innerHTML=entryForm();total();}
  }
  function chooseProduct(id){const product=W.context.products.find(p=>p.id===id);if(product){W.fields.name=product.name;W.fields.price='';}}
  document.addEventListener('input',e=>{
    if(e.target.id==='sale-search'){W.saleQuery=e.target.value;W.saleLimit=30;saleProducts();return;}
    if(e.target.dataset?.saleQty||e.target.dataset?.saleRate){const id=e.target.dataset.saleQty||e.target.dataset.saleRate,p=W.context.products.find(p=>p.id===id);let line=W.lines.find(l=>l.item===id);if(!line){line={item:id,qty:0,rate:p.rate};W.lines.push(line);}line[e.target.dataset.saleQty?'qty':'rate']=e.target.value;total();return;}

    if(e.target.dataset?.productKey){const state=stateForInput(e.target);state.query=e.target.value;state.row.item='';state.active=0;e.target.setCustomValidity('Choose a product from the search results.');if(W.kind==='image')$('#work-editor > #work-form fieldset > .product-preview')?.setAttribute('hidden','');refreshProducts(state);return;}
    if(e.target.id==='work-party-combo'){W.partySearch=e.target.value;W.fields.party='';W.partyActive=0;e.target.setCustomValidity('Choose a party from the search results.');refreshPartyOptions();return;}
    if(!e.target.dataset.workField)return;
    const line=e.target.closest('[data-work-line]'),name=e.target.dataset.workField;
    if(line)W.lines[+line.dataset.workLine][name]=e.target.value;else W.fields[name]=e.target.value;
    total();
  });
  document.addEventListener('focusin',e=>{if(e.target.dataset?.productKey){closeParties();const state=stateForInput(e.target);state.query=e.target.value;state.active=0;refreshProducts(state);}else if(e.target.id==='work-party-combo'){closeProducts();W.partyActive=0;refreshPartyOptions();}});
  document.addEventListener('focusout',e=>{if(e.target.dataset?.productKey&&!e.relatedTarget?.closest('.product-combobox'))closeProducts();if(e.target.id==='work-party-combo'&&!e.relatedTarget?.closest('.party-combobox'))closeParties();});
  document.addEventListener('mousedown',e=>{if(e.target.closest('[data-party-id], [data-product-id]'))e.preventDefault();});
  document.addEventListener('keydown',e=>{
    if(e.target.dataset?.productKey){
      const state=stateForInput(e.target);
      if(e.key==='Escape'){e.preventDefault();closeProducts();}
      else if(e.key==='ArrowDown'||e.key==='ArrowUp'){
        e.preventDefault();if(activeProduct!==state){state.active=0;refreshProducts(state);return;}
        state.active=Math.max(0,Math.min(state.active+(e.key==='ArrowDown'?1:-1),state.visible.length-1));refreshProducts(state);
        $('#'+state.key+'-option-'+state.active)?.scrollIntoView?.({block:'nearest'});
      }else if(e.key==='Enter'&&activeProduct===state){e.preventDefault();const product=state.visible[state.active];if(product)selectProduct(product.id);}
      return;
    }
    if(e.target.id!=='work-party-combo')return;
    if(e.key==='Escape'){e.preventDefault();closeParties();return;}
    if(e.key==='ArrowDown'||e.key==='ArrowUp'){
      e.preventDefault();if(!W.partyOpen){W.partyActive=0;refreshPartyOptions();return;}
      W.partyActive=Math.max(0,Math.min(W.partyActive+(e.key==='ArrowDown'?1:-1),W.partyVisible.length-1));refreshPartyOptions();
      $('#work-party-option-'+W.partyActive)?.scrollIntoView?.({block:'nearest'});
    }else if(e.key==='Enter'&&W.partyOpen){e.preventDefault();const p=W.partyVisible[W.partyActive];if(p)chooseParty(p.id);}
  });
  document.addEventListener('change',e=>{
    if(e.target.id==='sale-sort'){W.saleSort=e.target.value;W.saleLimit=30;saleProducts();return;}
    const name=e.target.dataset.workField;if(!name)return;
    const line=e.target.closest('[data-work-line]');
    if(line){
      const l=W.lines[+line.dataset.workLine];l[name]=e.target.value;
      if(name==='item'&&W.kind==='sale'){
        const p=W.context.products.find(p=>p.id===l.item);if(p&&p.rate!=null){l.rate=p.rate;line.querySelector('[data-work-field="rate"]').value=l.rate;}
      }
    }else{
      W.fields[name]=e.target.value;
      if(name==='item'&&W.kind==='product_update'){chooseProduct(e.target.value);$('#work-editor').innerHTML=entryForm();}
      else if(name==='item'&&W.kind==='image')$('#work-editor').innerHTML=entryForm();
    }
    total();
  });
  document.addEventListener('click',async e=>{
    if(activeProduct&&!e.target.closest('.product-combobox'))closeProducts();
    if(W.partyOpen&&!e.target.closest('.party-combobox'))closeParties();
    const button=e.target.closest('button');if(!button)return;
    try{
      if(button.id==='sale-more'){W.saleLimit=(W.saleLimit||30)+30;saleProducts();return;}
      if(button.id==='sale-back'){W.review=false;$('#work-editor').innerHTML=entryForm();saleProducts();return;}
      if(button.dataset.productId){selectProduct(button.dataset.productId);return;}
      if(button.dataset.partyId){chooseParty(button.dataset.partyId);return;}
      if(button.dataset.workKind){if(W.pending)return;return open(button.dataset.workKind);}
      if(button.dataset.workPage){W.page=+button.dataset.workPage;return load();}
      if(button.id==='work-add-line'){if(W.lines.length>=100)return;closeProducts();W.lines.push({});$('#work-editor').innerHTML=entryForm();total();return;}
      if(button.dataset.workRemove!==undefined){closeProducts();W.lines.splice(+button.dataset.workRemove,1);$('#work-editor').innerHTML=entryForm();total();return;}
      if(button.dataset.workRetry){await api('/api/work/retry',{id:button.dataset.workRetry});notify('Saved for the agent. Uncertain entries are checked without sending again.');return load();}
      if(button.dataset.workDetail){const d=await api('/api/work/detail?id='+button.dataset.workDetail);$('#detail-title').textContent=titles[d.kind]+' · '+d.state;$('#detail-body').innerHTML='<pre>'+esc(JSON.stringify(d,null,2))+'</pre>';$('#detail').showModal();return;}
      if(button.dataset.editProduct){$('#detail').close();return open('product_update',button.dataset.editProduct);}
      if(button.dataset.imageProduct){$('#detail').close();return open('image',button.dataset.imageProduct);}
    }catch(error){notify(error.message);}
  });
  document.addEventListener('submit',async e=>{
    if(e.target.id!=='work-form')return;e.preventDefault();const button=e.target.querySelector('button[type="submit"]');button.disabled=true;
    try{
      if(W.kind==='sale'&&!W.pending&&!W.review){const lines=W.lines.filter(l=>l.item&&Number(l.qty)>0);if(lines.length>100)throw new Error('A sale can contain at most 100 products.');if(!W.fields.party||!lines.length)throw new Error('Choose a party and add products first.');if(lines.some(l=>!Number.isFinite(Number(l.qty))||!Number.isFinite(Number(l.rate))||Number(l.rate)<=0))throw new Error('Check quantities and rates.');W.lines=lines;W.review=true;$('#work-editor').innerHTML=entryForm();total();return;}
      if(!W.pending){
        const rows=['sale','purchase'].includes(W.kind)?W.lines:['image','product_update'].includes(W.kind)?[W.fields]:[];
        if(rows.some(row=>!W.context.products.some(p=>p.id===row.item)))throw new Error('Choose a product from the search results for every product field.');
      }
      if(W.kind==='image'){
        const id=W.fields.item,form=e.target,picker=form.querySelector('.image-picker');
        const picture=window.productImage.selected(picker);
        if(!picture)throw new Error('Choose a product picture first.');
        await api('/api/work/image',{guid:id,image:picture.encoded});
        notify('Product image saved on this laptop.');
        if(form.isConnected&&W.fields.item===id){
          const preview=form.querySelector('.product-preview');
          preview?.setAttribute('src','/api/work/image?guid='+encodeURIComponent(id)+'&updated='+Date.now());
        }
        return;
      }
      if(!W.pending&&['sale','purchase'].includes(W.kind)&&!W.fields.party)throw new Error('Choose a party from the search results.');
      if(!W.pending){const pending={id:crypto.randomUUID(),kind:W.kind,body:JSON.parse(JSON.stringify({...W.fields,...(['sale','purchase'].includes(W.kind)?{lines:W.lines}:{})}))};localStorage.setItem(W.key,JSON.stringify(pending));W.pending=pending;}
      const saved=await api('/api/work/submit',W.pending);
      localStorage.removeItem(W.key);W.pending=null;W.fields={};W.lines=[{}];W.review=false;
      notify(saved.state==='confirmed'?'Already confirmed in Tally.':'Request saved locally · '+saved.state+'. Check Saved requests for the Tally result.');await load();
    }catch(error){
      if(error.status>=400&&error.status<500){localStorage.removeItem(W.key);W.pending=null;}
      notify(error.message);if(W.pending)$('#work-editor').innerHTML=entryForm();
    }finally{button.disabled=false;}
  });
  return {load,open,sorting:()=>({key:W.historySort,direction:W.historyDirection}),sort(key){W.historyDirection=W.historySort===key&&W.historyDirection==='asc'?'desc':'asc';W.historySort=key;W.page=1;return load();}};
})();

'use strict';
window.stockAdmin=(()=>{
  const I={data:null,tab:'catalogue',q:'',page:1,rates:null,skippedRates:0,images:[],picture:null,selected:new Set(),imageQuery:'',imageSort:'name:asc',imageLimit:40,imageBusy:false,sort:'name:asc',pending:null,busy:false};
  const hide = { query:'', limit:40, selected:new Set(), busy:false };
  const visibleMatches = () => window.ListTools.products(
    I.data.products.filter(p=>!p.website_hidden), hide.query, 'name:asc');
  function hideOptions(){
    const options = $('#hide-product-options');
    if(!options || I.tab!=='hidden')return;
    const matches = visibleMatches();
    options.innerHTML = `<div class="inline-actions">
      <b>${hide.selected.size} selected</b>
      <button id="hide-select-results" ${!matches.length||hide.busy?'disabled':''}>Select all ${matches.length} matches</button>
      <button id="hide-clear" ${!hide.selected.size||hide.busy?'disabled':''}>Clear selection</button>
      <button id="hide-save" ${!hide.selected.size||hide.busy?'disabled':''}>Hide selected products</button>
      </div><div class="image-product-options">${matches.slice(0,hide.limit).map(p=>
        `<label><input type="checkbox" data-hide-select="${esc(p.id)}" ${hide.selected.has(p.id)?'checked':''} ${hide.busy?'disabled':''}>
          <span>${esc(p.name)}<small>${esc(p.group||'')}</small></span></label>`).join('')||'<p>No visible products match.</p>'}</div>
      ${matches.length>hide.limit?'<button id="hide-more">Show more</button>':''}`;
  }
  const field=(label,name,value='',extra='')=>`<label>${esc(label)}<input name="${name}" value="${esc(value??'')}" ${extra}></label>`;
  const lastSold=p=>p.last_sold_rate==null?'No sale recorded':money(p.last_sold_rate)+(p.last_sold_date?' on '+date(p.last_sold_date):'');
  const key=()=> 'saarthi-inventory-rate-batch:'+I.data.database;
  function filtered(){return window.ListTools.products(I.data.products.filter(p=>(I.tab!=='restock'||p.restock)&&(I.tab!=='hidden'||p.website_hidden)),I.q,I.sort);}
  function list(){const rows=filtered(),size=40,pages=Math.max(1,Math.ceil(rows.length/size));I.page=Math.min(I.page,pages);
    $('#inventory-list').innerHTML=table(['Product','Stock','Our rate','Last sold price','Purchase rate · Tally','Est. margin','Reorder level','Target stock','Suggested reorder','Image','Website','Manage'],rows.slice((I.page-1)*size,I.page*size).map(p=>`<tr>${cells([esc(p.name)+`<small class="inventory-group">${esc(p.group||'')} · ${esc(p.unit||'')}</small>`,`<span class="stock-value ${p.stock==null?'unknown':p.stock<=0?'negative':'positive'}">${p.stock==null?'Unknown':esc(p.stock)+' '+esc(p.unit||'')}</span>`,p.our_rate==null?'—':money(p.our_rate),esc(lastSold(p)),p.purchase_rate==null?'Unavailable':money(p.purchase_rate)+' / '+esc(p.purchase_unit||'')+'<small class="inventory-group">'+esc(date(p.purchase_date))+'</small>',p.estimated_margin==null?'Unavailable':p.estimated_margin+'%',p.minimum,p.target??'—',p.reorder_qty??'Set target',p.has_image?`<img class="stock-thumb" loading="lazy" alt="${esc(p.name)}" src="/api/work/image?guid=${encodeURIComponent(p.id)}">`:'—',`<div class="website-visibility"><span class="badge ${p.website_hidden?'warn':'good'}">${p.website_hidden?'Hidden':'Visible'}</span><button data-stock-visibility="${esc(p.id)}" aria-label="${p.website_hidden?'Show':'Hide'} ${esc(p.name)} on website">${p.website_hidden?'Show':'Hide'}</button></div>`,`<button data-stock-edit="${esc(p.id)}">Image / reorder</button><button data-stock-rate="${esc(p.id)}">Edit our rate</button>`])}</tr>`))+`<div class="pagination"><span>${rows.length} products · ${I.page} / ${pages}</span><div><button data-stock-page="${I.page-1}" ${I.page<=1?'disabled':''}>Previous</button><button data-stock-page="${I.page+1}" ${I.page>=pages?'disabled':''}>Next</button></div></div>`;
  }
  function batchState(){return I.pending?`<div class="notice">A saved rate change needs acknowledgement. Retry saves the same values. <button id="inventory-rates-save" ${I.busy?'disabled':''}>Continue saved batch</button></div>`:'';}
  function render(){const products=I.data.products;
    $('#main').innerHTML=head('Products & stock','Tally products · '+I.data.company)+`<div class="metrics team-metrics"><div class="metric primary"><div class="label">Products</div><div class="value">${products.length}</div></div><div class="metric"><div class="label">Need restocking</div><div class="value">${products.filter(p=>p.restock).length}</div></div><div class="metric"><div class="label">Stock unknown</div><div class="value">${products.filter(p=>p.stock==null).length}</div></div></div><nav class="analysis-tabs">${[['catalogue','All products'],['restock','Reorder & out of stock'],['hidden','Hidden on website'],['uploads','Bulk rates & images']].map(([id,label])=>`<button data-stock-tab="${id}" class="${I.tab===id?'active':''}">${label}</button>`).join('')}</nav>`+batchState()+'<p id="inventory-progress" role="status"></p>'+(I.tab==='uploads'?uploads():`<div class="toolbar"><input id="inventory-search" type="search" aria-label="Search all products" placeholder="Search name, group or GUID…" value="${esc(I.q)}"><label>Sort products<select id="inventory-sort">${window.ListTools.options(I.sort,true)}</select></label></div><p class="analysis-note">Purchase rates are read-only, from Tally purchase invoices. Margin is an estimate before expenses. Restock lists known stock at or below the reorder level (default 0). Target stock is optional. Our rates save in Saarthi and apply to future quotes.</p><div class="panel" id="inventory-list"></div>`);
    if(I.tab==='hidden'){
      $('#inventory-list').insertAdjacentHTML('beforebegin',panel('Hide products on the website',`<div class="pad">
        <label>Search visible products <input id="hide-product-search" type="search" value="${esc(hide.query)}"></label>
        <p>Selections stay checked when you search. Hide applies after the next cloud sync.</p>
        <div id="hide-product-options"></div></div>`));
      hideOptions();
    }
    if(I.tab!=='uploads')list();else {imageOptions();window.productImage.show($('#inventory-picture .image-picker'),I.picture);imagePreview();}
  }
  function uploads(){return `<div class="analysis-grid">${panel('Bulk our rates',`<div class="pad"><p>CSV columns: <b>item_guid, product, rate</b>. Use an item GUID or exact Tally product name. Leave a rate blank to keep the current rate. Preview validates the numeric rates before saving them.</p><button id="inventory-template">Download product/rate CSV</button><label class="upload-field">Select rates CSV<input id="inventory-rates-file" type="file" accept=".csv,text/csv" ${I.pending?'disabled':''}></label><div id="inventory-rate-preview">${I.rates?rateTable():''}</div></div>`)}${panel('Product images',`<div class="pad"><p>Select one or several products, then choose one picture to use for all selected products. Pictures are resized for the sales website and published by the next cloud sync.</p><label>Search products<input id="image-product-search" type="search" placeholder="Product name or group…" value="${esc(I.imageQuery)}"></label><label>Sort products<select id="image-product-sort">${window.ListTools.options(I.imageSort)}</select></label><div id="image-product-options"></div><div id="inventory-picture">${window.productImage.picker(I.imageBusy)}</div><div id="inventory-image-preview"></div></div>`) }</div>`;}
  function rateTable(){
    const skipped=I.skippedRates?`<p>Skipped ${I.skippedRates} blank rate cells.</p>`:'';
    if(!I.rates.length)return skipped+'<p>No rate changes to queue.</p>';
    return skipped+table(['Product','New rate'],I.rates.map(r=>`<tr>${cells([esc(r.name),'₹'+esc(r.price)])}</tr>`))+
      `<button id="inventory-rates-save" class="work-primary" ${I.busy?'disabled':''}>Save ${I.rates.length} rates</button>`;
  }
  async function load(){if(S.view!=='products')return;const generation=++S.generation,data=await api('/api/inventory');if(generation!==S.generation||S.view!=='products')return;I.data=data;I.pending=JSON.parse(localStorage.getItem(key())||'null');render();}
  async function refresh(){if(I.tab!=='uploads')await load();}
  function edit(id){const p=I.data.products.find(p=>p.id===id);if(!p)return;$('#detail-title').textContent=p.name;$('#detail-body').innerHTML=`<form id="inventory-product-form" data-guid="${esc(id)}"><p>Last sold price: <b>${esc(lastSold(p))}</b></p><p>Purchase rate (Tally): <b>${p.purchase_rate==null?'Unavailable':money(p.purchase_rate)+' / '+esc(p.purchase_unit||'')}</b> · ${esc(date(p.purchase_date))}</p><div class="form-grid">${field('Reorder level','minimum',p.minimum,'type="number" min="0" max="10000000" step="any" required')}${field('Target stock (optional)','target',p.target,'type="number" min="0" max="10000000" step="any"')}</div>${window.productImage.picker()}<p>Reorder settings save locally; pictures publish to the website through cloud sync.</p><button type="submit">Save product settings</button></form>`;$('#detail').showModal();}
  function editRate(id){
    const product=I.data.products.find(p=>p.id===id);
    if(!product)return;
    $('#detail-title').textContent='Edit our rate';
    $('#detail-body').innerHTML=`<form id="inventory-single-rate" data-guid="${esc(id)}">
      <p><b>${esc(product.name)}</b></p>
      <p>Current Our rate: ${product.our_rate==null?'Unavailable':money(product.our_rate)}</p>
      ${field('New our rate (₹)','price','','type="number" step="any" required')}
      <p>The rate saves in Saarthi and appears on the website after the next cloud sync.</p>
      <button type="submit">Save our rate</button>
    </form>`;
    if(!$('#detail').open)$('#detail').showModal();
  }
  async function saveRates(rates=I.rates){
    if(I.busy)return;I.busy=true;
    try{
      if(!I.pending){if(!rates?.length)throw new Error('Select at least one rate to save.');const pending=rates.map(body=>({body}));localStorage.setItem(key(),JSON.stringify(pending));I.pending=pending;}
      await api('/api/inventory/rates',{rates:I.pending.map(request=>request.body)});
      localStorage.removeItem(key());I.pending=null;I.rates=null;notify('Our rates saved in Saarthi. The website updates on the next cloud sync.');await load();
    }finally{I.busy=false;}
  }
  function imageOptions(){const node=$('#image-product-options');if(!node)return;const rows=window.ListTools.products(I.data.products,I.imageQuery,I.imageSort);node.innerHTML=`<div class="inline-actions"><b>${I.selected.size} selected</b><button type="button" id="image-select-results">Select matching products (up to 100)</button><button type="button" id="image-clear">Clear selection</button></div><p class="selected-image-products">${esc(I.data.products.filter(p=>I.selected.has(p.id)).map(p=>p.name).join(' · '))}</p><div class="image-product-options">${rows.slice(0,I.imageLimit).map(p=>`<label><input type="checkbox" data-image-select="${esc(p.id)}" ${I.selected.has(p.id)?'checked':''}> <span>${esc(p.name)}<small>${esc(p.group||'')}</small></span></label>`).join('')||'<p>No matching products.</p>'}</div>${rows.length>I.imageLimit?'<button type="button" id="image-products-more">Show more</button>':''}<p>${rows.length} matches · selection is kept when searching</p>`;}
  function imagePreview(){
    const node=$('#inventory-image-preview');
    if(!node)return;
    node.innerHTML=I.images.length?table(['Product','Status'],I.images.map(r=>`<tr>${cells([esc(r.product.name),esc(r.state)])}</tr>`))+
      `<button id="inventory-images-save" class="work-primary" ${I.imageBusy||I.images.every(r=>r.saved)?'disabled':''}>Save picture for selected products</button>`:'';
  }
  function assignSharedImage(){
    if(I.imageBusy)return;
    const prior=new Map(I.images.map(row=>[row.product.id,row]));
    I.images=I.picture?I.data.products.filter(p=>I.selected.has(p.id)).map(product=>
      prior.get(product.id)||{product,state:product.has_image?'Ready · replaces existing picture':'Ready'}):[];
    imagePreview();
  }
  document.addEventListener('product-picture-change',e=>{
    if(!e.target.closest('#inventory-picture')||I.imageBusy)return;
    I.picture=window.productImage.selected(e.target);
    I.images=[];
    assignSharedImage();
  });
  document.addEventListener('input',e=>{if(e.target.id==='inventory-search'){I.q=e.target.value;I.page=1;list();}if(e.target.id==='image-product-search'){I.imageQuery=e.target.value;I.imageLimit=40;imageOptions();}
    if(e.target.id==='hide-product-search'){hide.query=e.target.value;hide.limit=40;hideOptions();}
  });
  document.addEventListener('change',async e=>{try{
    if(e.target.dataset?.hideSelect&&!hide.busy){
      const id=e.target.dataset.hideSelect;
      if(e.target.checked)hide.selected.add(id);else hide.selected.delete(id);
      hideOptions();
      $('#hide-product-options').querySelectorAll('[data-hide-select]').forEach(input=>{
        if(input.dataset.hideSelect===id)input.focus();
      });
    }
    if(e.target.id==='inventory-rates-file'){
      const input=e.target,file=input.files[0],preview=$('#inventory-rate-preview');
      if(!file)return;
      I.rates=null;I.skippedRates=0;preview.innerHTML='';
      if(file.size>500000)throw new Error('CSV must be smaller than 500 KB');
      input.disabled=true;
      try{
        const result=await api('/api/inventory/rates/preview',{csv:await file.text()});
        I.rates=result.rows;I.skippedRates=result.skipped||0;
        const current=$('#inventory-rate-preview');
        if(current)current.innerHTML=rateTable();
      }finally{input.disabled=false;input.value='';}
    }
    if(e.target.id==='inventory-sort'){I.sort=e.target.value;I.page=1;list();}
    if(e.target.id==='image-product-sort'){I.imageSort=e.target.value;imageOptions();}
    if(e.target.dataset.imageSelect){if(I.imageBusy){e.target.checked=I.selected.has(e.target.dataset.imageSelect);return;}if(e.target.checked){if(I.selected.size>=100){e.target.checked=false;throw new Error('Select at most 100 products per upload');}I.selected.add(e.target.dataset.imageSelect);}else I.selected.delete(e.target.dataset.imageSelect);imageOptions();assignSharedImage();}
  }catch(error){notify(error.message);}});
  document.addEventListener('click',async e=>{const b=e.target.closest('button');if(!b)return;try{
    if(b.dataset.stockTab){I.tab=b.dataset.stockTab;I.page=1;render();}
    if(b.dataset.stockPage){I.page=+b.dataset.stockPage;list();}
    if(b.dataset.stockEdit)edit(b.dataset.stockEdit);
    if(b.dataset.stockRate)editRate(b.dataset.stockRate);
    if(b.id==='hide-select-results'){for(const p of visibleMatches())hide.selected.add(p.id);hideOptions();}
    if(b.id==='hide-clear'){hide.selected.clear();hideOptions();}
    if(b.id==='hide-more'){hide.limit+=40;hideOptions();}
    if(b.id==='hide-save'&&!hide.busy&&hide.selected.size){
      const guids=[...hide.selected];
      hide.busy=true;hideOptions();
      try {
        await api('/api/inventory/visibility',{guids,hidden:true});
        const hidden = new Set(guids);
        for(const product of I.data.products)
          if(hidden.has(product.id))product.website_hidden=true;
        hide.selected.clear();
        if(S.view==='products'&&I.tab==='hidden')list();
        notify(`${guids.length} products hidden. The website updates on the next cloud sync.`);
      } finally {
        hide.busy=false;
        if(S.view==='products')hideOptions();
      }
    }
    if(b.dataset.stockVisibility){
      const product=I.data.products.find(p=>p.id===b.dataset.stockVisibility);
      if(!product)return;
      b.disabled=true;
      try{
        const result=await api('/api/inventory/visibility',{guid:product.id,hidden:!product.website_hidden});
        const current=I.data.products.find(p=>p.id===product.id);
        if(current)current.website_hidden=result.hidden;
        if(S.view==='products'&&I.tab!=='uploads')list();
        notify(result.hidden?'Product will be hidden on the website after sync.':'Product will be shown on the website after sync.');
      }finally{b.disabled=false;}
    }
    if(b.id==='image-select-results'){if(I.imageBusy)return;for(const p of window.ListTools.products(I.data.products,I.imageQuery,I.imageSort)){if(I.selected.size>=100)break;I.selected.add(p.id);}imageOptions();assignSharedImage();}
    if(b.id==='image-clear'){if(I.imageBusy)return;I.selected.clear();imageOptions();assignSharedImage();}
    if(b.id==='image-products-more'){I.imageLimit+=40;imageOptions();}
    if(b.id==='inventory-rates-save'){b.disabled=true;try{await saveRates();}finally{b.disabled=false;}}
    if(b.id==='inventory-template'){const quote=v=>'"'+String(v??'').replace(/^[=+@-]/,"'$&").replaceAll('"','""')+'"';const csv='\ufeff'+[['item_guid','product','rate'],...I.data.products.map(p=>[p.id,p.name,p.our_rate??''])].map(r=>r.map((value,index)=>index===2&&typeof value==='number'?String(value):quote(value)).join(',')).join('\r\n');const url=URL.createObjectURL(new Blob([csv],{type:'text/csv'}));const a=document.createElement('a');a.href=url;a.download='saarthi-product-rates.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
    if(b.id==='inventory-images-save'){
      if(I.imageBusy)return;
      const picker=$('#inventory-picture .image-picker');
      const picture=window.productImage.selected(picker);
      if(!picture||!I.images.length)throw new Error('Select at least one product and choose a picture.');
      I.imageBusy=true;picker.disabled=true;imagePreview();
      try{
        for(const row of I.images){
          if(row.saved)continue;
          try{
            await api('/api/work/image',{guid:row.product.id,image:picture.encoded});
            row.saved=true;
            row.state='Saved · pending cloud sync';
            const product=I.data.products.find(p=>p.id===row.product.id);
            if(product)product.has_image=true;
          }catch(error){row.state=error.message;}
          imagePreview();
        }
        const failed=I.images.filter(row=>!row.saved).length;
        notify(failed?`${failed} product picture(s) could not be saved. Review the errors and retry.`:
          'Pictures saved locally. The next cloud sync publishes them to the sales website.');
      }finally{
        I.imageBusy=false;
        const current=$('#inventory-picture .image-picker');
        if(current)current.disabled=false;
        imagePreview();
      }
    }
  }catch(error){notify(error.message);}});
  document.addEventListener('submit',async e=>{
    if(e.target.id==='inventory-single-rate'){
      e.preventDefault();
      if(I.busy)return;
      const form=e.target, button=form.querySelector('button[type="submit"]');
      const product=I.data.products.find(p=>p.id===form.dataset.guid);
      const price=new FormData(form).get('price').trim();
      if(!product||!price||!Number.isFinite(Number(price))){notify('Enter a valid rate.');return;}
      if(I.pending && (I.pending.length!==1 || I.pending[0].body.item!==product.id || I.pending[0].body.price!==price)){
        notify('Continue the saved rate request before saving another rate.');return;
      }
      button.disabled=true;
      try {
        await saveRates([{item:product.id,name:product.name,price,rate_only:true}]);
        $('#detail').close();
      } catch(error){notify(error.message);}
      finally{button.disabled=false;}
      return;
    }
    if(e.target.id!=='inventory-product-form')return;e.preventDefault();const form=e.target,button=form.querySelector('button[type="submit"]');button.disabled=true;try{
    const id=form.dataset.guid,values=new FormData(form),image=window.productImage.selected(form.querySelector('.image-picker'));
    await api('/api/inventory/policy',{guid:id,minimum:values.get('minimum'),target:values.get('target')});
    if(image)await api('/api/work/image',{guid:id,image:image.encoded});
    $('#detail').close();notify('Product settings saved.');await load();
  }catch(error){notify(error.message);}finally{button.disabled=false;}});
  return {load,refresh,sorting:()=>({key:I.sort.split(':')[0],direction:I.sort.split(':')[1]}),sort(key){const [prior,direction]=I.sort.split(':');I.sort=key+':'+(prior===key&&direction==='asc'?'desc':'asc');I.page=1;list();}};
})();

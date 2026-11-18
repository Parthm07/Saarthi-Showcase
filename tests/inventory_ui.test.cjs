const {test}=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
function app(saved=new Map()){
  const elements=new Map(),listeners={},requests=[],messages=[];let fail=false,sequence=0;
  const el=selector=>{if(!elements.has(selector))elements.set(selector,{innerHTML:'',textContent:'',disabled:false,showModal(){this.open=true;},close(){this.open=false;},insertAdjacentHTML(){},querySelectorAll(){return [];}});return elements.get(selector);};
  const data={company:'Demo',database:'demo.db',products:[{id:'p',name:'Tally Product',stock:-2,rate:80,minimum:0,restock:true,target:20,reorder_qty:22,unit:'PCS'},{id:'h',name:'Harpic blue',stock:9,rate:80},{id:'c',name:'Colgate charcoal',stock:20,rate:40}]};
  const context={window:{productImage:{picker:()=>'<fieldset class="image-picker"></fieldset>',show:(node,picture)=>{node.picture=picture;},selected:node=>node.picture}},S:{view:'products',generation:0},$:el,esc:String,money:String,date:String,head:()=>'',panel:(_,body)=>body,table:(headers,rows)=>headers.join('|')+rows.join(''),cells:r=>r.join('|'),notify:m=>messages.push(m),localStorage:{getItem:k=>saved.get(k)||null,setItem:(k,v)=>saved.set(k,v),removeItem:k=>saved.delete(k)},crypto:{randomUUID:()=> 'stable-'+(++sequence)},api:async(url,body)=>{
    if(url==='/api/inventory')return data;
    if(url==='/api/inventory/visibility'){if(body.guids)requests.push(JSON.parse(JSON.stringify(body)));return {hidden:body.hidden};}
    if(url.endsWith('/preview'))return body.csv.endsWith('p,\n')?{rows:[],skipped:1}:{rows:[{item:'p',name:'Tally Product',price:'75'}]};
    requests.push(JSON.parse(JSON.stringify(body)));if(fail){fail=false;throw new Error('Response lost');}return {state:'queued'};
  },document:{createElement:()=>({getContext:()=>({drawImage(){}}),toBlob:cb=>cb({})}),addEventListener:(name,fn)=>listeners[name]=fn}};
  vm.createContext(context);vm.runInContext(fs.readFileSync(path.join(__dirname,'../desktop/list-tools.js'),'utf8'),context);vm.runInContext(fs.readFileSync(path.join(__dirname,'../desktop/inventory.js'),'utf8'),context);
  return {context,el,listeners,requests,messages,saved,fail:()=>{fail=true},choosePicture(){
    const picker=el('#inventory-picture .image-picker');
    picker.closest=()=>true;
    picker.picture={encoded:'cGljdHVyZQ=='};
    listeners['product-picture-change']({target:picker});
  },click:async(id,dataset={})=>listeners.click({target:{closest:()=>({id,dataset,disabled:false})}})};
}
test('inventory has reorder and bulk sections; searches all items and displays reorder quantity',async()=>{
  const h=app();await h.context.window.stockAdmin.load();assert.match(h.el('#main').innerHTML,/Reorder & out of stock|Bulk rates & images/);
  await h.click('',{stockTab:'restock'});assert.match(h.el('#inventory-list').innerHTML,/Tally Product/);assert.match(h.el('#inventory-list').innerHTML,/22/);
  h.listeners.input({target:{id:'inventory-search',value:'not here',dataset:{}}});assert.doesNotMatch(h.el('#inventory-list').innerHTML,/Tally Product/);
});
test('hide keeps a product in the desktop catalogue and the hidden tab can restore it',async()=>{
  const h=app();await h.context.window.stockAdmin.load();
  await h.click('',{stockVisibility:'p'});
  assert.match(h.el('#inventory-list').innerHTML,/Show Tally Product on website/);
  await h.click('',{stockTab:'hidden'});
  assert.match(h.el('#inventory-list').innerHTML,/Tally Product/);
  assert.doesNotMatch(h.el('#inventory-list').innerHTML,/Harpic blue/);
  await h.click('',{stockVisibility:'p'});
  assert.doesNotMatch(h.el('#inventory-list').innerHTML,/Tally Product/);
  await h.click('',{stockTab:'catalogue'});
  assert.match(h.el('#inventory-list').innerHTML,/Hide Tally Product on website/);
});
test('bulk hide keeps selected products across searches and sends one update',async()=>{
  const h=app();await h.context.window.stockAdmin.load();await h.click('',{stockTab:'hidden'});
  h.listeners.input({target:{id:'hide-product-search',value:'harpic'}});
  await h.click('hide-select-results');
  h.listeners.input({target:{id:'hide-product-search',value:'colgate'}});
  await h.click('hide-select-results');
  assert.match(h.el('#hide-product-options').innerHTML,/2 selected/);
  await h.click('hide-save');
  assert.deepEqual(h.requests,[{guids:['h','c'],hidden:true}]);
  assert.match(h.el('#inventory-list').innerHTML,/Harpic blue/);
  assert.match(h.el('#inventory-list').innerHTML,/Colgate charcoal/);
});
test('a lost bulk-rate response keeps the same values across reload and retry',async()=>{
  const h=app();await h.context.window.stockAdmin.load();await h.click('',{stockTab:'uploads'});
  await h.listeners.change({target:{id:'inventory-rates-file',files:[{size:30,text:async()=> 'item_guid,rate\np,75'}]}});
  assert.match(h.el('#inventory-rate-preview').innerHTML,/Save 1 rates/);
  h.fail();await h.click('inventory-rates-save');assert.equal(h.saved.size,1);const original=h.requests[0];
  const second=app(h.saved);await second.context.window.stockAdmin.load();await second.click('inventory-rates-save');
  assert.deepEqual(second.requests[0],original);assert.equal(h.saved.size,0);assert.equal(second.requests[0].rates[0].price,'75');
});
test('editing one product rate saves only that product without a CSV',async()=>{
  const h=app();await h.context.window.stockAdmin.load();
  await h.click('',{stockRate:'p'});
  assert.match(h.el('#detail-body').innerHTML,/Edit our rate|Save our rate/);
  const button={disabled:false};
  h.context.FormData=class {get(){return '72.50';}};
  const form={id:'inventory-single-rate',dataset:{guid:'p'},querySelector:()=>button};
  h.fail();
  await h.listeners.submit({target:form,preventDefault(){}});
  assert.equal(h.el('#detail').open,true);
  await h.listeners.submit({target:form,preventDefault(){}});
  assert.deepEqual(h.requests[0],h.requests[1]);
  assert.deepEqual(h.requests[0].rates,[{item:'p',name:'Tally Product',price:'72.50',rate_only:true}]);
  assert.equal(h.el('#detail').open,false);
});
test('a CSV with only blank rates replaces the previous preview and offers nothing to save',async()=>{
  const h=app();await h.context.window.stockAdmin.load();await h.click('',{stockTab:'uploads'});
  const select=csv=>h.listeners.change({target:{id:'inventory-rates-file',files:[{size:csv.length,text:async()=>csv}]}});
  await select('item_guid,rate\np,75\n');
  assert.match(h.el('#inventory-rate-preview').innerHTML,/Save 1 rates/);
  await select('item_guid,rate\np,\n');
  assert.match(h.el('#inventory-rate-preview').innerHTML,/Skipped 1 blank rate cells/);
  assert.doesNotMatch(h.el('#inventory-rate-preview').innerHTML,/inventory-rates-save/);
  assert.deepEqual(h.requests,[]);
});

test('one uploaded picture can be assigned to several searched products',async()=>{
  const h=app();await h.context.window.stockAdmin.load();await h.click('',{stockTab:'uploads'});
  h.listeners.input({target:{id:'image-product-search',value:'har',dataset:{}}});
  const matches=h.el('#image-product-options').innerHTML;assert.ok(matches.indexOf('Harpic blue')<matches.indexOf('Colgate charcoal'));
  await h.click('image-select-results');assert.match(h.el('#image-product-options').innerHTML,/2 selected/);
  h.choosePicture();
  await h.click('inventory-images-save');assert.deepEqual(h.requests.map(r=>r.guid).sort(),['c','h']);
  assert.ok(h.requests.every(r=>r.image==='cGljdHVyZQ=='));
  await h.click('',{stockTab:'catalogue'});assert.match(h.el('#inventory-list').innerHTML,/api\/work\/image\?guid=h/);
});

test('chosen image remains available when the file input is rebuilt by navigation',async()=>{
 const h=app();await h.context.window.stockAdmin.load();await h.click('',{stockTab:'uploads'});
 await h.listeners.change({target:{id:'',checked:true,dataset:{imageSelect:'h'}}});
 h.choosePicture();
 await h.click('',{stockTab:'catalogue'});await h.click('',{stockTab:'uploads'});
 await h.listeners.change({target:{id:'',checked:true,dataset:{imageSelect:'c'}}});
 await h.click('inventory-images-save');assert.deepEqual(h.requests.map(r=>r.guid).sort(),['c','h']);
});

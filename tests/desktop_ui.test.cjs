const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
test('screen navigation acknowledges the selected screen before its data arrives',()=>{
  const elements=new Map();
  const context={URLSearchParams,console,setTimeout,clearTimeout,setInterval:()=>0,
    fetch:()=>new Promise(()=>{}),window:{scrollTo(){}},
    document:{addEventListener(){},querySelector:s=>{
      if(!elements.has(s))elements.set(s,{innerHTML:'Previous screen',textContent:'',
        setAttribute(k,v){this[k]=v},getAttribute(k){return this[k]},removeAttribute(k){delete this[k]},querySelectorAll:()=>[]});
      return elements.get(s);
    }}};
  vm.createContext(context);vm.runInContext(fs.readFileSync(path.join(__dirname,'../desktop/list-tools.js'),'utf8'),context);
  context.document.querySelector('#main');
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../desktop/admin.js'),'utf8'),context);
  vm.runInContext("navigate('products')",context);
  assert.match(elements.get('#main').innerHTML,/Loading Products &amp; stock/);
  assert.doesNotMatch(elements.get('#main').innerHTML,/Previous screen/);
  assert.equal(elements.get('#main')['aria-busy'],'true');
});

test('single party combobox searches all parties, ranks prefixes, selects by keyboard and rejects unselected text',async()=>{
  const elements=new Map(),listeners={};
  const element=s=>{if(!elements.has(s))elements.set(s,{innerHTML:'',textContent:'',value:'',setAttribute(k,v){this[k]=v},removeAttribute(k){delete this[k]},setCustomValidity(v){this.validity=v}});return elements.get(s);};
  const customers=Array.from({length:120},(_,i)=>({id:'c'+i,name:'Customer '+i,group:'Town'}));
  customers.push({id:'prefix',name:'Shipra Kirana',group:'Town'},{id:'inside',name:'Hari Om [Kshipra]',group:'Town'});
  const data={company:'Demo',database:'demo.db',enabled:true,customers,suppliers:[{id:'s',name:'Shipra Supplier',group:'Suppliers'}],products:[],godowns:[],sales_ledgers:[],purchase_ledgers:[]};
  const context={URLSearchParams,window:{},Intl,Date,JSON,console,
    S:{view:'transactions',generation:0},$:element,esc:s=>String(s??''),money:String,head:()=>'',panel:(title,body)=>body,table:()=>'',
    localStorage:{getItem:()=>null},api:async path=>path.includes('/context')?data:{rows:[],page:1,size:50,total:0},
    document:{addEventListener:(name,callback)=>{listeners[name]=callback;}}};
  context.navigate=async()=>context.window.adminWork.load();
  vm.createContext(context);vm.runInContext(fs.readFileSync(path.join(__dirname,'../desktop/list-tools.js'),'utf8'),context);vm.runInContext(fs.readFileSync(path.join(__dirname,'../desktop/transactions.js'),'utf8'),context);
  await context.window.adminWork.open('sale');
  assert.match(element('#main').innerHTML,/role="combobox"/);
  assert.doesNotMatch(element('#main').innerHTML,/work-party-select/);
  const input=value=>listeners.input({target:{id:'work-party-combo',value,setCustomValidity(v){this.validity=v}}});
  input('shipra');let html=element('#work-party-results').innerHTML;
  assert.ok(html.indexOf('Shipra Kirana')<html.indexOf('Hari Om [Kshipra]'));
  assert.doesNotMatch(html,/Shipra Supplier/);
  input('Customer 119');assert.match(element('#work-party-results').innerHTML,/Customer 119/);
  input('shipra');listeners.keydown({target:{id:'work-party-combo'},key:'Enter',preventDefault(){}});
  assert.equal(element('#work-party-combo').value,'Shipra Kirana');assert.equal(element('#work-party-combo').validity,'');
  assert.equal(element('#work-party-results').hidden,true);
  input('missing');assert.match(element('#work-party-results').innerHTML,/No matching parties/);
  assert.equal(element('#work-party-combo')['aria-expanded'],'true');
  await context.window.adminWork.open('purchase');input('shipra');
  assert.match(element('#work-party-results').innerHTML,/Shipra Supplier/);
  assert.doesNotMatch(element('#work-party-results').innerHTML,/Shipra Kirana/);
});
const productHarness=async kind=>{
  const elements=new Map(),listeners={},saved=[],messages=[];
  const element=s=>{if(!elements.has(s))elements.set(s,{innerHTML:'',textContent:'',value:'',dataset:{},setAttribute(k,v){this[k]=v},removeAttribute(k){delete this[k]},setCustomValidity(v){this.validity=v},closest(){return null}});return elements.get(s);};
  const products=Array.from({length:125},(_,i)=>({id:'p'+i,name:'Product '+i,group:'Soap',unit:'pcs',rate:20+i}));
  Object.assign(products[124],{stock:-54,order_load:6,last_sold_rate:12.5});
  products.push({id:'prefix',name:'Harpic Blue',group:'Cleaner',rate:84},{id:'inside',name:'Big Harpic Blue',group:'Cleaner',rate:90});
  const data={company:'Demo',database:'demo.db',enabled:true,customers:[{id:'c',name:'Customer'}],suppliers:[],stock_groups:[],units:[],products,godowns:[],sales_ledgers:[],purchase_ledgers:[]};
  const context={URLSearchParams,window:{},Intl,Date,JSON,console,crypto:{randomUUID:()=> 'request'},S:{view:'transactions',generation:0},$:element,esc:s=>String(s??''),money:String,head:()=>'',panel:(_,body)=>body,table:()=>'',cells:values=>values.join('|'),notify:m=>messages.push(m),localStorage:{getItem:()=>null,setItem(){},removeItem(){}},api:async(path,body)=>{if(path.includes('/context'))return data;if(path.includes('/submit')){saved.push(JSON.parse(JSON.stringify(body)));return {state:'queued'}}return {rows:[],page:1,size:50,total:0}},document:{addEventListener:(name,fn)=>listeners[name]=fn}};
  context.navigate=async()=>{element('#work-editor').innerHTML='';await context.window.adminWork.load();};vm.createContext(context);vm.runInContext(fs.readFileSync(path.join(__dirname,'../desktop/list-tools.js'),'utf8'),context);vm.runInContext(fs.readFileSync(path.join(__dirname,'../desktop/product-image.js'),'utf8'),context);vm.runInContext(fs.readFileSync(path.join(__dirname,'../desktop/transactions.js'),'utf8'),context);await context.window.adminWork.open(kind);
  const keys=()=>[...(element('#work-editor').innerHTML||element('#main').innerHTML).matchAll(/data-product-key="([^"]+)"/g)].map(m=>m[1]);
  const input=(key,value)=>{const el=element('#'+key);el.id=key;el.dataset={productKey:key};el.value=value;listeners.input({target:el});return el};
  const choose=(key,value,line=0)=>{const el=input(key,value);el.closest=()=>({querySelector:()=>element('rate'+line)});listeners.keydown({target:el,key:'Enter',preventDefault(){}});};
  const click=async(id,dataset={})=>listeners.click({target:{closest:selector=>selector==='button'?{id,dataset}:null}});
  const change=(line,name,value)=>listeners.input({target:{dataset:{workField:name},value,closest:()=>({dataset:{workLine:line}})}});
  const submit=async()=>listeners.submit({target:{id:'work-form',querySelector:()=>({disabled:false})},preventDefault(){}});
  return {context,element,listeners,keys,input,choose,click,change,submit,saved,messages};
};
test('sale uses a party-first product catalogue, global search, editable quantities and a separate review before submitting',async()=>{
  const h=await productHarness('sale');
  assert.doesNotMatch(h.element('#main').innerHTML,/data-work-kind="purchase"|data-work-kind="product_create"/);
  await h.click('',{partyId:'c'});
  h.listeners.input({target:{id:'sale-search',value:'Product 124',dataset:{}}});
  const card=h.element('#sale-products').innerHTML;
  assert.match(card,/Product 124/);
  assert.match(card,/stock-value negative">-54 pcs/);
  assert.match(card,/sale-last-price[\s\S]*12\.5[\s\S]*sale-product-alerts[\s\S]*Order load 6 pcs[\s\S]*Stock unavailable/);
  assert.deepEqual([...card.matchAll(/<input data-sale-(rate|qty)="p124"/g)].map(match=>match[1]), ['rate','qty']);
  assert.doesNotMatch(card,/data-sale-step|stock-short|order allowed/i);
  h.listeners.input({target:{dataset:{saleQty:'p124'},value:'5'}});
  h.listeners.input({target:{dataset:{saleRate:'p124'},value:'77'}});
  await h.submit();assert.equal(h.saved.length,0);assert.match(h.element('#work-editor').innerHTML,/Review sale/);
  await h.submit();assert.equal(h.saved.length,1);
  assert.deepEqual(h.saved[0].body.lines,[{item:'p124',rate:'77',qty:'5'}]);
});

test('product picker requires a selected result, preserves purchase rate, and supports edit and image forms',async()=>{
  const h=await productHarness('purchase');const key=h.keys()[0];h.change(0,'rate','55');h.choose(key,'Harpic Blue');
  await h.click('work-add-line');
  assert.match(h.element('#work-editor').innerHTML,/data-work-field="rate" type="number" value="55"/); // Keep the entered supplier rate.
  h.input(key,'nonexistent');assert.match(h.element('#'+key+'-results').innerHTML,/No matching products/);
  await h.submit();assert.equal(h.saved.length,0);assert.match(h.messages.at(-1),/Choose a product/);
  await h.context.window.adminWork.open('product_update');h.choose(h.keys()[0],'Harpic Blue');
  assert.equal(h.element('#work-form [data-work-field="name"]').value,'Harpic Blue');
  await h.context.window.adminWork.open('image');h.choose(h.keys()[0],'Product 124');
  assert.equal(h.element('#work-editor > #work-form fieldset > .product-preview').src,'/api/work/image?guid=p124');
  assert.match(h.element('#main').innerHTML,/Paste from clipboard[\s\S]*Direct image link/);
  const previousKeys=h.keys().length;await h.context.window.adminWork.open('product_create');assert.equal(h.keys().length,previousKeys);assert.match(h.messages.at(-1),/Create products in Tally/);
});

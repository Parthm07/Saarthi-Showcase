// Isolated browser regression tests: no API, Tally, or real browser storage.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {webcrypto} = require('node:crypto');
const root = path.join(__dirname, '..', 'web');
function browser(saved = new Map()) {
  const events = {};
  const app = {innerHTML:'<main id="main"></main>',querySelector:()=>null};
  const element = {innerHTML:'',style:{},classList:{add(){},remove(){}},appendChild(){}};
  const context = {
    console,crypto:webcrypto,URLSearchParams,AbortController,
    setTimeout,clearTimeout,setInterval:()=>0,
    localStorage:{getItem:k=>saved.get(k)||null,setItem:(k,v)=>saved.set(k,v)},
    location:{search:'',protocol:'https:',pathname:'/',reload(){}},
    document:{getElementById:id=>id==='app'?app:element,querySelector:()=>null,
      querySelectorAll:()=>[],addEventListener:(n,f)=>(events[n]??=[]).push(f)},
    innerWidth:400,addEventListener(){},scrollTo(){},
    fetch:async()=>{throw new Error('connection lost')},
  };
  context.window=context;
  vm.createContext(context);
  for (const name of ['data','core','sales','sales-simple','planner','visits',
    'analytics','desk','shell','admin','dash','ops','drafts','live']) {
    const p=path.join(root,name+'.js');
    if (fs.existsSync(p)) vm.runInContext(fs.readFileSync(p,'utf8'),context,{filename:p});
  }
  vm.runInContext(`var renderScreen=render; render=()=>{}; scrollTop=()=>{}; applyDevice=()=>{}; toast=(m)=>{lastToast=m};
    startApp=()=>{started++}; bootFailed=m=>{failure=m};
    var started=0, failure='', lastToast='';`,context);
  return {context,events,saved,run:s=>vm.runInContext(s,context)};
}

test('switching to Routes or applying a date filter preserves the phone dashboard scroll', () => {
  const b = browser();
  b.run(`var main={scrollTop:420,querySelectorAll:()=>[],set innerHTML(value){this.scrollTop=0}};
    document.querySelector=selector=>selector==='#main'?main:null;
    renderChrome=()=>{};
    resolveView=()=>({key:'dashboard-home',html:VIEWS.dash.home()});
    S._lastKey='dashboard-home'; render=renderScreen;
    A.dtab({k:'routes'});`);
  assert.equal(b.run('S.dtab'), 'routes');
  assert.equal(b.run('main.scrollTop'), 420);
  b.run("A.drange({k:'today'})");
  assert.equal(b.run('S.drange'), 'today');
  assert.equal(b.run('main.scrollTop'), 420);
});

test('selecting a product category keeps the swiped filter row in place', () => {
  const b = browser();
  b.run(`const categoryRow = scrollLeft => ({scrollLeft, querySelector:()=>({dataset:{a:'fcat'}})});
    var main = {scrollTop:0, row:categoryRow(260),
      querySelectorAll(){return [this.row]},
      set innerHTML(value){this.row=categoryRow(0)}};
    document.querySelector=selector=>selector==='#main'?main:null;
    renderChrome=()=>{};
    resolveView=()=>({key:'orders-menu',html:''});
    S._lastKey='orders-menu'; render=renderScreen;
    A.fcat({k:'toothpaste'});`);
  assert.equal(b.run('S.fcat'), 'toothpaste');
  assert.equal(b.run('main.row.scrollLeft'), 260);
});

test('live owner navigation cannot show demo users or role templates', () => {
  const b = browser();
  b.run(`LIVE.on=true; LIVE.me={email:'owner@example.com',role:'admin'};
    S.user=installUser(LIVE.me); TPL('owner').mods.push('people', 'newshops');
    LIVE.people=[{name:'Real account',email:'rep@example.com',role:'salesperson',active:true,routes:[],shops:10}];
    S.mod='people'; A.nav({v:'home'});`);
  assert.match(b.run('resolveView().html'), /Real account/);
  assert.doesNotMatch(b.run('resolveView().html'), /Role templates|Anil Mahajan/);
  assert.equal(b.run("myMods().includes('users')"), false);
  b.run("S.mod='newshops'; A.nav({v:'home'})");
  assert.match(b.run('resolveView().html'), /New shops/);
  b.run("LIVE.on=false; S.user='u1'; S.mod='users'; S.view='home'");
  assert.match(b.run('resolveView().html'), /Role templates/);
});

test('People saves the full email and name typed into the form', async () => {
  const b = browser();
  b.run(`var submittedPerson, rejectSave=true;
    api=async(path,body)=>{
      if(path==='/api/users' && body) {
        submittedPerson=body;
        if(rejectSave) throw new Error('Save failed');
      }
      return [];
    };
    A.pnew();`);
  assert.match(b.run('personForm()'),/<form id="person-form"/);
  const fields = {
    email:{value:'sales.long.address@example.test'},
    name:{value:'Anita Sharma'},
    'save-person':{},
  };
  const form = {id:'person-form', elements:{namedItem:name=>fields[name]}};
  b.context.document.querySelector = selector => selector === '#person-form' ? form : null;
  const submit = () => Promise.all(b.events.submit.map(dispatch =>
    dispatch({target:form, preventDefault(){}})));
  b.run("A.proute({r:'Dewas'})");
  await submit();
  assert.equal(b.run('submittedPerson.email'),'sales.long.address@example.test');
  assert.equal(b.run('submittedPerson.name'),'Anita Sharma');
  assert.equal(b.run('submittedPerson.routes[0]'),'Dewas');
  assert.match(b.run('personForm()'),/value="sales.long.address@example.test"/);
  assert.match(b.run('personForm()'),/value="Anita Sharma"/);
  b.run('rejectSave=false');
  await submit();
  assert.equal(b.run('S.person'),null);
});

test('a refresh already in flight leaves the newly opened People form in place', async () => {
  const b = browser();
  b.run(`LIVE.on=true; LIVE.me={email:'owner@example.test',role:'admin'};
    var completeRefresh;
    var replaced=false;
    api=()=>new Promise(resolve=>{completeRefresh=resolve});
    var refreshing=refreshLiveData(true);
    S.mod='people'; A.pnew();
    render=()=>{replaced=true};
    completeRefresh([]);`);
  assert.equal(await b.run('refreshing'),false);
  assert.equal(b.run('replaced'),false);
});

test('manual visit sync retains a local visit on failure and acknowledges it without resending after reload',async()=>{
  const b=browser();b.run(`LIVE.on=true;LIVE.me={email:'rep@example.com'};LIVE.status={company:'Books'};var visitRequests=[];var offline=true;api=async(path,body)=>{visitRequests.push({path,body});if(offline)throw new Error('offline');return {saved:true};};logVisit(CUSTOMERS[0].id);`);
  await b.run('flushVisitLog()');assert.equal(b.run('VISITS.filter(v=>v.logged).length'),1);assert.equal(b.run('VISITS.find(v=>v.logged).synced===true'),false);
  b.run('offline=false');await b.run('flushVisitLog()');assert.equal(b.run('VISITS.find(v=>v.logged).synced'),true);
  const count=b.run('visitRequests.length');b.run('restoreVisitLog()');await b.run('flushVisitLog()');assert.equal(b.run('visitRequests.length'),count);
});

test('item search reaches the whole catalogue outside the selected category and rendered page',()=>{
  const b=browser();
  b.run(`S.fp=CUSTOMERS[0].id;S.fcat='bis';S.fq='Hidden Tally product';
    PRODUCTS.push({id:'hidden',name:'Hidden Tally product MRP 99',grp:'other',price:80,stock:0});
    PCAT.hidden='other';`);
  assert.match(b.run('gridItems()'),/id="pc-hidden"/);
});

test('sales cards keep the Tally name, remove MRP metadata and buying-pattern text, and flag unavailable stock',()=>{
  const b=browser();
  b.run(`S.fp=CUSTOMERS[0].id;var p=PRODUCTS[0];p.name='Exact Tally Name MRP 99';p.stock=-3;
    var hist={p:p.id,rate:80,rateDate:'20261030',freq:[3,7],cyc:[8,28],last:15,due:true};`);
  const html=b.run('itemRow(p,C(S.fp),hist)');
  assert.match(html,/Exact Tally Name MRP 99/);
  assert.doesNotMatch(html,/taken in|usually every|purchases recorded|>MRP/);
  assert.match(html,/Stock unavailable/);
  assert.match(html,/data-a="fqty"/);
});

test('all live routes come from customer groups even without a published visit plan',()=>{
  const b=browser();
  b.run(`LIVE.on=true;CUSTOMERS.splice(0,CUSTOMERS.length,
    {id:'a',name:'Party A',territory:'Dewas Debtors',buys:[]},
    {id:'b',name:'Party B',territory:'Ujjain Debtors',buys:[]});`);
  const routes=JSON.parse(b.run('JSON.stringify(myRoutes())'));
  assert.deepEqual(routes.map(r=>r.name).sort(),['Dewas Debtors','Ujjain Debtors']);
});

test('party list pages all customers and searches beyond the current page and route',()=>{
  const b=browser();
  b.run(`LIVE.on=true;CUSTOMERS.length=0;for(let i=0;i<125;i++) CUSTOMERS.push({
    id:'c'+i,name:'Shop '+String(i).padStart(3,'0'),territory:i===124?'Ujjain':'Dewas',buys:[],rep:'someone-else'});
    S.froute='all';S.fpq='';S.fpage=0;`);
  assert.equal((b.run('partyRows()').match(/data-a="fparty"/g)||[]).length,50);
  b.run(`S.froute=myRoutes().find(r=>r.name==='Dewas').id;S.fpq='Shop 124';`);
  assert.match(b.run('partyRows()'),/Shop 124/);
});

test('party search ranks name prefixes before whole-word, substring and route matches',()=>{
  const b=browser();
  b.run(`LIVE.on=true;CUSTOMERS.splice(0,CUSTOMERS.length,...[
    ['substring','Hari Om Kirana [Kshipra]','Dewas'],
    ['word','Morvi Kirana [Shipra]','Dewas'],
    ['prefix','Shipra Kirana(Ujjain)','Ujjain'],
    ['route','Aardvark Stores','Shipra'],
    ['exact','Shipra','Dewas']
  ].map(([id,name,territory])=>({id,name,territory,buys:[]})));
  S.froute='all';S.fpq='sHiPrA';S.fpage=0;`);
  const ids=[...b.run('partyRows()').matchAll(/data-a="fparty" data-cid="([^"]+)"/g)].map(m=>m[1]);
  assert.deepEqual(ids,['exact','prefix','word','substring','route']);
});

test('party menu hides normal draft notice while party home keeps saved drafts',()=>{
  const b=browser();
  b.run(`LIVE.on=true;LIVE.me={email:'rep@example.com'};LIVE.status={company:'Books'};
    initOrderDrafts();S.fp=CUSTOMERS[0].id;cartOf(S.fp)[PRODUCTS[0].id]=1;saveOrderDrafts();`);
  assert.doesNotMatch(b.run('VIEWS.sales.menu()'),/Draft saved on this device/);
  assert.match(b.run('VIEWS.sales.home()'),/Saved drafts on this device/);
  assert.doesNotMatch(b.run('cartBar()'),/cb-k| qty|btn gold/);
});

test('restored low-stock drafts keep card warnings without the repeated top notice',()=>{
  const first=browser();
  first.run(`LIVE.on=true;LIVE.me={email:'rep@example.com'};LIVE.status={company:'Books'};
    initOrderDrafts();S.fp=CUSTOMERS[0].id;PRODUCTS[0].stock=-2;
    cartOf(S.fp)[PRODUCTS[0].id]=3;saveOrderDrafts();`);
  const next=browser(first.saved);
  next.run(`LIVE.on=true;LIVE.me={email:'rep@example.com'};LIVE.status={company:'Books'};
    PRODUCTS[0].stock=-2;initOrderDrafts();`);
  const html=next.run('VIEWS.sales.review()');
  assert.doesNotMatch(html,/requested quantity exceeds/);
  assert.match(html,/Stock unavailable/);
});

test('unknown and zero stock remain orderable and product prices retain paise',()=>{
  const b=browser();
  for (const stock of ['null','0','-2']) {
    b.run(`S.fp=CUSTOMERS[0].id;PRODUCTS[0].stock=${stock};PRODUCTS[0].price=69.42;`);
    const html=b.run('itemRow(PRODUCTS[0],C(S.fp))');
    assert.match(html,/<span class="stock-note">Stock unavailable<\/span>/);
    assert.doesNotMatch(html,/stock-alert|Order allowed/i);
    if(stock!=='null')assert.match(html,/class="num stock-qty-low"/);
    assert.match(html,/₹69\.42/);
    assert.doesNotMatch(html,/<button[^>]+disabled/);
  }
});

test('navigation hides while a mobile keyboard consumes the viewport and returns afterwards',()=>{
  const b=browser();
  b.run(`var keyboardOpen=false, shellHeight='';document.documentElement={clientHeight:844,style:{setProperty(_,value){shellHeight=value}}};
    var oldElement=document.getElementById;document.getElementById=id=>id==='app'
      ? {classList:{contains:()=>keyboardOpen,toggle:(name,on)=>{keyboardOpen=on}}}:oldElement(id);
    visualViewport={height:844,scale:1};document.activeElement=null;updateMobileViewport();
    document.activeElement={tagName:'INPUT'};visualViewport.height=500;updateMobileViewport();`);
  assert.equal(b.run('keyboardOpen'),true);
  b.run('visualViewport={height:400,scale:1.25};updateMobileViewport()');
  assert.equal(b.run('keyboardOpen'),true);
  b.run('document.activeElement=null; innerHeight=500; visualViewport={height:500,scale:1};updateMobileViewport()');
  assert.equal(b.run('keyboardOpen'),true);
  assert.equal(b.run('shellHeight'),'500px');
  // iOS can pan the page again without resizing the visual viewport.
  b.run('innerHeight=844;visualViewport.pageTop=180;visualViewport.offsetTop=0;updateMobileViewport()');
  assert.equal(b.run('shellHeight'),'680px');
  b.run('visualViewport.pageTop=235;updateMobileViewport()');
  assert.equal(b.run('shellHeight'),'735px');
  b.run('visualViewport={height:844,scale:1};updateMobileViewport()');
  assert.equal(b.run('keyboardOpen'),false);
  assert.equal(b.run('shellHeight'),'844px');
});
test('API failure shows retry instead of entering demo mode',async()=>{
  const b=browser(); await b.run('boot()');
  assert.equal(b.run('started'),0);
  assert.ok(b.run('failure').length>0);
});
test('website owners can save Our rate without changing a draft quote', async () => {
  const b = browser();
  b.run(`LIVE.on=true;LIVE.me={role:'admin'};
    var product=PRODUCTS[0], cid=CUSTOMERS[0].id;
    S.rates[cid]={[product.id]:55};
    var form={dataset:{ourRate:product.id},elements:{price:{value:'78,25'}},querySelectorAll:()=>[]};
    var calls=[];api=async(path,body)=>{calls.push([path,body.price]);return {price:78.25}};`);
  assert.match(b.run('ourRateField(product)'), /inputmode="decimal"/);
  await b.run('saveWebsiteRate(form)');
  assert.match(b.run('calls[0][0]'), /\/api\/products\/.*\/our-rate$/);
  assert.equal(b.run('calls[0][1]'), '78.25');
  assert.equal(b.run('product.price'), 78.25);
  assert.equal(b.run('rateOf(cid,product.id)'), 55);
  b.run(`LIVE.me.role='salesperson'`);
  assert.doesNotMatch(b.run('ourRateField(product)'), /<input/);
  await b.run('saveWebsiteRate(form)');
  assert.equal(b.run('calls.length'), 1);
});
test('visit checkmarks survive page reload and repeated data refresh for their account',()=>{
  const first=browser();
  first.run(`LIVE.on=true;LIVE.me={email:'rep@example.com'};LIVE.status={company:'Books'};
    var cid=CUSTOMERS[0].id;logVisit(cid);`);
  const second=browser(first.saved);
  second.run(`LIVE.on=true;LIVE.me={email:'rep@example.com'};LIVE.status={company:'Books'};
    restoreVisitLog();restoreVisitLog();`);
  assert.ok(second.run('todayVisit(CUSTOMERS[0].id)'));
  assert.equal(second.run('VISITS.filter(v=>v.logged).length'),1);
  second.run(`LIVE.me={email:'other@example.com'};restoreVisitLog()`);
  assert.equal(second.run('todayVisit(CUSTOMERS[0].id)'),undefined);
});
test('visit dates age at midnight and company storage stays isolated',()=>{
  const b=browser();
  b.run(`LIVE.on=true;LIVE.me={email:'rep@example.com'};LIVE.status={company:'Books'};
    var yesterday=new Date();yesterday.setDate(yesterday.getDate()-1);
    localStorage.setItem('saarthi.visits.v1:rep%40example.com:Books',JSON.stringify({v:1,
      rows:[{cid:CUSTOMERS[0].id,at:yesterday.toISOString()}]}));restoreVisitLog();`);
  assert.equal(b.run('VISITS.find(v=>v.logged).days'),1);
  assert.equal(b.run('todayVisit(CUSTOMERS[0].id)'),undefined);
  b.run(`LIVE.status={company:'Other Books'};restoreVisitLog()`);
  assert.equal(b.run('VISITS.filter(v=>v.logged).length'),0);
});
test('visit checkmarks and report survive invoicing and repeat refresh without double counting',()=>{
  const b=browser();
  b.run(`LIVE.on=true;LIVE.me={email:'rep@example.com'};LIVE.status={company:'Books'};
    var cid=CUSTOMERS[0].id,pid=PRODUCTS[0].id;logVisit(cid);
    var today=new Date(),ymd=today.getFullYear()+String(today.getMonth()+1).padStart(2,'0')+String(today.getDate()).padStart(2,'0');
    LIVE.orders=[{order_no:'WEB-TEST',customer:C(cid).name,at:today.toISOString(),by:LIVE.me.email,
      total:252,vchno:'4440',invoice:{guid:'v',state:'active',total:672,customer:C(cid).name}}];
    TALLY_LINES.push([ymd,cid,pid,8,672,'v',LIVE.me.email,672]);
    seedVisitsFromTally();seedVisitsFromOrders();restoreVisitLog();
    seedVisitsFromTally();seedVisitsFromOrders();restoreVisitLog();`);
  assert.ok(b.run('todayVisit(cid)'));
  assert.equal(b.run("visitStats('rep@example.com',0).shops"),1);
  assert.equal(b.run("visitStats('rep@example.com',0).orders"),1);
  assert.equal(b.run("visitStats('rep@example.com',0).rev"),672);
});
test('visit storage failure keeps the checkmark in memory and reports the failure',()=>{
  const b=browser();
  b.run(`LIVE.on=true;LIVE.me={email:'rep@example.com'};LIVE.status={company:'Books'};
    localStorage.setItem=()=>{throw new Error('full')};A.vlog({cid:CUSTOMERS[0].id});`);
  assert.ok(b.run('todayVisit(CUSTOMERS[0].id)'));
  assert.match(b.run('lastToast'),/storage failed/);
});
test('salesman visit report shows today without weekly history',()=>{
  const b=browser();
  b.run(`LIVE.on=true;LIVE.me={email:'rep@example.com',role:'salesperson',routes:['Route']};
    LIVE.status={company:'Books'};installUser(LIVE.me);ME=LIVE.me.email;
    S.user='live';`);
  assert.match(b.run('VIEWS.vis.home()'),/Today/);
  assert.doesNotMatch(b.run('VIEWS.vis.home()'),/History|Daily history|a day/);
});
test('order acknowledgements survive connection failure and flush after reload',async()=>{
  const first=browser();
  first.run(`LIVE.on=true;LIVE.me={email:'rep@example.com'};LIVE.status={company:'Books'};
    initOrderDrafts();DRAFTS.data.acks={'WEB-SAVED':'saved-request'};writeOrderDrafts();
    api=async()=>{throw new Error('offline')};`);
  await first.run('flushOrderAcknowledgements()');
  assert.equal(first.run("DRAFTS.data.acks['WEB-SAVED']"),'saved-request');
  const second=browser(first.saved);
  second.run(`LIVE.on=true;LIVE.me={email:'rep@example.com'};LIVE.status={company:'Books'};
    initOrderDrafts();var calls=[];api=async(p,b)=>{calls.push([p,b]);return {acknowledged:true}};`);
  await second.run('flushOrderAcknowledgements()');
  assert.equal(second.run("calls[0][0]"),'/api/orders/ack');
  assert.equal(second.run("calls[0][1].request_id"),'saved-request');
  assert.equal(second.run('Object.keys(DRAFTS.data.acks).length'),0);
});
test('retry of an archived order clears the draft without creating a new visit or today order',async()=>{
  const b=browser();
  b.run(`LIVE.on=true;LIVE.me={email:'rep@example.com'};LIVE.status={company:'Books'};
    initOrderDrafts();S.fp=CUSTOMERS[0].id;cartOf(S.fp)[PRODUCTS[0].id]=3;
    api=async path=>path==='/api/orders/ack'?{acknowledged:true}:{order_no:'WEB-OLD',
      customer:C(S.fp).name,total:252,lines:1,status:'invoiced',archived:true};`);
  await b.run('A.fplace()');
  assert.equal(b.run('LIVE.orders.length'),0);
  assert.equal(b.run('VISITS.filter(v=>v.logged).length'),0);
  assert.match(b.run('lastToast'),/No new order was created/);
});
test('party, ten quantities, and quoted rates survive reload for their user',()=>{
  const first=browser();
  first.run(`LIVE.on=true; LIVE.me={email:'rep@example.com'}; LIVE.status={company:'Books'};
    initOrderDrafts(); S.fp=CUSTOMERS[0].id;
    PRODUCTS.slice(0,10).forEach((p,i)=>{cartOf(S.fp)[p.id]=i+1;
      (S.rates[S.fp]??={})[p.id]=12.5+i;}); saveOrderDrafts();`);
  const second=browser(first.saved);
  second.run(`LIVE.on=true; LIVE.me={email:'rep@example.com'}; LIVE.status={company:'Books'};
    initOrderDrafts();`);
  assert.equal(second.run('Object.keys(cartOf(S.fp)).length'),10);
  assert.equal(second.run('Object.values(cartOf(S.fp))[9]'),10);
  assert.equal(second.run('rateOf(S.fp,PRODUCTS[0].id)'),12.5);
  const other=browser(first.saved);
  other.run(`LIVE.on=true; LIVE.me={email:'other@example.com'}; LIVE.status={company:'Books'};
    initOrderDrafts();`);
  assert.equal(other.run('Object.keys(S.carts).length'),0);
});
test('lost response preserves a frozen submission; reload retries the same ID and body',async()=>{
  const first=browser();
  first.run(`LIVE.on=true; LIVE.me={email:'rep@example.com'}; LIVE.status={company:'Books'};
    initOrderDrafts(); S.fp=CUSTOMERS[0].id; cartOf(S.fp)[PRODUCTS[0].id]=3;
    (S.rates[S.fp]??={})[PRODUCTS[0].id]=23; saveOrderDrafts();
    var sent=[]; api=async(p,b)=>{sent.push(JSON.parse(JSON.stringify(b)));
      throw new Error('reply lost after server commit');};`);
  await first.run('A.fplace()');
  assert.equal(first.run('cartCount(S.fp)'),1);
  assert.ok(first.run('sent[0].request_id'));
  const second=browser(first.saved);
  second.run(`LIVE.on=true; LIVE.me={email:'rep@example.com'}; LIVE.status={company:'Books'};
    initOrderDrafts(); var sent=[];
    api=async(p,b)=>{sent.push(JSON.parse(JSON.stringify(b))); return {
      order_no:'WEB-SAVED',customer:C(S.fp).name,total:69,lines:1,status:'placed'};};`);
  await second.run('A.fplace()');
  assert.equal(JSON.stringify(first.run('sent[0]')),JSON.stringify(second.run('sent[0]')));
  assert.equal(second.run('cartCount(S.fp)'),0);
  assert.equal(second.run('LIVE.orders[0].order_no'),'WEB-SAVED');
});
test('storage failure keeps the cart and prevents an unrepeatable submission',async()=>{
  const b=browser();
  b.run(`LIVE.on=true; LIVE.me={email:'rep@example.com'}; LIVE.status={company:'Books'};
    initOrderDrafts(); S.fp=CUSTOMERS[0].id; cartOf(S.fp)[PRODUCTS[0].id]=2;
    localStorage.setItem=()=>{throw new Error('storage full')};
    var calls=0; api=async()=>{calls++};`);
  await b.run('A.fplace()');
  assert.equal(b.run('calls'),0);
  assert.equal(b.run('cartCount(S.fp)'),1);
  assert.match(b.run('DRAFT_ERROR'),/cannot be saved/);
});
test('pending order cannot be edited and duplicate clicks send only once',async()=>{
  const b=browser();
  b.run(`LIVE.on=true; LIVE.me={email:'rep@example.com'}; LIVE.status={company:'Books'};
    initOrderDrafts(); S.fp=CUSTOMERS[0].id; cartOf(S.fp)[PRODUCTS[0].id]=2;
    var calls=0, release; api=()=>{calls++;return new Promise(r=>{release=r})};`);
  const first=b.run('A.fplace()');
  await b.run('A.fplace()');
  b.run(`A.fqty({cid:S.fp,p:PRODUCTS[0].id,d:'1'})`);
  assert.equal(b.run('calls'),1);
  assert.equal(b.run('cartOf(S.fp)[PRODUCTS[0].id]'),2);
  b.run(`release({order_no:'SAVED',total:100,lines:1,customer:'Shop',status:'placed'})`);
  await first;
});
test('second tab reuses the first tab submission ID',async()=>{
  const first=browser();
  const setup=`LIVE.on=true; LIVE.me={email:'rep@example.com'}; LIVE.status={company:'Books'};
    initOrderDrafts(); S.fp=CUSTOMERS[0].id; cartOf(S.fp)[PRODUCTS[0].id]=2; saveOrderDrafts();`;
  first.run(setup);
  const second=browser(first.saved); second.run(setup);
  first.run(`var sent=[];api=async(p,b)=>{sent.push(b);throw new Error('reply lost')}`);
  await first.run('A.fplace()');
  second.run(`var sent=[];api=async(p,b)=>{sent.push(b);throw new Error('reply lost')}`);
  await second.run('A.fplace()');
  assert.equal(first.run('sent[0].request_id'),second.run('sent[0].request_id'));
});
test('a restored missing item stays in the draft but cannot be newly submitted',async()=>{
  const first=browser();
  first.run(`LIVE.on=true;LIVE.me={email:'rep@example.com'};LIVE.status={company:'Books'};
    initOrderDrafts();S.fp=CUSTOMERS[0].id;cartOf(S.fp)[PRODUCTS[0].id]=2;saveOrderDrafts();`);
  const second=browser(first.saved);
  second.run(`var missing=PRODUCTS.shift().id;
    LIVE.on=true;LIVE.me={email:'rep@example.com'};LIVE.status={company:'Books'};initOrderDrafts();
    var calls=0;api=async()=>{calls++};`);
  assert.equal(second.run('cartOf(S.fp)[missing]'),2);
  assert.equal(second.run('P(missing)._draftUnavailable'),true);
  await second.run('A.fplace()');
  assert.equal(second.run('calls'),0);
});
test('pending retry survives a party disappearing from the current route list',async()=>{
  const first=browser();
  first.run(`LIVE.on=true;LIVE.me={email:'rep@example.com'};LIVE.status={company:'Books'};
    initOrderDrafts();S.fp=CUSTOMERS[0].id;cartOf(S.fp)[PRODUCTS[0].id]=2;saveOrderDrafts();
    api=async()=>{throw new Error('reply lost')};`);
  await first.run('A.fplace()');
  const second=browser(first.saved);
  second.run(`CUSTOMERS.shift();LIVE.on=true;LIVE.me={email:'rep@example.com'};
    LIVE.status={company:'Books'};initOrderDrafts();var calls=0;
    api=async path=>{if(path==='/api/orders')calls++;return {order_no:'SAVED',total:100,lines:1,customer:'Shop',status:'placed'}}`);
  await second.run('A.fplace()');
  assert.equal(second.run('calls'),1);
  assert.equal(second.run('LIVE.orders[0].order_no'),'SAVED');
});
test('drafts are also isolated by company and corrupt storage is not overwritten',()=>{
  const b=browser();
  b.run(`LIVE.on=true;LIVE.me={email:'rep@example.com',name:'Rep',role:'salesperson',routes:['Route']};LIVE.status={company:'Books'};
    initOrderDrafts();S.fp=CUSTOMERS[0].id;cartOf(S.fp)[PRODUCTS[0].id]=2;saveOrderDrafts();`);
  const other=browser(b.saved);
  other.run(`LIVE.on=true;LIVE.me={email:'rep@example.com'};LIVE.status={company:'Other'};initOrderDrafts()`);
  assert.equal(other.run('Object.keys(S.carts).length'),0);
  const key=b.run('DRAFTS.key');b.saved.set(key,'broken JSON');
  const corrupt=browser(b.saved);
  corrupt.run(`LIVE.on=true;LIVE.me={email:'rep@example.com'};LIVE.status={company:'Books'};
    initOrderDrafts();saveOrderDrafts()`);
  assert.equal(b.saved.get(key),'broken JSON');
  assert.ok(corrupt.run('DRAFT_ERROR'));
});
test('quantity and quoted price input persist before blur or page exit',()=>{
  const b=browser();
  b.run(`LIVE.on=true;LIVE.me={email:'rep@example.com'};LIVE.status={company:'Books'};
    initOrderDrafts();A.fparty({cid:CUSTOMERS[0].id});
    A.fdraftqty({cid:S.fp,p:PRODUCTS[0].id},{value:'17'});
    A.fdraftrate({cid:S.fp,p:PRODUCTS[0].id},{value:'35.75'});`);
  const second=browser(b.saved);
  second.run(`LIVE.on=true;LIVE.me={email:'rep@example.com'};LIVE.status={company:'Books'};initOrderDrafts()`);
  assert.equal(second.run('cartOf(S.fp)[PRODUCTS[0].id]'),17);
  assert.equal(second.run('rateOf(S.fp,PRODUCTS[0].id)'),35.75);
});
test('invalid API startup response shows retry and explicit demo stays intentional',async()=>{
  const b=browser();
  b.context.fetch=async()=>({ok:true,json:async()=>{throw new Error('HTML instead of JSON')}});
  await b.run('boot()');
  assert.equal(b.run('started'),0);
  assert.ok(b.run('failure'));
  b.context.location.search='?demo=1';
  await b.run('boot()');
  assert.equal(b.run('started'),1);
});
test('an unavailable database gives the loading screen a retryable status',async()=>{
  const b=browser();
  b.run(`api=async path=>{
    if(path==='/api/config')return {dev_login:false};
    throw Object.assign(new Error('Service Unavailable'),{status:503});
  };`);
  await b.run('boot()');
  assert.equal(b.run('started'),0);
  assert.match(b.run('failure'),/temporarily unavailable.*retry/i);
});
test('validation rejection retains editable items; changed prices are flagged on reload',async()=>{
  const b=browser();
  b.run(`LIVE.on=true;LIVE.me={email:'rep@example.com'};LIVE.status={company:'Books'};
    initOrderDrafts();S.fp=CUSTOMERS[0].id;cartOf(S.fp)[PRODUCTS[0].id]=2;
    (S.rates[S.fp]??={})[PRODUCTS[0].id]=5;saveOrderDrafts();
    api=async()=>{throw Object.assign(new Error('Invalid item'),{status:422})};`);
  await b.run('A.fplace()');
  assert.equal(b.run('cartCount(S.fp)'),1);
  assert.equal(b.run('pendingOrder(S.fp)'),undefined);
  const second=browser(b.saved);
  second.run(`LIVE.on=true;LIVE.me={email:'rep@example.com'};LIVE.status={company:'Books'};initOrderDrafts()`);
  assert.equal(second.run('rateOf(S.fp,PRODUCTS[0].id)'),5);
  assert.match(second.run('DRAFTS.warnings[S.fp].join(" ")'),/quoted price is kept/);
});
test('full live boot opens the parties list and keeps the saved cart available to resume',async()=>{
  const first=browser();
  first.run(`LIVE.on=true;LIVE.me={email:'rep@example.com'};LIVE.status={company:'Books'};
    initOrderDrafts();S.fp=CUSTOMERS[0].id;cartOf(S.fp)[PRODUCTS[0].id]=7;saveOrderDrafts();`);
  const second=browser(first.saved);
  second.run(`var user={email:'rep@example.com',name:'Rep',role:'salesperson',routes:['Route']};
    var data=copyDraft({me:user,status:{company:'Books'},products:PRODUCTS,
      customers:CUSTOMERS,cats:CATS,payments:{},daily:{},lines:[]});
    api=async path=>({'/api/config':{dev_login:false,google_client_id:''},
      '/api/me':user,'/api/data':data,'/api/orders':[]}[path]);
    startApp=uid=>{S.user=uid;S.fp=null;S.view='home'};`);
  await second.run('boot()');
  assert.equal(second.run('S.fp'),null);
  assert.equal(second.run('S.mod'),'orders');
  assert.equal(second.run('S.view'),'home');
  assert.match(second.run('VIEWS.sales.home()'),/Resume draft/);
  const cid=first.run('S.fp');
  assert.equal(second.run(`cartOf('${cid}')[PRODUCTS[0].id]`),7);
  second.run(`A.fparty({cid:'${cid}'})`);
  assert.equal(second.run('S.view'),'menu');
  assert.equal(second.run('cartOf(S.fp)[PRODUCTS[0].id]'),7);
});
test('salesman cannot see or invoke new-party creation',async()=>{
  const b=browser();
  b.run(`LIVE.on=true;LIVE.me={email:'rep@example.com',role:'salesperson',routes:['Route']};
    S.fpq='Novel Outlet Never Seen';var calls=0;api=async()=>{calls++};`);
  assert.doesNotMatch(b.run('partyRows()'),/data-a="fnew"/);
  b.run('A.fnew()');
  assert.notEqual(b.run('S.view'),'fnewparty');
  b.run(`S.newParty={name:'Novel Outlet',route:'Route'}`);
  await b.run('A.npsave()');
  assert.equal(b.run('calls'),0);
});
test('admin retains new-party creation and route choices',()=>{
  const b=browser();
  b.run(`LIVE.on=true;LIVE.me={email:'office@example.com',role:'admin',routes:null};
    LIVE.routes=[{route:'Route A',shops:3},{route:'Route B',shops:2}];
    S.fpq='Novel Outlet Never Seen';`);
  assert.match(b.run('partyRows()'),/data-a="fnew"/);
  b.run('A.fnew()');
  assert.equal(b.run('S.view'),'fnewparty');
  assert.match(b.run('VIEWS.sales.fnewparty()'),/Route A/);
  assert.match(b.run('VIEWS.sales.fnewparty()'),/Route B/);
});
test('current Tally invoice replaces bill quantities, not the original submitted order',()=>{
  const b=browser();
  b.run(`var changedOrder={order_no:'WEB-TEST',at:'2026-10-30T01:00:00Z',
    customer:CUSTOMERS[0].name,total:252,vchno:'4440',lines:[{item:'Test item',qty:3,rate:84,amount:252,unit:'PCS'}],
    invoice:{guid:'v',date:'20261030',number:'4440',state:'active',customer:CUSTOMERS[0].name,total:672,
      lines:[{item:'Test item',qty:8,rate:84,amount:672,unit:'PCS'}]}};`);
  assert.equal(b.run('orderAmount(changedOrder)'),672);
  assert.equal(b.run('changedOrder.total'),252);
  assert.match(b.run('liveBillHTML(changedOrder)'),/>8 PCS</);
  assert.doesNotMatch(b.run('liveBillHTML(changedOrder)'),/252|Original order|ordered/i);
  assert.doesNotMatch(b.run('orderAmountHTML(changedOrder)'),/252/);
  assert.match(b.run('liveBillHTML(changedOrder)'),/₹672/);
  b.run(`changedOrder.invoice.state='cancelled'`);
  assert.match(b.run('liveBillHTML(changedOrder)'),/Invoice cancelled/);
  assert.doesNotMatch(b.run('liveBillHTML(changedOrder)'),/>BILL</);
  assert.doesNotMatch(b.run('liveBillHTML(changedOrder)'),/252|3 PCS/);
  assert.equal(b.run('orderAmount(changedOrder)'),null);
});
test('billed orders never fall back to submitted values while pending orders retain them',()=>{
  const b=browser();
  b.run(`var order={customer:'Test Party',at:'2026-10-30',order_no:'WEB-TEST',
    total:252,lines:[{item:'Test item',qty:3,rate:84,amount:252,unit:'PCS'}]};`);
  assert.equal(b.run('orderAmount(order)'),252);
  assert.match(b.run('liveBillHTML(order)'),/₹252/);
  for (const state of ['awaiting','restricted','deleted','optional','unavailable']) {
    b.run(`order.vchno='4440';order.invoice=null;order.invoice_restricted=false`);
    if (state==='restricted') b.run('order.invoice_restricted=true');
    else if (state!=='awaiting') b.run(`order.invoice={state:${JSON.stringify(state)}}`);
    assert.equal(b.run('orderAmount(order)'),null);
    assert.doesNotMatch(b.run('liveBillHTML(order)'),/252|3 PCS|>BILL</);
    assert.equal(b.run('order.total'),252);
    assert.equal(b.run('order.lines[0].qty'),3);
  }
});
test('placed and invoiced orders do not imply that a Tally edit is expected',()=>{
  const b=browser();
  b.run(`var order={customer:'Test Party',at:'2026-10-30',order_no:'WEB-TEST',status:'placed',
    total:80,lines:[{item:'Harpic Blue',qty:1,rate:80,amount:80,unit:'PCS'}]};`);
  assert.match(b.run('liveStatus(order)'),/Order placed/);
  assert.match(b.run('liveBillHTML(order)'),/₹80/);
  assert.doesNotMatch(b.run('liveBillHTML(order)'),/awaiting sync|Details are loading/);
  b.run(`order.vchno='4440';order.invoice={state:'active',number:'4440',date:'20261030',
    customer:order.customer,total:80,lines:order.lines}`);
  assert.match(b.run('liveStatus(order)'),/Invoiced/);
  assert.doesNotMatch(b.run('liveBillHTML(order)'),/awaiting|loading/i);
});
test('successful discounted submission immediately displays its lines without waiting for refresh',async()=>{
  const b=browser();
  b.run(`LIVE.on=true;LIVE.me={email:'rep@example.com'};LIVE.status={company:'Books'};
    initOrderDrafts();S.fp=CUSTOMERS[0].id;cartOf(S.fp)[PRODUCTS[0].id]=1;
    (S.rates[S.fp]??={})[PRODUCTS[0].id]=80;
    api=async()=>({order_no:'WEB-DISCOUNT',customer:C(S.fp).name,total:80,lines:1,status:'placed'});`);
  await b.run('A.fplace()');
  assert.equal(b.run('LIVE.orders[0].lines[0].rate'),80);
  assert.equal(b.run('LIVE.orders[0].lines[0].qty'),1);
  assert.match(b.run('liveBillHTML(LIVE.orders[0])'),/₹80/);
  assert.doesNotMatch(b.run('liveBillHTML(LIVE.orders[0])'),/awaiting sync|Details are loading/);
});
test('our rate precedes party history and refresh preserves existing draft quotes',async()=>{
  const b=browser();
  b.run(`LIVE.on=true;LIVE.me={email:'rep@example.com',name:'Rep',role:'salesperson',routes:['Route']};
    LIVE.status={company:'Books'};initOrderDrafts();
    var cid=CUSTOMERS[0].id,other=CUSTOMERS[1].id,pid=PRODUCTS[0].id;
    PRODUCTS[0].price=84;CUSTOMERS[0].buys=[{p:pid,rate:84}];CUSTOMERS[1].buys=[{p:pid,rate:84}];
    S.fp=cid;cartOf(cid)[pid]=2;saveOrderDrafts();
    var data=copyDraft({me:LIVE.me,status:LIVE.status,products:PRODUCTS,customers:CUSTOMERS,
      cats:CATS,payments:{},daily:{},lines:[]});data.customers[0].buys[0].rate=80;
    api=async path=>path==='/api/data'?data:[];`);
  assert.equal(await b.run('refreshLiveData()'),true);
  assert.equal(b.run('rateOf(cid,pid)'),84); // Existing quote is protected.
  b.run('finishOrderDraft(cid)');
  assert.equal(b.run('rateOf(cid,pid)'),84);
  assert.equal(b.run('rateOf(other,pid)'),84);
  b.run('P(pid).price=null');
  assert.equal(b.run('rateOf(cid,pid)'),null); // Party history is shown separately.
});
test('live data rebuilds dashboard totals rather than retaining demo or old invoice lines',()=>{
  const b=browser();
  b.run(`var data=copyDraft({me:{email:'rep@example.com'},status:{company:'Books'},products:PRODUCTS,
    customers:CUSTOMERS,cats:CATS,payments:{},daily:{},lines:[]});
    var cid=CUSTOMERS[0].id,pid=PRODUCTS[0].id;
    data.lines=[['20261030',cid,pid,3,252,'v','rep@example.com']];applyData(data);`);
  assert.equal(b.run('tally(SALES).value'),252);
  b.run(`data.lines=[['20261030',cid,pid,8,672,'v','rep@example.com']];applyData(data)`);
  assert.equal(b.run('tally(SALES).value'),672);
  assert.equal(b.run('tally(SALES).orders'),1);
  assert.equal(b.run('SALES.length'),1);
});
test('foreground refresh updates invoice/dashboard data without losing an unfinished draft',async()=>{
  const b=browser();
  b.run(`LIVE.on=true;LIVE.me={email:'rep@example.com',name:'Rep',role:'salesperson',routes:['Route']};LIVE.status={company:'Books'};
    initOrderDrafts();S.fp=CUSTOMERS[0].id;cartOf(S.fp)[PRODUCTS[0].id]=7;
    (S.rates[S.fp]??={})[PRODUCTS[0].id]=35.75;saveOrderDrafts();
    var data=copyDraft({me:LIVE.me,status:LIVE.status,products:PRODUCTS,customers:CUSTOMERS,
      cats:CATS,payments:{},daily:{},lines:[['20261030',CUSTOMERS[0].id,PRODUCTS[0].id,8,672,'v',null]]});
    api=async path=>path==='/api/data'?data:[];`);
  assert.equal(await b.run('refreshLiveData()'),true);
  assert.equal(b.run('cartOf(S.fp)[PRODUCTS[0].id]'),7);
  assert.equal(b.run('rateOf(S.fp,PRODUCTS[0].id)'),35.75);
  assert.equal(b.run('tally(SALES).value'),672);
});
test('party catalogue and checkout suggestions put available stock first within each group',()=>{
  const b=browser();
  b.run(`S.fp=CUSTOMERS[0].id;
    PRODUCTS.splice(0,PRODUCTS.length,
      {id:'b0',name:'Bought unavailable',stock:0,price:100},
      {id:'b1',name:'Bought available',stock:2,price:20},
      {id:'p0',name:'Pitch unavailable',stock:null,price:50},
      {id:'p1',name:'Pitch available',stock:1,price:50});
    var customer=C(S.fp);
    CUSTOMERS.splice(1);
    customer.buys=[{p:'b0',freq:[5,5]},{p:'b1',freq:[1,5]}];
    customer.cross=[{p:'p0',qty:1,type:'cross',why:'Strong match'}];
    invoicesFor=()=>[{days:1,lines:[['b0',10],['b1',1]]}];`);
  assert.equal(b.run('JSON.stringify([...splitItems().mine,...splitItems().rest].map(p=>p.id))'),
    '["b1","b0","p1","p0"]');
  assert.equal(b.run('JSON.stringify(topMissing(customer).map(p=>p.p))'),'["b1","b0"]');
  assert.equal(b.run('JSON.stringify(pitchItems(customer).map(p=>p.p))'),'["p1","p0"]');
});
test('phone product search ranks prefixes above a previously purchased interior match',()=>{
 const b=browser();b.run(`S.fp=CUSTOMERS[0].id;S.fq='har';PRODUCTS.splice(0,PRODUCTS.length,{id:'c',name:'Colgate Charcoal',stock:99},{id:'h',name:'Harpic Blue',stock:0});C(S.fp).buys=[{p:'c',freq:[10,10]}];`);
 assert.deepEqual(JSON.parse(b.run('JSON.stringify(splitItems().rest.map(p=>p.id))')),['h','c']);
});
test('the open phone catalogue shows newly synced products while search stays focused', async () => {
  const b = browser();
  b.run(`LIVE.on=true; LIVE.me={email:'rep@example.com',role:'salesperson'}; LIVE.status={company:'Books'};
    initOrderDrafts(); S.fp=CUSTOMERS[0].id; S.fq='new tally';
    var search={dataset:{in:'fq'},matches:()=>true}, grid={innerHTML:''};
    document.activeElement=search;
    document.querySelector=selector=>selector==='#fgrid'?grid:null;
    render=()=>{throw new Error('Full render would replace the focused search')};
    var data=copyDraft({me:LIVE.me,status:LIVE.status,products:PRODUCTS,customers:CUSTOMERS,
      cats:CATS,payments:{},daily:{},lines:[]});
    data.products.push({...data.products[0],id:'tally-new',name:'New Tally Soap'});
    api=async path=>path==='/api/data'?data:[];`);
  assert.equal(await b.run('refreshLiveData()'), true);
  assert.match(b.run('grid.innerHTML'), /New Tally Soap/);
  assert.equal(b.run('document.activeElement===search'), true);
});
test('bringing the app to the foreground refreshes the catalogue after an in-flight orders poll', async () => {
  const b = browser();
  b.run(`LIVE.on=true; LIVE.me={email:'rep@example.com',role:'salesperson'}; LIVE.status={company:'Books'};
    initOrderDrafts();
    var data=copyDraft({me:LIVE.me,status:LIVE.status,products:PRODUCTS,customers:CUSTOMERS,
      cats:CATS,payments:{},daily:{},lines:[]});
    data.products.push({...data.products[0],id:'tally-new',name:'New Tally Soap'});
    var foreground, finishOrders, reads=0;
    var TestDate=Date; Date=class extends TestDate { constructor(...args) { super(...(args.length?args:['2026-11-14T05:30:00Z'])); } };
    window.addEventListener=(event, handler)=>{ if(event==='focus') foreground=handler; };
    api=async path=>path==='/api/data' ? (reads++,data)
      : !finishOrders ? new Promise(resolve=>{finishOrders=resolve}) : [];
    startLiveRefresh(); refreshLiveData(true);`);
  const refresh = b.run('var resumed=foreground(); finishOrders([]); resumed');
  assert.equal(await refresh, true);
  assert.equal(b.run('reads'), 1);
  assert.equal(b.run("P('tally-new').name"), 'New Tally Soap');
});
test('auto refresh stops at 7 pm IST and resumes at 9 am, while explicit refresh still works', async () => {
  const b = browser();
  b.run(`var OriginalDate=Date, now='2026-11-14T13:29:59Z';
    Date=class extends OriginalDate { constructor(...args) { super(...(args.length?args:[now])); } };
    var hooks={}, tick, calls=[];
    window.addEventListener=(event, callback)=>{hooks[event]=callback};
    setInterval=callback=>{tick=callback};
    LIVE.on=true;S.view='myorders';
    loadLiveData=async ordersOnly=>{calls.push(ordersOnly);return true};
    startLiveRefresh();`);
  assert.equal(await b.run('hooks.focus()'), true);
  b.run(`now='2026-11-14T13:30:00Z'; tick(); tick();
    hooks.online(); hooks.focus(); hooks.pageshow({persisted:true});`);
  for (const fire of b.events.visibilitychange) fire();
  assert.equal(b.run('calls.length'), 1);
  assert.equal(await b.run('refreshLiveData()'), true);
  assert.equal(await b.run(`now='2026-11-15T03:29:59Z'; hooks.focus()`), false);
  assert.equal(await b.run(`now='2026-11-15T03:30:00Z'; hooks.focus()`), true);
  assert.equal(b.run('calls.length'), 3);
});
test('the owner sync control confirms a pause with a follow-up status check', async () => {
  const b = browser();
  b.run(`LIVE.on=true; LIVE.me={role:'admin'};
    const calls=[], status={}, toggle={dataset:{}}, check={};
    const panel={isConnected:true, querySelector(selector) {
      return selector.includes('toggle') ? toggle : selector.includes('status]') ? status : check;
    }};
    const button={closest:()=>panel};
    api=async(path,body)=>{
      calls.push({path,body});
      return {paused:true,status:body?'Pausing':'Paused'};
    };
    setTimeout=callback=>{if(status.textContent!=='Pausing')throw Error('Missing pending status');callback();};`);
  await b.run("A.agentsynctoggle({paused:'true'},button)");
  assert.equal(b.run('status.textContent'), 'Paused');
  assert.equal(b.run('toggle.textContent'), 'Resume laptop sync');
  assert.equal(b.run('JSON.stringify(calls)'), '[{"path":"/api/agent-sync","body":{"paused":true}},{"path":"/api/agent-sync"}]');
});

test('phone cards use the published authenticated image URL and reject unrelated image locations',()=>{
 const b=browser();b.run(`PRODUCTS.push({id:'photo',name:'Tally Soap',image:'/api/product-image/'+'a'.repeat(64)});`);
 assert.match(b.run("art('photo')"),/<img src="\/api\/product-image\/[a-f0-9]{64}"/);
 b.run(`P('photo').image='https://unrelated.example/image.png';`);
 assert.doesNotMatch(b.run("art('photo')"),/unrelated\.example/);
});

test('only the owner party catalogue hides purchase rates behind the info button',()=>{
 const b=browser();b.run(`LIVE.on=true;LIVE.me={role:'admin'};S.fp=CUSTOMERS[0].id;
   PRODUCTS[0].purchaseRate=50;PRODUCTS[0].purchaseUnit='PCS';PRODUCTS[0].purchaseDate='20261101';
   CUSTOMERS[0].buys=[{p:PRODUCTS[0].id, rate:63, freq:[2,2]}];`);
 const html = b.run('VIEWS.sales.menu()');
 assert.match(html, /class="itop-actions"><div class="purchase-rate-info">\s*<button type="button" data-a="purchaseinfo" aria-expanded="false"/);
 assert.match(html, /class="purchase-rate-popover" hidden>Purchase rate: <b>₹50 \/ PCS<\/b>/);
 assert.match(html, /Last price ₹63/);
 assert.doesNotMatch(html, /20261101|Last purchase invoice/);
 assert.match(b.run('purchaseCostRows()'), /Purchase rate: <b>₹50 \/ PCS<\/b>/);
 assert.doesNotMatch(b.run('itemCard(PRODUCTS[0])'), /data-a="purchaseinfo"/);
 assert.doesNotMatch(b.run('purchaseCostRows()'),/type="number"|Add product|Edit rate/);
 b.run(`LIVE.me.role='salesperson'`);
 assert.match(b.run('purchaseCostRows()'),/Owner access only/);
 assert.doesNotMatch(b.run('VIEWS.sales.menu()'),/purchaseinfo|Purchase rate/);
});

test('a purchase rate closes after two seconds and only one card is open',()=>{
 const b = browser();
 b.run(`const timers = new Map(); let nextTimer = 0;
   setTimeout = (callback, delay) => { timers.set(++nextTimer, {callback, delay}); return nextTimer; };
   clearTimeout = id => timers.delete(id);
   function infoButton() {
     return {dataset:{a:'purchaseinfo'}, nextElementSibling:{hidden:true}, expanded:'false',
       closest() { return this; },
       getAttribute() { return this.expanded; },
       setAttribute(name, value) { this.expanded = value; }};
   }
   const first = infoButton(), second = infoButton();`);
 const click = b.events.click[0];
 click({target:b.run('first'), preventDefault(){}});
 assert.equal(b.run('first.nextElementSibling.hidden'), false);
 assert.equal(b.run('timers.get(1).delay'), 2000);
 click({target:b.run('second'), preventDefault(){}});
 assert.equal(b.run('first.nextElementSibling.hidden'), true);
 assert.equal(b.run('timers.has(1)'), false);
 assert.equal(b.run('second.expanded'), 'true');
 assert.equal(b.run('timers.get(2).delay'), 2000);
 b.run('timers.get(2).callback()');
 assert.equal(b.run('second.nextElementSibling.hidden'), true);
 assert.equal(b.run('second.expanded'), 'false');
});

test('checkout shows one retry error and preserves the dated submission until confirmed',async()=>{
  const b=browser();
  b.run(`LIVE.on=true; LIVE.me={email:'rep@example.com',role:'salesperson'}; LIVE.status={company:'Books'};
    initOrderDrafts(); S.fp=CUSTOMERS[0].id; cartOf(S.fp)[PRODUCTS[0].id]=1;
    A.invoiceDate({cid:S.fp},{value:'2099-11-15'});
    var requests=[]; api=async(path,body)=>{requests.push(body); throw Object.assign(new Error('Database temporarily unavailable.'), {status:503});};`);
  await b.run('A.fplace()');
  const review = b.run('VIEWS.sales.review()');
  assert.match(review, /Database temporarily unavailable/);
  assert.ok(review.indexOf('Check / Retry</button>') < review.indexOf('Invoice date'));
  await b.run('A.fplace()');
  assert.equal(b.run('lastToast'), '');
  assert.equal(b.run('VIEWS.sales.review().split("Database temporarily unavailable").length'), 2);
  assert.equal(b.run('requests[0].invoice_date'),'2099-11-15');
  assert.equal(b.run('JSON.stringify(requests[0])'),b.run('JSON.stringify(requests[1])'));
  b.run(`api=async(path,body)=>{
    if(path==='/api/orders/ack') return {acknowledged:true};
    requests.push(body);
    return {order_no:'WEB-RECOVERED',total:25,lines:1,customer:C(S.fp).name,status:'placed'};
  };`);
  await b.run('A.fplace()');
  assert.equal(b.run('JSON.stringify(requests[0])'),b.run('JSON.stringify(requests[2])'));
  assert.equal(b.run('Boolean(pendingOrder(S.fp))'),false);
  assert.equal(b.run("LIVE.orders.filter(order=>order.order_no==='WEB-RECOVERED').length"),1);
});

test('owner Desk uses the selected day and identifies direct Tally sales',()=>{
  const b=browser();
  b.run(`LIVE.on=true; LIVE.me={role:'admin'}; LIVE.orders=[
    {order_no:'WEB-TODAY',desk_date:indiaDay(),at:new Date().toISOString(),by:'rep@example.com',customer:'Today customer',source:'saarthi',total:100},
    {order_no:'TALLY-TODAY',desk_date:indiaDay(),at:new Date().toISOString(),by:'Tally',customer:'Tally customer',source:'tally',total:200,vchno:'555'},
    {order_no:'WEB-PAST',desk_date:'2020-01-01',customer:'Earlier customer',source:'saarthi',total:100}];`);
  assert.match(b.run('VIEWS.desk.home()'),/Today customer/);
  assert.match(b.run('VIEWS.desk.home()'),/Tally customer/);
  assert.doesNotMatch(b.run('VIEWS.desk.home()'),/Earlier customer/);
  b.run("A.deskrep(null,{value:'rep@example.com'})");
  assert.doesNotMatch(b.run('VIEWS.desk.home()'),/Tally customer/);
});

test('a Tally voucher dated tomorrow belongs in tomorrow’s Desk even when ordered today',()=>{
  const b=browser();
  b.run(`LIVE.on=true; LIVE.me={role:'admin'};
    var today = indiaDay(), tomorrow = indiaDay(new Date(Date.parse(today+'T12:00:00+05:30')+864e5));
    LIVE.orders=[{order_no:'WEB-FUTURE',at:today+'T12:00:00+05:30',desk_date:today,
      source:'saarthi',customer:'Future voucher',invoice:{date:tomorrow.replaceAll('-',''),state:'active',total:100}}];`);
  assert.doesNotMatch(b.run('VIEWS.desk.home()'),/Future voucher/);
  b.run("A.deskdate(null,{value:tomorrow})");
  const html=b.run('VIEWS.desk.home()');
  assert.match(html,/Future voucher/);
  assert.match(html,/Created: /);
  assert.match(html,/Invoice date: /);
});

const {test}=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs');const vm=require('node:vm');const path=require('node:path');
function app(){
  const elements=new Map(),events={},posts=[],files=[],gets=[];
  const el=s=>{if(!elements.has(s))elements.set(s,{innerHTML:'',value:'',disabled:false});return elements.get(s);};
  const customer=i=>({name:i===79?'=BAD()':'Customer '+i,route:'Dewas',gross:i,credits:0,net:i,invoices:1,average:i,prior_gross:0,change:null,balance:0,balance_asof:null,last_purchase:'20261031'});
  const data={start:'20261002',end:'20261031',prior_start:'20260902',prior_end:'20261001',company:'Demo Books',asof:'2026-10-31',routes:['Dewas'],reps:['rep@example.test'],metrics:{net:1000,gross:1000,credits:0,invoices:3,buyers:2,repeat_buyers:1,first_observed_buyers:0,top5_share:100},previous:{net:100},growth:900,baseline_covered:true,daily:[],target:null,target_progress:null,team:[],territories:[],customers:Array.from({length:80},(_,i)=>customer(i)),products:[],waiting:[],pipeline:[],pipeline_counts:{},pipeline_values:{},unattributed_gross:0,unattributed_credits:0,warnings:['Current active vouchers only']};
  data.party_options=['Customer 65'];data.monthly=[{label:'Jan',net:700,gross:800,credits:100,invoices:4,buyers:2,previous_net:500,change:40}];data.yearly=[{label:'2025',net:600,gross:600,credits:0},{label:'2026',net:1000,gross:1000,credits:0}];data.years=[2026,2025];data.party_products=[{party:'Customer 65',name:'Harpic',unit:'PCS',quantity:5,last_price:80,net:400,invoices:2}];
  const context={window:{},S:{view:'analysis',generation:0},URLSearchParams,Intl,Date,setTimeout:()=>0,Blob:class{constructor(parts){this.parts=parts}},URL:{createObjectURL:b=>{files.push(b.parts.join(''));return 'blob:demo'},revokeObjectURL(){}},$:el,esc:s=>String(s??'').replaceAll('<','&lt;'),money:n=>n==null?'—':'₹'+n,date:s=>s||'—',when:s=>s||'—',head:(t,b,actions)=>t+b+actions,panel:(t,b)=>t+b,table:(headers,rows)=>headers.join('|')+rows.join(''),cells:v=>v.join('|'),notify(){},api:async(url,body)=>{if(body)posts.push({url,body});else gets.push(url);return body?{saved:true}:data;},document:{addEventListener:(n,fn)=>events[n]=fn,createElement:()=>({click(){}})}};
  vm.createContext(context);vm.runInContext(fs.readFileSync(path.join(__dirname,'../desktop/list-tools.js'),'utf8'),context);vm.runInContext(fs.readFileSync(path.join(__dirname,'../desktop/analysis.js'),'utf8'),context);
  const click=async(id,dataset={})=>events.click({target:{closest:()=>({id,dataset})}});
  return {context,el,events,posts,files,gets,data,click};
}
test('analysis searches every customer beyond the first page and CSV exports all rows safely',async()=>{
  const h=app();await h.context.window.salesAnalysis.load();
  assert.match(h.el('#main').innerHTML,/Top 10 parties/);
  assert.match(h.el('#main').innerHTML,/Export all matching rows \(CSV\)/);
  assert.doesNotMatch(h.el('#analysis-rows').innerHTML,/Customer 65/);
  h.events.input({target:{id:'analysis-search',value:'Customer 65'}});
  assert.match(h.el('#analysis-rows').innerHTML,/Customer 65/);
  h.events.input({target:{id:'analysis-search',value:''}});await h.click('analysis-export');
  assert.match(h.files[0],/"Saarthi local analysis","Party sales · selected duration"/);
  assert.doesNotMatch(h.files[0],/Top 10 parties/);
  assert.match(h.files[0],/Customer 65/);assert.match(h.files[0],/"'=BAD\(\)"/);assert.match(h.files[0],/20261002/);
});
test('automatic date and party filters keep the simplified navigation and export party products',async()=>{
  const h=app();await h.context.window.salesAnalysis.load();
  assert.doesNotMatch(h.el('#main').innerHTML,/Compare with|Apply filters|data-analysis-tab="calendar"|data-analysis-tab="products"|data-analysis-tab="billing"|Net sales target|Average invoice/);
  assert.match(h.el('#main').innerHTML,/Gross sales|Month-on-month growth/);
  h.el('#analysis-mode').value='year';h.el('#analysis-year').value='2025';h.el('#analysis-party').value='Customer 65';
  await h.events.change({target:{id:'analysis-year'}});
  assert.match(h.gets.at(-1),/year=2025/);assert.match(h.gets.at(-1),/party=Customer\+65/);
  await h.click('',{analysisTab:'purchases'});
  assert.match(h.el('#analysis-rows').innerHTML,/Harpic/);assert.match(h.el('#analysis-rows').innerHTML,/₹80/);
  await h.click('analysis-export');assert.match(h.files.at(-1),/Harpic/);assert.match(h.files.at(-1),/Customer 65/);
});
test('clicking a customer opens its party product breakdown',async()=>{
  const h=app();await h.context.window.salesAnalysis.load();
  await h.click('',{analysisParty:'Customer 65'});
  assert.match(h.gets.at(-1),/party=Customer\+65/);
  assert.match(h.el('#main').innerHTML,/Recent invoices/);assert.match(h.el('#analysis-rows').innerHTML,/Harpic/);
});
test('sales team shows visits and orders and supports an individual rep filter without targets or a billing funnel',async()=>{
  const h=app();h.data.team=[{name:'rep@example.test',gross:1000,visits:7,submitted:4,billed_orders:3,buyers:2}];h.data.visit_sync_at='2026-10-31';
  await h.context.window.salesAnalysis.load();await h.click('',{analysisTab:'team'});
  assert.match(h.el('#main').innerHTML,/Visits|Orders taken/);assert.doesNotMatch(h.el('#main').innerHTML,/Save target|weighted forecast|Billing completion/);
  assert.match(h.el('#analysis-rows').innerHTML,/data-analysis-rep="rep@example.test"/);
  await h.click('',{analysisRep:'rep@example.test'});assert.match(h.gets.at(-1),/rep=rep%40example.test/);
});
test('a failed filter request leaves CSV tied to the last successful report',async()=>{
  const h=app();await h.context.window.salesAnalysis.load();
  for(const [id,value] of Object.entries({'analysis-from':'2026-10-01','analysis-to':'2026-10-31','analysis-route':'Wrong route','analysis-rep':'','analysis-comparison':'year'}))h.el('#'+id).value=value;
  h.context.api=async()=>{throw new Error('Archive temporarily busy')};
  await h.events.submit({target:{id:'analysis-filters',querySelector:()=>({disabled:false})},preventDefault(){}});
  await h.click('analysis-export');assert.match(h.files[0],/All routes/);assert.doesNotMatch(h.files[0],/Wrong route/);
});

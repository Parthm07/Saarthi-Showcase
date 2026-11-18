const {test}=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs');const path=require('node:path');const vm=require('node:vm');
function dashboard(managed){const main={innerHTML:''},calls=[],listeners={};const context={window:{},S:{view:'updates',generation:0},$:selector=>selector==='#main'?main:{value:'https://releases.example.test/latest.json',checked:true},esc:String,head:()=>'',panel:(title,body)=>title+body,badge:String,notify(){},api:async(url,body)=>{calls.push({url,body});if(url==='/api/updates')return {version:'0.6.0',managed_install:managed,state:'available',available_version:'0.6.1',message:'Ready',feed:''};if(url==='/api/agent')return {managed,billing_enabled:false};return {message:'Saved'};},document:{addEventListener:(event,fn)=>listeners[event]=fn}};vm.createContext(context);vm.runInContext(fs.readFileSync(path.join(__dirname,'../desktop/updates.js'),'utf8'),context);return {context,main,calls,listeners};}
test('source-mode update screen explains archive creation and never starts an update on load',async()=>{const h=dashboard(false);await h.context.window.saarthiUpdates.load();assert.match(h.main.innerHTML,/complete managed setup/);assert.match(h.main.innerHTML,/not Tally's own company folder/);assert.match(h.main.innerHTML,/id="updates-local" disabled/);assert.ok(h.calls.every(c=>c.body===undefined));});
test('managed update and live mode require explicit actions',async()=>{const h=dashboard(true);await h.context.window.saarthiUpdates.load();assert.match(h.main.innerHTML,/Local observation mode/);assert.match(h.main.innerHTML,/id="updates-online" >/);await h.listeners.click({target:{closest:()=>({id:'updates-online'})}});assert.equal(h.calls.at(-1).url,'/api/updates/open');assert.equal(h.calls.at(-1).body.online,true);await h.listeners.submit({target:{id:'agent-live-form'},preventDefault(){}});assert.ok(h.calls.some(c=>c.url==='/api/agent/live'&&c.body.enabled===true));});

test('a background check shows a sidebar update that installs from the overview', async () => {
  const h=dashboard(true);
  const button={id:'sidebar-update',hidden:true}, version={textContent:''};
  h.context.S.view='overview';
  h.context.$=selector=>selector==='#sidebar-update'?button:version;
  await h.listeners.visibilitychange();
  assert.equal(button.hidden,false);
  assert.equal(version.textContent,'Version 0.6.1');
  assert.ok(h.calls.every(c=>c.body===undefined));
  await h.listeners.click({target:{closest:()=>button}});
  assert.equal(h.calls.at(-1).url,'/api/updates/open');
  assert.equal(h.calls.at(-1).body.online,true);
});

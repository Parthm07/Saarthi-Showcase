const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const web=path.join(__dirname,'../web');
function setup(standalone=false){
  const events={},messages=[];
  const context={navigator:{standalone},window:{matchMedia:()=>({matches:false}),addEventListener:(name,fn)=>events[name]=fn},A:{},icon:()=>'',drawer:html=>messages.push(html),toast:html=>messages.push(html),closeOverlay:()=>messages.push('closed')};
  vm.createContext(context);vm.runInContext(fs.readFileSync(path.join(web,'pwa.js'),'utf8'),context);
  return {context,events,messages,run:code=>vm.runInContext(code,context)};
}

function updateSetup() {
  const h = setup(true);
  let banner = null, replaced = null;
  const current = 'a'.repeat(20), next = 'b'.repeat(20);
  const main = {parentNode:{insertBefore:element=>{banner=element;}}};
  Object.assign(h.context, {
    AbortController, setTimeout, clearTimeout, URL,
    document:{
      hidden:false, activeElement:{blur(){}},
      querySelector:selector=>selector.startsWith('meta')?{content:current}:null,
      getElementById:id=>id==='main'?main:banner,
      createElement:()=>({setAttribute(){},remove(){banner=null;}}),
    },
    fetch:async()=>({ok:true,json:async()=>({version:next})}),
    location:{href:'https://saarthi.example/?party=one',replace:url=>{replaced=url;}},
    DRAFT_SENDING:new Set(), S:{}, LIVE:{on:true}, DRAFTS:{}, DRAFT_ERROR:'',
    saveOrderDrafts:()=>true,
  });
  return {...h, banner:()=>banner, replaced:()=>replaced};
}

test('browser tabs skip installed-app update checks',async()=>{
  const h=updateSetup();h.context.navigator.standalone=false;
  let requests=0;h.context.fetch=async()=>{requests++;};
  await h.events.pageshow();await h.events.focus();
  assert.equal(requests,0);assert.equal(h.banner(),null);
});

test('foreground version checks show one update prompt and preserve the selected URL',async()=>{
  const h=updateSetup();let requests=0;
  h.context.fetch=async(path,options)=>{
    requests++;assert.equal(path,'/api/app-version');assert.equal(options.cache,'no-store');
    return {ok:true,json:async()=>({version:'b'.repeat(20)})};
  };
  await h.events.pageshow();
  assert.match(h.banner().innerHTML,/Update available/);
  await h.events.focus();assert.equal(requests,1);
  let saved=false;h.context.saveOrderDrafts=()=>{saved=true;return true;};
  h.context.A.reloadwebsite();assert.equal(saved,true);
  const url=new URL(h.replaced());
  assert.equal(url.searchParams.get('party'),'one');
  assert.equal(url.searchParams.get('_saarthi_version'),'b'.repeat(20));
});

test('reload waits for an order in flight and refuses to lose an unsaved draft or rate',async()=>{
  const h=updateSetup();await h.events.focus();
  h.context.DRAFT_SENDING.add('party');
  h.context.A.reloadwebsite();assert.equal(h.replaced(),null);
  h.context.DRAFT_SENDING.clear();
  h.context.saveOrderDrafts=()=>false;
  h.context.A.reloadwebsite();assert.equal(h.replaced(),null);
  h.context.saveOrderDrafts=()=>true;
  h.context.document.querySelector=()=>({});
  h.context.A.reloadwebsite();assert.equal(h.replaced(),null);
});

test('matching release and offline checks leave the app usable',async()=>{
  const h=updateSetup();
  h.context.fetch=async()=>({ok:true,json:async()=>({version:'a'.repeat(20)})});
  await h.events.pageshow();assert.equal(h.banner(),null);
  h.run('websiteVersionCheckedAt=0');
  h.context.fetch=async()=>{throw Error('offline');};
  await h.events.focus();assert.equal(h.banner(),null);
  assert.equal(h.run('websiteVersionCheck'),false);
});
test('installable entry points resolve to standalone root and valid launcher images',()=>{
  const manifest=JSON.parse(fs.readFileSync(path.join(web,'manifest.webmanifest'),'utf8'));
  assert.equal(manifest.display,'standalone');assert.equal(manifest.start_url,'/');assert.equal(manifest.scope,'/');
  assert.equal(manifest.prefer_related_applications,false);
  for(const icon of manifest.icons){
    const bytes=fs.readFileSync(path.join(web,icon.src));
    assert.equal(bytes.subarray(1,4).toString(),'PNG');
    assert.equal(bytes.readUInt32BE(16)+'x'+bytes.readUInt32BE(20),icon.sizes);
  }
  const html=fs.readFileSync(path.join(web,'index.html'),'utf8');
  assert.match(html,/<head>[\s\S]*rel="manifest"[^>]+manifest\.webmanifest[\s\S]*<\/head>/);
  assert.ok(html.indexOf('src="pwa.js')<html.indexOf('boot();'));
});
test('browser install prompt is shown only after an explicit user action, and once',async()=>{
  const h=setup();let prompts=0,prevented=false;
  h.events.beforeinstallprompt({preventDefault(){prevented=true},prompt:async()=>prompts++,userChoice:Promise.resolve({outcome:'accepted'})});
  assert.equal(prevented,true);assert.equal(prompts,0);
  assert.match(h.run('installAppCard()'),/Install Saarthi app/);
  await h.context.A.installapp();assert.equal(prompts,1);assert.equal(h.messages.at(-1),'closed');
  await h.context.A.installapp();assert.equal(prompts,1);assert.match(h.messages.at(-1),/Add to Home Screen/);
  h.events.appinstalled();assert.doesNotMatch(h.run('installAppCard()'),/data-a="installapp"/);
});
test('unsupported browsers receive manual instructions and installed apps do not prompt again',async()=>{
  const h=setup();await h.context.A.installapp();assert.match(h.messages.at(-1),/Android/);assert.match(h.messages.at(-1),/iPhone/);
  assert.match(h.messages.at(-1),/server confirms/);
  const installed=setup(true);await installed.context.A.installapp();assert.match(installed.messages.at(-1),/already open as an app/);
});

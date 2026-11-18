const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');

function picker({clipboard,fetch}={}){
  const listeners={},elements=new Map(),events=[];
  const node={isConnected:true,matches:()=>false,querySelector(selector){
    if(!elements.has(selector))elements.set(selector,{});
    return elements.get(selector);
  },dispatchEvent(event){events.push(event.type);}};
  const context={window:{},URL,Blob,Event,AbortSignal,fetch,navigator:{clipboard},
    createImageBitmap:async()=>({width:1200,height:600,close(){}}),
    document:{addEventListener:(name,fn)=>{listeners[name]=fn;},createElement:()=>({
      getContext:()=>({drawImage(){}}),toDataURL(){assert.equal(this.width,640);assert.equal(this.height,320);return 'data:image/webp;base64,photo';}
    })}};
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../desktop/product-image.js'),'utf8'),context);
  const flush=()=>new Promise(resolve=>setImmediate(resolve));
  return {node,events,selected:()=>context.window.productImage.selected(node),
    status:()=>node.querySelector('[data-image-status]').textContent,
    async click(action){
      listeners.click({target:{closest:()=>({dataset:{picture:action},closest:()=>node})}});
      await flush();
    },
    async paste(file){
      listeners.paste({target:{closest:()=>node},clipboardData:{files:[file]},preventDefault(){}});
      await flush();
    }};
}

const photo=new Blob(['picture'],{type:'image/png'});
test('clipboard paste and clipboard button prepare a resized picture before saving',async()=>{
  const h=picker({clipboard:{read:async()=>[{types:[photo.type],getType:async()=>photo}]}});
  await h.paste(photo);
  assert.equal(h.selected().encoded,'photo');
  assert.equal(h.status(),'Picture ready to save.');
  await h.click('clear');
  assert.equal(h.selected(),null);
  await h.click('clipboard');
  assert.equal(h.selected().encoded,'photo');
  assert.deepEqual(h.events,Array(3).fill('product-picture-change'));
});

test('direct HTTPS links use an anonymous image request and failures preserve the selected picture',async()=>{
  const urls=[];
  const h=picker({fetch:async(url,options)=>{
    urls.push(String(url));
    assert.equal(options.credentials,'omit');
    return new Response(new Uint8Array([1,2,3]),{headers:{'Content-Type':photo.type}});
  }});
  h.node.querySelector('[data-image-link]').value='https://example.test/product.png';
  await h.click('link');
  assert.equal(h.selected().encoded,'photo');
  h.node.querySelector('[data-image-link]').value='http://127.0.0.1/product.png';
  await h.click('link');
  assert.match(h.status(),/HTTPS/);
  assert.equal(h.selected().encoded,'photo');
  assert.deepEqual(urls,['https://example.test/product.png']);
});

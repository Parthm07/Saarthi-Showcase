'use strict';
window.productImage=(()=>{
  const maxInput=12*1024*1024;
  const accepted=/^image\/(png|jpeg|webp)$/i;
  const states=new WeakMap();
  const state=node=>{
    if(!states.has(node))states.set(node,{picture:null,busy:false});
    return states.get(node);
  };
  function picker(disabled=false){
    return `<fieldset class="image-picker" ${disabled?'disabled':''}>
      <legend>Product picture</legend>
      <button type="button" data-picture="clipboard">Paste from clipboard</button>
      <small>Copy an image, then use this button or focus it and press Ctrl+V.</small>
      <label>Direct image link<input type="text" inputmode="url" data-image-link placeholder="https://…"></label>
      <button type="button" data-picture="link">Load image</button>
      <label>Or choose a file<input type="file" data-image-file accept="image/png,image/jpeg,image/webp"></label>
      <div data-image-preview></div><p data-image-status role="status"></p>
    </fieldset>`;
  }
  function show(node,picture){
    if(!node)return;
    state(node).picture=picture;
    node.querySelector('[data-image-preview]').innerHTML=picture?
      `<img class="product-preview" src="${picture.url}" alt="New product picture"><button type="button" data-picture="clear">Clear picture</button>`:'';
  }
  function selected(node){
    const current=state(node);
    if(current.busy)throw new Error('Wait for the picture to finish loading.');
    return current.picture;
  }
  async function fromLink(value){
    let url;
    try{url=new URL(value.trim());}catch{throw new Error('Enter a direct HTTPS image link.');}
    if(url.protocol!=='https:'||url.username||url.password)throw new Error('Enter a direct HTTPS image link without login details.');
    let response;
    try{
      response=await fetch(url,{mode:'cors',credentials:'omit',referrerPolicy:'no-referrer',signal:AbortSignal.timeout(15000)});
    }catch{throw new Error('Could not load this image link. Copy the picture itself or choose a saved file.');}
    const type=response.headers.get('content-type')?.split(';')[0].trim();
    if(!response.ok||!accepted.test(type)||!response.body)throw new Error('Use a direct link to a PNG, JPEG or WebP image.');
    const reader=response.body.getReader(),chunks=[];
    let size=0;
    try{
      for(;;){
        const {done,value}=await reader.read();
        if(done)break;
        size+=value.byteLength;
        if(size>maxInput)throw new Error('Choose an image up to 12 MB.');
        chunks.push(value);
      }
    }finally{
      await reader.cancel().catch(()=>{});
    }
    return new Blob(chunks,{type});
  }
  async function prepare(file){
    if(!file?.size||!accepted.test(file.type))throw new Error('Choose a PNG, JPEG or WebP image.');
    if(file.size>maxInput)throw new Error('Choose an image up to 12 MB.');
    const bitmap=await createImageBitmap(file).catch(()=>{throw new Error('Could not read this picture.');});
    try{
      if(bitmap.width>8192||bitmap.height>8192)throw new Error('Maximum image dimensions are 8192 × 8192.');
      const scale=Math.min(1,640/Math.max(bitmap.width,bitmap.height));
      const canvas=document.createElement('canvas');
      canvas.width=Math.max(1,Math.round(bitmap.width*scale));
      canvas.height=Math.max(1,Math.round(bitmap.height*scale));
      canvas.getContext('2d').drawImage(bitmap,0,0,canvas.width,canvas.height);
      const url=canvas.toDataURL('image/webp',.82);
      if(!url.startsWith('data:image/webp;base64,'))throw new Error('Could not resize this picture.');
      return {url,encoded:url.split(',')[1]};
    }finally{bitmap.close();}
  }
  async function choose(node,source){
    const current=state(node),status=node.querySelector('[data-image-status]');
    if(current.busy||node.matches(':disabled'))return;
    current.busy=true;
    status.textContent='Loading picture…';
    try{
      const picture=await prepare(await source());
      if(!node.isConnected)return;
      show(node,picture);
      status.textContent='Picture ready to save.';
    }catch(error){status.textContent=error.message;return;}
    finally{current.busy=false;}
    node.dispatchEvent(new Event('product-picture-change',{bubbles:true}));
  }
  document.addEventListener('paste',e=>{
    const node=e.target.closest('.image-picker');
    if(!node)return;
    const file=Array.from(e.clipboardData?.files||[]).find(file=>accepted.test(file.type));
    if(file){e.preventDefault();void choose(node,()=>file);}
  });
  document.addEventListener('change',e=>{
    if(!e.target.matches('[data-image-file]'))return;
    const file=e.target.files[0];
    e.target.value='';
    if(file)void choose(e.target.closest('.image-picker'),()=>file);
  });
  document.addEventListener('keydown',e=>{
    if(e.key!=='Enter'||!e.target.matches('[data-image-link]'))return;
    e.preventDefault();
    e.target.closest('.image-picker').querySelector('[data-picture="link"]').click();
  });
  document.addEventListener('click',e=>{
    const button=e.target.closest('button[data-picture]');
    if(!button)return;
    const node=button.closest('.image-picker'),action=button.dataset.picture;
    if(action==='clear'&&!state(node).busy){
      show(node,null);
      node.querySelector('[data-image-status]').textContent='';
      node.dispatchEvent(new Event('product-picture-change',{bubbles:true}));
    }
    if(action==='link')void choose(node,()=>fromLink(node.querySelector('[data-image-link]').value));
    if(action==='clipboard')void choose(node,async()=>{
      let items;
      try{items=await navigator.clipboard.read();}
      catch{throw new Error('Clipboard access is blocked. Focus the paste button and press Ctrl+V.');}
      for(const item of items){
        const type=item.types.find(type=>accepted.test(type));
        if(type)return item.getType(type);
      }
      throw new Error('No supported picture found. Use Copy image, then try again.');
    });
  });
  return {picker,show,selected};
})();

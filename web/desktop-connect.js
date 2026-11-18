'use strict';
const params=new URLSearchParams(location.search),challenge=params.get('challenge'),state=params.get('state'),port=Number(params.get('port'));
const status=document.getElementById('status');
async function request(path,body){const res=await fetch(path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});const d=await res.json();if(!res.ok)throw new Error(typeof d.detail==='string'?d.detail:'Sign-in could not finish');return d;}
async function account(){
  try{const me=await request('/api/me');document.getElementById('signin').replaceChildren();
    if(me.role!=='admin')throw new Error('Only an owner account can connect the admin program.');
    status.className='';status.textContent='Signed in as '+me.email;document.getElementById('approve').hidden=false;
  }catch(e){status.textContent=e.message;status.className='error';return false;}return true;
}
async function start(){
  if(!/^[A-Za-z0-9_-]{43}$/.test(challenge||'')||!/^[A-Za-z0-9_-]{32,100}$/.test(state||'')||!Number.isInteger(port)||port<1024||port>65535){status.textContent='This connection link is invalid. Start again from Saarthi Admin.';return;}
  if(await account())return;
  const cfg=await request('/api/config');if(!cfg.google_client_id){status.textContent='Google sign-in must be configured on this website before connecting the desktop.';return;}
  let tries=0;
  const timer=setInterval(()=>{if(++tries>100){clearInterval(timer);status.textContent='Google sign-in did not load. Refresh this page.';return;}
    if(!window.google?.accounts?.id)return;clearInterval(timer);
    google.accounts.id.initialize({client_id:cfg.google_client_id,callback:async response=>{try{await request('/api/auth/google',{credential:response.credential});await account();}catch(e){status.textContent=e.message;}}});
    google.accounts.id.renderButton(document.getElementById('signin'),{theme:'outline',size:'large'});
    status.textContent='Sign in with your website owner Google account.';
  },100);
}
document.getElementById('connect').onclick=async function(){this.disabled=true;try{
  const d=await request('/api/desktop/approve',{challenge});
  location.replace('http://127.0.0.1:'+port+'/desktop/callback?'+new URLSearchParams({state,grant:d.grant}));
}catch(e){status.textContent=e.message;this.disabled=false;}};
start().catch(e=>status.textContent=e.message);

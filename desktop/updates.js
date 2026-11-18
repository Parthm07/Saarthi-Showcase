'use strict';
window.saarthiUpdates=(()=>{
  let opening=false, checkingBadge=false;
  const canInstall=d=>d.state==='available'&&d.managed_install&&!d.recovery_pending;
  function showBadge(d){
    const button=$('#sidebar-update');
    if(!button)return;
    button.hidden=!canInstall(d);
    $('#sidebar-update-version').textContent=d.available_version?'Version '+d.available_version:'';
  }
  async function refreshBadge(){
    if(document.hidden||checkingBadge)return;
    checkingBadge=true;
    try{showBadge(await api('/api/updates'));}
    catch{/* Keep the last known state during a temporary connection failure. */}
    finally{checkingBadge=false;}
  }
  async function installOnline(button){
    if(opening)return;
    opening=true;
    button.disabled=true;
    try{
      await api('/api/updates/open',{online:true,recover:false});
      notify('Saarthi is downloading the update. The app will reopen after installation.');
    }finally{
      opening=false;
      button.disabled=false;
    }
  }
  document.addEventListener('DOMContentLoaded',()=>{
    refreshBadge();
    setInterval(refreshBadge,30000);
  });
  document.addEventListener('visibilitychange',refreshBadge);

  async function load(){
    const generation=++S.generation,[d,agent]=await Promise.all([api('/api/updates'),api('/api/agent')]);
    if(generation!==S.generation||S.view!=='updates')return;
    showBadge(d);
    $('#main').innerHTML=head('Keep the business laptop current.','Version '+d.version+' · Dashboard and Tally agent updates')+
      (!d.managed_install?'<div class="notice">One-click updates require the complete managed setup on this laptop. The existing Python agent must be stopped manually once when moving to the packaged agent.</div>':'')+
      (d.recovery_pending?'<div class="notice error">An update was interrupted. The agent stays paused until its verified pre-update backup is recovered. <button id="updates-recover">Recover interrupted update</button></div>':'')+
      `<div class="sync-grid">${panel('Publisher updates',`<div class="pad"><p>${badge(d.state)} ${esc(d.message)}</p>${d.available_version?`<h2>Publisher version ${esc(d.available_version)}</h2>`:''}<form id="update-feed-form"><label>Publisher HTTPS latest.json URL<input id="update-feed" type="url" value="${esc(d.feed||'')}" placeholder="https://your-release-host/latest.json"></label><p>Only packages signed by this application's publisher are accepted. This URL is public release metadata, not a database password.</p><button type="submit">Save release feed</button></form><div class="inline-actions"><button id="updates-check">Check for updates</button><button id="updates-online" ${canInstall(d)?'':'disabled'}>Download & install update</button></div><p>Saarthi checks the feed every 15 minutes while Admin is open. Save unfinished forms before installing.</p></div>`)}${panel('Install a downloaded release',`<div class="pad"><p>Transfer the signed Saarthi release ZIP from the development laptop using a USB drive or a file transfer. No internet or Python installation is required on this laptop.</p><button id="updates-local" ${d.managed_install?'':'disabled'}>Choose signed release ZIP…</button><p>Save unfinished forms first. The updater waits for the current sync/import, closes the viewer, backs up both local databases and settings, and installs both application components.</p><p>A failed validation restores the pre-update databases and version. A completed update cannot simply restore an old backup after new business entries have been made.</p></div>`)}</div>`+
      (agent.managed?panel('Website pause control',`<div class="pad"><form id="agent-remote-form"><p>${agent.agent_sync_linked?'Remote sync settings saved.':'Link this laptop to the owner’s website controls.'}</p><label>Website HTTPS address<input type="url" id="agent-remote-url" value="${esc(agent.agent_sync_url||'')}" required></label><label>Remote sync token<input type="password" id="agent-remote-token" autocomplete="new-password" required></label><button type="submit">Save remote sync settings</button></form></div>`):'')+
      panel('Live work and your local archive',`<div class="pad"><p>${agent.managed?'Packaged agent · no separately installed Python required.':'Legacy/source agent · its Python installation is still required.'}</p><p>${agent.managed?(agent.billing_enabled?'Live invoicing, admin Tally writes and configured cloud jobs are enabled.':'Local observation mode: Tally is read into the archive. Invoice/admin writes and cloud jobs are disabled.'):'The new live-mode switch does not control a legacy worker.'}</p>${agent.managed?`<form id="agent-live-form"><label><input id="agent-live" type="checkbox" ${agent.billing_enabled?'checked':''}> Enable automatic invoicing, admin Tally writes and configured cloud sync</label><p>Review the company, pending orders and cloud destination before enabling. The current job always finishes; this does not interrupt an import.</p><button type="submit">Apply live-mode setting</button></form>`:''}<h2>What is tally_raw.db?</h2><p>It is Saarthi's SQLite copy of data read through Tally's local HTTP connection. It is not Tally's own company folder or a .900 file.</p><p>On a new laptop: run the complete Setup → Create tally_raw.db by reading Tally → select a data folder → Read company from Tally → verify the company → Install / import. Tally must be open with that company loaded and local connectivity enabled, normally on port 9000.</p><p>If the same company already has a Saarthi archive, choose Existing archive instead. Preserve tally_raw.db and saarthi_admin.db together, including unresolved orders, original request IDs, images, targets and backups. Application releases never contain your databases or cloud credentials.</p></div>`);
  }
  document.addEventListener('click',async e=>{
    const button=e.target.closest('button');if(!button)return;
    try{
      if(button.id==='sidebar-update'){await installOnline(button);return;}
      if(S.view!=='updates')return;
      if(button.id==='updates-check'){await api('/api/updates/check',{});await load();}
      if(button.id==='updates-online')await installOnline(button);
      if(['updates-local','updates-recover'].includes(button.id)){
        await api('/api/updates/open',{recover:button.id==='updates-recover'});notify('The setup/update window is opening. Save unfinished forms before continuing.');
      }
    }catch(error){notify(error.message);}
  });
  document.addEventListener('submit',async e=>{
    if(!['update-feed-form','agent-live-form','agent-remote-form'].includes(e.target.id))return;e.preventDefault();
    try{
      if(e.target.id==='update-feed-form')await api('/api/settings',{update_feed:$('#update-feed').value.trim()});
      else if(e.target.id==='agent-remote-form'){
        const d=await api('/api/agent/remote',{url:$('#agent-remote-url').value.trim(),token:$('#agent-remote-token').value.trim()});notify(d.message);
      }else {const d=await api('/api/agent/live',{enabled:$('#agent-live').checked});notify(d.message);}
      await load();
    }catch(error){notify(error.message);}
  });
  return {load};
})();

/* Installation adds a launcher for the live website. No API responses,
   sign-in credentials or submitted orders are put in a service-worker cache. */
let installPrompt = null, installBusy = false, installedThisSession = false;
const appInstalled = () => installedThisSession || !!navigator.standalone
  || !!window.matchMedia?.('(display-mode: standalone)').matches;
window.addEventListener('beforeinstallprompt', event => {
  event.preventDefault(); installPrompt = event;
});
window.addEventListener('appinstalled', () => {
  installPrompt = null; installedThisSession = true;
});
function installAppCard() {
  return appInstalled()
    ? '<div class="card mini"><b>Saarthi app</b><p class="faint sm">You are using the installed app.</p></div>'
    : '<div class="card mini"><b>Saarthi on your phone</b><p class="faint sm">Open Saarthi from your home screen in its own window.</p><button class="btn block" data-a="installapp">Install Saarthi app</button></div>';
}
function installAppHelp() {
  drawer(`<div class="drawer-h"><h2>Install Saarthi</h2><button class="btn ghost sm" data-a="close" aria-label="Close">${icon('x')}</button></div>
    <div class="drawer-b"><div class="card mini"><h4>Android · Chrome or Edge</h4><p>Open the browser menu and choose <b>Install app</b> or <b>Add to Home screen</b>.</p></div>
    <div class="card mini"><h4>iPhone · Safari</h4><p>Open this website in Safari, tap <b>Share → Add to Home Screen</b>, enable <b>Open as Web App</b> if offered, then tap <b>Add</b>.</p></div>
    <p class="faint sm">Sign in with your registered Google account. Internet is needed to load current data and confirm orders. Saved drafts remain on this device; an order is placed only after the server confirms it.</p></div>`);
}
A.installapp = async () => {
  if (appInstalled()) { toast('Saarthi is already open as an app.'); return; }
  if (installBusy) return;
  if (!installPrompt) { installAppHelp(); return; }
  installBusy = true;
  const prompt = installPrompt; installPrompt = null;
  try {
    await prompt.prompt();
    const choice = await prompt.userChoice;
    if (choice.outcome === 'accepted') closeOverlay();
  } catch (_) { installAppHelp(); }
  finally { installBusy = false; }
};

let websiteVersionCheck = false, websiteVersionCheckedAt = 0;
let availableWebsiteVersion = '';

async function checkWebsiteUpdate() {
  if (!appInstalled()) return;
  const current = document.querySelector('meta[name="saarthi-version"]')?.content;
  if (!current || document.hidden || websiteVersionCheck || Date.now() - websiteVersionCheckedAt < 30000) return;
  websiteVersionCheck = true;
  websiteVersionCheckedAt = Date.now();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 5000);
  try {
    const response = await fetch('/api/app-version', {cache:'no-store', signal:controller.signal});
    if (!response.ok) return;
    const {version} = await response.json();
    if (typeof version !== 'string' || !/^[a-f0-9]{20}$/.test(version)) return;
    availableWebsiteVersion = version === current ? '' : version;
    let banner = document.getElementById('website-update');
    if (!availableWebsiteVersion) { banner?.remove(); return; }
    if (!banner) {
      const main = document.getElementById('main');
      if (!main) return;
      banner = document.createElement('div');
      banner.id = 'website-update';
      banner.className = 'website-update';
      banner.setAttribute('role', 'status');
      banner.innerHTML = '<span>Update available</span><button type="button" data-a="reloadwebsite">Reload</button>';
      main.parentNode.insertBefore(banner, main);
    }
  } catch (_) {
    // Retry on the next foreground event if the phone has no connection.
  } finally {
    clearTimeout(timer);
    websiteVersionCheck = false;
  }
}

A.reloadwebsite = () => {
  if (!availableWebsiteVersion) return;
  if (DRAFT_SENDING.size) { toast('Wait for the order confirmation before updating.', 'warn'); return; }
  document.activeElement?.blur();
  if (document.querySelector('[data-our-rate][data-dirty]') || (S.mod === 'people' && S.person)) {
    toast('Save or cancel your changes before updating.', 'warn');
    return;
  }
  if (LIVE.on && DRAFTS && !saveOrderDrafts()) {
    toast(DRAFT_ERROR || 'Could not save your draft. Keep this page open.', 'warn');
    return;
  }
  const url = new URL(location.href);
  url.searchParams.set('_saarthi_version', availableWebsiteVersion);
  location.replace(url.href);
};

window.addEventListener('pageshow', () => checkWebsiteUpdate());
window.addEventListener('focus', () => checkWebsiteUpdate());
window.addEventListener('online', () => checkWebsiteUpdate());
if (typeof document !== 'undefined') {
  document.addEventListener('visibilitychange', () => { if (!document.hidden) checkWebsiteUpdate(); });
}

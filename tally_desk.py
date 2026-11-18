"""Order desk: a single-page UI and JSON API over tally_shop.

Separate from tally_shop so the catalogue/cart/order logic stays testable
without a server, and so this layer can be replaced (Next.js, FastAPI,
whatever) without touching the rules.

    python tally_desk.py            # http://localhost:8800
    python tally_desk.py --port 9100
    python tally_desk.py --self-check

Serves on 127.0.0.1 only. The local Tally port must never be exposed, and
neither should this until there is authentication -- Phase 3 Step 12 is NOT
built here: there are no users, roles or logins yet, so anyone who can reach
the port can place an order as any customer.

Stdlib only.
"""
import argparse
import json
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer

import tally_shop as shop

PAGE = r"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Order Desk</title><style>
:root{--bg:#fff;--fg:#1a1a1a;--mut:#666;--line:#e3e3e3;--accent:#1a5fb4;
--warn:#b45309;--bad:#b3261e;--ok:#1b6e3c;--panel:#fafafa}
@media(prefers-color-scheme:dark){:root{--bg:#161616;--fg:#ececec;
--mut:#9a9a9a;--line:#2e2e2e;--accent:#7aa6e8;--warn:#e0a252;--bad:#f2857b;
--ok:#73c58e;--panel:#1d1d1d}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.45 system-ui,-apple-system,'Segoe UI',sans-serif}
header{padding:10px 16px;border-bottom:1px solid var(--line);display:flex;
gap:12px;align-items:baseline;flex-wrap:wrap}
h1{font-size:15px;margin:0;font-weight:600}
h2{font-size:12px;text-transform:uppercase;letter-spacing:.05em;
color:var(--mut);margin:18px 0 6px}
.m{color:var(--mut);font-size:12px}
main{display:grid;grid-template-columns:minmax(0,1fr) 350px}
@media(max-width:860px){main{grid-template-columns:minmax(0,1fr)}}
section{padding:14px 16px;min-width:0}
aside{padding:14px 16px;border-left:1px solid var(--line);background:var(--panel)}
@media(max-width:860px){aside{border-left:0;border-top:1px solid var(--line)}}
input,select,button{font:inherit;padding:7px 9px;border:1px solid var(--line);
border-radius:6px;background:var(--bg);color:var(--fg)}
button{cursor:pointer}button:disabled{opacity:.45;cursor:default}
button.p{background:var(--accent);color:#fff;border-color:var(--accent);
font-weight:600}
table{width:100%;border-collapse:collapse}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line)}
th{font-size:11px;text-transform:uppercase;letter-spacing:.04em;
color:var(--mut);font-weight:600}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
.row{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px}
.row input[type=search]{flex:1 1 200px;min-width:0}
.stale{color:var(--warn)}.bad{color:var(--bad)}.ok{color:var(--ok)}
.tag{font-size:11px;color:var(--mut)}
.q{width:72px}
.e{color:var(--bad);margin:8px 0;white-space:pre-wrap;font-size:13px}
.tot{display:flex;justify-content:space-between;padding:3px 0}
.tot.g{font-weight:600;border-top:1px solid var(--line);margin-top:6px;
padding-top:7px}
.warn{background:#fff4e5;border:1px solid #e0a252;color:#7a4a00;
padding:7px 10px;border-radius:6px;font-size:12px;margin-bottom:10px}
@media(prefers-color-scheme:dark){.warn{background:#2a2113;color:#e0b878}}
</style></head><body>
<header><h1>Order Desk</h1><span class="m" id="fresh">loading...</span></header>
<main>
<section>
  <div class="row">
    <input type="search" id="q" placeholder="Search items...">
    <select id="grp"><option value="">All groups</option></select>
    <label class="m"><input type="checkbox" id="instock"> saleable only</label>
  </div>
  <table><thead><tr><th>Item</th><th class="n">Rate</th>
  <th class="n">Saleable</th><th class="n">Qty</th><th></th></tr></thead>
  <tbody id="items"></tbody></table>
</section>
<aside>
  <div id="stalewarn"></div>
  <select id="cust" style="width:100%"><option value="">Select customer...</option></select>
  <div id="custinfo" class="m" style="margin:6px 0 10px"></div>
  <h2>Cart</h2>
  <table><tbody id="cart"></tbody></table>
  <div id="totals"></div>
  <div id="err" class="e"></div>
  <div class="row" style="margin-top:10px">
    <button class="p" id="place">Place order</button>
    <button id="clear">Clear</button>
  </div>
  <div class="m" id="note"></div>
  <h2>Recent orders</h2>
  <table><tbody id="orders"></tbody></table>
</aside></main>
<script>
const $=s=>document.querySelector(s), cart=new Map();
let ITEMS=[], CUST=[];
const money=n=>'₹'+(Number(n)||0).toLocaleString('en-IN',
  {minimumFractionDigits:2,maximumFractionDigits:2});
const api=(p,o)=>fetch(p,o).then(r=>r.json());
const esc=s=>String(s==null?'':s).replace(/[&<>"]/g,
  c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

function fresh(f){
  const when=f.stock_asof?new Date(f.stock_asof).toLocaleString('en-IN'):'never';
  $('#fresh').innerHTML=esc(f.company||'?')+' · stock as of <b class="'+
    (f.stock_stale?'stale':'ok')+'">'+esc(when)+'</b>'+
    (f.stock_stale?' <span class="stale">(stale)</span>':'')+
    ' · GST '+(f.gst_rate*100).toFixed(0)+'% · '+
    f.open_orders+' open';
  $('#stalewarn').innerHTML=f.stock_stale?'<div class="warn">Stock was last '+
    'observed '+esc(when)+'. Tally may be closed. Quantities below are not '+
    'current — orders stay provisional.</div>':'';
}
function drawItems(){
  $('#items').innerHTML=ITEMS.map(i=>{
    const sale=i.saleable;
    const cls=sale===null?'':sale<=0?'bad':i.stale?'stale':'';
    return '<tr><td>'+esc(i.name)+'<div class="tag">'+esc(i.group||'')+
      (i.mrp?' · MRP '+i.mrp:'')+'</div></td>'+
      '<td class="n">'+(i.rate===null?'<span class="tag">no price</span>'
        :money(i.rate))+'</td>'+
      '<td class="n '+cls+'">'+(sale===null?'—'
        :sale.toLocaleString('en-IN'))+' '+esc(i.unit||'')+'</td>'+
      '<td class="n"><input class="q" type="number" min="0" step="1" value="'+
        (cart.get(i.guid)?cart.get(i.guid).qty:'')+'" data-g="'+i.guid+'"></td>'+
      '<td><button data-add="'+i.guid+'"'+(i.rate===null?' disabled':'')+
      '>+</button></td></tr>';
  }).join('')||'<tr><td colspan=5 class="m">no items match</td></tr>';
}
function drawCart(){
  if(!cart.size){
    $('#cart').innerHTML='<tr><td class="m">Cart is empty</td></tr>';
    $('#totals').innerHTML='';return;}
  $('#cart').innerHTML=[...cart.values()].map(l=>
    '<tr><td>'+esc(l.name)+'<div class="tag">'+l.qty+' × '+money(l.rate)+
    '</div></td><td class="n">'+money(l.qty*l.rate)+
    '</td><td><button data-del="'+l.guid+'">×</button></td></tr>').join('');
  const sub=[...cart.values()].reduce((a,l)=>a+l.qty*l.rate,0);
  $('#totals').innerHTML='<div class="tot"><span>Subtotal</span><span>'+
    money(sub)+'</span></div><div class="tot"><span class="m">GST (0%)</span>'+
    '<span class="m">'+money(0)+'</span></div>'+
    '<div class="tot g"><span>Total</span><span>'+money(sub)+'</span></div>';
}
function drawOrders(os){
  $('#orders').innerHTML=os.slice(0,12).map(o=>
    '<tr><td>'+esc(o.order_no)+'<div class="tag">'+esc(o.party_name)+
    '</div></td><td class="n">'+money(o.total)+'<div class="tag">'+
    esc(o.status)+'</div></td></tr>').join('')
    ||'<tr><td class="m">none yet</td></tr>';
}
async function reload(){
  const p=new URLSearchParams({q:$('#q').value,group:$('#grp').value,
    in_stock:$('#instock').checked?'1':''});
  ITEMS=await api('/api/items?'+p); drawItems();
  fresh(await api('/api/freshness'));
  drawOrders(await api('/api/orders'));
}
document.addEventListener('click',async e=>{
  const add=e.target.dataset.add, del=e.target.dataset.del;
  if(add){
    const i=ITEMS.find(x=>x.guid===add);
    const box=document.querySelector('.q[data-g="'+add+'"]');
    const qty=parseFloat(box.value||'0');
    if(qty>0){cart.set(add,{guid:add,name:i.name,rate:i.rate,qty:qty});
      drawCart();}
  }
  if(del){cart.delete(del);drawCart();drawItems();}
  if(e.target.id==='clear'){cart.clear();drawCart();drawItems();
    $('#err').textContent='';}
  if(e.target.id==='place'){
    $('#err').textContent='';$('#note').textContent='';
    const r=await api('/api/orders',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({party_guid:$('#cust').value,
        lines:[...cart.values()].map(l=>({item_guid:l.guid,qty:l.qty}))})});
    if(r.error){$('#err').textContent=r.error;return;}
    $('#note').textContent=r.order_no+
      ' placed — provisional until invoiced in Tally.';
    cart.clear();drawCart();reload();
  }
});
$('#cust').addEventListener('change',()=>{
  const c=CUST.find(x=>x.guid===$('#cust').value);
  $('#custinfo').innerHTML=c?esc(c.route||'')+' · outstanding <b>'+
    money(c.outstanding)+'</b>':'';
});
['#q','#grp','#instock'].forEach(k=>$(k).addEventListener('input',reload));
(async()=>{
  CUST=await api('/api/customers');
  $('#cust').insertAdjacentHTML('beforeend',CUST.map(c=>
    '<option value="'+c.guid+'">'+esc(c.name)+'</option>').join(''));
  $('#grp').insertAdjacentHTML('beforeend',(await api('/api/groups')).map(g=>
    '<option>'+esc(g)+'</option>').join(''));
  drawCart();await reload();
})();
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    # Path, not a connection: a sqlite3 connection cannot be used from a
    # thread other than the one that opened it, and ThreadingHTTPServer
    # hands each request to a new thread. Opening per request costs
    # microseconds and removes the whole class of problem.
    db_path = None
    server_version = "TallyDesk/1"

    def log_message(self, *a):
        pass

    def _send(self, obj, code=200, ctype="application/json"):
        body = (obj if isinstance(obj, bytes)
                else json.dumps(obj, default=str).encode())
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urllib.parse.urlsplit(self.path)
        p = urllib.parse.parse_qs(u.query)

        def one(k):
            return (p.get(k) or [""])[0]

        try:
            c = shop.open_db(self.db_path)
            if u.path in ("/", "/index.html"):
                return self._send(PAGE.encode(),
                                  ctype="text/html; charset=utf-8")
            if u.path == "/api/items":
                # `party` makes every rate the one THAT shop was last
                # charged; without it the list price is quoted, which is
                # wrong for 75% of party/item pairs.
                return self._send(shop.catalogue(
                    c, q=one("q") or None, group=one("group") or None,
                    in_stock_only=bool(one("in_stock")),
                    party=one("party") or None))
            if u.path == "/api/history":
                return self._send(shop.party_history(
                    c, one("party"), limit=int(one("limit") or 50)))
            if u.path == "/api/usual":
                return self._send(shop.party_top_items(
                    c, one("party"), n=int(one("n") or 20)))
            if u.path == "/api/customers":
                return self._send(shop.customers(
                    c, q=one("q") or None, route=one("route") or None))
            if u.path == "/api/groups":
                return self._send(shop.groups(c))
            if u.path == "/api/routes":
                return self._send(shop.routes(c))
            if u.path == "/api/orders":
                return self._send(shop.list_orders(c, one("status") or None))
            if u.path == "/api/freshness":
                return self._send(shop.freshness(c))
            if u.path.startswith("/api/order/"):
                o = shop.get_order(c, u.path.rsplit("/", 1)[1])
                return self._send(o or {"error": "unknown order"},
                                  200 if o else 404)
        except Exception as e:
            return self._send({"error": f"{type(e).__name__}: {e}"}, 500)
        finally:
            try:
                c.close()
            except Exception:
                pass
        return self._send({"error": "not found"}, 404)

    def do_POST(self):
        u = urllib.parse.urlsplit(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._send({"error": "bad JSON"}, 400)
        c = shop.open_db(self.db_path)
        try:
            if u.path == "/api/orders":
                return self._send(shop.place_order(
                    c, body.get("party_guid"),
                    body.get("lines") or [], note=body.get("note"),
                    allow_oversell=bool(body.get("allow_oversell"))))
            if u.path.startswith("/api/order/") and u.path.endswith("/status"):
                return self._send(shop.set_status(
                    c, u.path.split("/")[3], body.get("status")))
        except shop.OrderError as e:
            return self._send({"error": str(e)}, 400)
        except Exception as e:
            return self._send({"error": f"{type(e).__name__}: {e}"}, 500)
        finally:
            c.close()
        return self._send({"error": "not found"}, 404)


def serve(port=8800, path=None):
    # 127.0.0.1 only: there is no authentication yet.
    Handler.db_path = path
    c = shop.open_db(path)
    f = shop.freshness(c)
    c.close()
    print(f"order desk  http://localhost:{port}   (ctrl-c to stop)")
    print(f"  company={f['company']!r}  stock_asof={f['stock_asof']} "
          f"stale={f['stock_stale']}  gst={f['gst_rate']:.0%}")
    if f["stock_stale"]:
        print("  WARNING: stock is stale -- run the agent, or Tally is shut")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


def _self_check():
    import os
    import tempfile
    import threading
    import urllib.error
    import urllib.request

    db = os.path.join(tempfile.mkdtemp(), "desk.db")
    shop.open_db(db).close()
    Handler.db_path = db
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def get(p):
        try:
            with urllib.request.urlopen(base + p, timeout=20) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def post(p, obj):
        req = urllib.request.Request(
            base + p, json.dumps(obj).encode(),
            {"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    with urllib.request.urlopen(base + "/", timeout=20) as r:
        html = r.read()
    assert r.status == 200 and b"<title>Order Desk" in html
    assert b"GST (0%)" in html, "the page must state the GST assumption"

    for path in ("/api/items", "/api/customers", "/api/groups",
                 "/api/routes", "/api/orders", "/api/freshness",
                 "/api/history?party=nobody", "/api/usual?party=nobody"):
        code, _ = get(path)
        assert code == 200, f"{path} -> {code}"
    code, _ = get("/api/nope")
    assert code == 404
    code, body = get("/api/order/ORD-nope")
    assert code == 404 and body.get("error")

    # a bad order must come back as 400 with a message, not a 500
    code, body = post("/api/orders", {"party_guid": "nope", "lines": []})
    assert code == 400 and "customer" in body["error"], body
    code, body = post("/api/orders", {})
    assert code == 400, body

    srv.shutdown()
    print("desk api ok (page renders, endpoints respond, bad order -> 400)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8800)
    ap.add_argument("--db")
    ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        _self_check()
    else:
        try:
            serve(a.port, a.db)
        except KeyboardInterrupt:
            print("\nstopped")

"""surface.py — the human edge of the bridge.

A tiny stdlib HTTP server that shows pending `human://` tasks to a person and
lets them claim, complete (with a photo proof + note) or decline. This is the
"serwis usługowy dla ludzi": where the LLM/host's URI steps become something a
warehouse worker, field tech or operator actually taps on a phone.

It shares the SAME TaskStore the connector writes to, and resolves through the
SAME handler (`handlers.resolve_task`), so a tap here is identical to any other
`human://{node}/task/command/resolve` call. No framework, no build step.

Run:  python -m urirun_connector_human.surface
Configuration via .env (see project root) or environment variables.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import handlers
from ._env import get_lan_ip, get_lan_url, get_node, get_port, get_host
from .connector import MEMORY, STORE

PROOF_DIR = Path.home() / ".urirun-human" / "proofs"
PROOF_DIR.mkdir(parents=True, exist_ok=True)

KIND_BADGE = {"grant": "🔑 grant", "safety": "⚠ safety",
              "judgement": "👁 judgement", "action": "✋ action"}


PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>urirun · human tasks</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin:0; font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
         background:#0e1116; color:#e6edf3; -webkit-tap-highlight-color:transparent; }
  header { position:sticky; top:0; background:#0e1116ee; backdrop-filter:blur(8px);
           padding:14px 18px calc(14px + env(safe-area-inset-top)); border-bottom:1px solid #222b36;
           display:flex; align-items:center; gap:10px; }
  header b { font-size:17px; letter-spacing:.2px; }
  header .node { margin-left:auto; font-size:13px; color:#8b97a6;
                 background:#161b22; padding:4px 10px; border-radius:999px; }
  main { padding:16px; max-width:680px; margin:0 auto; }
  .empty { text-align:center; color:#6e7a89; padding:40px 20px; }
  .qr-card { background:#161b22; border:1px solid #222b36; border-radius:14px;
             padding:20px; margin:0 0 20px; text-align:center; }
  .qr-card img { width:180px; height:180px; border-radius:8px; background:#fff; }
  .qr-url { font-size:13px; color:#8b97a6; margin:10px 0 0; word-break:break-all; }
  .qr-url a { color:#58a6ff; }
  .card { background:#161b22; border:1px solid #222b36; border-radius:14px;
          padding:16px; margin:0 0 14px; }
  .card h3 { margin:0 0 6px; font-size:17px; }
  .meta { display:flex; gap:8px; flex-wrap:wrap; margin:0 0 10px; }
  .tag { font-size:12px; padding:3px 9px; border-radius:999px; background:#1f2630; color:#aeb9c7; }
  .tag.scope-per-env { background:#13312a; color:#7ee2b8; }
  .tag.scope-per-instance { background:#33271a; color:#f0c386; }
  .instr { color:#c2cbd6; margin:0 0 14px; white-space:pre-wrap; }
  .row { display:flex; gap:10px; }
  button { flex:1; border:0; border-radius:11px; padding:14px; font-size:16px; font-weight:600;
           cursor:pointer; }
  .done { background:#2da44e; color:#fff; } .decline { background:#30363d; color:#e6edf3; }
  .claim { background:#1f6feb; color:#fff; }
  textarea, input[type=file] { width:100%; margin:0 0 10px; padding:11px; border-radius:10px;
            border:1px solid #2a3340; background:#0e1116; color:#e6edf3; font:inherit; }
  .photo-ok { font-size:13px; color:#7ee2b8; margin:0 0 10px; }
  .foot { text-align:center; color:#56606e; font-size:12px; padding:20px; }
  a { color:#58a6ff; text-decoration:none; }
</style></head>
<body>
<header><b>urirun</b> <span style="color:#6e7a89">human tasks</span>
  <span class="node" id="node-pill">node: —</span></header>
<main>
  <div id="qr-section" style="display:none" class="qr-card">
    <div style="font-size:13px;color:#8b97a6;margin-bottom:12px">Scan to open on phone</div>
    <img id="qr-img" src="" alt="QR">
    <div class="qr-url"><a id="qr-link" href="#"></a></div>
  </div>
  <div id="list"><div class="empty">Loading…</div></div>
</main>
<div class="foot">A tap here = <code>human://{node}/task/command/resolve</code></div>
<script>
const qs = new URLSearchParams(location.search);
const NODE = qs.get('node') || '';
document.getElementById('node-pill').textContent = 'node: ' + (NODE || 'all');
let lastSig = '';

// Load server info + QR (only on desktop, skip when already on phone)
async function loadInfo(){
  try {
    const r = await fetch('/api/info');
    const info = await r.json();
    const workerUrl = info.workerUrl;
    const qrUrl = info.qrUrl;
    const sec = document.getElementById('qr-section');
    const img = document.getElementById('qr-img');
    const lnk = document.getElementById('qr-link');
    img.src = qrUrl;
    lnk.href = workerUrl;
    lnk.textContent = workerUrl;
    sec.style.display = 'block';
  } catch(e){}
}
// Show QR only on a larger screen (desktop/tablet); phone users see the task list directly
if (window.innerWidth > 500) loadInfo();

function badge(k){ return ({grant:'🔑 grant',safety:'⚠ safety',judgement:'👁 judgement',action:'✋ action'}[k]||k); }

async function load(){
  const r = await fetch('/api/tasks' + (NODE ? ('?node='+encodeURIComponent(NODE)) : ''));
  const tasks = await r.json();
  const sig = JSON.stringify(tasks.map(t=>[t.id,t.status]));
  if (sig === lastSig) return;
  lastSig = sig;
  const root = document.getElementById('list');
  if (!tasks.length){ root.innerHTML = '<div class="empty">✓ No pending tasks.<br>Waiting for the host…</div>'; return; }
  root.innerHTML = '';
  for (const t of tasks){
    const el = document.createElement('div'); el.className='card'; el.id='card-'+t.id;
    el.innerHTML = `
      <h3>${esc(t.title)}</h3>
      <div class="meta">
        <span class="tag">${badge(t.kind)}</span>
        <span class="tag scope-${t.scope}">${t.scope}</span>
        <span class="tag">env: ${esc(t.env)}</span>
        <span class="tag">${t.status}</span>
      </div>
      <p class="instr">${esc(t.instruction||'')}</p>
      <textarea id="note-${t.id}" rows="2" placeholder="Note (optional)"></textarea>
      ${t.kind==='action' ? `<input type="file" accept="image/*" capture="environment" id="ph-${t.id}" onchange="pickPhoto('${t.id}')"><div class="photo-ok" id="phok-${t.id}"></div>`:''}
      <div class="row">
        <button class="done" onclick="resolve('${t.id}','done')">Done ✓</button>
        <button class="decline" onclick="resolve('${t.id}','declined')">Decline</button>
      </div>`;
    root.appendChild(el);
  }
}
const photos = {};
function pickPhoto(id){
  const f = document.getElementById('ph-'+id).files[0]; if(!f) return;
  const rd = new FileReader();
  rd.onload = () => { photos[id]=rd.result; document.getElementById('phok-'+id).textContent='📷 photo attached'; };
  rd.readAsDataURL(f);
}
async function resolve(id, outcome){
  const note = (document.getElementById('note-'+id)||{}).value || '';
  const body = { taskId:id, outcome, by:'worker', note, photoDataUrl: photos[id]||null };
  const card = document.getElementById('card-'+id);
  if (card) card.style.opacity = .5;
  await fetch('/api/resolve', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  lastSig=''; load();
}
function esc(s){ return (s||'').replace(/[&<>"]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
load(); setInterval(load, 1500);
</script>
</body></html>"""


def _qr_png(url: str) -> bytes | None:
    try:
        import qrcode  # type: ignore
        qr = qrcode.make(url)
        buf = io.BytesIO()
        qr.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return None


def _print_qr(url: str) -> None:
    try:
        import qrcode  # type: ignore
        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
    except Exception:
        pass


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json")

    def log_message(self, *_):  # quiet
        return

    def do_GET(self) -> None:
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path in ("/", "/t") or u.path.startswith("/t/"):
            self._send(200, PAGE.encode(), "text/html; charset=utf-8")
        elif u.path == "/api/tasks":
            node = (q.get("node") or [None])[0]
            self._json(200, STORE.list_open(node))
        elif u.path == "/api/events":
            since = int((q.get("since") or ["0"])[0])
            node = (q.get("node") or [None])[0]
            self._json(200, STORE.events_since(since, node))
        elif u.path == "/api/qr":
            target = (q.get("url") or [None])[0]
            if not target:
                node = (q.get("node") or [get_node()])[0]
                port = self.server.server_address[1]
                target = f"{get_lan_url(port)}/?node={node}"
            png = _qr_png(target)
            if png:
                self._send(200, png, "image/png")
            else:
                self._json(503, {"error": "qrcode package not available", "url": target})
        elif u.path == "/api/info":
            port = self.server.server_address[1]
            node = get_node()
            lan = get_lan_url(port)
            self._json(200, {
                "lanUrl": lan,
                "workerUrl": f"{lan}/?node={node}",
                "qrUrl": f"{lan}/api/qr?node={node}",
                "node": node,
                "db": str(STORE.path),
            })
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        u = urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return self._json(400, {"error": "bad json"})

        if u.path == "/api/resolve":
            # Persist an attached photo as an artifact, then resolve via the handler.
            proof_path = None
            data_url = payload.pop("photoDataUrl", None)
            if data_url and "," in data_url:
                try:
                    b64 = data_url.split(",", 1)[1]
                    proof_path = str(PROOF_DIR / f"{payload['taskId']}.jpg")
                    Path(proof_path).write_bytes(base64.b64decode(b64))
                except Exception:
                    proof_path = None
            if proof_path:
                payload["proofPath"] = proof_path
            env = handlers.resolve_task(payload, STORE, MEMORY)
            return self._json(200 if env.get("ok") else 409, env)

        if u.path == "/api/claim":
            t = STORE.claim(payload.get("taskId"), payload.get("by", "worker"))
            return self._json(200, t or {"error": "not found"})

        self._json(404, {"error": "not found"})


def serve(port: int | None = None, host: str | None = None, node: str | None = None) -> None:
    port = port if port is not None else get_port()
    host = host if host is not None else get_host()
    node = node if node is not None else get_node()

    httpd = ThreadingHTTPServer((host, port), Handler)
    lan_url = get_lan_url(port)
    worker_url = f"{lan_url}/?node={node}"

    print(f"[urirun-human] surface  http://localhost:{port}/?node={node}")
    print(f"[urirun-human] LAN URL  {worker_url}")
    print(f"[urirun-human] QR code  {lan_url}/api/qr?node={node}")
    print(f"[urirun-human] DB       {STORE.path}")
    print()
    _print_qr(worker_url)
    print()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[urirun-human] stopped")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=None, help="override URIRUN_HUMAN_PORT")
    ap.add_argument("--host", default=None, help="override URIRUN_HUMAN_HOST")
    ap.add_argument("--node", default=None, help="override URIRUN_HUMAN_NODE")
    args = ap.parse_args()
    serve(args.port, args.host, args.node)

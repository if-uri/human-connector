"""surface.py — the human edge of the bridge.

A tiny stdlib HTTP server that shows pending `human://` tasks to a person and
lets them claim, complete (with a photo proof + note) or decline. This is the
"serwis usługowy dla ludzi": where the LLM/host's URI steps become something a
warehouse worker, field tech or operator actually taps on a phone.

Interaction modes (set by task.kind):
  action / safety / judgement / grant  → Done ✓ / Decline buttons
  choice   → one button per option (payload.options list)
  form     → structured input fields  (payload.fields list)

Live updates via SSE — /api/stream — no polling needed.

Shares the SAME TaskStore as the connector so a tap here is identical to any
other `human://{node}/task/resolve` call. No framework, no build step.

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


PAGE = r"""<!doctype html>
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
  header b { font-size:17px; }
  .node-pill { margin-left:auto; font-size:13px; color:#8b97a6;
               background:#161b22; padding:4px 10px; border-radius:999px; }
  .live-dot { width:8px; height:8px; border-radius:50%; background:#2da44e;
              display:inline-block; margin-right:4px;
              animation: pulse 2s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.3} }
  main { padding:16px; max-width:680px; margin:0 auto; }
  .empty { text-align:center; color:#6e7a89; padding:40px 20px; }
  .qr-card { background:#161b22; border:1px solid #222b36; border-radius:14px;
             padding:20px; margin:0 0 20px; text-align:center; }
  .qr-card img { width:180px; height:180px; border-radius:8px; background:#fff; }
  .qr-url { font-size:13px; color:#8b97a6; margin:10px 0 0; word-break:break-all; }
  .qr-url a { color:#58a6ff; }
  .card { background:#161b22; border:1px solid #222b36; border-radius:14px;
          padding:16px; margin:0 0 14px; transition:opacity .2s; }
  .card.kind-safety  { border-color:#e3b341; }
  .card.kind-grant   { border-color:#388bfd; }
  .card.kind-choice  { border-color:#bc8cff; }
  .card.kind-form    { border-color:#f78166; }
  .card h3 { margin:0 0 6px; font-size:17px; }
  .meta { display:flex; gap:8px; flex-wrap:wrap; margin:0 0 10px; }
  .tag { font-size:12px; padding:3px 9px; border-radius:999px; background:#1f2630; color:#aeb9c7; }
  .tag.scope-per-env  { background:#13312a; color:#7ee2b8; }
  .tag.scope-per-instance { background:#33271a; color:#f0c386; }
  .tag.kind-safety    { background:#3d2f00; color:#e3b341; }
  .tag.kind-choice    { background:#2a1f42; color:#bc8cff; }
  .tag.kind-form      { background:#3d1a18; color:#f78166; }
  .instr { color:#c2cbd6; margin:0 0 14px; white-space:pre-wrap; }
  .deadline { font-size:13px; color:#e3b341; margin:-8px 0 10px; }
  /* action / safety / judgement / grant — Done + Decline */
  .row { display:flex; gap:10px; flex-wrap:wrap; }
  button { flex:1; min-width:120px; border:0; border-radius:11px; padding:14px;
           font-size:16px; font-weight:600; cursor:pointer; transition:filter .1s; }
  button:active { filter:brightness(.85); }
  .btn-done    { background:#2da44e; color:#fff; }
  .btn-decline { background:#30363d; color:#e6edf3; }
  /* choice — full-width buttons per option */
  .choices { display:flex; flex-direction:column; gap:8px; margin:0 0 10px; }
  .btn-choice { background:#21262d; color:#e6edf3; border:1px solid #388bfd;
                border-radius:11px; padding:13px 16px; font-size:15px; font-weight:500;
                cursor:pointer; text-align:left; }
  .btn-choice:hover { background:#161b22; }
  /* form fields */
  .form-field { margin:0 0 12px; }
  .form-field label { display:block; font-size:13px; color:#8b97a6; margin-bottom:4px; }
  .form-field input[type=text],
  .form-field input[type=number],
  .form-field textarea {
    width:100%; padding:11px; border-radius:10px;
    border:1px solid #2a3340; background:#0e1116; color:#e6edf3; font:inherit; }
  .form-field input[type=file] { width:100%; padding:6px 0; color:#8b97a6; }
  .form-field .hint { font-size:12px; color:#56606e; margin-top:3px; }
  /* note + photo (shared) */
  textarea.note { width:100%; margin:0 0 10px; padding:11px; border-radius:10px;
                  border:1px solid #2a3340; background:#0e1116; color:#e6edf3; font:inherit; }
  .photo-ok { font-size:13px; color:#7ee2b8; margin:0 0 10px; }
  .foot { text-align:center; color:#56606e; font-size:12px; padding:20px; }
  a { color:#58a6ff; text-decoration:none; }
</style></head>
<body>
<header>
  <b>urirun</b> <span style="color:#6e7a89">human tasks</span>
  <span style="margin-left:8px;font-size:13px;color:#56606e">
    <span class="live-dot" id="dot"></span><span id="live-label">live</span>
  </span>
  <span class="node-pill" id="node-pill">node: —</span>
</header>
<main>
  <div id="qr-section" style="display:none" class="qr-card">
    <div style="font-size:13px;color:#8b97a6;margin-bottom:12px">Scan to open on phone</div>
    <img id="qr-img" src="" alt="QR">
    <div class="qr-url"><a id="qr-link" href="#"></a></div>
  </div>
  <div id="list"><div class="empty">Connecting…</div></div>
</main>
<div class="foot">
  <code>human://{node}/task/resolve</code> ·
  <a href="/api/tasks" target="_blank">tasks JSON</a>
</div>
<script>
const qs = new URLSearchParams(location.search);
const NODE = qs.get('node') || '';
document.getElementById('node-pill').textContent = 'node: ' + (NODE || 'all');

// ── QR card (desktop only) ───────────────────────────────────────────────────
if (window.innerWidth > 500) {
  fetch('/api/info').then(r=>r.json()).then(info=>{
    document.getElementById('qr-img').src  = info.qrUrl;
    document.getElementById('qr-link').href = info.workerUrl;
    document.getElementById('qr-link').textContent = info.workerUrl;
    document.getElementById('qr-section').style.display = 'block';
  }).catch(()=>{});
}

// ── Utilities ────────────────────────────────────────────────────────────────
function esc(s){ return (s||'').replace(/[&<>"]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
function badge(k){
  return ({grant:'🔑 grant',safety:'⚠ safety',judgement:'👁 judge',
           action:'✋ action',choice:'🔀 choice',form:'📋 form'}[k]||k);
}

const photos={}, fieldData={};

function pickPhoto(tid, fieldName){
  const fid = fieldName ? `fph-${tid}-${fieldName}` : `ph-${tid}`;
  const f = document.getElementById(fid).files[0]; if(!f) return;
  const rd = new FileReader();
  rd.onload = () => {
    photos[tid] = rd.result;
    const okEl = document.getElementById(fieldName ? `fpok-${tid}-${fieldName}` : `phok-${tid}`);
    if(okEl) okEl.textContent = '📷 photo attached';
    if(fieldName) {
      if(!fieldData[tid]) fieldData[tid]={};
      fieldData[tid][fieldName] = rd.result;
    }
  };
  rd.readAsDataURL(f);
}

function collectFormData(tid, fields){
  const fd={};
  for(const f of fields){
    const el = document.getElementById(`ff-${tid}-${f.name}`);
    if(el) fd[f.name] = el.value;
    if(fieldData[tid] && fieldData[tid][f.name]) fd[f.name] = fieldData[tid][f.name];
  }
  return fd;
}

async function resolve(tid, outcome, extra={}){
  const note = (document.getElementById('note-'+tid)||{}).value||'';
  const body = {taskId:tid, outcome, by:'worker', note, photoDataUrl:photos[tid]||null, ...extra};
  const card = document.getElementById('card-'+tid);
  if(card) card.style.opacity=.4;
  const r = await fetch('/api/resolve',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const data = await r.json();
  if(!data.ok){ if(card) card.style.opacity=1; alert(data.error||'error'); return; }
  render(currentTasks.filter(t=>t.id!==tid));
}

async function resolveChoice(tid, choice){
  await resolve(tid, 'done', {choice});
}

async function resolveForm(tid, fields){
  const formData = collectFormData(tid, fields);
  await resolve(tid, 'done', {formData});
}

// ── Card rendering ───────────────────────────────────────────────────────────
function buildCard(t){
  const el = document.createElement('div');
  el.className = `card kind-${t.kind}`;
  el.id = 'card-'+t.id;

  const deadlineHtml = t.deadline
    ? `<div class="deadline" id="dl-${t.id}"></div>` : '';

  let interactionHtml = '';

  if(t.kind === 'choice' && t.options && t.options.length){
    const btns = t.options.map(o=>
      `<button class="btn-choice" onclick="resolveChoice('${t.id}',${JSON.stringify(o)})">${esc(o)}</button>`
    ).join('');
    interactionHtml = `
      <p class="instr">${esc(t.instruction)}</p>
      <div class="choices">${btns}</div>
      <button class="btn-decline" style="width:100%;margin-top:4px" onclick="resolve('${t.id}','declined')">Cancel / Skip</button>`;

  } else if(t.kind === 'form' && t.fields && t.fields.length){
    const fieldHtmls = t.fields.map(f=>{
      const reqMark = f.required ? ' *':'';
      let input = '';
      if(f.type==='camera'||f.type==='photo'){
        input = `<input type="file" accept="image/*" capture="environment" id="fph-${t.id}-${f.name}" onchange="pickPhoto('${t.id}','${f.name}')">
                 <div class="photo-ok" id="fpok-${t.id}-${f.name}"></div>`;
      } else if(f.type==='number'){
        input = `<input type="number" id="ff-${t.id}-${f.name}" placeholder="${esc(f.hint||'')}">`;
      } else if(f.type==='textarea'){
        input = `<textarea rows="3" id="ff-${t.id}-${f.name}" placeholder="${esc(f.hint||'')}"></textarea>`;
      } else {
        input = `<input type="text" id="ff-${t.id}-${f.name}" placeholder="${esc(f.hint||'')}">`;
      }
      return `<div class="form-field">
        <label>${esc(f.label||f.name)}${reqMark}</label>
        ${input}
        ${f.hint&&f.type!=='number'?`<div class="hint">${esc(f.hint)}</div>`:''}
      </div>`;
    }).join('');
    const fieldsJson = JSON.stringify(t.fields).replace(/</g,'\\u003c');
    interactionHtml = `
      <p class="instr">${esc(t.instruction)}</p>
      ${fieldHtmls}
      <div class="row">
        <button class="btn-done" onclick="resolveForm('${t.id}',${fieldsJson})">Submit ✓</button>
        <button class="btn-decline" onclick="resolve('${t.id}','declined')">Decline</button>
      </div>`;

  } else {
    // action / safety / judgement / grant
    const photoHtml = (t.kind==='action'||t.kind==='safety')
      ? `<input type="file" accept="image/*" capture="environment" id="ph-${t.id}" onchange="pickPhoto('${t.id}')">
         <div class="photo-ok" id="phok-${t.id}"></div>` : '';
    interactionHtml = `
      <p class="instr">${esc(t.instruction)}</p>
      <textarea class="note" id="note-${t.id}" rows="2" placeholder="Note (optional)"></textarea>
      ${photoHtml}
      <div class="row">
        <button class="btn-done" onclick="resolve('${t.id}','done')">Done ✓</button>
        <button class="btn-decline" onclick="resolve('${t.id}','declined')">Decline</button>
      </div>`;
  }

  el.innerHTML = `
    <h3>${esc(t.title)}</h3>
    <div class="meta">
      <span class="tag kind-${t.kind}">${badge(t.kind)}</span>
      <span class="tag scope-${t.scope}">${t.scope}</span>
      <span class="tag">env: ${esc(t.env)}</span>
      <span class="tag">${t.status}</span>
    </div>
    ${deadlineHtml}
    ${interactionHtml}`;

  // Deadline countdown
  if(t.deadline){
    const update=()=>{
      const el2=document.getElementById(`dl-${t.id}`); if(!el2) return;
      const rem = Math.ceil(t.deadline - Date.now()/1000);
      if(rem<=0){el2.textContent='⏰ Overdue — escalate'; return;}
      const m=Math.floor(rem/60), s=rem%60;
      el2.textContent=`⏱ ${m}:${String(s).padStart(2,'0')} remaining`;
    };
    update(); setInterval(update, 1000);
  }

  return el;
}

let currentTasks = [];

function render(tasks){
  currentTasks = tasks;
  const root = document.getElementById('list');
  if(!tasks.length){
    root.innerHTML='<div class="empty">✓ No pending tasks.<br>Waiting for the host…</div>';
    return;
  }
  // Preserve existing cards where possible to avoid re-render flicker
  const existing = new Set([...root.querySelectorAll('.card')].map(c=>c.id.replace('card-','')));
  const incoming = new Set(tasks.map(t=>t.id));
  // Remove cards no longer in list
  for(const id of existing) if(!incoming.has(id)){
    const c = document.getElementById('card-'+id); if(c) c.remove();
  }
  // Add new cards
  for(const t of tasks) if(!existing.has(t.id)){
    root.appendChild(buildCard(t));
  }
  if(!root.querySelector('.card')) root.innerHTML='<div class="empty">✓ No pending tasks.</div>';
}

// ── SSE live stream ──────────────────────────────────────────────────────────
let sse = null;
function connectSSE(){
  const url = '/api/stream' + (NODE ? '?node='+encodeURIComponent(NODE) : '');
  sse = new EventSource(url);

  sse.addEventListener('tasks', e=>{
    const tasks = JSON.parse(e.data);
    render(tasks);
  });

  sse.addEventListener('event', e=>{
    const ev = JSON.parse(e.data);
    // On any task change, refresh the task list
    fetch('/api/tasks'+(NODE?'?node='+encodeURIComponent(NODE):'')).then(r=>r.json()).then(render);
  });

  sse.onopen = () => {
    document.getElementById('dot').style.background='#2da44e';
    document.getElementById('live-label').textContent='live';
  };

  sse.onerror = () => {
    document.getElementById('dot').style.background='#da3633';
    document.getElementById('live-label').textContent='reconnecting…';
    sse.close();
    setTimeout(connectSSE, 3000);
  };
}
connectSSE();
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
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json")

    def log_message(self, *_):
        return

    def do_GET(self) -> None:
        u = urlparse(self.path)
        q = parse_qs(u.query)
        node = (q.get("node") or [None])[0]

        if u.path in ("/", "/t") or u.path.startswith("/t/"):
            self._send(200, PAGE.encode(), "text/html; charset=utf-8")

        elif u.path == "/api/tasks":
            self._json(200, STORE.list_open(node))

        elif u.path == "/api/events":
            since = int((q.get("since") or ["0"])[0])
            self._json(200, STORE.events_since(since, node))

        elif u.path == "/api/stream":
            # SSE — send current tasks immediately, then push events as they arrive
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            try:
                # Initial snapshot
                tasks = STORE.list_open(node)
                data = json.dumps(tasks, ensure_ascii=False)
                self.wfile.write(f"event: tasks\ndata: {data}\n\n".encode())
                self.wfile.flush()

                last_seq = STORE.latest_seq(node)
                while True:
                    time.sleep(0.5)
                    events = STORE.events_since(last_seq, node)
                    if events:
                        last_seq = events[-1]["seq"]
                        for ev in events:
                            evdata = json.dumps(ev, ensure_ascii=False)
                            self.wfile.write(f"event: event\ndata: {evdata}\n\n".encode())
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

        elif u.path == "/api/qr":
            target = (q.get("url") or [None])[0]
            if not target:
                n = node or get_node()
                port = self.server.server_address[1]
                target = f"{get_lan_url(port)}/?node={n}"
            png = _qr_png(target)
            if png:
                self._send(200, png, "image/png")
            else:
                self._json(503, {"error": "qrcode package not available", "url": target})

        elif u.path == "/api/info":
            port = self.server.server_address[1]
            n = get_node()
            lan = get_lan_url(port)
            self._json(200, {
                "lanUrl": lan,
                "workerUrl": f"{lan}/?node={n}",
                "qrUrl": f"{lan}/api/qr?node={n}",
                "node": n,
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
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--host", default=None)
    ap.add_argument("--node", default=None)
    args = ap.parse_args()
    serve(args.port, args.host, args.node)

"""Minimal local web server showing a concept network visualization."""

from __future__ import annotations

import json
import os
import threading
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv

from .storage import ConceptStore


load_dotenv()
_init_state: dict[str, Any] = {"phase": "idle", "current_file": "", "done": 0, "total": 0}
_init_lock = threading.Lock()


def get_init_state() -> dict[str, Any]:
    with _init_lock:
        return dict(_init_state)


def set_init_state(phase: str, **kwargs: Any) -> None:
    with _init_lock:
        _init_state["phase"] = phase
        _init_state.update(kwargs)


# Red/green MCP connection button: green while a coding agent has recently
# called an MCP tool in this process, red otherwise.
AGENT_IDLE_TIMEOUT_SECONDS = 300.0
_connection: dict[str, Any] = {"last_seen": None}
_connection_lock = threading.Lock()


def mark_agent_seen(now: float | None = None) -> None:
    """Record that a coding agent just called an MCP tool."""

    if now is None:
        now = time.time()
    with _connection_lock:
        _connection["last_seen"] = float(now)


def get_connection_state(
    now: float | None = None,
    max_idle_seconds: float = AGENT_IDLE_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Return the button state; connected only while agent calls are recent."""

    if now is None:
        now = time.time()
    with _connection_lock:
        last_seen = _connection["last_seen"]
    if last_seen is None:
        return {"connected": False, "idle_seconds": None}
    idle_seconds = max(0.0, float(now) - float(last_seen))
    return {
        "connected": idle_seconds <= max_idle_seconds,
        "idle_seconds": round(idle_seconds, 1),
    }


# Latest search event, polled by the page so hit nodes turn blue.
_search_event: dict[str, Any] = {"seq": 0, "query": "", "direct": [], "related": []}
_search_lock = threading.Lock()


def record_search_event(
    query: str, direct_ids: list[str], related_ids: list[str]
) -> int:
    """Store one search's hit sets and bump the event sequence."""

    with _search_lock:
        _search_event["seq"] = int(_search_event["seq"]) + 1
        _search_event["query"] = query
        _search_event["direct"] = list(direct_ids)
        _search_event["related"] = list(related_ids)
        return int(_search_event["seq"])


def get_search_event(since_seq: int = 0) -> dict[str, Any]:
    """Return the latest event when it is newer than ``since_seq``."""

    with _search_lock:
        event = dict(_search_event)
    seq = int(event["seq"])
    if seq <= int(since_seq):
        return {"seq": seq, "fresh": False}
    event["fresh"] = True
    return event


def run_scan_with_progress(path: str) -> dict[str, Any]:
    """Delegate page-triggered scans to the shared MCP scan implementation."""

    from .mcp_server import scan_codebase

    # A scan started from this page is not agent activity; it must not
    # turn the MCP connection button green.
    return scan_codebase(path, open_browser=False, from_agent=False)

_HTML = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>Concept Memory</title>
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family: system-ui, sans-serif; background:#1a1a2e; color:#e0e0e0; overflow:hidden; }
#header { position:fixed; top:0; left:0; right:0; height:48px; background:#16213e; display:flex; align-items:center; padding:0 16px; gap:12px; z-index:10; }
#mcp-status { padding:4px 12px; border-radius:14px; border:none; font-size:12px; font-weight:600; color:#fff; letter-spacing:1px; }
#mcp-status.on { background:#16a34a; }
#mcp-status.off { background:#dc2626; }
#title { font-size:14px; font-weight:600; }
#count { font-size:12px; color:#94a3b8; }
#legend { display:flex; gap:10px; margin-left:auto; font-size:10px; color:#94a3b8; align-items:center; }
#legend i { display:inline-block; width:8px; height:8px; border-radius:2px; margin-right:3px; vertical-align:-1px; }
canvas { display:block; }
#tooltip { position:fixed; pointer-events:none; background:#0f3460; border:1px solid #16537e; border-radius:6px; padding:8px 10px; font-size:12px; max-width:280px; display:none; z-index:20; }
#tooltip .name { font-weight:600; margin-bottom:4px; }
#tooltip .def { color:#94a3b8; }
</style>
</head>
<body>
<div id="header">
  <button id="mcp-status" class="off">MCP 未连接</button>
  <div id="title">Concept Memory</div>
  <div id="count"></div>
  <div id="legend">
    <span><i style="background:rgba(148,163,184,0.4)"></i>同文件概念</span>
    <span><i style="background:#cbd5e1"></i>真实共同使用（越粗次数越多）</span>
    <span><i style="background:#3b82f6;border-radius:50%"></i>检索命中</span>
    <span><i style="background:#93c5fd;border-radius:50%"></i>相关扩散</span>
  </div>
</div>
<canvas id="cv"></canvas>
<div id="tooltip"><div class="name"></div><div class="def"></div></div>
<script>
const cv = document.getElementById('cv');
const ctx = cv.getContext('2d');
const tooltip = document.getElementById('tooltip');
const mcpBtn = document.getElementById('mcp-status');
let nodes = [], edges = [], hovered = null;
let W, H;

// red/green MCP connection button, polled even while the scan is running
async function pollConnection() {
  try {
    const s = await (await fetch('/api/connection')).json();
    mcpBtn.className = s.connected ? 'on' : 'off';
    mcpBtn.textContent = s.connected ? 'MCP 已连接' : 'MCP 未连接';
  } catch (e) {
    mcpBtn.className = 'off';
    mcpBtn.textContent = 'MCP 未连接';
  }
}
pollConnection();
setInterval(pollConnection, 2000);

// latest search event recolors hit nodes blue until the next search
let highlight = {seq: 0, direct: new Set(), all: new Set()};
async function pollSearchEvents() {
  try {
    const s = await (await fetch('/api/search-events?since=' + highlight.seq)).json();
    if (!s.fresh) return;
    highlight.seq = s.seq;
    highlight.direct = new Set(s.direct || []);
    highlight.all = new Set([...(s.direct || []), ...(s.related || [])]);
  } catch (e) {}
}
setInterval(pollSearchEvents, 1000);

function resize() {
  W = cv.width = window.innerWidth;
  H = cv.height = window.innerHeight;
}
window.addEventListener('resize', resize);
resize();

async function boot() {
  const state = await (await fetch('/api/init-state')).json();
  if (state.phase === 'idle') {
    await fetch('/api/scan?path=');
  }
  while (true) {
    const s = await (await fetch('/api/init-state')).json();
    if (s.phase === 'error') {
      document.getElementById('count').textContent = '初始化失败：' + (s.message || '未知错误');
      return;
    }
    if (s.phase === 'scanning') {
      document.getElementById('count').textContent =
        '初始化中... ' + (s.done||0) + ' / ' + (s.total||'?') + ' 个文件'
        + (s.current_file ? '（' + s.current_file + '）' : '');
      await new Promise(r=>setTimeout(r, 500));
      continue;
    }
    break;
  }
  const data = await (await fetch('/api/concepts')).json();
  const cards = data.cards;
  document.getElementById('count').textContent = cards.length + ' concepts';
  cards.forEach((c,i)=>{
    const cx = W/2 + (Math.random()-0.5)*W*0.6;
    const cy = H/2 + (Math.random()-0.5)*H*0.6;
    nodes.push({x:cx, y:cy, vx:0, vy:0, r:8, card:c, fixed:false});
  });
  const idToNode = {};
  nodes.forEach((n,i)=>{ idToNode[n.card.id] = i; });
  // same-file concepts are always connected (initial state)
  const byFile = {};
  cards.forEach((c,i)=>{
    (byFile[c.location.file_path] = byFile[c.location.file_path] || []).push(i);
  });
  Object.values(byFile).forEach(group=>{
    for (let i=0;i<group.length-1;i++)
      edges.push({a:group[i], b:group[i+1], usage:0});
  });
  // real-usage edges: only pairs actually fetched together by the agent
  const usage = await (await fetch('/api/usage-edges')).json();
  (usage.edges||[]).forEach(e=>{
    const i = idToNode[e.source_id], j = idToNode[e.target_id];
    if (i===undefined || j===undefined || i===j) return;
    edges.push({a:i, b:j, usage:e.count||1});
  });
  animate();
}
boot();

function animate() {
  step();
  draw();
  requestAnimationFrame(animate);
}

function step() {
  // repulsion
  for (let i=0;i<nodes.length;i++)
    for (let j=i+1;j<nodes.length;j++) {
      const a=nodes[i], b=nodes[j];
      let dx=b.x-a.x, dy=b.y-a.y;
      let d2=dx*dx+dy*dy;
      if (d2<1) d2=1;
      const f=800/d2;
      const d=Math.sqrt(d2);
      dx/=d; dy/=d;
      a.vx-=dx*f; a.vy-=dy*f;
      b.vx+=dx*f; b.vy+=dy*f;
    }
  // edge attraction
  edges.forEach(e=>{
    const a=nodes[e.a], b=nodes[e.b];
    if (!a || !b) return;
    const dx=b.x-a.x, dy=b.y-a.y;
    const d=Math.sqrt(dx*dx+dy*dy)||1;
    const f=0.02*(d-80);
    a.vx+=dx/d*f; a.vy+=dy/d*f;
    b.vx-=dx/d*f; b.vy-=dy/d*f;
  });
  // centering
  nodes.forEach(n=>{
    n.vx += (W/2-n.x)*0.002;
    n.vy += (H/2-n.y)*0.002;
    n.vx *= 0.85; n.vy *= 0.85;
    n.x += n.vx; n.y += n.vy;
    // keep in bounds
    n.x = Math.max(n.r+10, Math.min(W-n.r-10, n.x));
    n.y = Math.max(n.r+58, Math.min(H-n.r-10, n.y));
  });
}

function draw() {
  ctx.clearRect(0,0,W,H);
  const hasHits = highlight.all.size > 0;
  edges.forEach(e=>{
    const a=nodes[e.a], b=nodes[e.b];
    if (!a || !b) return;
    // same-file placeholders are faint; usage edges are lighter gray and
    // thicken with co-use count, capped so old links stay readable
    if (hasHits && highlight.all.has(a.card.id) && highlight.all.has(b.card.id)) {
      // links inside the current hit set light up for this search
      ctx.strokeStyle = 'rgba(147,197,253,0.8)';
      ctx.lineWidth = 2;
    } else if (e.usage > 0) {
      ctx.strokeStyle = '#cbd5e1';
      ctx.lineWidth = 1 + Math.min(e.usage, 8) * 0.9;
    } else {
      ctx.strokeStyle = 'rgba(148,163,184,0.15)';
      ctx.lineWidth = 1;
    }
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);
    ctx.stroke();
  });
  nodes.forEach(n=>{
    ctx.beginPath();
    ctx.arc(n.x, n.y, n.r, 0, Math.PI*2);
    let fill = 'rgba(148,163,184,0.5)';
    if (highlight.direct.has(n.card.id)) fill = '#3b82f6';
    else if (highlight.all.has(n.card.id)) fill = '#93c5fd';
    if (n===hovered) fill = '#60a5fa';
    ctx.fillStyle = fill;
    ctx.fill();
    if (highlight.direct.has(n.card.id) && n!==hovered) {
      ctx.strokeStyle = '#1e40af';
      ctx.lineWidth = 2;
      ctx.stroke();
    }
    if (n===hovered) {
      ctx.strokeStyle = '#93c5fd';
      ctx.lineWidth = 2;
      ctx.stroke();
    }
  });
}

cv.addEventListener('mousemove', e=>{
  let found = null;
  for (const n of nodes) {
    const dx = e.clientX-n.x, dy = e.clientY-n.y;
    if (dx*dx+dy*dy < (n.r+4)*(n.r+4)) { found=n; break; }
  }
  hovered = found;
  if (found) {
    tooltip.style.display='block';
    tooltip.style.left=(e.clientX+12)+'px';
    tooltip.style.top=(e.clientY+12)+'px';
    tooltip.querySelector('.name').textContent = found.card.name;
    tooltip.querySelector('.def').textContent = found.card.definition;
  } else {
    tooltip.style.display='none';
  }
});

cv.addEventListener('click', ()=>{
  if (hovered) {
    const c = hovered.card;
    const idx = nodes.indexOf(hovered);
    const names = {};
    nodes.forEach((n,i)=>{ names[i] = n.card.name; });
    const linked = edges
      .filter(e=>(e.a===idx||e.b===idx) && e.usage>0)
      .map(e=>'· ' + names[e.a===idx ? e.b : e.a] + '（共同使用 ' + e.usage + ' 次）');
    alert(
      '概念: ' + c.name + '\n\n' +
      '定义: ' + c.definition + '\n\n' +
      '文件: ' + c.location.file_path + '\n' +
      '行号: ' + c.location.start_line + '-' + c.location.end_line + '\n' +
      'card_id: ' + c.id +
      (linked.length ? '\n\n真实共同使用:\n' + linked.join('\n') : '')
    );
  }
});
</script>
</body>
</html>"""


class _Handler(SimpleHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/" or parsed.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(_HTML.encode("utf-8"))
        elif parsed.path == "/api/concepts":
            self._serve_concepts()
        elif parsed.path == "/api/usage-edges":
            self._serve_usage_edges()
        elif parsed.path == "/api/init-state":
            payload = json.dumps(get_init_state(), ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        elif parsed.path == "/api/connection":
            payload = json.dumps(get_connection_state(), ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        elif parsed.path == "/api/search-events":
            query = parse_qs(parsed.query)
            try:
                since = int(query.get("since", ["0"])[0])
            except ValueError:
                since = 0
            payload = json.dumps(get_search_event(since), ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        elif parsed.path == "/api/scan":
            query = parse_qs(parsed.query)
            target = query.get("path", [""])[0].strip()
            scan_root = getattr(self.server, "scan_root", "") or os.getcwd()
            if not target:
                target = scan_root
            elif not os.path.isabs(target):
                target = os.path.join(scan_root, target)
            thread = threading.Thread(
                target=run_scan_with_progress, args=(target,), daemon=True
            )
            thread.start()
            payload = json.dumps({"status": "started", "path": target}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        else:
            self.send_error(404)

    def _serve_concepts(self) -> None:
        db = self.server.database_path  # type: ignore[attr-defined]
        cards: list[dict[str, Any]] = []
        if Path(db).exists():
            store = ConceptStore(db)
            store.open()
            try:
                cards = [card.to_dict() for card in store.all_cards()]
            finally:
                store.close()
        payload = json.dumps({"cards": cards}, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _serve_usage_edges(self) -> None:
        """Serve recorded real-usage edges (co-use counts per card pair)."""

        db = self.server.database_path  # type: ignore[attr-defined]
        edges: list[dict[str, Any]] = []
        if Path(db).exists():
            try:
                import sqlite3

                connection = sqlite3.connect(db)
                connection.row_factory = sqlite3.Row
                try:
                    rows = connection.execute(
                        "SELECT source_id, target_id, count FROM concept_usage_edges"
                    ).fetchall()
                except sqlite3.OperationalError:
                    rows = []
                finally:
                    connection.close()
                edges = [dict(row) for row in rows]
            except Exception:
                edges = []
        payload = json.dumps({"edges": edges}, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: Any) -> None:
        pass


def start_web_server(
    database_path: str,
    port: int = 8080,
    scan_root: str = "",
) -> HTTPServer:
    """Start the web visualization server (non-blocking), trying nearby ports."""

    server: HTTPServer | None = None
    for candidate in range(port, port + 10):
        try:
            server = HTTPServer(("127.0.0.1", candidate), _Handler)
            break
        except OSError:
            continue
    if server is None:
        raise OSError(f"no free port in {port}-{port + 9} for the concept web UI")
    server.database_path = database_path  # type: ignore[attr-defined]
    server.scan_root = scan_root  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def main() -> None:
    import argparse
    import webbrowser

    parser = argparse.ArgumentParser(description="Concept Memory web visualization")
    parser.add_argument("--database", default=".concept-memory/concepts.sqlite")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    start_web_server(args.database, args.port)
    url = f"http://127.0.0.1:{args.port}"
    print(f"Concept Memory visualization running at {url}")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        import signal
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        import time
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

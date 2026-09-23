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
<title>Concept Memory · 概念网络</title>
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body {
  font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif;
  background: radial-gradient(1300px 900px at 50% 38%, #1c2542 0%, #101632 52%, #090e1f 100%);
  color:#e2e8f0; overflow:hidden; height:100vh;
}
canvas { display:block; cursor:grab; }
canvas.dragging { cursor:grabbing; }
canvas.onnode { cursor:pointer; }
#header {
  position:fixed; top:0; left:0; right:0; height:56px;
  display:flex; align-items:center; padding:0 20px; gap:14px; z-index:10;
  background:rgba(13,19,38,0.62); backdrop-filter:blur(14px); -webkit-backdrop-filter:blur(14px);
  border-bottom:1px solid rgba(148,163,184,0.14);
}
#title {
  font-size:15px; font-weight:700; letter-spacing:2px;
  background:linear-gradient(90deg,#e2e8f0 20%,#93c5fd 80%);
  -webkit-background-clip:text; background-clip:text; color:transparent;
}
#count {
  font-size:11px; color:#8ea0c0; padding:3px 10px; border-radius:10px;
  background:rgba(148,163,184,0.10); border:1px solid rgba(148,163,184,0.16);
}
#hint { font-size:11px; color:#5f7292; }
#mcp-status {
  display:flex; align-items:center; gap:7px; padding:5px 14px; border-radius:16px;
  border:1px solid transparent; font-size:12px; font-weight:600; color:#fff;
  letter-spacing:1px; cursor:default; transition:all .3s;
}
#mcp-status::before {
  content:""; width:8px; height:8px; border-radius:50%; background:#fff;
  box-shadow:0 0 8px rgba(255,255,255,0.9);
}
#mcp-status.on  { background:rgba(22,163,74,0.85);  box-shadow:0 0 18px rgba(34,197,94,0.35); }
#mcp-status.off { background:rgba(220,38,38,0.75);  box-shadow:0 0 14px rgba(239,68,68,0.25); }
#legend { display:flex; gap:9px; margin-left:auto; margin-right:2px; font-size:10px; color:#8ea0c0; align-items:center; flex-shrink:1; }
#legend span { display:flex; align-items:center; gap:4px; white-space:nowrap; }
#legend i { display:inline-block; width:12px; height:0; border-top:2px solid; border-radius:2px; }
#legend i.dot { width:8px; height:8px; border:none; border-radius:50%; }
#tooltip {
  position:fixed; pointer-events:none; z-index:30; display:none; max-width:300px;
  background:rgba(15,23,42,0.88); backdrop-filter:blur(10px); -webkit-backdrop-filter:blur(10px);
  border:1px solid rgba(148,163,184,0.25); border-radius:10px; padding:10px 12px;
  box-shadow:0 10px 30px rgba(0,0,0,0.45);
}
#tooltip .name { font-weight:700; font-size:13px; margin-bottom:4px; color:#e2e8f0; }
#tooltip .def { font-size:11px; line-height:1.55; color:#9fb0cd; }
#panel {
  position:fixed; top:72px; right:16px; width:330px; max-height:calc(100vh - 104px);
  overflow-y:auto; z-index:20; padding:18px 18px 14px;
  background:rgba(15,23,42,0.88); backdrop-filter:blur(14px); -webkit-backdrop-filter:blur(14px);
  border:1px solid rgba(148,163,184,0.22); border-radius:14px;
  box-shadow:0 16px 44px rgba(0,0,0,0.5);
  transform:translateX(380px); opacity:0; transition:transform .28s ease, opacity .28s ease;
}
#panel.open { transform:none; opacity:1; }
#panel-close {
  position:absolute; top:8px; right:10px; border:none; background:none; color:#8ea0c0;
  font-size:18px; cursor:pointer; line-height:1; padding:4px;
}
#panel-close:hover { color:#e2e8f0; }
#panel h2 { font-size:16px; font-weight:700; margin:2px 26px 8px 0; color:#e2e8f0; }
#panel .def { font-size:12px; line-height:1.7; color:#b6c4dc; margin-bottom:12px; }
#panel .meta { font-size:11px; color:#8ea0c0; border-top:1px dashed rgba(148,163,184,0.2); padding-top:10px; }
#panel .meta div { margin:3px 0; word-break:break-all; }
#panel .meta b { color:#b6c4dc; font-weight:600; margin-right:6px; }
#panel .links-title { font-size:11px; color:#8ea0c0; margin:12px 0 6px; }
#panel .link-row {
  font-size:11px; color:#c4d2ea; padding:5px 8px; margin:4px 0; border-radius:8px;
  background:rgba(96,165,250,0.08); border:1px solid rgba(96,165,250,0.18);
}
#panel .link-row em { font-style:normal; color:#7db6fc; float:right; }
#scan-overlay {
  position:fixed; inset:0; z-index:40; display:none; align-items:center; justify-content:center;
  background:rgba(9,14,31,0.55); backdrop-filter:blur(4px); -webkit-backdrop-filter:blur(4px);
}
#scan-overlay .card {
  width:380px; padding:26px 30px; text-align:center; border-radius:16px;
  background:rgba(15,23,42,0.9); border:1px solid rgba(148,163,184,0.22);
  box-shadow:0 20px 60px rgba(0,0,0,0.55);
}
#scan-overlay .ring {
  width:44px; height:44px; margin:0 auto 16px; border-radius:50%;
  border:3px solid rgba(96,165,250,0.18); border-top-color:#60a5fa;
  animation:spin 0.9s linear infinite;
}
@keyframes spin { to { transform:rotate(360deg); } }
#scan-text { font-size:13px; color:#c4d2ea; margin-bottom:14px; }
#scan-sub { font-size:11px; color:#8ea0c0; margin-top:8px; min-height:14px;
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.bar { height:6px; border-radius:3px; background:rgba(148,163,184,0.15); overflow:hidden; }
.bar i { display:block; height:100%; width:0%; border-radius:3px;
  background:linear-gradient(90deg,#3b82f6,#7dd3fc); transition:width .4s ease;
  box-shadow:0 0 12px rgba(96,165,250,0.6); }
</style>
</head>
<body>
<div id="header">
  <div id="title">CONCEPT MEMORY</div>
  <button id="mcp-status" class="off">MCP 未连接</button>
  <div id="count"></div>
  <div id="hint">拖拽转动 · 滚轮缩放 · 点击节点看卡片</div>
  <div id="legend">
    <span><i class="dot" style="background:#cbd5e1"></i>概念</span>
    <span><i style="border-color:rgba(148,163,184,0.3)"></i>同文件</span>
    <span><i style="border-color:#dbe4f0;border-top-width:4px"></i>真实共同使用</span>
    <span><i class="dot" style="background:#3b82f6;box-shadow:0 0 6px #3b82f6"></i>检索命中</span>
    <span><i class="dot" style="background:#93c5fd"></i>相关扩散</span>
  </div>
</div>
<canvas id="cv"></canvas>
<div id="tooltip"><div class="name"></div><div class="def"></div></div>
<aside id="panel">
  <button id="panel-close" title="关闭">×</button>
  <h2 id="p-name"></h2>
  <div class="def" id="p-def"></div>
  <div class="meta" id="p-meta"></div>
  <div class="links-title" id="p-links-title" style="display:none">真实共同使用</div>
  <div id="p-links"></div>
</aside>
<div id="scan-overlay"><div class="card">
  <div class="ring"></div>
  <div id="scan-text">正在初始化概念网络…</div>
  <div class="bar"><i id="scan-bar"></i></div>
  <div id="scan-sub"></div>
</div></div>
<script>
const cv = document.getElementById('cv');
const ctx = cv.getContext('2d');
const tooltip = document.getElementById('tooltip');
const mcpBtn = document.getElementById('mcp-status');
const panel = document.getElementById('panel');
let nodes = [], edges = [], hovered = null;
let W, H, dpr = 1, bootAt = 0;

// 3D ball state: rotation angles, spin velocity, zoom
const FOV = 1350;
let rotX = -0.28, rotY = 0.6, velX = 0, velY = 0, zoom = 1;
let dragging = false, dragMoved = 0, lastMX = 0, lastMY = 0;

function resize() {
  dpr = window.devicePixelRatio || 1;
  W = window.innerWidth; H = window.innerHeight;
  cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
  cv.style.width = W + 'px'; cv.style.height = H + 'px';
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}
window.addEventListener('resize', resize);
resize();

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

let highlight = {seq: 0, direct: new Set(), all: new Set(), at: 0};
async function pollSearchEvents() {
  try {
    const s = await (await fetch('/api/search-events?since=' + highlight.seq)).json();
    if (!s.fresh) return;
    highlight.seq = s.seq;
    highlight.direct = new Set(s.direct || []);
    highlight.all = new Set([...(s.direct || []), ...(s.related || [])]);
    highlight.at = performance.now();
    closePanel();
  } catch (e) {}
}
setInterval(pollSearchEvents, 1000);

async function boot() {
  const overlay = document.getElementById('scan-overlay');
  const scanText = document.getElementById('scan-text');
  const scanBar = document.getElementById('scan-bar');
  const scanSub = document.getElementById('scan-sub');
  const state = await (await fetch('/api/init-state')).json();
  if (state.phase === 'idle') {
    await fetch('/api/scan?path=');
  }
  if (state.phase !== 'done') {
    overlay.style.display = 'flex';
    while (true) {
      const s = await (await fetch('/api/init-state')).json();
      if (s.phase === 'error') {
        overlay.style.display = 'none';
        document.getElementById('count').textContent = '初始化失败：' + (s.message || '未知错误');
        return;
      }
      if (s.phase === 'scanning') {
        const total = s.total || 0, done = s.done || 0;
        scanBar.style.width = (total ? Math.round(done * 100 / total) : 5) + '%';
        scanText.textContent = '正在初始化概念网络… ' + done + ' / ' + (total || '?') + ' 个文件';
        scanSub.textContent = s.current_file || '';
        await new Promise(r=>setTimeout(r, 400));
        continue;
      }
      break;
    }
    overlay.style.display = 'none';
  }
  const data = await (await fetch('/api/concepts')).json();
  const cards = data.cards;
  document.getElementById('count').textContent = cards.length + ' 个概念';
  // seed nodes inside a sphere so the cloud starts ball-shaped
  const R0 = Math.min(W, H) * 0.20;
  cards.forEach((c,i)=>{
    const theta = Math.random() * Math.PI * 2;
    const phi = Math.acos(2 * Math.random() - 1);
    const rad = R0 * (0.35 + 0.65 * Math.cbrt(Math.random()));
    nodes.push({
      x: rad * Math.sin(phi) * Math.cos(theta),
      y: rad * Math.sin(phi) * Math.sin(theta),
      z: rad * Math.cos(phi),
      vx:0, vy:0, vz:0, r:8, card:c, phase:(i * 0.618) % 1,
      sx:0, sy:0, sr:0, depth:0, scale:1
    });
  });
  const idToNode = {};
  nodes.forEach((n,i)=>{ idToNode[n.card.id] = i; });
  const byFile = {};
  cards.forEach((c,i)=>{
    (byFile[c.location.file_path] = byFile[c.location.file_path] || []).push(i);
  });
  Object.values(byFile).forEach(group=>{
    for (let i=1;i<group.length;i++)
      edges.push({a:group[0], b:group[i], usage:0});
  });
  const usage = await (await fetch('/api/usage-edges')).json();
  (usage.edges||[]).forEach(e=>{
    const i = idToNode[e.source_id], j = idToNode[e.target_id];
    if (i===undefined || j===undefined || i===j) return;
    edges.push({a:i, b:j, usage:e.count||1});
  });
  const degree = {};
  edges.forEach(e=>{ if (e.usage > 0) { degree[e.a]=(degree[e.a]||0)+1; degree[e.b]=(degree[e.b]||0)+1; } });
  nodes.forEach((n,i)=>{ n.r = 6.5 + Math.min(degree[i] || 0, 8) * 0.55; });
  bootAt = performance.now();
  requestAnimationFrame(animate);
}
boot();

function step() {
  const maxR = Math.min(W, H) * 0.31;
  for (let i=0;i<nodes.length;i++)
    for (let j=i+1;j<nodes.length;j++) {
      const a=nodes[i], b=nodes[j];
      let dx=b.x-a.x, dy=b.y-a.y, dz=b.z-a.z;
      let d2=dx*dx+dy*dy+dz*dz;
      if (d2<1) d2=1;
      const f=1700/d2;
      const d=Math.sqrt(d2);
      dx/=d; dy/=d; dz/=d;
      a.vx-=dx*f; a.vy-=dy*f; a.vz-=dz*f;
      b.vx+=dx*f; b.vy+=dy*f; b.vz+=dz*f;
      const minD = a.r + b.r + 20;
      if (d < minD) {
        const push = (minD - d) * 0.08;
        a.vx-=dx*push; a.vy-=dy*push; a.vz-=dz*push;
        b.vx+=dx*push; b.vy+=dy*push; b.vz+=dz*push;
      }
    }
  edges.forEach(e=>{
    const a=nodes[e.a], b=nodes[e.b];
    if (!a || !b) return;
    const dx=b.x-a.x, dy=b.y-a.y, dz=b.z-a.z;
    const d=Math.sqrt(dx*dx+dy*dy+dz*dz)||1;
    const rest = e.usage > 0 ? 125 : 88;
    const f=0.016*(d-rest);
    a.vx+=dx/d*f; a.vy+=dy/d*f; a.vz+=dz/d*f;
    b.vx-=dx/d*f; b.vy-=dy/d*f; b.vz-=dz/d*f;
  });
  nodes.forEach(n=>{
    // spherical gravity: everything folds toward the ball center
    n.vx += -n.x*0.0045; n.vy += -n.y*0.0045; n.vz += -n.z*0.0045;
    // a firm shell so the cloud stays a ball, not a pancake
    const d = Math.sqrt(n.x*n.x + n.y*n.y + n.z*n.z) || 1;
    if (d > maxR) {
      const pull = (d - maxR) * 0.03;
      n.vx -= n.x/d*pull; n.vy -= n.y/d*pull; n.vz -= n.z/d*pull;
    }
    n.vx *= 0.86; n.vy *= 0.86; n.vz *= 0.86;
    n.x += n.vx; n.y += n.vy; n.z += n.vz;
  });
}

function project(n, t) {
  // water ripple: the outer shell sways most, the core stays still
  const maxR = Math.min(W, H) * 0.31;
  const r3 = Math.sqrt(n.x*n.x + n.y*n.y + n.z*n.z) || 1;
  const amp = 9 * Math.pow(Math.min(1, r3/maxR), 2.2);
  const wave = Math.sin(r3 * 0.028 - t * 2.4) * amp;
  const k = (r3 + wave) / r3;
  const px = n.x * k, py = n.y * k, pz = n.z * k;
  const cy = Math.cos(rotY), sy = Math.sin(rotY);
  const x1 =  px*cy + pz*sy;
  const z1 = -px*sy + pz*cy;
  const cx = Math.cos(rotX), sx = Math.sin(rotX);
  const y1 =  py*cx - z1*sx;
  const z2 =  py*sx + z1*cx;
  const s = (FOV / (FOV - z2)) * zoom;
  n.sx = W/2 + x1*s;
  n.sy = H/2 + 26 + y1*s;
  n.sr = n.r * s;
  n.depth = z2;
  n.scale = s;
}

function edgePath(a, b) {
  ctx.beginPath();
  ctx.moveTo(a.sx, a.sy);
  ctx.lineTo(b.sx, b.sy);
}

function draw(now) {
  ctx.clearRect(0,0,W,H);
  const t = now/1000;
  const fadeIn = bootAt ? Math.min(1, (now-bootAt)/900) : 1;
  ctx.globalAlpha = fadeIn;
  const hasHits = highlight.all.size > 0;
  const maxR = Math.min(W, H) * 0.31;

  nodes.forEach(n => project(n, t));

  edges.forEach(e=>{
    const a=nodes[e.a], b=nodes[e.b];
    if (!a || !b) return;
    // far side of the ball fades into the background
    const depthFade = Math.max(0.25, Math.min(1, 1.25 - ((a.depth+b.depth)/2) / (maxR*1.6)));
    if (hasHits && highlight.all.has(a.card.id) && highlight.all.has(b.card.id)) {
      edgePath(a, b);
      ctx.strokeStyle = 'rgba(59,130,246,' + 0.16*depthFade + ')';
      ctx.lineWidth = 6;
      ctx.stroke();
      ctx.strokeStyle = 'rgba(147,197,253,' + 0.95*depthFade + ')';
      ctx.lineWidth = 1.8;
      ctx.setLineDash([7,7]);
      ctx.lineDashOffset = -now/36;
      ctx.stroke();
      ctx.setLineDash([]);
    } else if (e.usage > 0) {
      const w = (1 + Math.min(e.usage, 8) * 0.85) * (a.scale+b.scale)/2;
      edgePath(a, b);
      ctx.strokeStyle = 'rgba(203,213,225,' + 0.10*depthFade + ')';
      ctx.lineWidth = w * 2.8;
      ctx.stroke();
      ctx.strokeStyle = 'rgba(222,232,244,' + (0.30 + Math.min(e.usage,8)*0.06)*depthFade + ')';
      ctx.lineWidth = w;
      ctx.stroke();
    } else {
      edgePath(a, b);
      ctx.strokeStyle = 'rgba(148,163,184,' + 0.085*depthFade + ')';
      ctx.lineWidth = 1;
      ctx.stroke();
    }
  });

  // painter's order: far nodes first, near nodes last
  const order = nodes.map((n,i)=>i).sort((i,j)=>nodes[i].depth - nodes[j].depth);
  order.forEach(i=>{
    const n = nodes[i];
    const isDirect = highlight.direct.has(n.card.id);
    const isRelated = highlight.all.has(n.card.id);
    const isHover = n === hovered;
    const near = Math.max(0, Math.min(1, 0.5 + n.depth/(maxR*1.5)));   // 0 far, 1 near
    const scale = isHover ? 1.28 : 1;
    const r = n.sr * scale * (0.82 + 0.18*near);
    const depthFade = 0.38 + 0.62*near;

    const haloR = r * (isDirect ? 3.4 : 2.6);
    const halo = ctx.createRadialGradient(n.sx, n.sy, r*0.4, n.sx, n.sy, haloR);
    if (isDirect)      { halo.addColorStop(0,'rgba(96,165,250,' + 0.55*depthFade + ')'); halo.addColorStop(1,'rgba(96,165,250,0)'); }
    else if (isRelated){ halo.addColorStop(0,'rgba(147,197,253,' + 0.30*depthFade + ')'); halo.addColorStop(1,'rgba(147,197,253,0)'); }
    else if (isHover)  { halo.addColorStop(0,'rgba(148,163,184,' + 0.40*depthFade + ')'); halo.addColorStop(1,'rgba(148,163,184,0)'); }
    else               { halo.addColorStop(0,'rgba(148,163,184,' + 0.16*depthFade + ')'); halo.addColorStop(1,'rgba(148,163,184,0)'); }
    ctx.fillStyle = halo;
    ctx.beginPath(); ctx.arc(n.sx, n.sy, haloR, 0, Math.PI*2); ctx.fill();

    if (isDirect && highlight.at) {
      const age = (now - highlight.at) / 1000;
      if (age < 6) {
        const p = (t*0.9 + n.phase) % 1;
        ctx.strokeStyle = 'rgba(96,165,250,' + (0.5 * (1-p) * Math.max(0, 1-age/6) * depthFade) + ')';
        ctx.lineWidth = 1.5;
        ctx.beginPath(); ctx.arc(n.sx, n.sy, r + 4 + p*20, 0, Math.PI*2); ctx.stroke();
      }
    }

    const body = ctx.createRadialGradient(n.sx-r*0.35, n.sy-r*0.4, r*0.15, n.sx, n.sy, r);
    if (isDirect)      { body.addColorStop(0,'#dbeafe'); body.addColorStop(1,'#2563eb'); }
    else if (isRelated){ body.addColorStop(0,'#e0eaff'); body.addColorStop(1,'#7fa8f5'); }
    else               { body.addColorStop(0,'#eef2f7'); body.addColorStop(1,'#8494ab'); }
    ctx.globalAlpha = fadeIn * depthFade;
    ctx.fillStyle = body;
    ctx.beginPath(); ctx.arc(n.sx, n.sy, r, 0, Math.PI*2); ctx.fill();
    ctx.strokeStyle = isDirect ? 'rgba(37,99,235,0.9)'
                    : isRelated ? 'rgba(127,168,245,0.8)'
                    : 'rgba(15,23,42,0.45)';
    ctx.lineWidth = 1;
    ctx.stroke();
    ctx.globalAlpha = fadeIn;

    if (isHover || (isDirect && highlight.at && now - highlight.at < 8000)) {
      ctx.font = (isHover ? '600 ' : '') + '11px "Segoe UI","PingFang SC","Microsoft YaHei",sans-serif';
      ctx.textAlign = 'center';
      const label = n.card.name;
      const tw = ctx.measureText(label).width;
      const ly = n.sy + r + 14;
      ctx.fillStyle = 'rgba(9,14,31,0.55)';
      ctx.beginPath();
      ctx.roundRect(n.sx - tw/2 - 5, ly - 9.5, tw + 10, 15, 7.5);
      ctx.fill();
      ctx.fillStyle = isHover ? '#eef4ff' : '#cdd9f0';
      ctx.fillText(label, n.sx, ly + 3.5);
    }
  });
  ctx.globalAlpha = 1;
}

function animate(now) {
  now = now || performance.now();
  step();
  if (!dragging) {
    rotY += velY; rotX += velX;
    rotX = Math.max(-1.25, Math.min(1.25, rotX));
    velX *= 0.94; velY *= 0.94;
    // idle drift keeps the ball alive
    if (Math.abs(velY) < 0.0004) rotY += 0.0011;
  }
  draw(now);
  requestAnimationFrame(animate);
}

function nodeAt(x, y) {
  for (let i = nodes.length - 1; i >= 0; i--) {
    const n = nodes[i];
    const dx = x - n.sx, dy = y - n.sy;
    if (dx*dx + dy*dy < (n.sr+5)*(n.sr+5)) return n;
  }
  return null;
}

cv.addEventListener('mousedown', e=>{
  dragging = true; dragMoved = 0;
  lastMX = e.clientX; lastMY = e.clientY;
  cv.classList.add('dragging');
});
window.addEventListener('mousemove', e=>{
  if (dragging) {
    const dx = e.clientX - lastMX, dy = e.clientY - lastMY;
    lastMX = e.clientX; lastMY = e.clientY;
    dragMoved += Math.abs(dx) + Math.abs(dy);
    rotY += dx * 0.006;
    rotX = Math.max(-1.25, Math.min(1.25, rotX + dy * 0.006));
    velY = dx * 0.006; velX = dy * 0.006;
    tooltip.style.display = 'none';
    return;
  }
  hovered = nodeAt(e.clientX, e.clientY);
  cv.classList.toggle('onnode', !!hovered);
  if (hovered) {
    tooltip.style.display='block';
    tooltip.style.left=Math.min(e.clientX+14, W-310)+'px';
    tooltip.style.top=Math.min(e.clientY+14, H-90)+'px';
    tooltip.querySelector('.name').textContent = hovered.card.name;
    tooltip.querySelector('.def').textContent = hovered.card.definition;
  } else {
    tooltip.style.display='none';
  }
});
window.addEventListener('mouseup', e=>{
  if (!dragging) return;
  dragging = false;
  cv.classList.remove('dragging');
  if (dragMoved < 6) {
    // a click, not a drag: open the card panel when a node is under it
    const n = nodeAt(e.clientX, e.clientY);
    if (n) openPanel(n); else closePanel();
  }
});
cv.addEventListener('wheel', e=>{
  e.preventDefault();
  zoom = Math.max(0.6, Math.min(1.9, zoom * (1 - e.deltaY * 0.0012)));
}, {passive:false});

function closePanel() { panel.classList.remove('open'); }
document.getElementById('panel-close').addEventListener('click', closePanel);

function openPanel(n) {
  const c = n.card;
  const idx = nodes.indexOf(n);
  const names = {};
  nodes.forEach((m,i)=>{ names[i] = m.card.name; });
  const linked = edges
    .filter(ed=>(ed.a===idx||ed.b===idx) && ed.usage>0)
    .map(ed=>({name: names[ed.a===idx ? ed.b : ed.a], count: ed.usage}));
  document.getElementById('p-name').textContent = c.name;
  document.getElementById('p-def').textContent = c.definition;
  const meta = document.getElementById('p-meta');
  meta.textContent = '';
  [['文件', c.location.file_path],
   ['行号', c.location.start_line + ' – ' + c.location.end_line],
   ['card_id', c.id]].forEach(function(pair) {
    const row = document.createElement('div');
    const b = document.createElement('b');
    b.textContent = pair[0];
    row.appendChild(b);
    row.appendChild(document.createTextNode(String(pair[1])));
    meta.appendChild(row);
  });
  const title = document.getElementById('p-links-title');
  const box = document.getElementById('p-links');
  box.innerHTML = '';
  if (linked.length) {
    title.style.display = 'block';
    linked.forEach(l=>{
      const row = document.createElement('div');
      row.className = 'link-row';
      row.textContent = l.name;
      const em = document.createElement('em');
      em.textContent = '× ' + l.count;
      row.appendChild(em);
      box.appendChild(row);
    });
  } else {
    title.style.display = 'none';
  }
  panel.classList.add('open');
}
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
            if not self._scan_request_allowed():
                self._send_json(
                    403, {"status": "rejected", "reason": "request origin not allowed"}
                )
                return
            query = parse_qs(parsed.query)
            target = query.get("path", [""])[0].strip()
            scan_root = getattr(self.server, "scan_root", "") or os.getcwd()
            root_real = os.path.normcase(os.path.realpath(scan_root))
            if not target:
                target = os.path.realpath(scan_root)
            else:
                if not os.path.isabs(target):
                    target = os.path.join(scan_root, target)
                target_real = os.path.normcase(os.path.realpath(target))
                if target_real != root_real and not target_real.startswith(
                    root_real + os.sep
                ):
                    self._send_json(
                        403,
                        {
                            "status": "rejected",
                            "reason": "path escapes scan root",
                            "scan_root": os.path.realpath(scan_root),
                        },
                    )
                    return
                target = os.path.realpath(target)
            thread = threading.Thread(
                target=run_scan_with_progress, args=(target,), daemon=True
            )
            thread.start()
            self._send_json(200, {"status": "started", "path": target})
        else:
            self.send_error(404)

    def _send_json(self, status: int, obj: dict[str, Any]) -> None:
        payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _scan_request_allowed(self) -> bool:
        """Gate the state-changing /api/scan endpoint to loopback same-origin callers."""

        port = self.server.server_address[1]
        host = (self.headers.get("Host") or "").strip().lower()
        if host not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            return False
        origin = (self.headers.get("Origin") or "").strip()
        if not origin:
            return True
        parsed_origin = urlparse(origin)
        return parsed_origin.scheme == "http" and parsed_origin.netloc.lower() == host

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

    server = start_web_server(args.database, args.port)
    url = f"http://127.0.0.1:{server.server_address[1]}"
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

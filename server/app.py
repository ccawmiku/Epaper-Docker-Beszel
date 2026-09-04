import collections
import json
import os
import socket
import time
from datetime import datetime
from typing import Any

import uvicorn
from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response

from beszel_client import BeszelApiError, BeszelClient, BeszelConfigError, BeszelCredentials, compact_snapshot
from config import settings
from display_renderer import EPD_HEIGHT, EPD_WIDTH, render_error, render_snapshot
from runtime_config import bump_force_refresh, load_runtime_config, update_runtime_config


app = FastAPI(title="EPaper NAS Display Service", version=os.getenv("APP_VERSION", "0.1.7"))

_beszel_client: BeszelClient | None = None
_device_logs = collections.deque(maxlen=30)
_last_device_info: dict[str, Any] = {"last_seen": "", "ip": "", "path": "", "status": "", "user_agent": ""}


def record_device_access(request: Request, path: str, status_code: int = 200, error: str = "") -> None:
    client_ip = request.client.host if request.client else "unknown"
    ua = request.headers.get("user-agent", "")
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = {
        "time": now_str,
        "ip": client_ip,
        "path": path,
        "status": status_code,
        "error": error,
        "user_agent": ua,
    }
    _device_logs.appendleft(entry)
    _last_device_info.update({
        "last_seen": now_str,
        "ip": client_ip,
        "path": path,
        "status": status_code,
        "user_agent": ua,
        "error": error,
    })


def get_beszel_client() -> BeszelClient:
    global _beszel_client
    if _beszel_client is None:
        _beszel_client = BeszelClient(
            BeszelCredentials(
                base_url=settings.beszel_base_url,
                email=settings.beszel_email,
                password=settings.beszel_password,
            )
        )
    return _beszel_client


def get_broadcast_addresses() -> list[str]:
    addrs = {"255.255.255.255"}
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            parts = ip.split(".")
            if len(parts) == 4 and parts[0] != "127":
                addrs.add(f"{parts[0]}.{parts[1]}.{parts[2]}.255")
    except Exception:
        pass
    if settings.server_host_override:
        parts = settings.server_host_override.split(".")
        if len(parts) == 4:
            addrs.add(f"{parts[0]}.{parts[1]}.{parts[2]}.255")
    return sorted(list(addrs))


def send_udp_broadcast(server_url: str, broadcast_port: int | None = None, broadcast_addr: str | None = None) -> dict[str, Any]:
    port = broadcast_port or settings.broadcast_port
    payload = {
        "service": "epaper-nas",
        "magic": "EPAPER_SERVER",
        "url": server_url.rstrip("/"),
        "timestamp": int(time.time()),
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    targets = [broadcast_addr] if (broadcast_addr and broadcast_addr != "255.255.255.255") else get_broadcast_addresses()
    if "255.255.255.255" not in targets:
        targets.append("255.255.255.255")

    sent_targets = []
    errors = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for t in targets:
            try:
                sock.sendto(data, (t, port))
                sent_targets.append(f"{t}:{port}")
            except Exception as e:
                errors.append(f"{t}: {e}")

    return {
        "status": "broadcast_sent",
        "payload": payload,
        "targets": sent_targets,
        "target": ", ".join(sent_targets),
        "errors": errors,
    }


def detect_server_ip(request: Request) -> str:
    if settings.server_host_override:
        return settings.server_host_override
    host_header = request.headers.get("host", "")
    if host_header:
        host = host_header.split(":")[0].strip("[]")
        if host and host not in {"localhost", "127.0.0.1", "0.0.0.0"}:
            return host
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        pass
    return "127.0.0.1"


def _beszel_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=500 if isinstance(exc, BeszelConfigError) else 503, detail=str(exc))


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return """
<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>EPaper NAS Display Service</title>
<body style="font-family:ui-monospace,Consolas,monospace;background:#f6f2de;color:#24221c;margin:32px">
<h1>EPaper NAS Display Service</h1>
<p><a href="/preview">/preview</a></p>
<p><a href="/frame.bin">/frame.bin</a></p>
<p><a href="/screen.png">/screen.png</a></p>
<p><a href="/api/display/data">/api/display/data</a></p>
</body></html>
"""


@app.get("/health")
def health() -> dict[str, object]:
    runtime = load_runtime_config()
    return {
        "ok": True,
        "version": os.getenv("APP_VERSION", "0.1.7"),
        "beszel_base_url": settings.beszel_base_url,
        "display_interval_seconds": runtime.display_interval_seconds,
        "display_chart_minutes": runtime.chart_minutes,
        "font_name": runtime.font_name,
        "timezone": runtime.timezone,
        "force_refresh_seq": runtime.force_refresh_seq,
    }


@app.get("/api/beszel/systems")
def beszel_systems() -> dict[str, object]:
    try:
        systems = get_beszel_client().get_all(
            "systems",
            filter_query=None,
            sort="name",
        )
    except (BeszelConfigError, BeszelApiError) as exc:
        raise _beszel_error(exc) from exc
    return {"items": systems}


@app.get("/api/display/systems")
def display_systems() -> dict[str, object]:
    runtime = load_runtime_config()
    try:
        systems = get_beszel_client().get_all("systems", filter_query=None, sort="name")
    except (BeszelConfigError, BeszelApiError) as exc:
        raise _beszel_error(exc) from exc
    modes = _display_modes_for(systems, runtime)
    return {
        "items": [
            {
                "id": item.get("id"),
                "name": item.get("name") or item.get("host") or item.get("id"),
                "status": item.get("status"),
                "mode": modes.get(str(item.get("id")), "normal"),
                "enabled": modes.get(str(item.get("id")), "normal") != "disabled",
                "invert": modes.get(str(item.get("id")), "normal") == "invert",
            }
            for item in systems
        ]
    }


@app.get("/api/beszel/snapshot")
def beszel_snapshot(
    minutes: int = Query(default=settings.beszel_history_minutes, ge=1, le=1440),
    compact: bool = Query(default=False),
) -> dict[str, object]:
    try:
        snapshot = get_beszel_client().snapshot(
            names=getattr(settings, "beszel_system_names", []),
            ids=getattr(settings, "beszel_system_ids", []),
            minutes=minutes,
            container_minutes=1,
            record_type=settings.beszel_record_type,
        )
        return compact_snapshot(snapshot) if compact else snapshot
    except (BeszelConfigError, BeszelApiError) as exc:
        raise _beszel_error(exc) from exc


@app.get("/api/display/data")
def display_data(minutes: int | None = Query(default=None, ge=1, le=1440)) -> dict[str, object]:
    return _display_snapshot(minutes or load_runtime_config().chart_minutes)


@app.get("/frame.bin")
def frame_bin(request: Request, minutes: int | None = Query(default=None, ge=1, le=1440)) -> Response:
    try:
        frame = _render_display_frame(minutes or load_runtime_config().chart_minutes)
        record_device_access(request, "/frame.bin", 200)
        return Response(
            content=frame.to_bin(),
            media_type="application/octet-stream",
            headers={"Cache-Control": "no-store", "X-Frame-Format": "EPD1", "X-Frame-Size": f"{EPD_WIDTH}x{EPD_HEIGHT}"},
        )
    except Exception as exc:
        record_device_access(request, "/frame.bin", 500, str(exc))
        raise


@app.get("/screen.png")
def screen_png(minutes: int | None = Query(default=None, ge=1, le=1440)) -> Response:
    frame = _render_display_frame(minutes or load_runtime_config().chart_minutes)
    return Response(content=frame.to_png(), media_type="image/png", headers={"Cache-Control": "no-store"})


@app.get("/preview", response_class=HTMLResponse)
def preview() -> str:
    return f"""
<!doctype html>
<html lang="zh-CN">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ESP32 墨水屏看板管理中心</title>
<style>
:root {{
  --bg: #ece5d6;
  --card-bg: #f7f3e8;
  --border: #cec3ab;
  --text: #2c2822;
  --btn-bg: #e2d7be;
  --btn-hover: #d2c5a7;
  --accent: #af231c;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  padding: 20px 24px;
  line-height: 1.4;
}}
header {{
  max-width: 1060px;
  margin: 0 auto 16px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}}
h1 {{ font-size: 19px; margin: 0; font-weight: 700; letter-spacing: 0.5px; }}
.layout-wrapper {{
  max-width: 1060px;
  margin: 0 auto;
  display: flex;
  gap: 20px;
  align-items: flex-start;
}}
/* 左栏：控制与状态面板 */
.left-col {{
  flex: 1;
  min-width: 320px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}}
/* 右栏：400x300 粘性固定渲染看板 */
.right-col {{
  width: 440px;
  flex-shrink: 0;
  position: sticky;
  top: 20px;
}}
.card {{
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 13px 16px;
  box-shadow: 0 2px 8px rgba(0,0,0,0.03);
}}
.card-header {{
  font-size: 13px;
  font-weight: bold;
  margin-bottom: 10px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid var(--border);
  padding-bottom: 6px;
}}
.grid-fields {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(115px, 1fr));
  gap: 10px;
  margin-bottom: 10px;
}}
.field {{ display: flex; flex-direction: column; gap: 4px; }}
.field label {{ font-size: 11px; font-weight: 600; color: #5c5647; }}
input, select, button {{
  font: inherit;
  border: 1px solid var(--border);
  border-radius: 4px;
  background: #fff;
  padding: 6px 9px;
  font-size: 12px;
  color: var(--text);
  outline: none;
}}
input:focus, select:focus {{ border-color: #7b6f58; }}
button {{
  background: var(--btn-bg);
  cursor: pointer;
  font-weight: 600;
  transition: all .15s ease;
}}
button:hover {{ background: var(--btn-hover); }}
.actions {{ display: flex; gap: 8px; justify-content: flex-end; }}
.broadcast-box {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }}
.systems-list {{ display: grid; gap: 7px; max-height: 160px; overflow-y: auto; }}
.system-row {{
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #ece4d2;
  padding: 6px 10px;
  border-radius: 4px;
  border: 1px solid #ddd1bb;
  font-size: 12px;
}}
.sys-left {{ display: flex; align-items: center; gap: 8px; overflow: hidden; }}
.sys-left span {{ white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.status-badge {{ font-size: 10px; padding: 2px 5px; border-radius: 3px; font-weight: bold; }}
.status-up {{ background: #d4edda; color: #155724; }}
.status-down {{ background: #f8d7da; color: #721c24; }}
.sys-right {{ display: flex; align-items: center; gap: 6px; font-size: 11px; color: #555; }}
.screen-shell {{
  background: #1e1d1b;
  padding: 12px;
  border-radius: 6px;
  box-shadow: 0 8px 24px rgba(0,0,0,0.25);
  display: flex;
  justify-content: center;
  margin: 10px 0;
}}
canvas {{
  display: block;
  width: {EPD_WIDTH}px;
  height: {EPD_HEIGHT}px;
  background: #f6f2de;
  image-rendering: pixelated;
}}
.bar-footer {{
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 11px;
  margin-top: 6px;
  color: #666;
}}
.bar-footer a {{
  color: var(--text);
  margin-left: 8px;
  text-decoration: none;
  border-bottom: 1px dotted #888;
}}
.log-box {{
  background: #1e1d1b;
  color: #a8e6cf;
  padding: 8px 10px;
  border-radius: 4px;
  font-family: ui-monospace, monospace;
  font-size: 11px;
  max-height: 125px;
  overflow-y: auto;
}}
.log-row {{ line-height: 1.45; border-bottom: 1px solid #2f2d29; padding: 2px 0; }}
.log-row:last-child {{ border-bottom: none; }}
.text-error {{ color: #ff8b8b; }}
.text-success {{ color: #88e39f; }}
.msg-tip {{ font-size: 11px; color: #706856; margin-top: 4px; word-break: break-all; }}

@media (max-width: 900px) {{
  .layout-wrapper {{ flex-direction: column-reverse; align-items: center; }}
  .right-col {{ width: 100%; max-width: 440px; position: static; }}
  .left-col {{ width: 100%; max-width: 440px; }}
}}
</style>

<header>
  <h1>ESP32 墨水屏看板管理中心</h1>
  <span style="font-size:12px;color:#7a6f59">v0.1.7 (400×300 专属排版)</span>
</header>

<div class="layout-wrapper">
  <!-- 左栏：控制配置与诊断 -->
  <div class="left-col">
    <!-- 1. 屏幕设置 (移除字号，固化黄金比例) -->
    <section class="card">
      <div class="card-header">
        <span>⚙️ 屏幕刷新与排版设置</span>
        <span id="saveTip" style="font-size:11px;color:#6b5f48"></span>
      </div>
      <div class="grid-fields">
        <div class="field"><label>刷新间隔(秒)</label><input id="interval" type="number" min="30" max="3600" step="30"></div>
        <div class="field"><label>折线时间 (Range)</label><select id="chart"><option value="720">12小时 (12h)</option><option value="1440">24小时 (24h)</option></select></div>
        <div class="field"><label>字体类型</label><select id="font"><option value="pixel">pixel (默认点阵)</option><option value="narrow">narrow</option><option value="wide">wide</option><option value="bold">bold</option></select></div>
        <div class="field"><label>时区</label><input id="timezone" list="timezones"><datalist id="timezones"><option value="Asia/Shanghai"><option value="UTC"><option value="Asia/Tokyo"><option value="America/Los_Angeles"><option value="America/New_York"></datalist></div>
      </div>
      <div class="actions">
        <button id="save" style="padding:6px 14px">💾 保存配置</button>
        <button id="force" style="padding:6px 14px">🔄 立即强刷</button>
      </div>
    </section>

    <!-- 2. 局域网 UDP 广播宣告 -->
    <section class="card">
      <div class="card-header"><span>📢 局域网 UDP 广播宣告 (ESP32 免配对)</span></div>
      <div class="broadcast-box">
        <input id="broadcastHost" placeholder="服务对外IP" style="flex:2;min-width:130px" title="ESP32 访问的主机 IP 或域名">
        <input id="broadcastPort" type="number" value="17001" style="width:70px" title="ESP32 HTTP 访问端口">
        <button id="broadcastBtn" style="flex:1;min-width:115px;background:#eddab4">📢 广播服务器</button>
      </div>
      <div id="broadcastStatus" class="msg-tip">点击后将向局域网广播服务地址，ESP32 墨水屏将自动捕获并永久存入 NVS。</div>
    </section>

    <!-- 3. 轮巡设备选择 -->
    <section class="card">
      <div class="card-header">
        <span>💻 轮巡监控设备 (勾选要显示的设备)</span>
        <span style="font-size:11px;font-weight:normal;color:#666">勾选即参与屏幕轮播</span>
      </div>
      <div class="systems-list" id="systems">正在获取 Beszel 系统设备...</div>
    </section>

    <!-- 4. ESP32 连接诊断与请求日志 -->
    <section class="card">
      <div class="card-header">
        <span>📡 ESP32 设备连接诊断与请求日志</span>
        <button id="refreshLogs" style="font-size:11px;padding:2px 8px">刷新状态</button>
      </div>
      <div id="deviceSummary" style="font-size:12px;margin-bottom:8px;font-weight:600">正在检查设备在线记录...</div>
      <div class="log-box" id="deviceLogsList">暂无日志</div>
    </section>
  </div>

  <!-- 右栏：400x300 实时渲染预览 (粘性吸顶) -->
  <div class="right-col">
    <section class="card" style="padding: 14px 18px;">
      <div class="card-header">
        <span>🖥️ 400×300 实时渲染预览</span>
        <button id="refreshFrame" style="font-size:11px;padding:2px 8px">刷新预览</button>
      </div>
      <div class="screen-shell">
        <canvas id="screen" width="{EPD_WIDTH}" height="{EPD_HEIGHT}"></canvas>
      </div>
      <div class="bar-footer">
        <span id="status">准备就绪</span>
        <div>
          <a href="/frame.bin" target="_blank">frame.bin</a>
          <a href="/screen.png" target="_blank">screen.png</a>
          <a href="/api/display/data" target="_blank">data</a>
        </div>
      </div>
    </section>
  </div>
</div>

<script>
const W = {EPD_WIDTH}, H = {EPD_HEIGHT}, HEADER = 15;
const canvas = document.getElementById('screen'), ctx = canvas.getContext('2d'), statusEl = document.getElementById('status');
const intervalInput = document.getElementById('interval'), chartInput = document.getElementById('chart'), fontInput = document.getElementById('font'), timezoneInput = document.getElementById('timezone'), systemsEl = document.getElementById('systems');
const broadcastHostInput = document.getElementById('broadcastHost'), broadcastPortInput = document.getElementById('broadcastPort'), broadcastBtn = document.getElementById('broadcastBtn'), broadcastStatus = document.getElementById('broadcastStatus');
const deviceSummary = document.getElementById('deviceSummary'), deviceLogsList = document.getElementById('deviceLogsList');

if (!broadcastHostInput.value && window.location.hostname && window.location.hostname !== '127.0.0.1' && window.location.hostname !== 'localhost') {{
  broadcastHostInput.value = window.location.hostname;
}}
if (window.location.port) {{
  broadcastPortInput.value = window.location.port;
}}

const paper = [246, 242, 222, 255], ink = [36, 34, 28, 255], red = [176, 28, 22, 255];
let settings = {{ display_interval_seconds: 60, chart_minutes: 1440, system_modes: {{}}, font_name: 'pixel', timezone: 'Asia/Shanghai', force_refresh_seq: 0 }}, timer = null;

function bit(bytes, index) {{
  return (bytes[index >> 3] & (0x80 >> (index & 7))) !== 0;
}}

async function drawFrame() {{
  statusEl.textContent = '正在加载 frame.bin...';
  try {{
    const res = await fetch(`/frame.bin?minutes=${{settings.chart_minutes}}&t=${{Date.now()}}`, {{ cache: 'no-store' }});
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const buf = await res.arrayBuffer(), view = new DataView(buf);
    const magic = String.fromCharCode(...new Uint8Array(buf, 0, 4));
    const width = view.getUint16(5, true), height = view.getUint16(7, true), planes = view.getUint8(9), planeBytes = view.getUint32(11, true);
    if (magic !== 'EPD1' || width !== W || height !== H || planes !== 2) throw new Error('无效的帧头部格式');
    const black = new Uint8Array(buf, HEADER, planeBytes), redPlane = new Uint8Array(buf, HEADER + planeBytes, planeBytes);
    const img = ctx.createImageData(W, H);
    for (let i = 0; i < W * H; i++) {{
      const isRed = bit(redPlane, i), isBlack = bit(black, i);
      const color = isRed ? red : (isBlack ? ink : paper), p = i * 4;
      img.data[p] = color[0]; img.data[p + 1] = color[1]; img.data[p + 2] = color[2]; img.data[p + 3] = 255;
    }}
    ctx.putImageData(img, 0, 0);
    statusEl.textContent = `已渲染 ${{width}}×${{height}}，更新于 ${{new Date().toLocaleTimeString()}}`;
  }} catch (err) {{
    statusEl.textContent = `渲染失败: ${{err.message}}`;
  }}
}}

async function loadSettings() {{
  const res = await fetch('/api/admin/settings', {{ cache: 'no-store' }});
  settings = await res.json();
  intervalInput.value = settings.display_interval_seconds;
  chartInput.value = String(Number(settings.chart_minutes) <= 720 ? 720 : 1440);
  fontInput.value = settings.font_name || 'pixel';
  timezoneInput.value = settings.timezone || 'Asia/Shanghai';
  await loadSystems();
  await loadDeviceLogs();
  resetTimer();
}}

async function loadSystems() {{
  const res = await fetch('/api/display/systems', {{ cache: 'no-store' }});
  const data = await res.json();
  systemsEl.innerHTML = '';
  if (!data.items || data.items.length === 0) {{
    systemsEl.innerHTML = '<div style="color:#777;font-size:12px">暂未检测到 Beszel 系统</div>';
    return;
  }}
  for (const item of data.items) {{
    const row = document.createElement('div');
    row.className = 'system-row';
    const isUp = item.status === 'up';
    const currentMode = (settings.system_modes && settings.system_modes[item.id]) !== undefined ? settings.system_modes[item.id] : item.mode;
    const isEnabled = currentMode !== 'disabled';
    const isInvert = currentMode === 'invert';

    row.innerHTML = `
      <div class="sys-left">
        <input type="checkbox" id="chk_en_${{item.id}}" data-sys-enable="${{item.id}}" ${{isEnabled ? 'checked' : ''}}>
        <label for="chk_en_${{item.id}}" style="display:flex;align-items:center;gap:6px;cursor:pointer">
          <span style="font-weight:600">${{item.name}}</span>
          <span class="status-badge ${{isUp ? 'status-up' : 'status-down'}}">${{item.status || '--'}}</span>
        </label>
      </div>
      <div class="sys-right">
        <label style="display:flex;align-items:center;gap:4px;cursor:pointer">
          <input type="checkbox" id="chk_inv_${{item.id}}" data-sys-invert="${{item.id}}" ${{isInvert ? 'checked' : ''}}>
          <span>黑白反转</span>
        </label>
      </div>
    `;
    systemsEl.append(row);
  }}
}}

function collectModes() {{
  const modes = {{}};
  for (const row of systemsEl.querySelectorAll('.system-row')) {{
    const enChk = row.querySelector('input[data-sys-enable]');
    const invChk = row.querySelector('input[data-sys-invert]');
    if (enChk && invChk) {{
      const sysId = enChk.dataset.sysEnable;
      if (!enChk.checked) {{
        modes[sysId] = 'disabled';
      }} else if (invChk.checked) {{
        modes[sysId] = 'invert';
      }} else {{
        modes[sysId] = 'normal';
      }}
    }}
  }}
  return modes;
}}

async function saveSettings() {{
  const saveTip = document.getElementById('saveTip');
  saveTip.textContent = '保存中...';
  const payload = {{
    display_interval_seconds: Number(intervalInput.value),
    chart_minutes: Number(chartInput.value),
    font_name: fontInput.value,
    timezone: timezoneInput.value,
    system_modes: collectModes()
  }};
  const res = await fetch('/api/admin/settings', {{
    method: 'POST',
    headers: {{ 'content-type': 'application/json' }},
    body: JSON.stringify(payload)
  }});
  settings = await res.json();
  saveTip.textContent = '已保存！';
  setTimeout(() => saveTip.textContent = '', 2500);
  await loadSystems();
  resetTimer();
  await drawFrame();
}}

async function forceRefresh() {{
  const res = await fetch('/api/admin/force-refresh', {{ method: 'POST' }});
  settings = await res.json();
  await drawFrame();
}}

async function announceServer() {{
  broadcastStatus.textContent = '正在向局域网广播宣告服务器地址...';
  try {{
    const ip = broadcastHostInput.value.trim();
    const port = Number(broadcastPortInput.value.trim()) || 17001;
    const res = await fetch('/api/admin/broadcast', {{
      method: 'POST',
      headers: {{ 'content-type': 'application/json' }},
      body: JSON.stringify({{ ip, port }})
    }});
    const data = await res.json();
    if (res.ok) {{
      broadcastStatus.innerHTML = `<span class="text-success">✔ 已向局域网广播宣告: <b>${{data.url}}</b> (目标: ${{data.detail?.target || '局域网'}})</span>`;
    }} else {{
      broadcastStatus.innerHTML = `<span class="text-error">✖ 广播失败: ${{data.detail || res.statusText}}</span>`;
    }}
  }} catch (err) {{
    broadcastStatus.innerHTML = `<span class="text-error">✖ 错误: ${{err.message}}</span>`;
  }}
}}

async function loadDeviceLogs() {{
  try {{
    const res = await fetch('/api/device/logs');
    const data = await res.json();
    const last = data.last_seen;
    if (last && last.last_seen) {{
      deviceSummary.innerHTML = `最近请求: <span style="color:#2a7a40">${{last.ip}}</span> (${{last.last_seen}}) · 状态: ${{last.status}} · 接口: ${{last.path}}`;
    }} else {{
      deviceSummary.innerHTML = `<span style="color:#a87400">⚠️ 暂无 ESP32 设备连接记录 (请确认 ESP32 与路由器连接同一 WiFi)</span>`;
    }}

    if (!data.logs || data.logs.length === 0) {{
      deviceLogsList.innerHTML = '<div style="color:#777">暂无请求记录</div>';
    }} else {{
      deviceLogsList.innerHTML = data.logs.map(log => `
        <div class="log-row">
          <span style="color:#888">[${{log.time}}]</span>
          <b style="color:#7ec8e3">${{log.ip}}</b>
          <span>${{log.path}}</span>
          <span class="${{log.status === 200 ? 'text-success' : 'text-error'}}">[${{log.status}}]</span>
          ${{log.error ? `<span class="text-error">(${{log.error}})</span>` : ''}}
        </div>
      `).join('');
    }}
  }} catch(e) {{
    deviceSummary.textContent = '获取设备日志失败: ' + e.message;
  }}
}}

function resetTimer() {{
  if (timer) clearInterval(timer);
  timer = setInterval(() => {{
    drawFrame().catch(() => {{}});
    loadDeviceLogs().catch(() => {{}});
  }}, settings.display_interval_seconds * 1000);
}}

document.getElementById('refreshFrame').onclick = () => drawFrame();
document.getElementById('save').onclick = () => saveSettings();
document.getElementById('force').onclick = () => forceRefresh();
document.getElementById('refreshLogs').onclick = () => loadDeviceLogs();
broadcastBtn.onclick = () => announceServer();

loadSettings().then(drawFrame).catch(e => statusEl.textContent = e.message);
</script></html>
"""


@app.get("/api/admin/settings")
def admin_settings() -> dict[str, object]:
    return load_runtime_config().__dict__


@app.post("/api/admin/settings")
def admin_update_settings(payload: dict[str, object] = Body(...)) -> dict[str, object]:
    return update_runtime_config(payload).__dict__


@app.post("/api/admin/force-refresh")
def admin_force_refresh() -> dict[str, object]:
    return bump_force_refresh().__dict__


@app.post("/api/admin/broadcast")
def admin_broadcast(request: Request, payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    server_ip = str(payload.get("ip") or "").strip()
    port = int(payload.get("port") or settings.device_port)
    broadcast_port = int(payload.get("broadcast_port") or settings.broadcast_port)
    broadcast_addr = str(payload.get("broadcast_address") or settings.broadcast_address).strip()

    if not server_ip:
        server_ip = detect_server_ip(request)

    url = str(payload.get("url") or "").strip()
    if not url:
        url = f"http://{server_ip}:{port}"

    try:
        detail = send_udp_broadcast(url, broadcast_port=broadcast_port, broadcast_addr=broadcast_addr)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"发送广播失败: {exc}") from exc

    return {
        "ok": True,
        "message": f"广播已成功发送至 {detail.get('target')}",
        "url": url,
        "detail": detail,
    }


@app.get("/api/device/config")
def device_config(request: Request) -> dict[str, object]:
    runtime = load_runtime_config()
    record_device_access(request, "/api/device/config", 200)
    return {"display_interval_seconds": runtime.display_interval_seconds, "force_refresh_seq": runtime.force_refresh_seq}


@app.get("/api/device/logs")
def device_logs() -> dict[str, Any]:
    return {
        "last_seen": _last_device_info,
        "logs": list(_device_logs),
    }


def _display_snapshot(minutes: int) -> dict[str, object]:
    runtime = load_runtime_config()
    record_type, sample_count = _beszel_chart_window(minutes)
    snapshot = get_beszel_client().snapshot(
        names=getattr(settings, "beszel_system_names", []),
        ids=getattr(settings, "beszel_system_ids", []),
        minutes=minutes,
        sample_count=sample_count,
        container_minutes=1,
        record_type=record_type,
    )
    snapshot["chart_minutes"] = minutes
    _apply_display_rotation(snapshot, runtime)
    return snapshot


def _display_modes_for(systems: list[dict[str, object]], runtime) -> dict[str, str]:
    modes = dict(runtime.system_modes or {})
    for index, item in enumerate(systems):
        system_id = str(item.get("id") or "")
        name = str(item.get("name") or item.get("host") or "").lower()
        if system_id and system_id not in modes:
            if "istore" in name or "openwrt" in name or "router" in name:
                modes[system_id] = "invert"
            elif "nas" in name or "synology" in name:
                modes[system_id] = "normal"
            else:
                modes[system_id] = "disabled"
    return modes


def _apply_display_rotation(snapshot: dict[str, object], runtime) -> None:
    systems = snapshot.get("systems")
    if not isinstance(systems, list) or not systems:
        snapshot["display"] = {"active_system_id": "", "invert": False, "mode": "normal", "font_name": runtime.font_name, "timezone": runtime.timezone}
        return
    source_systems = [(item.get("system") or {}) for item in systems if isinstance(item, dict)]
    modes = _display_modes_for(source_systems, runtime)

    active_pool = [
        item for item in systems
        if isinstance(item, dict) and modes.get(str((item.get("system") or {}).get("id") or ""), "normal") != "disabled"
    ]
    if not active_pool:
        active_pool = systems

    index = (int(time.time()) // max(1, int(runtime.display_interval_seconds)) + int(runtime.force_refresh_seq)) % len(active_pool)
    active = active_pool[index] if isinstance(active_pool[index], dict) else {}
    active_id = str((active.get("system") or {}).get("id") or "")
    mode = modes.get(active_id, "normal")
    snapshot["display"] = {
        "active_system_id": active_id,
        "invert": mode == "invert",
        "mode": mode,
        "font_name": runtime.font_name,
        "timezone": runtime.timezone,
        "chart_minutes": runtime.chart_minutes,
    }


def _beszel_chart_window(minutes: int) -> tuple[str, int]:
    configured = settings.beszel_record_type.lower()
    if configured not in {"", "auto", "1m"}:
        return settings.beszel_record_type, minutes
    if minutes <= 180:
        return "1m", minutes
    if minutes <= 1440:
        return "10m", max(1, round(minutes / 10))
    return "20m", max(1, round(minutes / 20))


def _render_display_frame(minutes: int):
    try:
        return render_snapshot(_display_snapshot(minutes))
    except Exception as exc:
        return render_error(str(exc))


if __name__ == "__main__":
    uvicorn.run("app:app", host=settings.app_host, port=settings.app_port, reload=False)

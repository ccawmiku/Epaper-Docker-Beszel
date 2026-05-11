from __future__ import annotations

import os
import time

import uvicorn
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, Response

from beszel_client import BeszelApiError, BeszelClient, BeszelConfigError, BeszelCredentials, compact_snapshot
from config import settings
from display_renderer import EPD_HEIGHT, EPD_WIDTH, render_error, render_snapshot
from runtime_config import bump_force_refresh, load_runtime_config, update_runtime_config


app = FastAPI(title="EPaper NAS Display Service", version=os.getenv("APP_VERSION", "0.1.0"))


def get_beszel_client() -> BeszelClient:
    return BeszelClient(
        BeszelCredentials(
            base_url=settings.beszel_base_url,
            email=settings.beszel_email,
            password=settings.beszel_password,
        )
    )


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
        "version": os.getenv("APP_VERSION", "0.1.0"),
        "beszel_base_url": settings.beszel_base_url,
        "display_interval_seconds": runtime.display_interval_seconds,
        "display_chart_minutes": runtime.chart_minutes,
        "font_name": runtime.font_name,
        "font_size": runtime.font_size,
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
def frame_bin(minutes: int | None = Query(default=None, ge=1, le=1440)) -> Response:
    frame = _render_display_frame(minutes or load_runtime_config().chart_minutes)
    return Response(
        content=frame.to_bin(),
        media_type="application/octet-stream",
        headers={"Cache-Control": "no-store", "X-Frame-Format": "EPD1", "X-Frame-Size": f"{EPD_WIDTH}x{EPD_HEIGHT}"},
    )


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
<title>EPaper Preview</title>
<style>
body{{margin:0;background:#d9d1bd;color:#24221c;font-family:ui-monospace,Consolas,monospace}}
main{{display:grid;place-items:center;min-height:100vh;padding:24px;box-sizing:border-box}}
.controls{{width:{EPD_WIDTH}px;display:grid;grid-template-columns:1fr 1fr 90px 64px auto auto;gap:8px;align-items:end;margin-bottom:10px;font-size:12px}}
label{{display:grid;gap:3px}}
input,button,select{{font:inherit;border:1px solid #24221c;background:#f6f2de;padding:4px 8px;min-width:0}}
button{{cursor:pointer}}
.systems{{width:{EPD_WIDTH}px;display:grid;gap:4px;margin:0 0 10px;font-size:12px}}
.system-row{{display:grid;grid-template-columns:1fr auto;gap:8px;align-items:center}}
.system-row span{{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.bar,.meta{{width:{EPD_WIDTH}px;display:flex;align-items:center;justify-content:space-between;font-size:12px}}
.bar{{margin-bottom:8px}}
.meta{{margin-top:8px;font-size:11px}}
.shell{{background:#222;padding:12px;box-shadow:0 10px 30px rgba(0,0,0,.26)}}
canvas{{display:block;width:{EPD_WIDTH}px;height:{EPD_HEIGHT}px;background:#f6f2de;image-rendering:pixelated}}
a{{color:#24221c}}
</style>
<main><section>
<div class="controls">
  <label>刷新间隔/秒<input id="interval" type="number" min="30" max="3600" step="30"></label>
  <label>折线时间/分钟<input id="chart" type="number" min="60" max="1440" step="60"></label>
  <label>font<select id="font"><option value="mono">mono</option><option value="sans">sans</option><option value="serif">serif</option></select></label>
  <label>size<input id="fontSize" type="number" min="-3" max="4" step="1"></label>
  <button id="save">save</button><button id="force">force</button>
</div>
<div class="systems" id="systems"></div>
<div class="bar"><span>EPD preview: {EPD_WIDTH}x{EPD_HEIGHT}, decoded from frame.bin</span><button id="refresh">refresh</button></div>
<div class="shell"><canvas id="screen" width="{EPD_WIDTH}" height="{EPD_HEIGHT}"></canvas></div>
<div class="meta"><span id="status">loading</span><a href="/frame.bin">frame.bin</a><a href="/screen.png">screen.png</a><a href="/api/display/data">data</a></div>
</section></main>
<script>
const W={EPD_WIDTH}, H={EPD_HEIGHT}, HEADER=15;
const canvas=document.getElementById('screen'), ctx=canvas.getContext('2d'), statusEl=document.getElementById('status');
const intervalInput=document.getElementById('interval'), chartInput=document.getElementById('chart'), fontInput=document.getElementById('font'), fontSizeInput=document.getElementById('fontSize'), systemsEl=document.getElementById('systems');
const paper=[246,242,222,255], ink=[36,34,28,255], red=[176,28,22,255];
let settings={{display_interval_seconds:60,chart_minutes:1440,system_modes:{{}},font_name:'mono',font_size:0,force_refresh_seq:0}}, timer=null;
function bit(bytes,index){{return (bytes[index>>3]&(0x80>>(index&7)))!==0}}
async function drawFrame(){{
  statusEl.textContent='loading frame.bin';
  const res=await fetch(`/frame.bin?minutes=${{settings.chart_minutes}}&t=${{Date.now()}}`,{{cache:'no-store'}});
  if(!res.ok) throw new Error('HTTP '+res.status);
  const buf=await res.arrayBuffer(), view=new DataView(buf);
  const magic=String.fromCharCode(...new Uint8Array(buf,0,4));
  const width=view.getUint16(5,true), height=view.getUint16(7,true), planes=view.getUint8(9), planeBytes=view.getUint32(11,true);
  if(magic!=='EPD1'||width!==W||height!==H||planes!==2) throw new Error('bad frame header');
  const black=new Uint8Array(buf,HEADER,planeBytes), redPlane=new Uint8Array(buf,HEADER+planeBytes,planeBytes);
  const img=ctx.createImageData(W,H);
  for(let i=0;i<W*H;i++){{
    const isRed=bit(redPlane,i), isBlack=bit(black,i);
    const color=isRed?red:(isBlack?ink:paper), p=i*4;
    img.data[p]=color[0]; img.data[p+1]=color[1]; img.data[p+2]=color[2]; img.data[p+3]=255;
  }}
  ctx.putImageData(img,0,0);
  statusEl.textContent=`${{width}}x${{height}}, ${{planeBytes}} bytes/plane, updated ${{new Date().toLocaleTimeString()}}`;
}}
async function loadSettings(){{
  const res=await fetch('/api/admin/settings',{{cache:'no-store'}});
  settings=await res.json(); intervalInput.value=settings.display_interval_seconds; chartInput.value=settings.chart_minutes; fontInput.value=settings.font_name||'mono'; fontSizeInput.value=settings.font_size||0; await loadSystems(); resetTimer();
}}
async function loadSystems(){{
  const res=await fetch('/api/display/systems',{{cache:'no-store'}});
  const data=await res.json();
  systemsEl.innerHTML='';
  for(const item of data.items){{
    const row=document.createElement('div'); row.className='system-row';
    const name=document.createElement('span'); name.textContent=`${{item.name}} · ${{item.status||'--'}}`;
    const select=document.createElement('select'); select.dataset.systemId=item.id;
    select.innerHTML='<option value="normal">normal</option><option value="invert">invert</option>';
    select.value=(settings.system_modes&&settings.system_modes[item.id])||item.mode||'normal';
    row.append(name,select); systemsEl.append(row);
  }}
}}
function collectModes(){{
  const modes={{}};
  for(const select of systemsEl.querySelectorAll('select[data-system-id]')) modes[select.dataset.systemId]=select.value;
  return modes;
}}
async function saveSettings(){{
  const res=await fetch('/api/admin/settings',{{method:'POST',headers:{{'content-type':'application/json'}},body:JSON.stringify({{display_interval_seconds:Number(intervalInput.value),chart_minutes:Number(chartInput.value),font_name:fontInput.value,font_size:Number(fontSizeInput.value),system_modes:collectModes()}})}});
  settings=await res.json(); intervalInput.value=settings.display_interval_seconds; chartInput.value=settings.chart_minutes; fontInput.value=settings.font_name||'mono'; fontSizeInput.value=settings.font_size||0; await loadSystems(); resetTimer(); await drawFrame();
}}
async function forceRefresh(){{const res=await fetch('/api/admin/force-refresh',{{method:'POST'}}); settings=await res.json(); await drawFrame()}}
function resetTimer(){{if(timer)clearInterval(timer); timer=setInterval(()=>drawFrame().catch(e=>statusEl.textContent=e.message),settings.display_interval_seconds*1000)}}
document.getElementById('refresh').onclick=()=>drawFrame().catch(e=>statusEl.textContent=e.message);
document.getElementById('save').onclick=()=>saveSettings().catch(e=>statusEl.textContent=e.message);
document.getElementById('force').onclick=()=>forceRefresh().catch(e=>statusEl.textContent=e.message);
loadSettings().then(drawFrame).catch(e=>statusEl.textContent=e.message);
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


@app.get("/api/device/config")
def device_config() -> dict[str, object]:
    runtime = load_runtime_config()
    return {"display_interval_seconds": runtime.display_interval_seconds, "force_refresh_seq": runtime.force_refresh_seq}


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
    _apply_display_rotation(snapshot, runtime)
    return snapshot


def _display_modes_for(systems: list[dict[str, object]], runtime) -> dict[str, str]:
    modes = dict(runtime.system_modes or {})
    for index, item in enumerate(systems):
        system_id = str(item.get("id") or "")
        if system_id and system_id not in modes:
            modes[system_id] = "invert" if index == 1 else "normal"
    return modes


def _apply_display_rotation(snapshot: dict[str, object], runtime) -> None:
    systems = snapshot.get("systems")
    if not isinstance(systems, list) or not systems:
        snapshot["display"] = {"active_system_id": "", "invert": False, "mode": "normal", "font_name": runtime.font_name, "font_size": runtime.font_size}
        return
    source_systems = [(item.get("system") or {}) for item in systems if isinstance(item, dict)]
    modes = _display_modes_for(source_systems, runtime)
    index = (int(time.time()) // max(1, int(runtime.display_interval_seconds)) + int(runtime.force_refresh_seq)) % len(systems)
    active = systems[index] if isinstance(systems[index], dict) else {}
    active_id = str((active.get("system") or {}).get("id") or "")
    mode = modes.get(active_id, "normal")
    snapshot["display"] = {"active_system_id": active_id, "invert": mode == "invert", "mode": mode, "font_name": runtime.font_name, "font_size": runtime.font_size}


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

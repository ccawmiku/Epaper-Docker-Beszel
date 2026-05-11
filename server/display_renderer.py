from __future__ import annotations

import io
import struct
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


EPD_WIDTH = 400
EPD_HEIGHT = 300
PLANE_BYTES = EPD_WIDTH * EPD_HEIGHT // 8

PAPER = (246, 242, 222)
INK = (36, 34, 28)
SOFT_INK = (82, 78, 68)
RED = (176, 28, 22)

CPU_RISK_PERCENT = 70.0
CPU_DANGER_PERCENT = 90.0
MEMORY_RISK_PERCENT = 80.0
MEMORY_DANGER_PERCENT = 90.0
LOAD_RISK_PER_CORE = 0.8
LOAD_DANGER_PER_CORE = 1.2


@dataclass(frozen=True)
class RenderedFrame:
    image: Image.Image
    black_plane: bytes
    red_plane: bytes

    def to_bin(self) -> bytes:
        header = b"EPD1" + struct.pack("<BHHBBI", 1, EPD_WIDTH, EPD_HEIGHT, 2, 0, len(self.black_plane))
        return header + self.black_plane + self.red_plane

    def to_png(self) -> bytes:
        out = io.BytesIO()
        self.image.save(out, format="PNG", optimize=True)
        return out.getvalue()


class Fonts:
    def __init__(self) -> None:
        self.tiny = _load_font(12)
        self.small = _load_font(14)
        self.medium = _load_font(16)
        self.large = _load_font(20)
        self.clock = _load_font(24)


def render_snapshot(snapshot: dict[str, Any]) -> RenderedFrame:
    img = Image.new("RGB", (EPD_WIDTH, EPD_HEIGHT), PAPER)
    draw = ImageDraw.Draw(img)
    fonts = Fonts()
    systems = snapshot.get("systems") or []
    nas = _find_system(systems, "NAS") or (systems[0] if systems else {})
    router = _find_system(systems, "iStoreOS") or (systems[1] if len(systems) > 1 else {})
    _draw_header(draw, fonts, nas)
    _draw_metric_panels(draw, fonts, nas, router)
    _draw_container_table(draw, fonts, nas)
    if snapshot.get("errors"):
        draw.text((340, 288), f"{len(snapshot['errors'])} ERR", fill=INK, font=fonts.tiny)
    return _encode_frame(img)


def render_error(message: str) -> RenderedFrame:
    img = Image.new("RGB", (EPD_WIDTH, EPD_HEIGHT), PAPER)
    draw = ImageDraw.Draw(img)
    fonts = Fonts()
    draw.rectangle((0, 0, EPD_WIDTH - 1, EPD_HEIGHT - 1), outline=INK)
    draw.text((18, 18), "EPAPER DATA ERROR", fill=INK, font=fonts.large)
    draw.line((18, 50, 382, 50), fill=INK)
    _wrapped_text(draw, (18, 70), message, 48, fonts.small, INK, 16)
    return _encode_frame(img)


def _draw_header(draw: ImageDraw.ImageDraw, fonts: Fonts, nas: dict[str, Any]) -> None:
    system = nas.get("system") or {}
    latest = nas.get("latest") or {}
    alert = _system_alert(system, latest)
    _draw_status_mark(draw, 10, 10, 58, alert)

    chips = [
        (78, 10, 86, "NAME", _clip(str(system.get("name") or "EPAPER").upper(), 8)),
        (170, 10, 76, "TEMP", _temperature_label(latest.get("temperatures"))),
        (252, 10, 72, "LOAD", _load_label(latest.get("load_average"))),
    ]
    for x, y, w, label, value in chips:
        _info_chip(draw, fonts, x, y, w, 28, label, value)

    now = datetime.now()
    draw.text((338, 4), now.strftime("%m/%d"), fill=SOFT_INK, font=fonts.small)
    draw.text((326, 25), now.strftime("%H:%M"), fill=INK, font=fonts.clock)
    draw.line((78, 48, 390, 48), fill=INK)
    draw.text(
        (78, 55),
        f"CPU {_fmt_pct(latest.get('cpu_percent'))}  MEM {_fmt_gb_pair(latest.get('memory_used_gb'), latest.get('memory_gb'))}  DISK {_fmt_gb_pair(latest.get('disk_used_gb'), latest.get('disk_gb'))}",
        fill=INK,
        font=fonts.tiny,
    )


def _draw_metric_panels(draw: ImageDraw.ImageDraw, fonts: Fonts, nas: dict[str, Any], router: dict[str, Any]) -> None:
    panels = [
        (10, 82, 122, 50, "NAS CPU", nas.get("cpu_history", []), _fmt_pct((nas.get("latest") or {}).get("cpu_percent"))),
        (139, 82, 122, 50, "NAS MEM", nas.get("memory_history", []), _fmt_pct((nas.get("latest") or {}).get("memory_percent"))),
        (268, 82, 122, 50, "ROUTER", router.get("cpu_history", []), _fmt_pct((router.get("latest") or {}).get("cpu_percent"))),
    ]
    for x, y, w, h, title, values, value in panels:
        _panel(draw, fonts, x, y, w, h, title, value, values)


def _draw_container_table(draw: ImageDraw.ImageDraw, fonts: Fonts, nas: dict[str, Any]) -> None:
    containers = list(nas.get("container_latest") or [])
    containers.sort(key=lambda item: (float(item.get("cpu_percent") or 0), float(item.get("memory_mb") or 0)), reverse=True)
    total_cpu = sum(float(item.get("cpu_percent") or 0) for item in containers)
    total_mem = sum(float(item.get("memory_mb") or 0) for item in containers)

    draw.text((10, 143), "CONTAINERS", fill=INK, font=fonts.medium)
    _small_box(draw, fonts, 132, 140, 56, f"RUN {len(containers)}")
    _small_box(draw, fonts, 196, 140, 72, f"CPU {total_cpu:.1f}%")
    _small_box(draw, fonts, 276, 140, 94, f"MEM:{_fmt_mb(total_mem)}")

    left, top, right, bottom = 10, 166, 390, 286
    header_y = top + 20
    draw.rectangle((left, top, right, bottom), outline=INK)
    draw.line((left, header_y, right, header_y), fill=INK)
    draw.line((left, bottom - 1, right, bottom - 1), fill=INK)
    cols = {"name": 16, "cpu": 176, "mem": 226, "port": 282, "status": 334}
    for key, label in [("name", "NAME"), ("cpu", "CPU"), ("mem", "MEM"), ("port", "PORT"), ("status", "STATUS")]:
        draw.text((cols[key], top + 4), label, fill=INK, font=fonts.small)

    row_y = header_y + 6
    for item in containers[:5]:
        draw.text((cols["name"], row_y), _clip(str(item.get("name") or "--"), 18), fill=INK, font=fonts.small)
        draw.text((cols["cpu"], row_y), _fmt_pct(item.get("cpu_percent")), fill=INK, font=fonts.small)
        draw.text((cols["mem"], row_y), _fmt_mb(item.get("memory_mb")), fill=INK, font=fonts.small)
        draw.text((cols["port"], row_y), _port_label(item.get("ports")), fill=INK, font=fonts.small)
        draw.text((cols["status"], row_y), _status_label(item.get("status")), fill=INK, font=fonts.small)
        row_y += 18


def _panel(draw: ImageDraw.ImageDraw, fonts: Fonts, x: int, y: int, w: int, h: int, title: str, value: str, values: list[Any]) -> None:
    draw.rectangle((x, y, x + w, y + h), outline=INK)
    draw.line((x, y + 18, x + w, y + 18), fill=INK)
    draw.text((x + 5, y + 3), title, fill=INK, font=fonts.tiny)
    draw.text((x + w - 48, y + 3), value, fill=INK, font=fonts.tiny)
    _sparkline(draw, x + 8, y + 29, w - 16, h - 36, values)


def _sparkline(draw: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int, values: list[Any]) -> None:
    draw.line((x, y + h, x + w, y + h), fill=SOFT_INK)
    nums = [float(v) for v in values if v is not None]
    if len(nums) > 120:
        step = len(nums) / 120
        nums = [nums[int(i * step)] for i in range(120)]
    if len(nums) < 2:
        draw.line((x, y + h // 2, x + w, y + h // 2), fill=INK)
        return
    span = max(max(nums) - min(nums), 1.0)
    low = min(nums)
    points = [(x + round(i * w / (len(nums) - 1)), y + h - round((v - low) * h / span)) for i, v in enumerate(nums)]
    draw.line(points, fill=INK, width=1)


def _draw_status_mark(draw: ImageDraw.ImageDraw, x: int, y: int, size: int, alert: str) -> None:
    draw.rectangle((x, y, x + size, y + size), fill=INK)
    circle = (x + 12, y + 12, x + size - 12, y + size - 12)
    if alert == "risk":
        cell = 5
        draw.ellipse(circle, fill=PAPER)
        cx = (circle[0] + circle[2]) / 2
        cy = (circle[1] + circle[3]) / 2
        radius = (circle[2] - circle[0]) / 2
        for yy in range(y + 12, y + size - 12, cell):
            for xx in range(x + 12, x + size - 12, cell):
                corners = [(xx, yy), (xx + cell - 1, yy), (xx, yy + cell - 1), (xx + cell - 1, yy + cell - 1)]
                inside = all((px - cx) ** 2 + (py - cy) ** 2 <= (radius - 1) ** 2 for px, py in corners)
                if inside and ((xx - x) // cell + (yy - y) // cell) % 2 == 0:
                    draw.rectangle((xx, yy, xx + cell - 1, yy + cell - 1), fill=RED)
        draw.ellipse(circle, outline=PAPER, width=2)
    elif alert == "danger":
        draw.ellipse(circle, fill=RED)
        draw.ellipse((circle[0] + 8, circle[1] + 8, circle[2] - 8, circle[3] - 8), fill=PAPER)
    else:
        draw.ellipse(circle, fill=PAPER)


def _encode_frame(img: Image.Image) -> RenderedFrame:
    black = bytearray(PLANE_BYTES)
    red = bytearray(PLANE_BYTES)
    pixels = img.load()
    for y in range(EPD_HEIGHT):
        for x in range(EPD_WIDTH):
            r, g, b = pixels[x, y]
            idx = y * EPD_WIDTH + x
            bit = 0x80 >> (idx & 7)
            byte_idx = idx >> 3
            if r > 130 and g < 90 and b < 90:
                red[byte_idx] |= bit
            elif (r + g + b) < 690:
                black[byte_idx] |= bit
    return RenderedFrame(img, bytes(black), bytes(red))


def _load_font(size: int) -> ImageFont.ImageFont:
    candidates = [
        Path("C:/Windows/Fonts/consolab.ttf"),
        Path("C:/Windows/Fonts/consola.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"),
    ]
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def _info_chip(draw: ImageDraw.ImageDraw, fonts: Fonts, x: int, y: int, w: int, h: int, label: str, value: str) -> None:
    draw.rectangle((x, y, x + w, y + h), outline=INK)
    draw.text((x + 5, y + 5), label, fill=SOFT_INK, font=fonts.tiny)
    draw.text((x + 44, y + 5), value, fill=INK, font=fonts.tiny)


def _small_box(draw: ImageDraw.ImageDraw, fonts: Fonts, x: int, y: int, w: int, text: str) -> None:
    draw.rectangle((x, y, x + w, y + 18), outline=INK)
    draw.text((x + 5, y + 3), text, fill=INK, font=fonts.tiny)


def _find_system(systems: list[dict[str, Any]], name_part: str) -> dict[str, Any] | None:
    needle = name_part.lower()
    for item in systems:
        name = str((item.get("system") or {}).get("name") or "").lower()
        if needle in name:
            return item
    return None


def _system_alert(system: dict[str, Any], latest: dict[str, Any]) -> str:
    cpu = _float_or_none(latest.get("cpu_percent"))
    memory = _float_or_none(latest.get("memory_percent"))
    loads = latest.get("load_average")
    load = _float_or_none(loads[0] if isinstance(loads, list) and loads else None)
    cores = _float_or_none(system.get("cpu_threads")) or 1.0
    load_per_core = load / cores if load is not None else None
    if (cpu and cpu >= CPU_DANGER_PERCENT) or (memory and memory >= MEMORY_DANGER_PERCENT) or (load_per_core and load_per_core >= LOAD_DANGER_PER_CORE):
        return "danger"
    if (cpu and cpu >= CPU_RISK_PERCENT) or (memory and memory >= MEMORY_RISK_PERCENT) or (load_per_core and load_per_core >= LOAD_RISK_PER_CORE):
        return "risk"
    return "normal"


def _temperature_label(temperatures: Any) -> str:
    if not isinstance(temperatures, dict) or not temperatures:
        return "--C"
    values = [float(v) for v in temperatures.values() if isinstance(v, (int, float))]
    return f"{max(values):.0f}C" if values else "--C"


def _load_label(load_average: Any) -> str:
    return f"{float(load_average[0]):.2f}" if isinstance(load_average, list) and load_average else "--"


def _fmt_pct(value: Any) -> str:
    return "--%" if value is None else f"{float(value):.1f}%"


def _fmt_mb(value: Any) -> str:
    if value is None:
        return "--"
    number = float(value)
    return f"{number / 1024:.1f}G" if number >= 1024 else f"{number:.0f}M"


def _fmt_gb_pair(used: Any, total: Any) -> str:
    if used is None or total is None:
        return "--/--"
    unit = "T" if float(total) >= 1024 else "G"
    scale = 1024 if unit == "T" else 1
    return f"{float(used) / scale:.1f}/{float(total) / scale:.1f}{unit}"


def _port_label(value: Any) -> str:
    return "--" if value in (None, "") else _clip(str(value).split(",")[0].strip(), 7)


def _status_label(value: Any) -> str:
    text = str(value or "UP").lower()
    if text.startswith("up "):
        rest = text[3:]
        number = rest.split()[0]
        if number in {"a", "an"}:
            number = "1"
        if "day" in rest:
            return f"UP {number}D"
        if "hour" in rest:
            return f"UP {number}H"
        if "minute" in rest:
            return f"UP {number}M"
        return "UP"
    return _clip(text.upper(), 7)


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clip(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: max(0, limit - 1)] + "."


def _wrapped_text(draw: ImageDraw.ImageDraw, pos: tuple[int, int], text: str, width: int, font: ImageFont.ImageFont, fill: tuple[int, int, int], line_height: int) -> None:
    x, y = pos
    line = ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if len(trial) > width:
            draw.text((x, y), line, fill=fill, font=font)
            y += line_height
            line = word
        else:
            line = trial
    if line:
        draw.text((x, y), line, fill=fill, font=font)

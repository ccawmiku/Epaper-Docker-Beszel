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
    _draw_header(draw, fonts, nas, snapshot)
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


def _draw_header(draw: ImageDraw.ImageDraw, fonts: Fonts, nas: dict[str, Any], snapshot: dict[str, Any]) -> None:
    system = nas.get("system") or {}
    latest = nas.get("latest") or {}
    alert = _system_alert(system, latest)
    _draw_status_mark(draw, 2, 2, 58, alert)

    chips = [
        ("NAME", _clip(str(system.get("name") or "EPAPER").upper(), 8)),
        ("TEMP", _temperature_label(latest.get("temperatures"))),
        ("LOAD", _load_label(latest.get("load_average"))),
    ]
    x = 64
    for label, value in chips:
        width = _chip_width(fonts, label, value)
        _info_chip(draw, fonts, x, 2, width, 24, label, value)
        x += width + 8

    now = datetime.now()
    _right_text(draw, 397, 0, now.strftime("%m/%d"), fonts.small, SOFT_INK)
    _right_text(draw, 397, 18, now.strftime("%H:%M"), fonts.clock, INK)
    draw.line((64, 42, 397, 42), fill=INK)
    chart_label = _chart_window_label(snapshot.get("history_minutes"))
    draw.text(
        (64, 49),
        f"MEM {_fmt_gb_pair(latest.get('memory_used_gb'), latest.get('memory_gb'))}  DISK {_fmt_disk_gb_pair(latest.get('disk_used_gb'), latest.get('disk_gb'))}  {chart_label}",
        fill=INK,
        font=fonts.tiny,
    )


def _draw_metric_panels(draw: ImageDraw.ImageDraw, fonts: Fonts, nas: dict[str, Any], router: dict[str, Any]) -> None:
    panels = [
        (2, 73, 126, 54, "NAS CPU", nas.get("cpu_history", []), _fmt_pct((nas.get("latest") or {}).get("cpu_percent"))),
        (140, 73, 126, 54, "NAS MEM", nas.get("memory_history", []), _fmt_pct((nas.get("latest") or {}).get("memory_percent"))),
        (278, 73, 120, 54, "ROUTER", router.get("cpu_history", []), _fmt_pct((router.get("latest") or {}).get("cpu_percent"))),
    ]
    for x, y, w, h, title, values, value in panels:
        _panel(draw, fonts, x, y, w, h, title, value, values)


def _draw_container_table(draw: ImageDraw.ImageDraw, fonts: Fonts, nas: dict[str, Any]) -> None:
    containers = list(nas.get("container_latest") or [])
    containers.sort(key=lambda item: (float(item.get("cpu_percent") or 0), float(item.get("memory_mb") or 0)), reverse=True)
    total_cpu = sum(float(item.get("cpu_percent") or 0) for item in containers)
    total_mem = sum(float(item.get("memory_mb") or 0) for item in containers)

    draw.text((2, 139), "CONTAINERS", fill=INK, font=fonts.medium)
    summary = [("RUN", str(len(containers))), ("CPU", f"{total_cpu:.1f}%"), ("MEM", _fmt_mb(total_mem))]
    widths = [_chip_width(fonts, label, value) for label, value in summary]
    x = 397 - sum(widths) - 12 * (len(widths) - 1)
    for (label, value), width in zip(summary, widths):
        _info_chip(draw, fonts, x, 134, width, 24, label, value)
        x += width + 12

    left, top, right, bottom = 2, 170, 397, 297
    header_y = top + 22
    draw.rectangle((left, top, right, bottom), outline=INK)
    draw.line((left, header_y, right, header_y), fill=INK)
    draw.line((left, bottom - 1, right, bottom - 1), fill=INK)
    cols = {"name": 8, "cpu": 184, "mem": 234, "port": 292, "status": 344}
    for key, label in [("name", "NAME"), ("cpu", "CPU"), ("mem", "MEM"), ("port", "PORT"), ("status", "STATUS")]:
        draw.text((cols[key], top + 4), label, fill=INK, font=fonts.small)

    row_y = header_y + 5
    row_step = 17
    max_rows = max(0, (bottom - row_y - 2) // row_step + 1)
    for item in containers[:max_rows]:
        draw.text((cols["name"], row_y), _clip_to_width(str(item.get("name") or "--"), fonts.small, cols["cpu"] - cols["name"] - 6), fill=INK, font=fonts.small)
        draw.text((cols["cpu"], row_y), _fmt_pct(item.get("cpu_percent")), fill=INK, font=fonts.small)
        draw.text((cols["mem"], row_y), _fmt_mb(item.get("memory_mb")), fill=INK, font=fonts.small)
        draw.text((cols["port"], row_y), _clip_to_width(_port_label(item.get("ports")), fonts.small, cols["status"] - cols["port"] - 6), fill=INK, font=fonts.small)
        draw.text((cols["status"], row_y), _clip_to_width(_status_label(item.get("status")), fonts.small, right - cols["status"] - 6), fill=INK, font=fonts.small)
        row_y += row_step


def _panel(draw: ImageDraw.ImageDraw, fonts: Fonts, x: int, y: int, w: int, h: int, title: str, value: str, values: list[Any]) -> None:
    draw.rectangle((x, y, x + w, y + h), outline=INK)
    draw.line((x, y + 20, x + w, y + 20), fill=INK)
    draw.text((x + 6, y + 4), title, fill=INK, font=fonts.tiny)
    _right_text(draw, x + w - 6, y + 4, value, fonts.tiny, INK)
    _sparkline(draw, x + 8, y + 32, w - 16, h - 39, values)


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
    text_y = y + (h - _font_height(fonts.tiny)) // 2
    draw.text((x + 5, text_y), label, fill=SOFT_INK, font=fonts.tiny)
    _right_text(draw, x + w - 5, text_y, value, fonts.tiny, INK)


def _small_box(draw: ImageDraw.ImageDraw, fonts: Fonts, x: int, y: int, w: int, text: str) -> None:
    h = 24
    draw.rectangle((x, y, x + w, y + h), outline=INK)
    draw.text((x + 5, y + (h - _font_height(fonts.tiny)) // 2), text, fill=INK, font=fonts.tiny)


def _chip_width(fonts: Fonts, label: str, value: str) -> int:
    label_width, _ = _text_size(fonts.tiny, label)
    value_width, _ = _text_size(fonts.tiny, value)
    return max(44, label_width + value_width + 22)


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


def _right_text(draw: ImageDraw.ImageDraw, right: int, y: int, text: str, font: ImageFont.ImageFont, fill: tuple[int, int, int]) -> None:
    width, _ = _text_size(font, str(text))
    draw.text((right - width, y), text, fill=fill, font=font)


def _text_size(font: ImageFont.ImageFont, text: str) -> tuple[int, int]:
    bbox = ImageDraw.Draw(Image.new("1", (1, 1))).textbbox((0, 0), str(text), font=font)
    return max(1, bbox[2] - bbox[0]), max(1, bbox[3] - bbox[1])


def _font_height(font: ImageFont.ImageFont) -> int:
    return _text_size(font, "Ag")[1]


def _fmt_gb_pair(used: Any, total: Any) -> str:
    if used is None or total is None:
        return "--/--"
    unit = "Tb" if float(total) >= 1024 else "G"
    scale = 1024 if unit == "Tb" else 1
    return f"{float(used) / scale:.1f}/{float(total) / scale:.1f}{unit}"


def _fmt_disk_gb_pair(used: Any, total: Any) -> str:
    if used is None or total is None:
        return "--/--Gb"
    return f"{float(used):.0f}/{float(total):.0f}Gb"


def _chart_window_label(minutes: Any) -> str:
    try:
        value = int(minutes)
    except (TypeError, ValueError):
        value = 1440
    if value >= 60 and value % 60 == 0:
        return f"{value // 60}H↓"
    return f"{value}Min↓"


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


def _clip_to_width(value: str, font: ImageFont.ImageFont, width: int) -> str:
    text = str(value)
    if _text_size(font, text)[0] <= width:
        return text
    suffix = "."
    while text and _text_size(font, text + suffix)[0] > width:
        text = text[:-1]
    return (text + suffix) if text else suffix


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

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import io
import math
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from PIL import Image, ImageDraw

EPD_WIDTH = 400
EPD_HEIGHT = 300
PLANE_BYTES = EPD_WIDTH * EPD_HEIGHT // 8

PAPER = (246, 242, 222)
INK = (36, 34, 28)
SOFT_INK = (108, 101, 86)
RED = (176, 28, 22)

CPU_RISK_PERCENT = 80.0
CPU_DANGER_PERCENT = 92.0
MEMORY_RISK_PERCENT = 85.0
MEMORY_DANGER_PERCENT = 94.0
LOAD_RISK_PER_CORE = 1.5
LOAD_DANGER_PER_CORE = 2.5

_GLYPHS_5X7 = {
    " ": ["00000", "00000", "00000", "00000", "00000", "00000", "00000"],
    "!": ["00100", "00100", "00100", "00100", "00000", "00100", "00000"],
    '"': ["01010", "01010", "01010", "00000", "00000", "00000", "00000"],
    "#": ["01010", "11111", "01010", "01010", "11111", "01010", "00000"],
    "$": ["00100", "01111", "10100", "01110", "00101", "11110", "00100"],
    "%": ["11001", "11010", "00100", "01000", "01011", "10011", "00000"],
    "&": ["01100", "10010", "10100", "01000", "10101", "10010", "01101"],
    "'": ["00100", "00100", "01000", "00000", "00000", "00000", "00000"],
    "(": ["00010", "00100", "01000", "01000", "01000", "00100", "00010"],
    ")": ["01000", "00100", "00010", "00010", "00010", "00100", "01000"],
    "*": ["00000", "01010", "00100", "11111", "00100", "01010", "00000"],
    "+": ["00000", "00100", "00100", "11111", "00100", "00100", "00000"],
    ",": ["00000", "00000", "00000", "00000", "00100", "00100", "01000"],
    "-": ["00000", "00000", "00000", "11111", "00000", "00000", "00000"],
    ".": ["00000", "00000", "00000", "00000", "00000", "01100", "01100"],
    "/": ["00001", "00010", "00100", "01000", "10000", "00000", "00000"],
    "0": ["01110", "10001", "10011", "10101", "11001", "10001", "01110"],
    "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    "2": ["01110", "10001", "00001", "00010", "00100", "01000", "11111"],
    "3": ["11111", "00010", "00100", "00010", "00001", "10001", "01110"],
    "4": ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
    "5": ["11111", "10000", "11110", "00001", "00001", "10001", "01110"],
    "6": ["00110", "01000", "10000", "11110", "10001", "10001", "01110"],
    "7": ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
    "8": ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
    "9": ["01110", "10001", "10001", "01111", "00001", "00010", "01100"],
    ":": ["00000", "01100", "01100", "00000", "01100", "01100", "00000"],
    ";": ["00000", "01100", "01100", "00000", "01100", "01100", "10000"],
    "<": ["00010", "00100", "01000", "10000", "01000", "00100", "00010"],
    "=": ["00000", "11111", "00000", "11111", "00000", "00000", "00000"],
    ">": ["01000", "00100", "00010", "00001", "00010", "00100", "01000"],
    "?": ["01110", "10001", "00001", "00010", "00100", "00000", "00100"],
    "@": ["01110", "10001", "00001", "01101", "10101", "10101", "01110"],
    "A": ["01110", "10001", "10001", "11111", "10001", "10001", "10001"],
    "B": ["11110", "10001", "10001", "11110", "10001", "10001", "11110"],
    "C": ["01110", "10001", "10000", "10000", "10000", "10001", "01110"],
    "D": ["11100", "10010", "10001", "10001", "10001", "10010", "11100"],
    "E": ["11111", "10000", "10000", "11110", "10000", "10000", "11111"],
    "F": ["11111", "10000", "10000", "11110", "10000", "10000", "10000"],
    "G": ["01110", "10001", "10000", "10111", "10001", "10001", "01110"],
    "H": ["10001", "10001", "10001", "11111", "10001", "10001", "10001"],
    "I": ["01110", "00100", "00100", "00100", "00100", "00100", "01110"],
    "J": ["00001", "00001", "00001", "00001", "00001", "10001", "01110"],
    "K": ["10001", "10010", "10100", "11000", "10100", "10010", "10001"],
    "L": ["10000", "10000", "10000", "10000", "10000", "10000", "11111"],
    "M": ["10001", "11011", "10101", "10001", "10001", "10001", "10001"],
    "N": ["10001", "11001", "10101", "10011", "10001", "10001", "10001"],
    "O": ["01110", "10001", "10001", "10001", "10001", "10001", "01110"],
    "P": ["11110", "10001", "10001", "11110", "10000", "10000", "10000"],
    "Q": ["01110", "10001", "10001", "10001", "10101", "10010", "01101"],
    "R": ["11110", "10001", "10001", "11110", "10100", "10010", "10001"],
    "S": ["01110", "10001", "10000", "01110", "00001", "10001", "01110"],
    "T": ["11111", "00100", "00100", "00100", "00100", "00100", "00100"],
    "U": ["10001", "10001", "10001", "10001", "10001", "10001", "01110"],
    "V": ["10001", "10001", "10001", "10001", "10001", "01010", "00100"],
    "W": ["10001", "10001", "10001", "10101", "10101", "11011", "10001"],
    "X": ["10001", "10001", "01010", "00100", "01010", "10001", "10001"],
    "Y": ["10001", "10001", "01010", "00100", "00100", "00100", "00100"],
    "Z": ["11111", "00001", "00010", "00100", "01000", "10000", "11111"],
    "[": ["01110", "01000", "01000", "01000", "01000", "01000", "01110"],
    "\\": ["10000", "01000", "00100", "00010", "00001", "00000", "00000"],
    "]": ["01110", "00010", "00010", "00010", "00010", "00010", "01110"],
    "^": ["00100", "01010", "10001", "00000", "00000", "00000", "00000"],
    "_": ["00000", "00000", "00000", "00000", "00000", "00000", "11111"],
}


@dataclass(frozen=True)
class RenderedFrame:
    image: Image.Image
    black_plane: bytes
    red_plane: bytes

    def to_bin(self) -> bytes:
        header = bytearray(15)
        header[0:4] = b"EPD1"
        header[4] = 1
        header[5:7] = EPD_WIDTH.to_bytes(2, "little")
        header[7:9] = EPD_HEIGHT.to_bytes(2, "little")
        header[9] = 2
        header[10] = 0
        header[11:15] = PLANE_BYTES.to_bytes(4, "little")
        return bytes(header) + self.black_plane + self.red_plane

    def to_png(self) -> bytes:
        buf = io.BytesIO()
        self.image.save(buf, format="PNG")
        return buf.getvalue()


@dataclass(frozen=True)
class BitmapFont:
    scale: int = 1
    x_scale: int = 1
    spacing: int = 1
    weight: int = 1

    @property
    def height(self) -> int:
        return 7 * self.scale

    def measure(self, text: str) -> tuple[int, int]:
        chars = _bitmap_text(text)
        if not chars:
            return 1, self.height
        width = 0
        for ch in chars:
            glyph = _GLYPHS_5X7.get(ch, _GLYPHS_5X7["?"])
            width += len(glyph[0]) * self.x_scale + self.spacing
        return max(1, width - self.spacing), self.height

    def draw(self, draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, fill: tuple[int, int, int]) -> None:
        x, y = xy
        for ch in _bitmap_text(text):
            glyph = _GLYPHS_5X7.get(ch, _GLYPHS_5X7["?"])
            for row, bits in enumerate(glyph):
                for col, bit in enumerate(bits):
                    if bit == "1":
                        x0 = x + col * self.x_scale
                        y0 = y + row * self.scale
                        draw.rectangle((x0, y0, x0 + self.x_scale + self.weight - 2, y0 + self.scale - 1), fill=fill)
            x += len(glyph[0]) * self.x_scale + self.spacing


# 400x300 专属黄金比例字阶定义 (彻底固定，移除可调字号)
FONT_TINY = BitmapFont(scale=1, x_scale=1, spacing=1, weight=1)       # 5x7 单倍紧凑清晰
FONT_BOLD_TINY = BitmapFont(scale=1, x_scale=1, spacing=1, weight=2)  # 5x7 加粗
FONT_MEDIUM = BitmapFont(scale=2, x_scale=2, spacing=1, weight=1)     # 10x14 标题与芯片
FONT_CLOCK = BitmapFont(scale=3, x_scale=3, spacing=1, weight=2)      # 15x21 时钟大字


def render_snapshot(snapshot: dict[str, Any]) -> RenderedFrame:
    img = Image.new("RGB", (EPD_WIDTH, EPD_HEIGHT), PAPER)
    draw = ImageDraw.Draw(img)

    systems = snapshot.get("systems") or []
    active_id = (snapshot.get("display") or {}).get("active_system_id")

    nas = _find_system_id(systems, active_id) or _find_system(systems, "NAS") or (systems[0] if systems else {})
    router = _other_system(systems, nas) or _find_system(systems, "iStoreOS") or (systems[1] if len(systems) > 1 else {})

    system = (nas or {}).get("system") or {}
    latest = (nas or {}).get("latest") or {}
    sys_name = _clip(str(system.get("name") or "DEVICE").upper(), 10)

    # =========================================================================
    # 1. 顶部 Header (y: 3 ~ 61)
    # =========================================================================
    alert = _system_alert(system, latest)
    _draw_status_block(draw, 3, 3, 56, alert)

    # 右侧时间卡片: LAST UPDATE + HH:MM (宽 96, x: 300 ~ 396, y: 3 ~ 59)
    now = datetime.now(_timezone(snapshot))
    draw.rectangle((300, 3, 396, 59), outline=INK, width=1)
    _draw_center(draw, 300, 396, 7, "LAST UPDATE", FONT_TINY, SOFT_INK)
    draw.line((305, 18, 391, 18), fill=SOFT_INK)
    time_str = now.strftime("%H:%M")
    _draw_center(draw, 300, 396, 26, time_str, FONT_CLOCK, INK)

    # 中间信息芯片 (x: 63 ~ 296, 宽 233)
    # 上排 3 芯片 (y: 3 ~ 29)
    draw.rectangle((63, 3, 146, 29), outline=INK)
    FONT_TINY.draw(draw, (67, 6), "HOST", SOFT_INK)
    FONT_TINY.draw(draw, (67, 16), sys_name, INK)

    temp_str = _temperature_label(latest.get("temperatures"))
    draw.rectangle((150, 3, 218, 29), outline=INK)
    FONT_TINY.draw(draw, (154, 6), "TEMP", SOFT_INK)
    FONT_TINY.draw(draw, (154, 16), temp_str, INK)

    load_str = _load_label(latest.get("load_average"))
    draw.rectangle((222, 3, 296, 29), outline=INK)
    FONT_TINY.draw(draw, (226, 6), "LOAD", SOFT_INK)
    FONT_TINY.draw(draw, (226, 16), load_str, INK)

    # 下排资源条 (y: 33 ~ 59)
    draw.rectangle((63, 33, 296, 59), outline=INK)
    mem_str = f"MEM: {_fmt_gb_pair(latest.get('memory_used_gb'), latest.get('memory_gb'))} ({_fmt_pct(latest.get('memory_percent'))})"
    disk_str = f"DISK: {_fmt_disk_gb_pair(latest.get('disk_used_gb'), latest.get('disk_gb'))}"
    FONT_TINY.draw(draw, (67, 36), mem_str, INK)
    FONT_TINY.draw(draw, (67, 47), disk_str, INK)

    draw.line((254, 33, 254, 59), fill=INK)
    FONT_TINY.draw(draw, (259, 37), "RANGE", SOFT_INK)
    FONT_TINY.draw(draw, (262, 47), "24H", INK)

    # =========================================================================
    # 2. 中间 3 监控波形卡片 (y: 63 ~ 125, 高 62)
    # =========================================================================
    cw = 128
    gap = 6
    r_name = _clip(str((router.get("system") or {}).get("name") or "SYS2").upper(), 8)
    panels = [
        (3, 63, cw, 62, f"{sys_name} CPU", nas.get("cpu_history", []), _fmt_pct((nas.get("latest") or {}).get("cpu_percent"))),
        (3 + cw + gap, 63, cw, 62, f"{sys_name} MEM", nas.get("memory_history", []), _fmt_pct((nas.get("latest") or {}).get("memory_percent"))),
        (3 + (cw + gap) * 2, 63, 396 - (3 + (cw + gap) * 2), 62, f"{r_name} CPU", router.get("cpu_history", []), _fmt_pct((router.get("latest") or {}).get("cpu_percent"))),
    ]

    for px, py, pw, ph, ptitle, pvals, pval in panels:
        draw.rectangle((px, py, px + pw, py + ph), outline=INK)
        draw.line((px, py + 18, px + pw, py + 18), fill=INK)
        FONT_TINY.draw(draw, (px + 5, py + 6), ptitle, SOFT_INK)
        vw = FONT_TINY.measure(pval)[0]
        FONT_TINY.draw(draw, (px + pw - vw - 5, py + 6), pval, INK)
        _sparkline(draw, px + 5, py + 22, pw - 10, ph - 28, pvals)

    # =========================================================================
    # 3. 底部 Docker 容器表格 (y: 129 ~ 297, 高 168) - 小字号精致版
    # =========================================================================
    containers = list((nas or {}).get("container_latest") or [])
    containers.sort(key=lambda item: (float(item.get("cpu_percent") or 0), float(item.get("memory_mb") or 0)), reverse=True)
    total_cpu = sum(float(item.get("cpu_percent") or 0) for item in containers)
    total_mem = sum(float(item.get("memory_mb") or 0) for item in containers)

    # 顶头条 (高度 18px)
    draw.rectangle((3, 129, 396, 147), fill=INK)
    FONT_TINY.draw(draw, (8, 134), "DOCKER CONTAINERS", PAPER)
    stat_str = f"RUN:{len(containers)}  CPU:{total_cpu:.1f}%  MEM:{_fmt_mb(total_mem)}"
    sw = FONT_TINY.measure(stat_str)[0]
    FONT_TINY.draw(draw, (391 - sw, 134), stat_str, PAPER)

    # 表格主框
    t_top = 147
    t_bottom = 297
    draw.rectangle((3, t_top, 396, t_bottom), outline=INK)

    # 5 列小字标准布局 (宽敞舒适，可容纳完整容器名，绝不溢出):
    col_x = {"name": 8, "cpu": 168, "mem": 218, "port": 276, "status": 340}

    header_h = 16
    draw.rectangle((3, t_top, 396, t_top + header_h), fill=PAPER, outline=INK)
    FONT_BOLD_TINY.draw(draw, (col_x["name"], t_top + 5), "NAME", INK)
    FONT_BOLD_TINY.draw(draw, (col_x["cpu"], t_top + 5), "CPU", INK)
    FONT_BOLD_TINY.draw(draw, (col_x["mem"], t_top + 5), "MEM", INK)
    FONT_BOLD_TINY.draw(draw, (col_x["port"], t_top + 5), "PORT", INK)
    FONT_BOLD_TINY.draw(draw, (col_x["status"], t_top + 5), "STATUS", INK)

    # 数据行: 小字号高 7px，行距 14px，视觉清爽精致
    row_y = t_top + header_h + 3
    row_h = 14
    max_rows = (t_bottom - row_y - 2) // row_h
    display_rows = containers[:max_rows]

    if not display_rows:
        _draw_center(draw, 3, 396, t_top + 40, "NO ACTIVE CONTAINERS", FONT_TINY, SOFT_INK)
    else:
        for i, item in enumerate(display_rows):
            if i > 0:
                draw.line((6, row_y - 2, 393, row_y - 2), fill=(225, 220, 205))

            c_name = _clip_to_width(str(item.get("name") or "--").upper(), FONT_TINY, col_x["cpu"] - col_x["name"] - 8)
            c_cpu = _fmt_pct(item.get("cpu_percent"))
            c_mem = _fmt_mb(item.get("memory_mb"))
            c_port = _clip_to_width(_port_label(item.get("ports")), FONT_TINY, col_x["status"] - col_x["port"] - 6)
            c_status = _clip_to_width(_status_label(item.get("status")), FONT_TINY, 392 - col_x["status"])

            FONT_TINY.draw(draw, (col_x["name"], row_y), c_name, INK)
            FONT_TINY.draw(draw, (col_x["cpu"], row_y), c_cpu, INK)
            FONT_TINY.draw(draw, (col_x["mem"], row_y), c_mem, INK)
            FONT_TINY.draw(draw, (col_x["port"], row_y), c_port, SOFT_INK)
            FONT_TINY.draw(draw, (col_x["status"], row_y), c_status, INK)

            row_y += row_h

        rem = len(containers) - len(display_rows)
        if rem > 0 and row_y < t_bottom - 10:
            FONT_TINY.draw(draw, (col_x["name"], row_y), f"... AND {rem} MORE CONTAINERS", SOFT_INK)

    if (snapshot.get("display") or {}).get("invert"):
        _invert_black_white(img)
    return _encode_frame(img)


def render_error(message: str) -> RenderedFrame:
    img = Image.new("RGB", (EPD_WIDTH, EPD_HEIGHT), PAPER)
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, EPD_WIDTH - 1, EPD_HEIGHT - 1), outline=INK)
    FONT_MEDIUM.draw(draw, (18, 18), "EPAPER DATA ERROR", INK)
    draw.line((18, 46, 382, 46), fill=INK)
    _wrapped_text(draw, (18, 60), message, 48, FONT_TINY, INK, 14)
    return _encode_frame(img)


def _draw_status_block(draw: ImageDraw.ImageDraw, x: int, y: int, size: int, alert: str) -> None:
    draw.rectangle((x, y, x + size, y + size), fill=INK)
    circle = (x + 8, y + 8, x + size - 8, y + size - 8)
    if alert == "risk":
        draw.ellipse(circle, fill=PAPER)
        draw.ellipse((circle[0] + 5, circle[1] + 5, circle[2] - 5, circle[3] - 5), fill=RED)
    elif alert == "danger":
        draw.ellipse(circle, fill=RED)
        draw.ellipse((circle[0] + 6, circle[1] + 6, circle[2] - 6, circle[3] - 6), fill=PAPER)
    else:
        draw.ellipse(circle, fill=PAPER)


def _draw_center(draw: ImageDraw.ImageDraw, left: int, right: int, y: int, text: str, font: BitmapFont, fill: tuple) -> None:
    w = font.measure(text)[0]
    x = left + (right - left - w) // 2
    font.draw(draw, (x, y), text, fill)


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


def _find_system(systems: list[dict[str, Any]], name_part: str) -> dict[str, Any] | None:
    needle = name_part.lower()
    for item in systems:
        name = str((item.get("system") or {}).get("name") or "").lower()
        if needle in name:
            return item
    return None


def _find_system_id(systems: list[dict[str, Any]], system_id: Any) -> dict[str, Any] | None:
    if not system_id:
        return None
    for item in systems:
        if str((item.get("system") or {}).get("id") or "") == str(system_id):
            return item
    return None


def _other_system(systems: list[dict[str, Any]], current: dict[str, Any]) -> dict[str, Any] | None:
    current_id = (current.get("system") or {}).get("id")
    for item in systems:
        if (item.get("system") or {}).get("id") != current_id:
            return item
    return None


def _invert_black_white(img: Image.Image) -> None:
    pixels = img.load()
    for y in range(EPD_HEIGHT):
        for x in range(EPD_WIDTH):
            r, g, b = pixels[x, y]
            if r > 130 and g < 90 and b < 90:
                continue
            pixels[x, y] = PAPER if (r + g + b) < 690 else INK


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
    unit = "Tb" if float(total) >= 1024 else "G"
    scale = 1024 if unit == "Tb" else 1
    return f"{float(used) / scale:.1f}/{float(total) / scale:.1f}{unit}"


def _fmt_disk_gb_pair(used: Any, total: Any) -> str:
    if used is None or total is None:
        return "--/--Gb"
    return f"{float(used):.0f}/{float(total):.0f}Gb"


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


def _clip_to_width(value: str, font: BitmapFont, width: int) -> str:
    text = str(value)
    if font.measure(text)[0] <= width:
        return text
    suffix = "."
    while text and font.measure(text + suffix)[0] > width:
        text = text[:-1]
    return (text + suffix) if text else suffix


def _wrapped_text(draw: ImageDraw.ImageDraw, pos: tuple[int, int], text: str, width: int, font: BitmapFont, fill: tuple[int, int, int], line_height: int) -> None:
    x, y = pos
    line = ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if len(trial) > width:
            font.draw(draw, (x, y), line, fill)
            y += line_height
            line = word
        else:
            line = trial
    if line:
        font.draw(draw, (x, y), line, fill)


def _bitmap_text(text: Any) -> str:
    out = []
    for ch in str(text).upper():
        if ch in _GLYPHS_5X7:
            out.append(ch)
        elif ch.isascii() and ch.isalpha():
            out.append(ch)
        elif ch in {"⬇", "↓"}:
            out.append("V")
        else:
            out.append("?")
    return "".join(out)


def _timezone(snapshot: dict[str, Any]) -> ZoneInfo:
    name = str((snapshot.get("display") or {}).get("timezone") or "Asia/Shanghai")
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("Asia/Shanghai")

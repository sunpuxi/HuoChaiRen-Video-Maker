from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any, Iterable, Optional


def _srt_time(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d},{milliseconds:03d}"


def _ass_time(seconds: float) -> str:
    centiseconds = max(0, round(seconds * 100))
    hours, remainder = divmod(centiseconds, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    whole_seconds, centiseconds = divmod(remainder, 100)
    return f"{hours:d}:{minutes:02d}:{whole_seconds:02d}.{centiseconds:02d}"


def _ass_text(value: str) -> str:
    return (
        value.replace("\\", r"\\")
        .replace("{", r"\{")
        .replace("}", r"\}")
        .replace("\r\n", r"\N")
        .replace("\r", r"\N")
        .replace("\n", r"\N")
    )


def _ass_color(value: str, default: str) -> str:
    raw = value.strip().lstrip("#")
    if len(raw) not in {6, 8} or any(char not in "0123456789abcdefABCDEF" for char in raw):
        raw = default.lstrip("#")
    if len(raw) == 6:
        red, green, blue = raw[0:2], raw[2:4], raw[4:6]
        alpha = "00"
    else:
        red, green, blue = raw[0:2], raw[2:4], raw[4:6]
        # Config uses CSS-style RGBA opacity; ASS stores inverse alpha.
        alpha = f"{255 - int(raw[6:8], 16):02X}"
    return f"&H{alpha}{blue}{green}{red}"


def write_srt(cues: Iterable[dict[str, Any]], output: Path) -> None:
    blocks: list[str] = []
    for cue in cues:
        text = str(cue["text"]).strip()
        if not text:
            continue
        index = len(blocks) + 1
        blocks.append(
            f"{index}\n{_srt_time(float(cue['start']))} --> "
            f"{_srt_time(float(cue['end']))}\n{text}"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n\n".join(blocks) + ("\n" if blocks else ""), encoding="utf-8")


def write_ass(
    cues: Iterable[dict[str, Any]],
    output: Path,
    *,
    width: int,
    height: int,
    style: Optional[dict[str, Any]] = None,
) -> None:
    options = style or {}
    reference_scale = height / 720
    font_name = str(options.get("font_name", "PingFang SC"))
    font_size = max(12, round(float(options.get("font_size", 42)) * reference_scale))
    margin_v = max(8, round(float(options.get("margin_bottom", 34)) * reference_scale))
    outline = max(1, round(float(options.get("outline", 3)) * reference_scale))
    shadow = max(0, round(float(options.get("shadow", 1)) * reference_scale))
    primary = _ass_color(str(options.get("color", "#FFFFFF")), "#FFFFFF")
    outline_color = _ass_color(str(options.get("outline_color", "#171717")), "#171717")
    background = _ass_color(str(options.get("background_color", "#171717B8")), "#171717B8")

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{font_size},{primary},{primary},{outline_color},{background},-1,0,0,0,100,100,0,0,3,{outline},{shadow},2,40,40,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    for cue in cues:
        text = _ass_text(str(cue["text"]).strip())
        if not text:
            continue
        events.append(
            f"Dialogue: 0,{_ass_time(float(cue['start']))},"
            f"{_ass_time(float(cue['end']))},Default,,0,0,0,,{text}"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(header + "\n".join(events) + ("\n" if events else ""), encoding="utf-8")


def _wrap_caption(text: str, max_characters: int) -> list[str]:
    explicit_lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lines: list[str] = []
    for explicit in explicit_lines:
        value = explicit.strip()
        if not value:
            continue
        while len(value) > max_characters:
            split_at = max_characters
            for punctuation in "，。！？；：、,.!?;: ":
                candidate = value.rfind(punctuation, max_characters // 2, max_characters + 1)
                if candidate >= max_characters // 2:
                    split_at = candidate + 1
                    break
            lines.append(value[:split_at].strip())
            value = value[split_at:].strip()
        if value:
            lines.append(value)
    return lines or [""]


def render_subtitle_svg(
    text: str,
    *,
    width: int,
    height: int,
    style: Optional[dict[str, Any]] = None,
) -> str:
    options = style or {}
    scale = height / 720
    font_name = escape(str(options.get("font_name", "PingFang SC")), quote=True)
    font_size = max(12, round(float(options.get("font_size", 42)) * scale))
    margin_bottom = max(8, round(float(options.get("margin_bottom", 34)) * scale))
    outline = max(0, round(float(options.get("outline", 2)) * scale))
    color = escape(str(options.get("color", "#FFFFFF")), quote=True)
    outline_color = escape(str(options.get("outline_color", "#171717")), quote=True)
    background = escape(str(options.get("background_color", "#171717B8")), quote=True)
    max_characters = max(8, int(width * 0.78 / font_size))
    lines = _wrap_caption(text.strip(), max_characters)
    line_height = round(font_size * 1.28)
    horizontal_padding = round(font_size * 0.8)
    vertical_padding = round(font_size * 0.45)
    estimated_width = min(
        round(width * 0.9),
        max(round(font_size * 4), max(len(line) for line in lines) * font_size + horizontal_padding * 2),
    )
    box_height = len(lines) * line_height + vertical_padding * 2
    box_x = round((width - estimated_width) / 2)
    box_y = height - margin_bottom - box_height
    radius = max(6, round(16 * scale))
    first_baseline = box_y + vertical_padding + font_size
    tspans = "".join(
        f'<tspan x="{width / 2:g}" y="{first_baseline + index * line_height:g}">'
        f"{escape(line)}</tspan>"
        for index, line in enumerate(lines)
    )
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
      <rect x="{box_x}" y="{box_y}" width="{estimated_width}" height="{box_height}" rx="{radius}" fill="{background}"/>
      <text text-anchor="middle" font-family="{font_name}, sans-serif" font-size="{font_size}" font-weight="700" fill="{color}" stroke="{outline_color}" stroke-width="{outline}" paint-order="stroke fill">{tspans}</text>
    </svg>'''

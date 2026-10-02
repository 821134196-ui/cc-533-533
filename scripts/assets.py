"""素材生成：物品缩略图与作品照片（纯 SVG，白底 + 木色 + 靛蓝）。"""
from __future__ import annotations

from pathlib import Path

INDIGO = "#2f4b7c"
INDIGO_DARK = "#22345a"
WOOD = "#c8a06a"
WOOD_DARK = "#a87f4c"
PAPER = "#efe6d8"
INK = "#3a3a36"
RED = "#c0504d"
GREEN = "#4f8a5b"


def _save(path: Path, body: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return f"/assets/icons/{path.name}" if path.parent.name == "icons" \
        else f"/assets/photos/{path.name}"


# ---------------------------------------------------------------- 物品图标

_STYLE = f"fill:none;stroke:{INDIGO};stroke-width:5;stroke-linecap:round;stroke-linejoin:round"


def _icon(name: str, inner: str, *, fill: str = "", stroke: str = INDIGO) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" role="img" aria-label="{name}">'
        f'<rect width="64" height="64" rx="12" fill="#fff"/>'
        f'<g style="fill:none;stroke:{stroke};stroke-width:4.5;stroke-linecap:round;stroke-linejoin:round">'
        f'{inner}</g></svg>'
    )


ICONS: dict[str, tuple[str, str]] = {
    "turntable": (
        "迷你转盘",
        '<ellipse cx="32" cy="24" rx="18" ry="7" fill="#eaf0f8"/>'
        '<path d="M14 24v6c0 4 8 7 18 7s18-3 18-7v-6"/>'
        '<rect x="22" y="40" width="20" height="7" rx="2"/>'
        '<rect x="26" y="49" width="12" height="6" rx="2"/>'),
    "clay-tool": (
        "塑形刀",
        '<path d="M12 52 L40 24"/><rect x="9" y="46" width="10" height="10" rx="2" '
        f'transform="rotate(-45 14 51)" fill="{WOOD}" stroke="{WOOD_DARK}"/>'
        '<path d="M40 24 L52 12 L54 14 L42 26 Z" fill="#dce4f0"/>'),
    "brush": (
        "描金笔",
        f'<rect x="27" y="8" width="7" height="30" rx="3" fill="{WOOD}" stroke="{WOOD_DARK}"/>'
        '<path d="M27 38 h7 l-2 8 h-3 Z" fill="#d9d5cc"/>'
        '<path d="M30 46 v8 M28 54 h4"/>'),
    "clay": (
        "陶土包",
        f'<path d="M16 26 h32 v22 a6 6 0 0 1 -6 6 h-20 a6 6 0 0 1 -6 -6 Z" fill="{PAPER}"/>'
        '<path d="M16 26 c0-6 8-8 16-8 s16 2 16 8"/>'
        '<path d="M24 38 h16" stroke-width="3"/>'),
    "cloth": (
        "擦拭布",
        f'<path d="M12 20 h40 v28 l-10 -4 -10 4 -10 -4 -10 4 Z" fill="#eef2f7"/>'
        '<path d="M22 28 h20 M22 34 h14" stroke-width="3"/>'),
    "sandpaper": (
        "砂纸",
        f'<rect x="14" y="16" width="36" height="32" rx="3" fill="#e4ddd0"/>'
        '<path d="M14 16 l36 32 M50 16 l-36 32" stroke-width="2.5" stroke-dasharray="2 4"/>'),
    "lacquer": (
        "金缮漆",
        f'<rect x="20" y="22" width="24" height="30" rx="4" fill="{PAPER}"/>'
        '<rect x="24" y="14" width="16" height="9" rx="2" fill="#d9b44a" stroke="#b89434"/>'
        '<path d="M25 34 h14" stroke-width="3"/>'),
    "sponge": (
        "吸水海绵",
        '<rect x="14" y="22" width="36" height="22" rx="6" fill="#f2e07a" stroke="#c9b23f"/>'
        '<circle cx="23" cy="30" r="2" stroke-width="2"/>'
        '<circle cx="33" cy="33" r="2" stroke-width="2"/>'
        '<circle cx="42" cy="29" r="2" stroke-width="2"/>'
        '<path d="M18 44 v6 M46 44 v6"/>'),
}


def color_icon(hex_code: str) -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" role="img" aria-label="釉料">'
        '<rect width="64" height="64" rx="12" fill="#fff"/>'
        f'<path d="M32 10 c10 12 16 20 16 28 a16 16 0 0 1 -32 0 c0-8 6-16 16-28 Z" '
        f'fill="{hex_code}" stroke="{INDIGO}" stroke-width="3.5"/>'
        '<ellipse cx="26" cy="36" rx="4" ry="3" fill="#ffffff" opacity="0.45"/></svg>'
    )


COLOR_HEX = {
    "indigo": "#2f4b7c",
    "rice": "#efe9dc",
    "persimmon": "#d77e4f",
    "loess": "#b0794b",
    "slate": "#6c8aa6",
}


def generate_icons(icons_dir: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, (label, inner) in ICONS.items():
        p = icons_dir / f"{key}.svg"
        _save(p, _icon(label, inner))
        out[key] = f"/assets/icons/{key}.svg"
    for key, hexv in COLOR_HEX.items():
        p = icons_dir / f"glaze-{key}.svg"
        _save(p, color_icon(hexv))
        out[f"glaze-{key}"] = f"/assets/icons/glaze-{key}.svg"
    return out


# ---------------------------------------------------------------- 作品照片

def _photo_svg(title: str, subtitle: str, state: str, tag: str, tag_color: str) -> str:
    """state: intact / damaged / finished / received 四种场景构图。"""
    crack = (
        '<path d="M300 150 l-8 22 l10 14 l-9 18 l8 16" fill="none" '
        f'stroke="{RED}" stroke-width="3.5"/>'
        '<path d="M292 172 l-14 -6 M302 186 l16 -8 M293 204 l-13 8" fill="none" '
        f'stroke="{RED}" stroke-width="2.5"/>')
    torn = (
        f'<path d="M150 150 l30 -14 l26 10 l30 -12 l28 12 l26 -8 l20 12" '
        f'fill="none" stroke="{RED}" stroke-width="4"/>')
    if state == "damaged":
        cup_fill, extra = "#e8ddd0", crack
        box_deco = torn
    elif state == "finished":
        cup_fill, extra, box_deco = INDIGO, (
            '<path d="M270 188 q30 -14 60 0" fill="none" stroke="#f2d98c" stroke-width="4"/>'
            '<circle cx="300" cy="176" r="6" fill="#f2d98c"/>'), ""
    else:
        cup_fill, extra, box_deco = "#f6f1e8", "", ""

    chips = ""
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 480 320" font-family="sans-serif">
<rect width="480" height="320" fill="#ffffff"/>
<rect x="0" y="252" width="480" height="68" fill="{WOOD}" opacity="0.55"/>
<rect x="150" y="120" width="300" height="120" rx="6" fill="{PAPER}" stroke="{WOOD_DARK}" stroke-width="3"/>
{box_deco}
<path d="M150 120 l36 -18 M450 120 l-36 -18" fill="none" stroke="{WOOD_DARK}" stroke-width="3"/>
<path d="M270 168 h60 l-6 52 a14 14 0 0 1 -14 12 h-20 a14 14 0 0 1 -14 -12 Z"
      fill="{cup_fill}" stroke="{INDIGO_DARK if state=='finished' else INDIGO}" stroke-width="3"/>
<ellipse cx="300" cy="168" rx="30" ry="9" fill="#ffffff" opacity="0.7" stroke="{INDIGO}" stroke-width="2.5"/>
{extra}
<text x="24" y="46" font-size="22" font-weight="700" fill="{INK}">{title}</text>
<text x="24" y="74" font-size="14" fill="#777063">{subtitle}</text>
<rect x="24" y="270" width="118" height="28" rx="14" fill="{tag_color}"/>
<text x="83" y="289" font-size="14" font-weight="700" fill="#fff" text-anchor="middle">{tag}</text>
</svg>'''


PHOTOS = [
    ("work_sent_e001", "作品寄出 · 学员拍摄", "陶杯以气泡膜双层包装，外观完好", "intact", "去程·已揽收", INDIGO),
    ("work_received_e001", "作品签收 · 工作室核对", "开箱照与寄出照片比对一致，无磕碰", "intact", "去程·已签收", GREEN),
    ("finished_sent_e001", "成品返寄 · 工作室装箱", "烧制完成，定制木框装裱，泡沫定型", "finished", "返程·已发出", INDIGO),
    ("finished_received_e001", "成品收回 · 学员确认", "木框与釉面完好，签收无异议", "finished", "返程·已签收", GREEN),
    ("kit_packed_e006", "材料包装箱 · 现场自取", "两人核对后封箱，学员到课领取", "intact", "现场·自取", WOOD_DARK),
    ("damage_kit_e004", "材料包签收异常", "外箱一角受潮塌陷，釉料瓶碎裂渗漏", "damaged", "配送破损", RED),
    ("damage_work_e005", "作品回寄破损", "外箱挤压，陶杯口沿出现新磕裂纹", "damaged", "运输损坏", RED),
]


def generate_photos(photos_dir: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, title, subtitle, state, tag, color in PHOTOS:
        p = photos_dir / f"{name}.svg"
        url = _save(p, _photo_svg(title, subtitle, state, tag, color))
        out[name] = url
    return out

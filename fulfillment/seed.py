"""种子数据：一键重建演示库，内置缺件 / 未确认替代色 / 运输损坏索赔案例。

运行后各材料包状态：
* KIT-E001 林晓：全流程已完成（去程→烧制→返程），期间有返寄地址变更；
* KIT-E002 陈岳：缺件挂账（陶土少 1 块），复核被阻止 —— 缺件案例；
* KIT-E003 周岚：靛蓝釉替代为雾蓝釉，学员尚未确认 —— 替代色案例；
* KIT-E004 赵衡：课程改版到 v2 后报名，材料包按新 BOM 生成；
* KIT-E005 苏白：作品回寄运输破损，已提索赔待审批 —— 损坏索赔案例。
"""
from __future__ import annotations

import base64
import shutil
from pathlib import Path

from .db import connect, init_db
from .services import FulfillmentService

# ---- 极简 SVG 生成器：不依赖图片素材，缩略图/照片均可被浏览器直接渲染 --------

def _svg_thumbnail(name: str, kind: str, swatch: str | None = None) -> bytes:
    bg = {"tool": "#eef2f7", "consumable": "#f4ece2", "color": "#f6f6f6"}[kind]
    border = {"tool": "#2f4b7c", "consumable": "#a9845b",
              "color": swatch or "#888"}[kind]
    shape = ""
    if kind == "tool":
        shape = (f'<rect x="26" y="42" width="48" height="10" rx="5" '
                 f'fill="{border}"/><circle cx="30" cy="47" r="7" fill="#c99b6d"/>')
    elif kind == "consumable":
        shape = (f'<path d="M38 30h24l6 34v6H32v-6z" fill="#d8b58a" '
                 f'stroke="{border}" stroke-width="2"/>')
    else:
        shape = (f'<rect x="30" y="28" width="40" height="40" rx="6" '
                 f'fill="{swatch}" stroke="#333" stroke-width="1.5"/>')
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 100 100">
<rect width="100" height="100" rx="12" fill="{bg}" stroke="{border}" stroke-width="2"/>
{shape}
<text x="50" y="90" text-anchor="middle" font-size="9" fill="#333"
 font-family="sans-serif">{name}</text>
</svg>'''.encode()


def _svg_photo(label: str, tone: str, note: str = "") -> bytes:
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="480" height="320" viewBox="0 0 480 320">
<rect width="480" height="320" fill="#faf8f4"/>
<rect x="40" y="50" width="400" height="200" rx="10" fill="#fff" stroke="#d8cdbd" stroke-width="2"/>
<rect x="70" y="95" width="150" height="110" rx="6" fill="{tone}" stroke="#8a7a63"/>
<path d="M250 150 l30 -34 l30 34 l30 -22 l40 40 v37 h-200 z" fill="#c99b6d" opacity="0.55"/>
<text x="240" y="290" text-anchor="middle" font-size="20" fill="#2f4b7c"
 font-family="sans-serif">{label}</text>
<text x="240" y="312" text-anchor="middle" font-size="13" fill="#8a7a63"
 font-family="sans-serif">{note}</text>
</svg>'''.encode()


# ---- BOM 定义 --------------------------------------------------------------

POTTERY_V1 = [
    ("tool-roller", "木质擀泥棍", "tool", None, "根", 1, "tool"),
    ("tool-loop", "修坯刀套装", "tool", None, "套", 1, "tool"),
    ("tool-calipers", "游标卡规", "tool", None, "把", 1, "tool"),
    ("cons-clay", "陶土", "consumable", None, "块", 2, "consumable"),
    ("cons-base-glaze", "透明釉底料", "consumable", None, "瓶", 1, "consumable"),
    ("color-indigo", "靛蓝釉", "color", "釉色", "瓶", 1, "color", "#2f4b7c"),
    ("color-rust", "赭石釉", "color", "釉色", "瓶", 1, "color", "#9c5a3c"),
    ("color-bamboo", "竹青釉", "color", "釉色", "瓶", 1, "color", "#7d8f69"),
]

POTTERY_V2 = [  # 改版：陶土加到 3 块、新增海绵与月白釉、下架修坯刀
    ("tool-roller", "木质擀泥棍", "tool", None, "根", 1, "tool"),
    ("tool-sponge", "吸水海绵", "tool", None, "块", 2, "tool"),
    ("tool-calipers", "游标卡规", "tool", None, "把", 1, "tool"),
    ("cons-clay", "陶土", "consumable", None, "块", 3, "consumable"),
    ("cons-base-glaze", "透明釉底料", "consumable", None, "瓶", 1, "consumable"),
    ("color-indigo", "靛蓝釉", "color", "釉色", "瓶", 1, "color", "#2f4b7c"),
    ("color-rust", "赭石釉", "color", "釉色", "瓶", 1, "color", "#9c5a3c"),
    ("color-bamboo", "竹青釉", "color", "釉色", "瓶", 1, "color", "#7d8f69"),
    ("color-moon", "月白釉", "color", "釉色", "瓶", 1, "color", "#dfe3e8"),
]

WEAVING_V1 = [
    ("tool-frame", "木质织架", "tool", None, "台", 1, "tool"),
    ("tool-shuttle", "木梭", "tool", None, "支", 2, "tool"),
    ("cons-warp", "经纱卷", "consumable", None, "卷", 1, "consumable"),
    ("yarn-indigo", "靛蓝毛线", "color", "织线色", "团", 2, "color", "#2f4b7c"),
    ("yarn-sand", "沙色毛线", "color", "织线色", "团", 2, "color", "#d8b58a"),
    ("yarn-terra", "陶土色毛线", "color", "织线色", "团", 1, "color", "#9c5a3c"),
]


def _bom(rows: list[tuple]) -> list[dict]:
    out = []
    for r in rows:
        d = dict(item_id=r[0], name=r[1], category=r[2], color_role=r[3],
                 unit=r[4], qty=r[5], thumbnail=f"{r[0]}.svg")
        if len(r) > 6:
            d["swatch"] = r[6]
        out.append (d)
    return out


def _write_thumbnails(static_img: Path) -> None:
    static_img.mkdir(parents=True, exist_ok=True)
    for spec in _bom(POTTERY_V1) + _bom(POTTERY_V2) + _bom(WEAVING_V1):
        p = static_img / spec["thumbnail"]
        if not p.exists():
            p.write_bytes(_svg_thumbnail(
                spec["name"], spec["category"], spec.get("swatch")))
    # 临时替代色（不在任何版本 BOM 中，仅替代场景使用）
    mist = static_img / "color-mist.svg"
    if not mist.exists():
        mist.write_bytes(_svg_thumbnail("雾蓝釉(替代)", "color", "#8fa6bf"))


def seed(db_path: str | Path, photo_dir: str | Path,
         static_img: str | Path, *, reset: bool = True) -> None:
    db_path, photo_dir, static_img = (
        Path(db_path), Path(photo_dir), Path(static_img))
    if reset and db_path.exists():
        db_path.unlink()
    if reset and photo_dir.exists():
        shutil.rmtree(photo_dir)
    photo_dir.mkdir(parents=True, exist_ok=True)
    _write_thumbnails(static_img)

    conn = connect(db_path)
    init_db(conn)
    svc = FulfillmentService(conn, photo_dir=photo_dir)

    # 课程与版本
    svc.create_course("C-POTTERY", "陶艺手作课")
    svc.create_version("C-POTTERY", "POT-V1", "v1", _bom(POTTERY_V1),
                       notes="初版：8 件套")
    svc.create_course("C-WEAVING", "编织挂毯课")
    svc.create_version("C-WEAVING", "WV-V1", "v1", _bom(WEAVING_V1))

    # ---- 案例 1：林晓，全流程履约完成，中途变更返寄地址 --------------------
    svc.create_enrollment("E001", "林晓", "POT-V1",
                          "杭州市西湖区文三路 88 号 3-201",
                          work_due_date="2026-09-20")
    k1 = svc.create_kit("E001", actor="admin")
    for line in svc.get_kit(k1)["lines"]:
        svc.check_line(k1, line["item_id"], line["planned_qty"], "阿珍")
    svc.confirm_packing(k1, "阿珍", "packer")
    svc.confirm_packing(k1, "老沈", "checker")
    wb = svc.ship_kit(k1)
    out1 = _sid(conn, k1, "kit_outbound")
    svc.deliver_shipment(out1)
    svc.add_photo("kit_outbound",
                  _svg_photo("材料包寄出装箱", "#e7e0d2", "去程 · 双人核对封箱"),
                  "svg", kit_no=k1, caption="材料包寄出时")
    svc.send_work_back(k1, late=False, actor="林晓")
    in1 = _sid(conn, k1, "work_inbound")
    svc.deliver_shipment(in1)
    svc.add_photo("return_inbound",
                  _svg_photo("作品寄出前状态", "#dfe6ef", "作品回寄 · 学员寄出"),
                  "svg", kit_no=k1, shipment_id=in1, caption="学员寄出作品时")
    svc.receive_work(k1, "firing")
    for stage, note in [("queued", "入窑排队"), ("in_progress", "素烧+釉烧"),
                        ("qa", "出窑质检"), ("done", "烧制完成")]:
        svc.advance_job(k1, stage, note)
    svc.change_address("E001", "杭州市滨江区江虹路 1750 号 6-1102",
                       actor="林晓", reason="搬家至新居")
    svc.ship_finished(k1)
    fin1 = _sid(conn, k1, "finish_outbound")
    svc.add_photo("finish_outbound",
                  _svg_photo("成品返寄包装", "#e3e9f2", "返程 · 按新地址寄出"),
                  "svg", kit_no=k1, shipment_id=fin1, caption="成品返寄寄出时")
    svc.deliver_shipment(fin1)
    svc.add_photo("finish_returned",
                  _svg_photo("学员收回成品", "#e6efe6", "返程 · 本人签收完好"),
                  "svg", kit_no=k1, shipment_id=fin1, caption="学员收回成品时")

    # ---- 案例 2：陈岳，缺件 —— 陶土仅装 1/2，异常未结，复核被阻止 ---------
    svc.create_enrollment("E002", "陈岳", "POT-V1",
                          "南京市鼓楼区中山北路 200 号",
                          work_due_date="2026-09-25")
    k2 = svc.create_kit("E002", actor="admin")
    for line in svc.get_kit(k2)["lines"]:
        qty = 1 if line["item_id"] == "cons-clay" else line["planned_qty"]
        svc.check_line(k2, line["item_id"], qty, "阿珍")
    svc.confirm_packing(k2, "阿珍", "packer")   # 自动登记缺件异常

    # ---- 案例 3：周岚，替代色未确认 —— 靛蓝釉 → 雾蓝釉 --------------------
    svc.create_enrollment("E003", "周岚", "POT-V1",
                          "苏州市姑苏区干将东路 100 号",
                          work_due_date="2026-09-25")
    k3 = svc.create_kit("E003", actor="admin")
    for line in svc.get_kit(k3)["lines"]:
        svc.check_line(k3, line["item_id"], line["planned_qty"], "小柯")
    svc.substitute_color(k3, "color-indigo", "雾蓝釉（临时替代）",
                         "color-mist.svg", actor="小柯")
    svc.confirm_packing(k3, "小柯", "packer")

    # ---- 课程改版 v2（不影响 E001~E003 已生成的包）----------------------
    svc.create_version("C-POTTERY", "POT-V2", "v2", _bom(POTTERY_V2),
                       notes="陶土加量、配海绵、新增月白釉、下架修坯刀")
    svc.create_enrollment("E004", "赵衡", "POT-V2",
                          "成都市武侯区天府三街 218 号",
                          work_due_date="2026-10-15")
    k4 = svc.create_kit("E004", actor="admin")  # 清单按 v2 快照（9 行）

    # ---- 案例 5：苏白，作品回寄运输破损，索赔待审批 -----------------------
    svc.create_enrollment("E005", "苏白", "WV-V1",
                          "长沙市岳麓区麓山南路 60 号",
                          work_due_date="2026-09-30")
    k5 = svc.create_kit("E005", actor="admin")
    for line in svc.get_kit(k5)["lines"]:
        svc.check_line(k5, line["item_id"], line["planned_qty"], "老沈")
    svc.confirm_packing(k5, "老沈", "packer")
    svc.confirm_packing(k5, "阿珍", "checker")
    svc.ship_kit(k5)
    out5 = _sid(conn, k5, "kit_outbound")
    svc.deliver_shipment(out5)
    svc.add_photo("kit_outbound",
                  _svg_photo("材料包寄出装箱", "#e7e0d2", "去程 · 完好发出"),
                  "svg", kit_no=k5, caption="材料包寄出时")
    svc.send_work_back(k5, late=True, actor="苏白")   # 同时演示迟交留痕
    in5 = _sid(conn, k5, "work_inbound")
    svc.add_photo("return_inbound",
                  _svg_photo("作品寄出前状态", "#dfe6ef", "作品回寄 · 寄出时完好"),
                  "svg", kit_no=k5, shipment_id=in5, caption="学员寄出作品时")
    svc.deliver_shipment(in5, damaged=True)           # 收回时外箱破损
    svc.add_photo("damage",
                  _svg_photo("收回时外箱破损", "#e8d8d2", "作品回寄 · 运输损坏"),
                  "svg", kit_no=k5, shipment_id=in5, caption="工作室收回时（损坏）")
    svc.file_claim(in5, "回寄作品运输损坏索赔",
                   "外箱受挤压，织架变形、挂毯沾污，申请物料与工时补偿。",
                   260, actor="苏白")

    conn.commit()
    conn.close()


def _sid(conn, kit_no: str, direction: str) -> int:
    row = conn.execute(
        "SELECT shipment_id FROM shipments WHERE kit_no=? AND direction=? "
        "ORDER BY shipment_id DESC LIMIT 1", (kit_no, direction)).fetchone()
    return row["shipment_id"]

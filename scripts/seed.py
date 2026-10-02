"""种子数据：两个课程版本 + 六个学员案例（含缺件/替代色/破损/迟交/地址变更）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from studio import services as svc  # noqa: E402
from studio.db import connect, init_db  # noqa: E402
from scripts.assets import generate_icons, generate_photos  # noqa: E402

V1 = "kintsugi-v1"
V2 = "kintsugi-v2"


def _items(icons: dict[str, str], v2: bool = False) -> list[dict]:
    items = [
        {"sku": "tool-turntable", "name": "迷你转盘", "category": "tool",
         "icon": icons["turntable"], "qty": 1},
        {"sku": "tool-knife", "name": "塑形刀", "category": "tool",
         "icon": icons["clay-tool"], "qty": 2},
        {"sku": "tool-brush", "name": "描金笔", "category": "tool",
         "icon": icons["brush"], "qty": 2},
        {"sku": "con-clay", "name": "陶土包（500g）", "category": "consumable",
         "icon": icons["clay"], "qty": 3 if v2 else 2},
        {"sku": "con-cloth", "name": "擦拭布", "category": "consumable",
         "icon": icons["cloth"], "qty": 2},
        {"sku": "con-sandpaper", "name": "砂纸套装", "category": "consumable",
         "icon": icons["sandpaper"], "qty": 1},
        {"sku": "con-lacquer", "name": "金缮漆", "category": "consumable",
         "icon": icons["lacquer"], "qty": 1},
        {"sku": "con-sponge", "name": "吸水海绵", "category": "consumable",
         "icon": icons["sponge"], "qty": 1},
        {"sku": "glaze-indigo", "name": "釉料·靛蓝", "category": "color",
         "icon": icons["glaze-indigo"], "qty": 1, "color_code": "indigo"},
        {"sku": "glaze-rice", "name": "釉料·米白", "category": "color",
         "icon": icons["glaze-rice"], "qty": 1, "color_code": "rice"},
        {"sku": "glaze-persimmon", "name": "釉料·柿红", "category": "color",
         "icon": icons["glaze-persimmon"], "qty": 1, "color_code": "persimmon"},
    ]
    if v2:
        items.append({"sku": "glaze-slate", "name": "釉料·灰蓝", "category": "color",
                      "icon": icons["glaze-slate"], "qty": 1, "color_code": "slate"})
    return items


def _pack_everything(conn, kit_id: str, version_items: list[dict]) -> None:
    for it in version_items:
        svc.pack_item(conn, kit_id, it["sku"], it["qty"])


def seed(db_path: str | Path, web_root: Path, *, force: bool = False) -> None:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if force and db_path.exists():
        db_path.unlink()
        for suffix in ("-wal", "-shm"):
            p = Path(str(db_path) + suffix)
            if p.exists():
                p.unlink()

    icons = generate_icons(web_root / "assets" / "icons")
    photos = generate_photos(web_root / "assets" / "photos")

    conn = connect(db_path)
    init_db(conn)
    if conn.execute("SELECT COUNT(*) c FROM course_versions").fetchone()["c"]:
        print("数据库已有数据，跳过播种。")
        return

    # ---- 课程版本：v1 已开课；v2 是改版（陶土加量 + 新增灰蓝釉色）----
    svc.create_version(conn, V1, "金缮修复手作课",
                       "器物清洁、拼接与描金全流程，含烧制。", _items(icons))
    svc.create_version(conn, V2, "金缮修复手作课（秋季改版）",
                       "在 v1 基础上陶土加量至 3 包，新增灰蓝釉色；已发材料包清单不变。",
                       _items(icons, v2=True))

    # ---- E001 全链路顺利案例：打包→发运→作品回寄→烧制→地址变更→成品返寄 ----
    svc.create_enrollment(conn, "E001", "林一禾", "online", V1,
                          "杭州市西湖区文三路 88 号 3-201", "138****0101")
    kit1 = svc.generate_kit(conn, "E001")
    _pack_everything(conn, kit1["id"], _items(icons))
    svc.confirm_packing(conn, kit1["id"], "阿川", "晓岚")
    out1 = svc.ship_kit(conn, kit1["id"])
    svc.deliver_shipment(conn, out1["tracking_no"])

    wo = svc.create_work_order(conn, "E001", "firing", "2026-10-20")
    svc.add_photo(conn, "work_sent", wo["id"], photos["work_sent_e001"],
                  "寄出时陶杯完好，气泡膜双层包装")
    inb1 = svc.submit_work(conn, wo["id"])
    svc.deliver_shipment(conn, inb1["tracking_no"])
    svc.add_photo(conn, "work_received", wo["id"], photos["work_received_e001"],
                  "开箱与寄出照片比对一致")
    svc.update_work_status(conn, wo["id"], "in_progress")
    svc.update_work_status(conn, wo["id"], "firing")
    svc.update_work_status(conn, wo["id"], "qc")
    svc.update_work_status(conn, wo["id"], "done")
    svc.change_return_address(conn, wo["id"], "杭州市滨江区江南大道 100 号 12 幢 801",
                              by="学员电话客服")
    svc.add_photo(conn, "finished_sent", wo["id"], photos["finished_sent_e001"],
                  "烧制完成，木框装裱，泡沫定型装箱")
    ret1 = svc.return_finished(conn, wo["id"])
    svc.deliver_shipment(conn, ret1["tracking_no"])
    svc.add_photo(conn, "finished_received", wo["id"], photos["finished_received_e001"],
                  "学员确认木框釉面完好")

    # ---- E002 替代颜色未经确认：封箱被拦截 ----
    svc.create_enrollment(conn, "E002", "苏叶", "online", V1,
                          "南京市鼓楼区汉口路 22 号", "139****0202")
    kit2 = svc.generate_kit(conn, "E002")
    for it in _items(icons):
        if it["sku"] == "glaze-persimmon":
            svc.pack_item(conn, kit2["id"], it["sku"], 1, substitute_color="slate")
        else:
            svc.pack_item(conn, kit2["id"], it["sku"], it["qty"])

    # ---- E003 缺件案例：陶土缺货，装箱核对不通过 ----
    svc.create_enrollment(conn, "E003", "陈屿", "online", V1,
                          "苏州市姑苏区平江路 9 号", "137****0303")
    kit3 = svc.generate_kit(conn, "E003")
    for it in _items(icons):
        qty = 0 if it["sku"] == "con-clay" else it["qty"]
        svc.pack_item(conn, kit3["id"], it["sku"], qty)

    # ---- E004 配送破损：正常封箱发运，签收时釉料破损，已开索赔并处理 ----
    svc.create_enrollment(conn, "E004", "何苗", "online", V1,
                          "成都市武侯区桐梓林东路 5 号", "136****0404")
    kit4 = svc.generate_kit(conn, "E004")
    _pack_everything(conn, kit4["id"], _items(icons))
    svc.confirm_packing(conn, kit4["id"], "晓岚", "阿川")
    out4 = svc.ship_kit(conn, kit4["id"])
    res4 = svc.deliver_shipment(
        conn, out4["tracking_no"], damaged=True,
        damage_note="外箱一角受潮塌陷，金缮漆瓶碎裂渗漏",
        damaged_items=["金缮漆 x1"])
    svc.add_photo(conn, "claim", res4["claim"]["id"], photos["damage_kit_e004"],
                  "学员签收时拍摄的破损照片")
    svc.resolve_claim(conn, res4["claim"]["id"], "approved",
                      "核实属运输破损，补发金缮漆一瓶并补偿运费 12 元", by="客服 阿澈")

    # ---- E005 迟交 + 作品回寄运输损坏 ----
    svc.create_enrollment(conn, "E005", "韩立", "online", V1,
                          "武汉市洪山区珞喻路 129 号", "135****0505")
    kit5 = svc.generate_kit(conn, "E005")
    _pack_everything(conn, kit5["id"], _items(icons))
    svc.confirm_packing(conn, kit5["id"], "阿川", "晓岚")
    out5 = svc.ship_kit(conn, kit5["id"])
    svc.deliver_shipment(conn, out5["tracking_no"])

    wo5 = svc.create_work_order(conn, "E005", "firing", "2026-09-25")
    inb5 = svc.submit_work(conn, wo5["id"])  # 今天 2026-10-02，超过约定交件日 → 迟交留痕
    res5 = svc.deliver_shipment(
        conn, inb5["tracking_no"], damaged=True,
        damage_note="外箱受挤压，陶杯口沿出现新磕裂纹",
        damaged_items=["陶杯口沿磕裂"])
    svc.add_photo(conn, "claim", res5["claim"]["id"], photos["damage_work_e005"],
                  "工作室开箱拍摄的损坏照片")
    svc.resolve_claim(conn, res5["claim"]["id"], "resolved",
                      "与学员协商一致：工作室免费二次修复后再烧制，学员补寄包装要求已同步",
                      by="客服 阿澈")

    # ---- E006 现场学员（v2 改版）：两人核对后到课自取，无运单 ----
    svc.create_enrollment(conn, "E006", "周晴", "onsite", V2, "", "")
    kit6 = svc.generate_kit(conn, "E006")
    _pack_everything(conn, kit6["id"], _items(icons, v2=True))
    svc.confirm_packing(conn, kit6["id"], "阿川", "晓岚")
    svc.hand_over_onsite(conn, kit6["id"], by="前台")
    svc.add_photo(conn, "kit_packed", kit6["id"], photos["kit_packed_e006"],
                  "现场班材料包封箱照")

    # ---- E007 v2 线上学员：材料包已生成待装箱（演示改版不影响旧包的对照）----
    svc.create_enrollment(conn, "E007", "顾南", "online", V2,
                          "厦门市思明区曾厝垵北路 3 号", "134****0707")
    svc.generate_kit(conn, "E007")

    conn.close()
    print(f"种子数据完成：{db_path}")


if __name__ == "__main__":
    db = sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "data" / "fulfillment.db")
    force = "--force" in sys.argv
    seed(db, ROOT / "web", force=force)

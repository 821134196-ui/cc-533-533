"""领域服务：材料包生成/打包/发运、作品回寄、加工、成品返寄、索赔与留痕。

所有业务规则集中在本模块：
- 材料包按报名唯一生成，清单生成时从课程版本快照复制；版本改版不影响已生成的包。
- 打包必须主确认 + 复核两人，且不能是同一人；同一包不能被重复确认（含并发）。
- 缺件、未经学员确认的替代颜色都会阻止装箱完成，并写入留痕。
- 去程寄包 / 作品回寄 / 成品返寄使用三张独立运单（模拟物流适配器）。
- 迟交、运输损坏、返寄地址变更均留痕；损坏产生索赔单。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from .shipping import get_carrier


class DomainError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- 课程版本

def create_version(conn: sqlite3.Connection, version_id: str, title: str,
                   description: str, items: list[dict[str, Any]]) -> dict:
    """新建课程版本（改版即新版本）。items: [{sku,name,category,icon,qty,color_code}]"""
    if conn.execute("SELECT 1 FROM course_versions WHERE id=?", (version_id,)).fetchone():
        raise DomainError("version_exists", f"课程版本 {version_id} 已存在")
    ts = _now()
    conn.execute(
        "INSERT INTO course_versions(id,title,description,published_at,is_active) VALUES(?,?,?,?,1)",
        (version_id, title, description, ts),
    )
    for it in items:
        conn.execute(
            """INSERT INTO course_version_items
               (version_id,sku,name,category,icon,qty,color_code)
               VALUES(?,?,?,?,?,?,?)""",
            (version_id, it["sku"], it["name"], it["category"],
             it.get("icon", ""), int(it["qty"]), it.get("color_code", "")),
        )
    conn.commit()
    return get_version(conn, version_id)


def get_version(conn: sqlite3.Connection, version_id: str) -> dict:
    row = conn.execute("SELECT * FROM course_versions WHERE id=?", (version_id,)).fetchone()
    if not row:
        raise DomainError("version_not_found", f"课程版本 {version_id} 不存在")
    items = conn.execute(
        "SELECT sku,name,category,icon,qty,color_code FROM course_version_items "
        "WHERE version_id=? ORDER BY category, id", (version_id,)).fetchall()
    d = dict(row)
    d["items"] = [dict(r) for r in items]
    return d


def list_versions(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM course_versions ORDER BY published_at, id").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["item_count"] = conn.execute(
            "SELECT COUNT(*) c FROM course_version_items WHERE version_id=?", (r["id"],)).fetchone()["c"]
        out.append(d)
    return out


# ---------------------------------------------------------------- 报名与材料包生成

def create_enrollment(conn: sqlite3.Connection, enrollment_id: str, student_name: str,
                      channel: str, version_id: str, address: str = "",
                      phone: str = "") -> dict:
    if not get_version(conn, version_id):
        raise DomainError("version_not_found", f"课程版本 {version_id} 不存在")
    if conn.execute("SELECT 1 FROM enrollments WHERE id=?", (enrollment_id,)).fetchone():
        raise DomainError("enrollment_exists", f"报名 {enrollment_id} 已存在")
    conn.execute(
        """INSERT INTO enrollments(id,student_name,channel,version_id,address,phone,enrolled_at)
           VALUES(?,?,?,?,?,?,?)""",
        (enrollment_id, student_name, channel, version_id, address, phone, _now()),
    )
    conn.commit()
    return get_enrollment(conn, enrollment_id)


def get_enrollment(conn: sqlite3.Connection, enrollment_id: str) -> dict:
    row = conn.execute("SELECT * FROM enrollments WHERE id=?", (enrollment_id,)).fetchone()
    if not row:
        raise DomainError("enrollment_not_found", f"报名 {enrollment_id} 不存在")
    return dict(row)


def generate_kit(conn: sqlite3.Connection, enrollment_id: str) -> dict:
    """按报名唯一生成材料包；清单从当前版本快照复制，此后与版本解耦。"""
    enr = get_enrollment(conn, enrollment_id)
    existing = conn.execute("SELECT id FROM kits WHERE enrollment_id=?", (enrollment_id,)).fetchone()
    if existing:
        raise DomainError("kit_exists", f"报名 {enrollment_id} 的材料包已生成：{existing['id']}")

    version = get_version(conn, enr["version_id"])
    kit_id = f"K-{enrollment_id}"
    snapshot = json.dumps(version["items"], ensure_ascii=False)
    conn.execute(
        """INSERT INTO kits(id,enrollment_id,version_id,version_snapshot,status,created_at)
           VALUES(?,?,?,?, 'generated', ?)""",
        (kit_id, enrollment_id, enr["version_id"], snapshot, _now()),
    )
    for it in version["items"]:
        conn.execute(
            """INSERT INTO kit_items
               (kit_id,sku,name,category,icon,qty_required,qty_packed,color_code,item_status)
               VALUES(?,?,?,?,?, ?, 0, ?, 'pending')""",
            (kit_id, it["sku"], it["name"], it["category"], it["icon"],
             it["qty"], it.get("color_code", "")),
        )
    conn.commit()
    _log(conn, "kit", kit_id, f"材料包已按 {enr['version_id']} 版本清单生成（快照锁定）")
    conn.commit()
    return get_kit(conn, kit_id)


def _load_kit_items(conn: sqlite3.Connection, kit_id: str) -> list[dict]:
    rows = conn.execute("SELECT * FROM kit_items WHERE kit_id=? ORDER BY category, id",
                        (kit_id,)).fetchall()
    return [dict(r) for r in rows]


def get_kit(conn: sqlite3.Connection, kit_id: str) -> dict:
    row = conn.execute(
        """SELECT k.*, e.student_name, e.channel, e.address, e.phone,
                  cv.title AS course_title
           FROM kits k JOIN enrollments e ON e.id=k.enrollment_id
           JOIN course_versions cv ON cv.id=e.version_id
           WHERE k.id=?""", (kit_id,)).fetchone()
    if not row:
        raise DomainError("kit_not_found", f"材料包 {kit_id} 不存在")
    d = dict(row)
    d["items"] = _load_kit_items(conn, kit_id)
    d["snapshot"] = json.loads(d["version_snapshot"])
    d["shipments"] = [dict(r) for r in conn.execute(
        "SELECT * FROM shipments WHERE ref_id=? ORDER BY created_at", (kit_id,)).fetchall()]
    return d


def list_kits(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT k.id,k.enrollment_id,k.version_id,k.status,k.created_at,
                  k.packed_at,k.packed_by,k.checked_by,k.shipped_at,k.delivered_at,
                  e.student_name,e.channel,cv.title AS course_title
           FROM kits k JOIN enrollments e ON e.id=k.enrollment_id
           JOIN course_versions cv ON cv.id=e.version_id
           ORDER BY k.created_at, k.id""").fetchall()
    kits = [dict(r) for r in rows]
    for k in kits:
        k["items"] = _load_kit_items(conn, k["id"])
        k["missing_count"] = sum(1 for i in k["items"] if i["item_status"] == "short")
        k["pending_color_count"] = sum(
            1 for i in k["items"] if i["item_status"] == "substitute_pending")
    return kits


# ---------------------------------------------------------------- 装箱核对

def pack_item(conn: sqlite3.Connection, kit_id: str, sku: str, qty_packed: int,
              substitute_color: str = "", confirmed: bool = False,
              by: str = "") -> dict:
    """登记一件物品的实际装箱数量（可分次更新）。

    - 数量不足 → 标记 short 并留痕（缺件）。
    - 使用替代颜色 → 默认 substitute_pending；须学员确认后方可 substitute_confirmed。
    """
    item = conn.execute("SELECT * FROM kit_items WHERE kit_id=? AND sku=?",
                        (kit_id, sku)).fetchone()
    if not item:
        raise DomainError("item_not_found", f"材料包 {kit_id} 中无物品 {sku}")
    kit = conn.execute("SELECT status FROM kits WHERE id=?", (kit_id,)).fetchone()
    if kit["status"] != "generated":
        raise DomainError("kit_locked", f"材料包 {kit_id} 已{kit['status']}，不能再改动装箱明细")

    qty_packed = int(qty_packed)
    if qty_packed < 0:
        raise DomainError("bad_qty", "装箱数量不能为负")

    status = "packed"
    sub = (substitute_color or "").strip()
    is_substitute = bool(sub) and sub != item["color_code"]

    if is_substitute:
        if not confirmed:
            status = "substitute_pending"
            _log(conn, "kit", kit_id,
                 f"颜色替代待确认：{item['name']} 原定 {item['color_code'] or '原色'}，"
                 f"拟用 {sub}（未经学员确认，不可封箱）", by, kind="color_substitute")
        else:
            status = "substitute_confirmed"
            _log(conn, "kit", kit_id,
                 f"颜色替代已确认：{item['name']} {item['color_code'] or '原色'} → {sub}",
                 by, kind="color_substitute")
    elif sub and sub == item["color_code"]:
        sub = ""  # 与原色一致，不算替代

    if qty_packed < item["qty_required"]:
        status = "short"
        _log(conn, "kit", kit_id,
             f"缺件：{item['name']} 需 {item['qty_required']} 件，实装 {qty_packed} 件",
             by, kind="shortage")
    elif not is_substitute:
        status = "packed"

    conn.execute(
        """UPDATE kit_items SET qty_packed=?, substitute_color=?,
               substitute_confirmed=?, item_status=? WHERE id=?""",
        (qty_packed, sub if is_substitute else "", 1 if confirmed and is_substitute else 0,
         status, item["id"]))
    conn.commit()
    return get_kit(conn, kit_id)


def packing_blockers(conn: sqlite3.Connection, kit_id: str) -> list[str]:
    items = _load_kit_items(conn, kit_id)
    blockers: list[str] = []
    for it in items:
        if it["item_status"] == "short" or it["qty_packed"] < it["qty_required"]:
            blockers.append(f"缺件：{it['name']} 需 {it['qty_required']}，实装 {it['qty_packed']}")
        elif it["item_status"] == "substitute_pending":
            blockers.append(f"替代颜色未确认：{it['name']} → {it['substitute_color']}")
        elif it["item_status"] == "pending":
            blockers.append(f"尚未装箱：{it['name']}")
    return blockers


def confirm_packing(conn: sqlite3.Connection, kit_id: str, packer: str, checker: str) -> dict:
    """两人确认封箱。BEGIN IMMEDIATE 行锁保证两个打包员不能同时确认同一包。"""
    packer, checker = (packer or "").strip(), (checker or "").strip()
    if not packer or not checker:
        raise DomainError("need_two_packers", "需要主确认人与复核人")
    if packer == checker:
        raise DomainError("same_packer", "主确认人与复核人不能是同一人")

    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT status FROM kits WHERE id=?", (kit_id,)).fetchone()
        if not row:
            raise DomainError("kit_not_found", f"材料包 {kit_id} 不存在")
        if row["status"] != "generated":
            raise DomainError("already_packed",
                              f"材料包 {kit_id} 已完成封箱（{row['status']}），不能重复确认")
        blockers = packing_blockers(conn, kit_id)
        if blockers:
            raise DomainError("packing_incomplete", "；".join(blockers))

        cur = conn.execute(
            """UPDATE kits SET status='packed', packed_at=?, packed_by=?, checked_by=?
               WHERE id=? AND status='generated'""",
            (_now(), packer, checker, kit_id))
        if cur.rowcount != 1:  # 并发下另一事务抢先确认
            raise DomainError("already_packed", f"材料包 {kit_id} 刚被其他打包员确认")
        _log(conn, "kit", kit_id, f"装箱完成：主确认 {packer}，复核 {checker}")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return get_kit(conn, kit_id)


# ---------------------------------------------------------------- 发运 / 签收

def _create_shipment(conn: sqlite3.Connection, direction: str, ref_id: str,
                     from_addr: str, to_addr: str) -> dict:
    carrier = get_carrier()
    tracking_no = carrier.create_tracking(direction, ref_id)
    sid = f"SHP-{tracking_no.split('-')[-1]}"
    conn.execute(
        """INSERT INTO shipments(id,direction,ref_id,carrier,tracking_no,status,
                                 from_addr,to_addr,created_at,picked_up_at)
           VALUES(?,?,?,?,?,'picked_up',?,?,?,?)""",
        (sid, direction, ref_id, carrier.__class__.__name__, tracking_no,
         from_addr, to_addr, _now(), _now()),
    )
    conn.commit()
    return dict(conn.execute("SELECT * FROM shipments WHERE id=?", (sid,)).fetchone())


def ship_kit(conn: sqlite3.Connection, kit_id: str) -> dict:
    kit = get_kit(conn, kit_id)
    if kit["status"] == "generated":
        raise DomainError("not_packed", "材料包尚未完成两人装箱确认，不能发运")
    if kit["status"] in ("shipped", "delivered"):
        raise DomainError("already_shipped", f"材料包 {kit_id} 已发运")
    shp = _create_shipment(conn, "outbound_kit", kit_id,
                           "手作工作室 仓储间", kit["address"] or "学员地址待补")
    conn.execute("UPDATE kits SET status='shipped', shipped_at=? WHERE id=?",
                 (_now(), kit_id))
    _log(conn, "kit", kit_id, f"材料包发运（去程运单 {shp['tracking_no']}）")
    conn.commit()
    return shp


def deliver_shipment(conn: sqlite3.Connection, tracking_no: str,
                     damaged: bool = False, damage_note: str = "",
                     damaged_items: list[str] | None = None) -> dict:
    """签收任一运单。damaged=True 时标记运输损坏、留痕并自动开索赔单。"""
    shp = conn.execute("SELECT * FROM shipments WHERE tracking_no=?",
                       (tracking_no,)).fetchone()
    if not shp:
        raise DomainError("shipment_not_found", f"运单 {tracking_no} 不存在")
    if shp["status"] in ("delivered", "damaged"):
        raise DomainError("already_delivered", f"运单 {tracking_no} 已签收")

    final_status = "damaged" if damaged else "delivered"
    conn.execute(
        "UPDATE shipments SET status=?, delivered_at=?, damaged=?, damage_note=? WHERE id=?",
        (final_status, _now(), 1 if damaged else 0, damage_note, shp["id"]))

    scene = {
        "outbound_kit": "kit_delivery",
        "inbound_work": "work_transit",
        "outbound_finished": "finished_transit",
    }[shp["direction"]]
    label = {"outbound_kit": "材料包配送", "inbound_work": "作品回寄",
             "outbound_finished": "成品返寄"}[shp["direction"]]

    claim = None
    if damaged:
        detail = f"{label}运输损坏（运单 {tracking_no}）：{damage_note or '外包装破损/物品受损'}"
        if damaged_items:
            detail += "；涉及：" + "、".join(damaged_items)
        _log(conn, "inbound", shp["ref_id"], detail, kind="transit_damage")
        claim_id = f"CLM-{_now()[2:10].replace('-', '')}-{shp['id'].split('-')[-1]}"
        conn.execute(
            """INSERT INTO claims(id,shipment_id,scene,description,status,created_at)
               VALUES(?,?,?,?,'open',?)""",
            (claim_id, shp["id"], scene, detail, _now()))
        conn.commit()
        claim = dict(conn.execute("SELECT * FROM claims WHERE id=?", (claim_id,)).fetchone())
    else:
        if shp["direction"] == "outbound_kit":
            conn.execute("UPDATE kits SET status='delivered', delivered_at=? WHERE id=?",
                         (_now(), shp["ref_id"]))
        elif shp["direction"] == "inbound_work":
            _mark_work_received(conn, shp["ref_id"])
        elif shp["direction"] == "outbound_finished":
            conn.execute("UPDATE work_orders SET status='returned' WHERE id=?",
                         (shp["ref_id"],))
        _log(conn, "inbound", shp["ref_id"], f"{label}正常签收（运单 {tracking_no}）")
    conn.commit()
    return {"shipment": dict(conn.execute("SELECT * FROM shipments WHERE id=?",
                                          (shp["id"],)).fetchone()), "claim": claim}


# ---------------------------------------------------------------- 作品回寄 / 加工

def hand_over_onsite(conn: sqlite3.Connection, kit_id: str, by: str = "") -> dict:
    """现场学员到课自取：无运单，直接交付并留痕。"""
    kit = get_kit(conn, kit_id)
    if kit["channel"] != "onsite":
        raise DomainError("not_onsite", "仅现场报名可走到课自取交付")
    if kit["status"] == "generated":
        raise DomainError("not_packed", "材料包尚未完成两人装箱确认")
    if kit["status"] in ("delivered",):
        raise DomainError("already_delivered", f"材料包 {kit_id} 已交付")
    conn.execute("UPDATE kits SET status='delivered', delivered_at=? WHERE id=?",
                 (_now(), kit_id))
    _log(conn, "kit", kit_id, "现场学员到课自取交付（无运单）", by)
    conn.commit()
    return get_kit(conn, kit_id)


def create_work_order(conn: sqlite3.Connection, enrollment_id: str,
                      service: str = "firing", due_date: str = "") -> dict:
    enr = get_enrollment(conn, enrollment_id)
    if conn.execute("SELECT 1 FROM work_orders WHERE enrollment_id=?",
                    (enrollment_id,)).fetchone():
        raise DomainError("wo_exists", f"报名 {enrollment_id} 已有加工单")
    wo_id = f"WO-{enrollment_id}"
    conn.execute(
        """INSERT INTO work_orders(id,enrollment_id,due_date,service,status,return_addr)
           VALUES(?,?,?,?, 'awaiting_submission', ?)""",
        (wo_id, enrollment_id, due_date, service, enr["address"]))
    conn.commit()
    return get_work_order(conn, wo_id)


def get_work_order(conn: sqlite3.Connection, wo_id: str) -> dict:
    row = conn.execute(
        """SELECT w.*, e.student_name, e.channel, cv.title AS course_title
           FROM work_orders w JOIN enrollments e ON e.id=w.enrollment_id
           JOIN course_versions cv ON cv.id=e.version_id
           WHERE w.id=?""", (wo_id,)).fetchone()
    if not row:
        raise DomainError("wo_not_found", f"加工单 {wo_id} 不存在")
    d = dict(row)
    d["shipments"] = [dict(r) for r in conn.execute(
        "SELECT * FROM shipments WHERE ref_id=? ORDER BY created_at", (wo_id,)).fetchall()]
    d["photos"] = [dict(r) for r in conn.execute(
        "SELECT * FROM photos WHERE ref_id=? ORDER BY created_at", (wo_id,)).fetchall()]
    return d


def list_work_orders(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT w.id,w.enrollment_id,w.status,w.service,w.due_date,w.received_at,
                  w.late,w.finished_at,w.return_addr,e.student_name,e.channel
           FROM work_orders w JOIN enrollments e ON e.id=w.enrollment_id
           ORDER BY w.due_date, w.id""").fetchall()
    return [dict(r) for r in rows]


def submit_work(conn: sqlite3.Connection, wo_id: str) -> dict:
    """学员寄出作品 → 生成独立的作品回寄运单（与材料包运单不同）。迟交留痕。"""
    wo = get_work_order(conn, wo_id)
    if wo["status"] not in ("awaiting_submission",):
        raise DomainError("wo_not_submittable", f"加工单 {wo_id} 当前状态 {wo['status']}，不可再寄件")
    kit = conn.execute("SELECT status FROM kits WHERE enrollment_id=?",
                       (wo["enrollment_id"],)).fetchone()
    if not kit or kit["status"] not in ("shipped", "delivered"):
        raise DomainError("kit_not_delivered", "材料包尚未送达，暂不接受作品回寄")

    late = 0
    if wo["due_date"] and _now()[:10] > wo["due_date"]:
        late = 1
        _log(conn, "inbound", wo_id,
             f"学员迟交作品：约定交件日 {wo['due_date']}，实际寄出 {_now()[:10]}（仍受理）",
             kind="late_submission")

    shp = _create_shipment(conn, "inbound_work", wo_id,
                           wo["student_name"] + "（学员）", "手作工作室 收件台")
    conn.execute("UPDATE work_orders SET status='in_transit', late=? WHERE id=?",
                 (late, wo_id))
    _log(conn, "inbound", wo_id, f"作品已寄出回寄（回程取件运单 {shp['tracking_no']}）")
    conn.commit()
    return shp


def _mark_work_received(conn: sqlite3.Connection, wo_id: str) -> None:
    conn.execute("UPDATE work_orders SET status='received', received_at=? WHERE id=?",
                 (_now(), wo_id))


# 加工阶段顺序（只能在已收件后向前推进）
PROCESS_FLOW = ["received", "in_progress", "firing", "framing", "qc", "done"]


def update_work_status(conn: sqlite3.Connection, wo_id: str, status: str,
                       note: str = "") -> dict:
    if status not in PROCESS_FLOW:
        raise DomainError("bad_status", f"非法加工状态 {status}")
    wo = get_work_order(conn, wo_id)
    if wo["status"] not in PROCESS_FLOW:
        raise DomainError("wo_not_received",
                          f"作品尚未由工作室签收（当前 {wo['status']}），不能更新加工进度")
    cur_idx = PROCESS_FLOW.index(wo["status"])
    next_idx = PROCESS_FLOW.index(status)
    if next_idx <= cur_idx:
        raise DomainError("status_regress",
                          f"加工进度不能回退：{wo['status']} → {status}")
    label = {"received": "已收件", "in_progress": "开始加工", "firing": "烧制中",
             "framing": "装裱中", "qc": "质检中", "done": "加工完成"}[status]
    conn.execute("UPDATE work_orders SET status=? WHERE id=?", (status, wo_id))
    if status == "done":
        conn.execute("UPDATE work_orders SET finished_at=? WHERE id=?", (_now(), wo_id))
    _log(conn, "inbound", wo_id, f"加工进度：{label}" + (f"（{note}）" if note else ""))
    conn.commit()
    return get_work_order(conn, wo_id)


def change_return_address(conn: sqlite3.Connection, wo_id: str, new_addr: str,
                          by: str = "") -> dict:
    wo = get_work_order(conn, wo_id)
    new_addr = (new_addr or "").strip()
    if not new_addr:
        raise DomainError("bad_address", "返寄地址不能为空")
    if new_addr == wo["return_addr"]:
        raise DomainError("same_address", "新地址与当前地址相同")
    shipped = any(s["direction"] == "outbound_finished" for s in wo["shipments"])
    if shipped:
        raise DomainError("already_returned", "成品已返寄，地址变更需联系承运商改派")
    conn.execute("UPDATE work_orders SET return_addr=? WHERE id=?", (new_addr, wo_id))
    _log(conn, "inbound", wo_id,
         f"返寄地址变更：{(wo['return_addr'] or '（空）')[-40:]} → {new_addr[-40:]}",
         by, kind="address_change")
    conn.commit()
    return get_work_order(conn, wo_id)


def return_finished(conn: sqlite3.Connection, wo_id: str) -> dict:
    """成品返寄：使用与作品回寄不同的新运单。"""
    wo = get_work_order(conn, wo_id)
    if wo["status"] not in ("done",):
        raise DomainError("not_done", f"加工单 {wo_id} 尚未完成（当前 {wo['status']}），不能返寄")
    if not wo["return_addr"]:
        raise DomainError("no_return_addr", "缺少返寄地址，请先登记地址变更")
    shp = _create_shipment(conn, "outbound_finished", wo_id,
                           "手作工作室 成品仓", wo["return_addr"])
    _log(conn, "inbound", wo_id, f"成品返寄发出（去程派送运单 {shp['tracking_no']}）")
    conn.commit()
    return shp


# ---------------------------------------------------------------- 照片 / 索赔 / 留痕

def add_photo(conn: sqlite3.Connection, ref_type: str, ref_id: str, path: str,
              caption: str = "") -> dict:
    cur = conn.execute(
        "INSERT INTO photos(ref_type,ref_id,path,caption,created_at) VALUES(?,?,?,?,?)",
        (ref_type, ref_id, path, caption, _now()))
    conn.commit()
    return dict(conn.execute("SELECT * FROM photos WHERE id=?", (cur.lastrowid,)).fetchone())


def resolve_claim(conn: sqlite3.Connection, claim_id: str, status: str,
                  resolution: str, by: str = "") -> dict:
    if status not in ("investigating", "approved", "rejected", "resolved"):
        raise DomainError("bad_status", f"非法索赔状态 {status}")
    row = conn.execute("SELECT * FROM claims WHERE id=?", (claim_id,)).fetchone()
    if not row:
        raise DomainError("claim_not_found", f"索赔单 {claim_id} 不存在")
    conn.execute("UPDATE claims SET status=?, resolution=?, resolved_at=? WHERE id=?",
                 (status, resolution, _now() if status in ("approved", "resolved") else None,
                  claim_id))
    _log(conn, "claim", claim_id, f"索赔处理：{status} — {resolution}", by)
    # 作品回寄损坏且赔付/协商成立：工作室接收受损作品，进入修复加工
    if row["scene"] == "work_transit" and status in ("approved", "resolved"):
        shp = conn.execute("SELECT ref_id FROM shipments WHERE id=?",
                           (row["shipment_id"],)).fetchone()
        wo_id = shp["ref_id"]
        wo = conn.execute("SELECT status FROM work_orders WHERE id=?",
                          (wo_id,)).fetchone()
        if wo and wo["status"] == "in_transit":
            conn.execute(
                "UPDATE work_orders SET status='received', received_at=? WHERE id=?",
                (_now(), wo_id))
            _log(conn, "inbound", wo_id,
                 "损坏索赔成立，工作室接收受损作品并安排修复后加工")
    conn.commit()
    return dict(conn.execute("SELECT * FROM claims WHERE id=?", (claim_id,)).fetchone())


def list_claims(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT c.*, s.tracking_no, s.direction FROM claims c
           JOIN shipments s ON s.id=c.shipment_id ORDER BY c.created_at DESC""").fetchall()
    return [dict(r) for r in rows]


def list_incidents(conn: sqlite3.Connection, ref_id: str | None = None) -> list[dict]:
    if ref_id:
        rows = conn.execute(
            "SELECT * FROM incidents WHERE ref_id=? ORDER BY created_at, id",
            (ref_id,)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM incidents ORDER BY created_at DESC, id DESC LIMIT 100").fetchall()
    return [dict(r) for r in rows]


def _log(conn: sqlite3.Connection, ref_type: str, ref_id: str, detail: str,
         created_by: str = "", kind: str = "audit") -> None:
    conn.execute(
        "INSERT INTO incidents(kind,ref_type,ref_id,detail,created_by,created_at) "
        "VALUES(?,?,?,?,?,?)",
        (kind, ref_type, ref_id, detail, created_by, _now()))

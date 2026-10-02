"""领域服务：材料包履约全流程的业务规则。

关键不变量：
* 材料包按报名唯一生成，清单在生成时从课程版本 BOM 快照；之后课程改版
  不影响已生成的材料包（版本锁定）。
* 装箱需要打包员(packer)与复核员(checker)两人确认，两角色各一次且不能同人。
* 材料包去程、作品回寄、成品返寄各自生成不同运单号。
* 缺件、未确认替代色、运输损坏、迟交、地址变更全部留痕（exceptions /
  claims / address_changes / audit_log）。
"""
from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

from .db import audit, now
from .shipping import MockShippingAdapter, _PREFIX


class ServiceError(Exception):
    """业务规则冲突（4xx 语义）。"""


class FulfillmentService:
    def __init__(self, conn: sqlite3.Connection,
                 shipping: MockShippingAdapter | None = None,
                 photo_dir: str | Path = "photos") -> None:
        self.conn = conn
        self.shipping = shipping or MockShippingAdapter()
        self.photo_dir = Path(photo_dir)
        self.photo_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ 课程
    def create_course(self, course_id: str, name: str) -> None:
        self.conn.execute(
            "INSERT INTO courses(course_id, name, created_at) VALUES(?,?,?)",
            (course_id, name, now()),
        )
        self.conn.commit()

    def create_version(self, course_id: str, version_id: str, label: str,
                       items: list[dict], notes: str = "",
                       actor: str = "admin") -> None:
        """发布课程版本并写入 BOM（工具 / 消耗品 / 颜色选择）。"""
        self.conn.execute(
            "INSERT INTO course_versions(version_id, course_id, version_label, "
            "notes, created_at) VALUES(?,?,?,?,?)",
            (version_id, course_id, label, notes, now()),
        )
        for i, it in enumerate(items):
            self.conn.execute(
                "INSERT INTO course_items(version_id, item_id, name, category, "
                "color_role, unit, qty, thumbnail, sort_order) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (version_id, it["item_id"], it["name"], it["category"],
                 it.get("color_role"), it.get("unit", "件"), it["qty"],
                 it["thumbnail"], i),
            )
        audit(self.conn, actor, "publish_version", "course_version", version_id,
              {"course_id": course_id, "label": label, "items": len(items)})
        self.conn.commit()

    def update_bom(self, version_id: str, items: list[dict],
                   actor: str = "admin") -> None:
        """改版：整体替换某版本的 BOM 模板。

        只影响之后新生成的材料包；已有 kit_lines 是快照，不会被改动。
        """
        self.conn.execute("DELETE FROM course_items WHERE version_id=?",
                          (version_id,))
        for i, it in enumerate(items):
            self.conn.execute(
                "INSERT INTO course_items(version_id, item_id, name, category, "
                "color_role, unit, qty, thumbnail, sort_order) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (version_id, it["item_id"], it["name"], it["category"],
                 it.get("color_role"), it.get("unit", "件"), it["qty"],
                 it["thumbnail"], i),
            )
        audit(self.conn, actor, "update_bom", "course_version", version_id,
              {"items": len(items)})
        self.conn.commit()

    def list_versions(self, course_id: str) -> list[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT * FROM course_versions WHERE course_id=? ORDER BY created_at",
            (course_id,)))

    def get_bom(self, version_id: str) -> list[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT * FROM course_items WHERE version_id=? ORDER BY sort_order",
            (version_id,)))

    # ------------------------------------------------------------------ 报名
    def create_enrollment(self, enrollment_id: str, student_name: str,
                          version_id: str, address: str, phone: str = "",
                          work_due_date: str | None = None) -> None:
        row = self.conn.execute(
            "SELECT course_id FROM course_versions WHERE version_id=?",
            (version_id,)).fetchone()
        if row is None:
            raise ServiceError(f"课程版本不存在: {version_id}")
        self.conn.execute(
            "INSERT INTO enrollments(enrollment_id, student_name, phone, "
            "course_id, version_id, address, work_due_date, created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (enrollment_id, student_name, phone, row["course_id"], version_id,
             address, work_due_date, now()),
        )
        self.conn.commit()

    def change_address(self, enrollment_id: str, new_address: str,
                       actor: str, reason: str = "") -> None:
        row = self.conn.execute(
            "SELECT address FROM enrollments WHERE enrollment_id=?",
            (enrollment_id,)).fetchone()
        if row is None:
            raise ServiceError("报名不存在")
        if row["address"] == new_address:
            raise ServiceError("新地址与当前地址相同")
        self.conn.execute(
            "INSERT INTO address_changes(enrollment_id, old_address, "
            "new_address, reason, requested_by, requested_at) "
            "VALUES(?,?,?,?,?,?)",
            (enrollment_id, row["address"], new_address, reason, actor, now()),
        )
        self.conn.execute(
            "UPDATE enrollments SET address=? WHERE enrollment_id=?",
            (new_address, enrollment_id),
        )
        audit(self.conn, actor, "change_address", "enrollment", enrollment_id,
              {"old": row["address"], "new": new_address, "reason": reason})
        self.conn.commit()

    # ---------------------------------------------------------------- 材料包
    def create_kit(self, enrollment_id: str, actor: str = "admin") -> str:
        """按报名唯一生成材料包，并把当时 BOM 快照进 kit_lines。"""
        enr = self.conn.execute(
            "SELECT * FROM enrollments WHERE enrollment_id=?",
            (enrollment_id,)).fetchone()
        if enr is None:
            raise ServiceError("报名不存在")
        exists = self.conn.execute(
            "SELECT kit_no FROM material_kits WHERE enrollment_id=?",
            (enrollment_id,)).fetchone()
        if exists:
            raise ServiceError(f"该报名已生成材料包 {exists['kit_no']}")
        kit_no = f"KIT-{enrollment_id}"
        self.conn.execute(
            "INSERT INTO material_kits(kit_no, enrollment_id, version_id, "
            "status, created_at) VALUES(?,?,?,?,?)",
            (kit_no, enrollment_id, enr["version_id"], "created", now()),
        )
        for it in self.get_bom(enr["version_id"]):
            self.conn.execute(
                "INSERT INTO kit_lines(kit_no, item_id, name, category, "
                "color_role, unit, planned_qty, thumbnail, sort_order) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (kit_no, it["item_id"], it["name"], it["category"],
                 it["color_role"], it["unit"], it["qty"], it["thumbnail"],
                 it["sort_order"]),
            )
        audit(self.conn, actor, "create_kit", "material_kit", kit_no,
              {"enrollment_id": enrollment_id, "version_id": enr["version_id"]})
        self.conn.commit()
        return kit_no

    def get_kit(self, kit_no: str) -> dict:
        kit = self.conn.execute(
            "SELECT k.*, e.student_name, e.address, e.enrollment_id, "
            "cv.version_label, c.name AS course_name "
            "FROM material_kits k "
            "JOIN enrollments e ON e.enrollment_id=k.enrollment_id "
            "JOIN course_versions cv ON cv.version_id=k.version_id "
            "JOIN courses c ON c.course_id=e.course_id "
            "WHERE k.kit_no=?", (kit_no,)).fetchone()
        if kit is None:
            raise ServiceError("材料包不存在")
        lines = list(self.conn.execute(
            "SELECT * FROM kit_lines WHERE kit_no=? ORDER BY sort_order",
            (kit_no,)))
        confs = list(self.conn.execute(
            "SELECT * FROM packing_confirmations WHERE kit_no=? ORDER BY id",
            (kit_no,)))
        excs = list(self.conn.execute(
            "SELECT * FROM exceptions WHERE kit_no=? ORDER BY exception_id DESC",
            (kit_no,)))
        ships = list(self.conn.execute(
            "SELECT * FROM shipments WHERE kit_no=? ORDER BY shipment_id",
            (kit_no,)))
        job = self.conn.execute(
            "SELECT * FROM processing_jobs WHERE kit_no=?", (kit_no,)).fetchone()
        events = self.job_timeline(kit_no)
        return {"kit": dict(kit), "lines": [dict(x) for x in lines],
                "confirmations": [dict(x) for x in confs],
                "exceptions": [dict(x) for x in excs],
                "shipments": [dict(x) for x in ships],
                "job": dict(job) if job else None,
                "job_events": events}

    def list_kits(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT kit_no FROM material_kits ORDER BY created_at").fetchall()
        return [self.get_kit(r["kit_no"]) for r in rows]

    def check_line(self, kit_no: str, item_id: str, packed_qty: int,
                   actor: str) -> None:
        """装箱台勾选物品数量。"""
        line = self.conn.execute(
            "SELECT * FROM kit_lines WHERE kit_no=? AND item_id=?",
            (kit_no, item_id)).fetchone()
        if line is None:
            raise ServiceError("清单物品不存在")
        if packed_qty < 0 or packed_qty > line["planned_qty"]:
            raise ServiceError(
                f"装箱数量需在 0~{line['planned_qty']} 之间")
        self.conn.execute(
            "UPDATE kit_lines SET packed_qty=?, checked=1 WHERE kit_no=? "
            "AND item_id=?", (packed_qty, kit_no, item_id))
        audit(self.conn, actor, "check_line", "kit_line",
              f"{kit_no}:{item_id}", {"packed_qty": packed_qty})
        self.conn.commit()

    def substitute_color(self, kit_no: str, item_id: str,
                         substitute_name: str, thumbnail: str,
                         actor: str) -> None:
        """登记替代颜色；学员确认前标记为未确认异常，不允许完成复核。

        原 BOM 行保留（item_id 不变，快照不丢），只替换展示名称与缩略图。
        """
        line = self.conn.execute(
            "SELECT * FROM kit_lines WHERE kit_no=? AND item_id=?",
            (kit_no, item_id)).fetchone()
        if line is None or line["category"] != "color":
            raise ServiceError("只能对颜色选择项做替代")
        self.conn.execute(
            "UPDATE kit_lines SET is_substitute=1, substitute_for=?, "
            "color_confirmed=0, name=?, thumbnail=? WHERE kit_no=? AND item_id=?",
            (item_id, substitute_name, thumbnail, kit_no, item_id))
        self._open_exception(
            kit_no, "color_substitute", item_id,
            f"颜色替代待确认：{line['name']} → {substitute_name}",
            f"原选项 {item_id} 暂缺，拟以 {substitute_name} 替代",
            actor)
        self.conn.commit()

    def confirm_color(self, kit_no: str, item_id: str, accepted: bool,
                      actor: str) -> None:
        """学员确认（接受/拒绝）替代颜色。"""
        exc = self.conn.execute(
            "SELECT * FROM exceptions WHERE kit_no=? AND item_id=? "
            "AND type='color_substitute' AND status='open' ORDER BY exception_id DESC",
            (kit_no, item_id)).fetchone()
        if exc is None:
            raise ServiceError("没有待确认的颜色替代")
        if accepted:
            self.conn.execute(
                "UPDATE kit_lines SET color_confirmed=1 WHERE kit_no=? "
                "AND item_id=?", (kit_no, item_id))
            self._resolve_exception(exc["exception_id"], "accept", actor)
        else:
            self.conn.execute(
                "UPDATE kit_lines SET is_substitute=0, substitute_for=NULL, "
                "color_confirmed=0 WHERE kit_no=? AND item_id=?",
                (kit_no, item_id))
            self._resolve_exception(exc["exception_id"], "cancel", actor)
        self.conn.commit()

    def confirm_packing(self, kit_no: str, packer: str, role: str,
                        actor: str | None = None) -> None:
        """打包员/复核员确认。角色各一次，两人不能为同一人，不可重复确认。"""
        actor = actor or packer
        kit = self.conn.execute(
            "SELECT status FROM material_kits WHERE kit_no=?",
            (kit_no,)).fetchone()
        if kit is None:
            raise ServiceError("材料包不存在")
        if kit["status"] in ("shipped", "delivered"):
            raise ServiceError("材料包已发货，不能再确认装箱")
        same_role = self.conn.execute(
            "SELECT 1 FROM packing_confirmations WHERE kit_no=? AND role=?",
            (kit_no, role)).fetchone()
        if same_role:
            raise ServiceError(f"{role} 角色已确认，不能重复确认")
        same_person = self.conn.execute(
            "SELECT 1 FROM packing_confirmations WHERE kit_no=? AND packer=?",
            (kit_no, packer)).fetchone()
        if same_person:
            raise ServiceError("两名打包员不能为同一人")

        if role == "packer":
            # 打包员确认即登记缺件（实际装箱少于计划）
            self._record_shortages(kit_no, actor)
            self.conn.execute(
                "UPDATE material_kits SET status='packing' WHERE kit_no=?",
                (kit_no,))
        else:
            # 复核员确认前校验：存在未确认替代色则拒绝
            unconfirmed = self.conn.execute(
                "SELECT item_id FROM kit_lines WHERE kit_no=? "
                "AND is_substitute=1 AND color_confirmed=0", (kit_no,)).fetchall()
            if unconfirmed:
                raise ServiceError(
                    "存在未经学员确认的替代颜色："
                    + ", ".join(r["item_id"] for r in unconfirmed))
            open_short = self.conn.execute(
                "SELECT 1 FROM exceptions WHERE kit_no=? AND type='missing' "
                "AND status='open'", (kit_no,)).fetchone()
            if open_short:
                raise ServiceError("存在未处理缺件，需补发或登记处理方案后才能封箱")
            self.conn.execute(
                "UPDATE material_kits SET status='packed', packed_at=? "
                "WHERE kit_no=?", (now(), kit_no))

        self.conn.execute(
            "INSERT INTO packing_confirmations(kit_no, packer, role, "
            "confirmed_at) VALUES(?,?,?,?)", (kit_no, packer, role, now()))
        audit(self.conn, actor, "confirm_packing", "material_kit", kit_no,
              {"packer": packer, "role": role})
        self.conn.commit()

    def _record_shortages(self, kit_no: str, actor: str) -> None:
        rows = self.conn.execute(
            "SELECT item_id, name, planned_qty, packed_qty FROM kit_lines "
            "WHERE kit_no=? AND checked=1 AND packed_qty < planned_qty",
            (kit_no,)).fetchall()
        for r in rows:
            already = self.conn.execute(
                "SELECT 1 FROM exceptions WHERE kit_no=? AND item_id=? "
                "AND type='missing' AND status='open'",
                (kit_no, r["item_id"])).fetchone()
            if already:
                continue
            self._open_exception(
                kit_no, "missing", r["item_id"],
                f"缺件：{r['name']} 少 {r['planned_qty'] - r['packed_qty']} "
                f"{''}",
                f"计划 {r['planned_qty']}，实装 {r['packed_qty']}", actor)

    def _open_exception(self, kit_no: str, etype: str, item_id: str | None,
                        title: str, description: str, actor: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO exceptions(kit_no, type, item_id, title, description, "
            "created_by, created_at) VALUES(?,?,?,?,?,?,?)",
            (kit_no, etype, item_id, title, description, actor, now()))
        audit(self.conn, actor, "open_exception", "exception",
              str(cur.lastrowid), {"type": etype, "title": title})
        return cur.lastrowid

    def _resolve_exception(self, exception_id: int, resolution: str,
                           actor: str) -> None:
        self.conn.execute(
            "UPDATE exceptions SET status='resolved', resolution=?, "
            "resolved_at=?, resolved_by=? WHERE exception_id=?",
            (resolution, now(), actor, exception_id))
        audit(self.conn, actor, "resolve_exception", "exception",
              str(exception_id), {"resolution": resolution})

    def resolve_exception(self, exception_id: int, resolution: str,
                          actor: str) -> None:
        row = self.conn.execute(
            "SELECT * FROM exceptions WHERE exception_id=?",
            (exception_id,)).fetchone()
        if row is None:
            raise ServiceError("异常不存在")
        if row["status"] != "open":
            raise ServiceError("异常已处理")
        self._resolve_exception(exception_id, resolution, actor)
        self.conn.commit()

    # ------------------------------------------------------------------ 运单
    def _new_shipment(self, kit_no: str, direction: str, from_addr: str,
                      to_addr: str, actor: str, late: bool = False) -> int:
        """每次运输都新开运单；适配器保证运单号互不相同。"""
        prefix = _PREFIX[direction]
        existing_count = self.conn.execute(
            "SELECT COUNT(*) FROM shipments WHERE waybill LIKE ?",
            (prefix + "-%",)).fetchone()[0]
        waybill = self.shipping.create_waybill(direction, start=existing_count)
        cur = self.conn.execute(
            "INSERT INTO shipments(kit_no, direction, waybill, status, "
            "from_addr, to_addr, late, created_at, shipped_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (kit_no, direction, waybill, "in_transit", from_addr, to_addr,
             1 if late else 0, now(), now()),
        )
        sid = cur.lastrowid
        audit(self.conn, actor, "ship", "shipment", str(sid),
              {"kit_no": kit_no, "direction": direction, "waybill": waybill,
               "late": late})
        return sid

    def ship_kit(self, kit_no: str, actor: str = "packer") -> str:
        kit = self.get_kit(kit_no)
        if kit["kit"]["status"] != "packed":
            raise ServiceError("只有双人确认完成(packed)的材料包才能发出")
        enr = self.conn.execute(
            "SELECT address FROM enrollments WHERE enrollment_id=?",
            (kit["kit"]["enrollment_id"],)).fetchone()
        sid = self._new_shipment(kit_no, "kit_outbound", "工作室材料仓",
                                 enr["address"], actor)
        waybill = self.conn.execute(
            "SELECT waybill FROM shipments WHERE shipment_id=?", (sid,)).fetchone()
        self.conn.execute(
            "UPDATE material_kits SET status='shipped', outbound_waybill=?, "
            "shipped_at=? WHERE kit_no=?",
            (waybill["waybill"], now(), kit_no))
        self.conn.commit()
        return waybill["waybill"]

    def deliver_shipment(self, shipment_id: int, actor: str = "courier",
                         damaged: bool = False) -> None:
        s = self.conn.execute("SELECT * FROM shipments WHERE shipment_id=?",
                              (shipment_id,)).fetchone()
        if s is None:
            raise ServiceError("运单不存在")
        if s["status"] not in ("in_transit", "created"):
            raise ServiceError("运单已签收")
        status = "damaged" if damaged else "delivered"
        self.conn.execute(
            "UPDATE shipments SET status=?, delivered_at=? WHERE shipment_id=?",
            (status, now(), shipment_id))
        if damaged:
            self._open_exception(
                s["kit_no"], "transit_damage", None,
                f"{self.shipping.describe(s['direction'])}运输破损 "
                f"({s['waybill']})",
                "签收时发现外箱/内容物损坏，待提交索赔", actor)
        elif s["direction"] == "kit_outbound":
            self.conn.execute(
                "UPDATE material_kits SET status='delivered', delivered_at=? "
                "WHERE kit_no=?", (now(), s["kit_no"]))
        audit(self.conn, actor, "deliver", "shipment", str(shipment_id),
              {"damaged": damaged})
        self.conn.commit()

    # ------------------------------------------------------- 作品回寄 + 加工
    def send_work_back(self, kit_no: str, late: bool, actor: str = "student") -> int:
        """学员寄回作品：独立运单；迟交留痕。"""
        kit = self.get_kit(kit_no)
        if kit["kit"]["status"] not in ("shipped", "delivered"):
            raise ServiceError("材料包尚未送达，不能寄回作品")
        enr = self.conn.execute(
            "SELECT address FROM enrollments WHERE enrollment_id=?",
            (kit["kit"]["enrollment_id"],)).fetchone()
        sid = self._new_shipment(kit_no, "work_inbound", enr["address"],
                                 "工作室加工间", actor, late=late)
        if late:
            self._open_exception(
                kit_no, "late_submission", None, "学员迟交作品",
                f"作品晚于约定日期寄回（运单见作品回寄单）", actor)
        self.conn.commit()
        return sid

    def receive_work(self, kit_no: str, service_type: str,
                     actor: str = "studio") -> int:
        """工作室签收作品并建立加工单（烧制/装裱）。"""
        inbound = self.conn.execute(
            "SELECT * FROM shipments WHERE kit_no=? AND direction='work_inbound' "
            "ORDER BY shipment_id DESC LIMIT 1", (kit_no,)).fetchone()
        if inbound is None:
            raise ServiceError("没有作品回寄运单")
        if inbound["status"] == "in_transit":
            self.conn.execute(
                "UPDATE shipments SET status='delivered', delivered_at=? "
                "WHERE shipment_id=?", (now(), inbound["shipment_id"]))
        if inbound["status"] == "damaged":
            raise ServiceError("回寄作品运输已报损，请先处理索赔")
        exists = self.conn.execute(
            "SELECT 1 FROM processing_jobs WHERE kit_no=?", (kit_no,)).fetchone()
        if exists:
            raise ServiceError("该材料包已有加工单")
        cur = self.conn.execute(
            "INSERT INTO processing_jobs(kit_no, inbound_sid, service_type, "
            "status, created_at, updated_at) VALUES(?,?,?,?,?,?)",
            (kit_no, inbound["shipment_id"], service_type, "received",
             now(), now()))
        job_id = cur.lastrowid
        self._job_event(job_id, "received", "作品签收，待入加工队列")
        audit(self.conn, actor, "receive_work", "processing_job", str(job_id),
              {"inbound_waybill": inbound["waybill"]})
        self.conn.commit()
        return job_id

    def _job_event(self, job_id: int, stage: str, note: str) -> None:
        self.conn.execute(
            "INSERT INTO job_events(job_id, stage, note, at) VALUES(?,?,?,?)",
            (job_id, stage, note, now()))

    def advance_job(self, kit_no: str, stage: str, note: str = "",
                    actor: str = "studio") -> None:
        job = self.conn.execute(
            "SELECT * FROM processing_jobs WHERE kit_no=?", (kit_no,)).fetchone()
        if job is None:
            raise ServiceError("加工单不存在")
        allowed = ["received", "queued", "in_progress", "qa", "done"]
        if stage not in allowed:
            raise ServiceError(f"未知加工阶段: {stage}")
        self.conn.execute(
            "UPDATE processing_jobs SET status=?, updated_at=? WHERE job_id=?",
            (stage, now(), job["job_id"]))
        self._job_event(job["job_id"], stage, note)
        audit(self.conn, actor, "advance_job", "processing_job",
              str(job["job_id"]), {"stage": stage})
        self.conn.commit()

    def job_timeline(self, kit_no: str) -> list[dict]:
        job = self.conn.execute(
            "SELECT * FROM processing_jobs WHERE kit_no=?", (kit_no,)).fetchone()
        if not job:
            return []
        rows = self.conn.execute(
            "SELECT * FROM job_events WHERE job_id=? ORDER BY id",
            (job["job_id"],)).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------ 成品返寄
    def ship_finished(self, kit_no: str, actor: str = "studio") -> str:
        """加工完成后返寄成品：使用与去程/回寄不同的新运单，按最新地址寄出。"""
        job = self.conn.execute(
            "SELECT * FROM processing_jobs WHERE kit_no=?", (kit_no,)).fetchone()
        if job is None:
            raise ServiceError("加工单不存在")
        if job["status"] != "done":
            raise ServiceError("加工未完成，不能返寄成品")
        enr = self.conn.execute(
            "SELECT address FROM enrollments e JOIN material_kits k "
            "ON k.enrollment_id=e.enrollment_id WHERE k.kit_no=?",
            (kit_no,)).fetchone()
        sid = self._new_shipment(kit_no, "finish_outbound", "工作室加工间",
                                 enr["address"], actor)
        self.conn.execute(
            "UPDATE processing_jobs SET outbound_sid=? WHERE job_id=?",
            (sid, job["job_id"]))
        self.conn.commit()
        row = self.conn.execute(
            "SELECT waybill FROM shipments WHERE shipment_id=?", (sid,)).fetchone()
        return row["waybill"]

    # ------------------------------------------------------------------ 索赔
    def file_claim(self, shipment_id: int, title: str, description: str,
                   amount: int, actor: str = "student") -> int:
        s = self.conn.execute("SELECT * FROM shipments WHERE shipment_id=?",
                              (shipment_id,)).fetchone()
        if s is None:
            raise ServiceError("运单不存在")
        if s["status"] != "damaged":
            raise ServiceError("只能对已记录破损的运单提出索赔")
        dup = self.conn.execute(
            "SELECT 1 FROM claims WHERE shipment_id=? AND status='open'",
            (shipment_id,)).fetchone()
        if dup:
            raise ServiceError("该运单已有未结案索赔")
        cur = self.conn.execute(
            "INSERT INTO claims(shipment_id, kit_no, title, description, "
            "status, amount, created_at) VALUES(?,?,?,?, 'open', ?,?)",
            (shipment_id, s["kit_no"], title, description, amount, now()))
        audit(self.conn, actor, "file_claim", "claim", str(cur.lastrowid),
              {"shipment_id": shipment_id, "amount": amount})
        self.conn.commit()
        return cur.lastrowid

    def resolve_claim(self, claim_id: int, approved: bool, resolution: str,
                      actor: str = "admin") -> None:
        row = self.conn.execute("SELECT * FROM claims WHERE claim_id=?",
                                (claim_id,)).fetchone()
        if row is None:
            raise ServiceError("索赔不存在")
        if row["status"] != "open":
            raise ServiceError("索赔已结案")
        self.conn.execute(
            "UPDATE claims SET status=?, resolution=?, resolved_at=?, "
            "resolved_by=? WHERE claim_id=?",
            ("approved" if approved else "rejected", resolution, now(),
             actor, claim_id))
        audit(self.conn, actor, "resolve_claim", "claim", str(claim_id),
              {"approved": approved})
        self.conn.commit()

    def list_claims(self, kit_no: str | None = None) -> list[dict]:
        if kit_no:
            rows = self.conn.execute(
                "SELECT * FROM claims WHERE kit_no=? ORDER BY claim_id DESC",
                (kit_no,)).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM claims ORDER BY claim_id DESC").fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ 照片
    def add_photo(self, kind: str, data: bytes, suffix: str,
                  kit_no: str | None = None, shipment_id: int | None = None,
                  caption: str = "", actor: str = "studio") -> str:
        """把照片字节存入本地 photo_dir，元数据写库。返回可访问的相对路径。"""
        name = f"{kind}-{uuid.uuid4().hex[:8]}.{suffix.lstrip('.')}"
        path = self.photo_dir / name
        path.write_bytes(data)
        rel = f"photos/{name}"
        self.conn.execute(
            "INSERT INTO photos(kind, kit_no, shipment_id, file_path, caption, "
            "uploaded_at) VALUES(?,?,?,?,?,?)",
            (kind, kit_no, shipment_id, rel, caption, now()))
        audit(self.conn, actor, "add_photo", "photo", rel,
              {"kind": kind, "kit_no": kit_no, "shipment_id": shipment_id})
        self.conn.commit()
        return rel

    def list_photos(self, kit_no: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM photos WHERE kit_no=? ORDER BY photo_id",
            (kit_no,)).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------- 综合时间线
    def progress_timeline(self, kit_no: str) -> dict:
        """把去程 / 加工 / 返程三段组织清楚，供进度页展示。"""
        kit = self.get_kit(kit_no)
        outbound = [s for s in kit["shipments"]
                    if s["direction"] == "kit_outbound"]
        inbound = [s for s in kit["shipments"]
                   if s["direction"] == "work_inbound"]
        finish = [s for s in kit["shipments"]
                  if s["direction"] == "finish_outbound"]
        return {
            "kit_no": kit_no,
            "outbound": outbound,                    # 去程：材料包
            "processing": {
                "job": kit["job"],
                "events": self.job_timeline(kit_no),
            },
            "return_inbound": inbound,               # 学员→工作室
            "finish_outbound": finish,               # 返程：成品
        }

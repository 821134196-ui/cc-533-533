"""测试：版本锁定、重复打包/同人双角色、双运单链路、损坏索赔，及其他留痕规则。

运行：python -m unittest discover -s tests -v
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fulfillment.db import connect, init_db
from fulfillment.services import FulfillmentService, ServiceError
from fulfillment.shipping import MockShippingAdapter

BOM_V1 = [
    dict(item_id="t1", name="工具A", category="tool", unit="把", qty=1,
         thumbnail="t1.svg"),
    dict(item_id="cons-clay", name="陶土", category="consumable", unit="块",
         qty=2, thumbnail="c.svg"),
    dict(item_id="col1", name="靛蓝釉", category="color", color_role="釉色",
         unit="瓶", qty=1, thumbnail="col1.svg"),
]
BOM_V2 = [
    dict(item_id="t1", name="工具A", category="tool", unit="把", qty=1,
         thumbnail="t1.svg"),
    dict(item_id="cons-clay", name="陶土", category="consumable", unit="块",
         qty=3, thumbnail="c.svg"),
    dict(item_id="col2", name="月白釉", category="color", color_role="釉色",
         unit="瓶", qty=1, thumbnail="col2.svg"),
]


class ServiceCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.conn = connect(base / "test.db")
        init_db(self.conn)
        self.shipping = MockShippingAdapter()
        self.svc = FulfillmentService(
            self.conn, shipping=self.shipping, photo_dir=base / "photos")
        self.svc.create_course("C1", "测试课")
        self.svc.create_version("C1", "V1", "v1", BOM_V1)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def enroll_and_kit(self, eid="E1", version="V1", addr="旧地址 A"):
        self.svc.create_enrollment(eid, "学员", version, addr)
        return self.svc.create_kit(eid)

    def pack_all(self, kit_no: str, p="阿珍", c="老沈",
                 qty_override: dict | None = None):
        for line in self.svc.get_kit(kit_no)["lines"]:
            qty = (qty_override or {}).get(line["item_id"], line["planned_qty"])
            self.svc.check_line(kit_no, line["item_id"], qty, p)
        self.svc.confirm_packing(kit_no, p, "packer")
        self.svc.confirm_packing(kit_no, c, "checker")

    # -------------------------------------------------------- 测试要求 1
    def test_version_lock_after_revision(self):
        """改版不能改变已发材料包的清单。"""
        kit_no = self.enroll_and_kit("E1")
        before = [(l["item_id"], l["planned_qty"])
                  for l in self.svc.get_kit(kit_no)["lines"]]
        self.assertEqual(before, [("t1", 1), ("cons-clay", 2), ("col1", 1)])

        # 课程改版
        self.svc.create_version("C1", "V2", "v2", BOM_V2)
        after = [(l["item_id"], l["planned_qty"])
                 for l in self.svc.get_kit(kit_no)["lines"]]
        # 旧包仍是 v1 快照
        self.assertEqual(after, before)
        self.assertEqual(self.svc.get_kit(kit_no)["kit"]["version_id"], "V1")

        # 新报名按 v2 生成
        kit2 = self.enroll_and_kit("E2", version="V2")
        self.assertEqual(
            [(l["item_id"], l["planned_qty"])
             for l in self.svc.get_kit(kit2)["lines"]],
            [("t1", 1), ("cons-clay", 3), ("col2", 1)])

    def test_kit_unique_per_enrollment(self):
        """材料包按报名唯一生成。"""
        self.enroll_and_kit("E1")
        with self.assertRaises(ServiceError):
            self.svc.create_kit("E1")

    # -------------------------------------------------------- 测试要求 2
    def test_duplicate_packing_rejected(self):
        """同一角色不能重复确认；同一人不能占两个角色。"""
        kit_no = self.enroll_and_kit()
        for line in self.svc.get_kit(kit_no)["lines"]:
            self.svc.check_line(kit_no, line["item_id"], line["planned_qty"],
                                "阿珍")
        self.svc.confirm_packing(kit_no, "阿珍", "packer")
        # 重复打包员确认
        with self.assertRaises(ServiceError):
            self.svc.confirm_packing(kit_no, "别人", "packer")
        # 同一人当复核员
        with self.assertRaises(ServiceError):
            self.svc.confirm_packing(kit_no, "阿珍", "checker")
        # 第二个人复核成功
        self.svc.confirm_packing(kit_no, "老沈", "checker")
        self.assertEqual(self.svc.get_kit(kit_no)["kit"]["status"], "packed")
        # 已封箱后任何人再确认都拒绝
        with self.assertRaises(ServiceError):
            self.svc.confirm_packing(kit_no, "第三人", "checker")

    def test_missing_item_blocks_seal(self):
        """缺件登记为异常，未处理不能封箱。"""
        kit_no = self.enroll_and_kit()
        for line in self.svc.get_kit(kit_no)["lines"]:
            qty = 1 if line["item_id"] == "cons-clay" else line["planned_qty"]
            self.svc.check_line(kit_no, line["item_id"], qty, "阿珍")
        self.svc.confirm_packing(kit_no, "阿珍", "packer")
        with self.assertRaises(ServiceError):
            self.svc.confirm_packing(kit_no, "老沈", "checker")
        excs = [e for e in self.svc.get_kit(kit_no)["exceptions"]
                if e["type"] == "missing" and e["status"] == "open"]
        self.assertEqual(len(excs), 1)
        self.assertIn("陶土", excs[0]["title"])
        # 处理缺件后可封箱
        self.svc.resolve_exception(excs[0]["exception_id"], "backfill", "仓管")
        self.svc.confirm_packing(kit_no, "老沈", "checker")
        self.assertEqual(self.svc.get_kit(kit_no)["kit"]["status"], "packed")

    def test_unconfirmed_substitute_blocks_seal(self):
        """替代色未经学员确认不能封箱；确认后可以。"""
        kit_no = self.enroll_and_kit()
        for line in self.svc.get_kit(kit_no)["lines"]:
            self.svc.check_line(kit_no, line["item_id"], line["planned_qty"],
                                "小柯")
        self.svc.substitute_color(kit_no, "col1", "雾蓝釉", "col1.svg", "小柯")
        self.svc.confirm_packing(kit_no, "小柯", "packer")
        with self.assertRaises(ServiceError):
            self.svc.confirm_packing(kit_no, "老沈", "checker")
        # 学员接受后放行
        self.svc.confirm_color(kit_no, "col1", accepted=True, actor="学员")
        self.svc.confirm_packing(kit_no, "老沈", "checker")
        self.assertEqual(self.svc.get_kit(kit_no)["kit"]["status"], "packed")

    # -------------------------------------------------------- 测试要求 3
    def test_three_distinct_waybills_chain(self):
        """去程 / 作品回寄 / 成品返寄使用不同运单号且链路完整。"""
        kit_no = self.enroll_and_kit()
        self.pack_all(kit_no)
        wb_out = self.svc.ship_kit(kit_no)
        out_sid = self._sid(kit_no, "kit_outbound")
        self.svc.deliver_shipment(out_sid)
        self.svc.send_work_back(kit_no, late=False)
        in_sid = self._sid(kit_no, "work_inbound")
        self.assertNotEqual(in_sid, out_sid)
        self.svc.deliver_shipment(in_sid)
        self.svc.receive_work(kit_no, "firing")
        for stage in ("queued", "in_progress", "qa", "done"):
            self.svc.advance_job(kit_no, stage, note="")
        wb_fin = self.svc.ship_finished(kit_no)
        fin_sid = self._sid(kit_no, "finish_outbound")
        self.svc.deliver_shipment(fin_sid)

        ships = self.svc.get_kit(kit_no)["shipments"]
        waybills = [s["waybill"] for s in ships]
        self.assertEqual(len(waybills), 3)
        self.assertEqual(len(set(waybills)), 3, "三张运单号必须互不相同")
        by_dir = {s["direction"]: s for s in ships}
        self.assertEqual(by_dir["kit_outbound"]["waybill"], wb_out)
        self.assertEqual(by_dir["finish_outbound"]["waybill"], wb_fin)
        self.assertTrue(by_dir["kit_outbound"]["waybill"].startswith("CK-"))
        self.assertTrue(by_dir["work_inbound"]["waybill"].startswith("HS-"))
        self.assertTrue(by_dir["finish_outbound"]["waybill"].startswith("FC-"))
        self.assertTrue(all(s["status"] == "delivered" for s in ships))

    def test_late_submission_logged(self):
        """迟交作品留痕。"""
        kit_no = self.enroll_and_kit()
        self.pack_all(kit_no)
        self.svc.ship_kit(kit_no)
        self.svc.deliver_shipment(self._sid(kit_no, "kit_outbound"))
        self.svc.send_work_back(kit_no, late=True)
        sid = self._sid(kit_no, "work_inbound")
        shipment = self.conn.execute(
            "SELECT late FROM shipments WHERE shipment_id=?", (sid,)).fetchone()
        self.assertEqual(shipment["late"], 1)
        late = [e for e in self.svc.get_kit(kit_no)["exceptions"]
                if e["type"] == "late_submission"]
        self.assertEqual(len(late), 1)

    def test_address_change_uses_latest_for_finish(self):
        """返寄地址变更留痕，成品按最新地址返寄。"""
        kit_no = self.enroll_and_kit(addr="旧地址 A")
        self.pack_all(kit_no)
        self.svc.ship_kit(kit_no)
        self.svc.deliver_shipment(self._sid(kit_no, "kit_outbound"))
        self.svc.send_work_back(kit_no, late=False)
        self.svc.deliver_shipment(self._sid(kit_no, "work_inbound"))
        self.svc.receive_work(kit_no, "framing")
        for stage in ("queued", "in_progress", "qa", "done"):
            self.svc.advance_job(kit_no, stage, "")
        self.svc.change_address("E1", "新地址 B", actor="学员", reason="搬家")
        self.svc.ship_finished(kit_no)
        fin = self.conn.execute(
            "SELECT to_addr FROM shipments WHERE kit_no=? AND "
            "direction='finish_outbound'", (kit_no,)).fetchone()
        self.assertEqual(fin["to_addr"], "新地址 B")
        changes = self.conn.execute(
            "SELECT * FROM address_changes WHERE enrollment_id='E1'").fetchall()
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["old_address"], "旧地址 A")

    # -------------------------------------------------------- 测试要求 4
    def test_damage_claim_flow(self):
        """运输损坏 → 异常留痕 → 索赔 → 审批。"""
        kit_no = self.enroll_and_kit()
        self.pack_all(kit_no)
        self.svc.ship_kit(kit_no)
        self.svc.deliver_shipment(self._sid(kit_no, "kit_outbound"))
        self.svc.send_work_back(kit_no, late=False)
        in_sid = self._sid(kit_no, "work_inbound")
        self.svc.deliver_shipment(in_sid, damaged=True)
        # 破损已留异常
        damage_exc = [e for e in self.svc.get_kit(kit_no)["exceptions"]
                      if e["type"] == "transit_damage"]
        self.assertEqual(len(damage_exc), 1)
        # 破损运单不能直接进加工
        with self.assertRaises(ServiceError):
            self.svc.receive_work(kit_no, "firing")
        cid = self.svc.file_claim(in_sid, "损坏索赔", "作品碎裂", 300)
        # 不允许重复索赔
        with self.assertRaises(ServiceError):
            self.svc.file_claim(in_sid, "重复索赔", "", 10)
        self.svc.resolve_claim(cid, approved=True, resolution="赔付300")
        claim = self.conn.execute(
            "SELECT * FROM claims WHERE claim_id=?", (cid,)).fetchone()
        self.assertEqual(claim["status"], "approved")
        # 索赔只能针对破损运单
        out_sid = self._sid(kit_no, "kit_outbound")
        with self.assertRaises(ServiceError):
            self.svc.file_claim(out_sid, "无破损索赔", "", 10)

    def test_claim_must_be_on_damaged_waybill(self):
        kit_no = self.enroll_and_kit()
        self.pack_all(kit_no)
        self.svc.ship_kit(kit_no)
        out_sid = self._sid(kit_no, "kit_outbound")
        self.svc.deliver_shipment(out_sid)
        with self.assertRaises(ServiceError):
            self.svc.file_claim(out_sid, "无损坏", "", 50)

    # -------------------------------------------------------- 其他守卫
    def test_cannot_ship_before_two_packers(self):
        kit_no = self.enroll_and_kit()
        with self.assertRaises(ServiceError):
            self.svc.ship_kit(kit_no)

    def test_photo_local_storage(self):
        self.enroll_and_kit()
        rel = self.svc.add_photo("kit_outbound", b"<svg/>", "svg",
                                 kit_no="KIT-E1", caption="测试")
        self.assertTrue(Path(self.tmp.name, "photos", Path(rel).name).exists())
        rows = self.svc.list_photos("KIT-E1")
        self.assertEqual(len(rows), 1)

    def _sid(self, kit_no: str, direction: str) -> int:
        row = self.conn.execute(
            "SELECT shipment_id FROM shipments WHERE kit_no=? AND direction=? "
            "ORDER BY shipment_id DESC LIMIT 1", (kit_no, direction)).fetchone()
        return row["shipment_id"]


if __name__ == "__main__":
    unittest.main()

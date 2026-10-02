"""损坏索赔 / 配送破损 / 迟交 / 地址变更 / 照片 留痕。"""
from tests.base import StudioCase
from studio import services as svc


def kinds_for(conn, ref_id):
    return {r["kind"] for r in conn.execute(
        "SELECT DISTINCT kind FROM incidents WHERE ref_id=?", (ref_id,))}


class TestClaimsAndIncidents(StudioCase):
    def test_kit_delivery_damage_creates_claim(self):
        kit_id = self.enroll_and_kit("E1")
        self.pack_all(kit_id)
        svc.confirm_packing(self.conn, kit_id, "阿川", "晓岚")
        shp = svc.ship_kit(self.conn, kit_id)
        res = svc.deliver_shipment(
            self.conn, shp["tracking_no"], damaged=True,
            damage_note="外箱塌陷，釉料瓶碎裂", damaged_items=["靛蓝釉 x1"])
        self.assertIsNotNone(res["claim"])
        self.assertEqual(res["claim"]["scene"], "kit_delivery")
        self.assertEqual(res["shipment"]["status"], "damaged")
        # 材料包在损坏签收时不应被置为 delivered
        kit = svc.get_kit(self.conn, kit_id)
        self.assertEqual(kit["status"], "shipped")
        self.assertIn("transit_damage", kinds_for(self.conn, kit_id))

        svc.resolve_claim(self.conn, res["claim"]["id"], "approved", "补发釉料")
        claim = self.conn.execute("SELECT * FROM claims WHERE id=?",
                                  (res["claim"]["id"],)).fetchone()
        self.assertEqual(claim["status"], "approved")

    def test_work_transit_damage_blocks_processing_until_claim_resolved(self):
        kit_id = self.enroll_and_kit("E1")
        self.full_outbound(kit_id)
        wo = svc.create_work_order(self.conn, "E1", "firing", "2026-09-20")
        inb = svc.submit_work(self.conn, wo["id"])  # due 已过 → 迟交
        res = svc.deliver_shipment(
            self.conn, inb["tracking_no"], damaged=True,
            damage_note="杯口磕裂", damaged_items=["陶杯"])
        # 损坏时作品仍在运输中状态，不能推进加工
        with self.assertRaises(svc.DomainError):
            svc.update_work_status(self.conn, wo["id"], "firing")
        # 索赔协商解决 → 接收受损作品进入修复
        svc.resolve_claim(self.conn, res["claim"]["id"], "resolved", "免费二次修复")
        wo2 = svc.get_work_order(self.conn, wo["id"])
        self.assertEqual(wo2["status"], "received")
        self.assertIn("late_submission", kinds_for(self.conn, wo["id"]))
        self.assertIn("transit_damage", kinds_for(self.conn, wo["id"]))

    def test_late_submission_recorded(self):
        kit_id = self.enroll_and_kit("E1")
        self.full_outbound(kit_id)
        wo = svc.create_work_order(self.conn, "E1", "firing", "2026-09-25")
        svc.submit_work(self.conn, wo["id"])
        self.assertIn("late_submission", kinds_for(self.conn, wo["id"]))
        self.assertEqual(svc.get_work_order(self.conn, wo["id"])["late"], 1)

    def test_on_time_submission_not_flagged(self):
        kit_id = self.enroll_and_kit("E1")
        self.full_outbound(kit_id)
        wo = svc.create_work_order(self.conn, "E1", "firing", "2026-12-31")
        svc.submit_work(self.conn, wo["id"])
        self.assertNotIn("late_submission", kinds_for(self.conn, wo["id"]))

    def test_address_change_logged_and_blocked_after_return(self):
        kit_id = self.enroll_and_kit("E1")
        self.full_outbound(kit_id)
        wo = svc.create_work_order(self.conn, "E1", "firing", "2026-10-20")
        inb = svc.submit_work(self.conn, wo["id"])
        svc.deliver_shipment(self.conn, inb["tracking_no"])
        svc.update_work_status(self.conn, wo["id"], "done")
        svc.change_return_address(self.conn, wo["id"], "新地址 99 号", by="客服")
        self.assertIn("address_change", kinds_for(self.conn, wo["id"]))
        self.assertEqual(svc.get_work_order(self.conn, wo["id"])["return_addr"], "新地址 99 号")

        ret = svc.return_finished(self.conn, wo["id"])
        # 已返寄后拒绝再改地址
        with self.assertRaises(svc.DomainError) as cm:
            svc.change_return_address(self.conn, wo["id"], "又一个地址")
        self.assertEqual(cm.exception.code, "already_returned")
        svc.deliver_shipment(self.conn, ret["tracking_no"])
        self.assertEqual(svc.get_work_order(self.conn, wo["id"])["status"], "returned")

    def test_address_change_rejects_same_and_empty(self):
        kit_id = self.enroll_and_kit("E1")
        self.full_outbound(kit_id)
        wo = svc.create_work_order(self.conn, "E1", "firing", "2026-10-20")
        with self.assertRaises(svc.DomainError) as cm:
            svc.change_return_address(self.conn, wo["id"], "测试地址 1 号")
        self.assertEqual(cm.exception.code, "same_address")
        with self.assertRaises(svc.DomainError):
            svc.change_return_address(self.conn, wo["id"], "  ")

    def test_photo_metadata_saved(self):
        p = svc.add_photo(self.conn, "work_sent", "WO-E1", "/photos/x.png", "寄出照")
        self.assertEqual(p["path"], "/photos/x.png")
        row = self.conn.execute("SELECT COUNT(*) c FROM photos WHERE ref_id=?",
                                ("WO-E1",)).fetchone()
        self.assertEqual(row["c"], 1)

    def test_finished_transit_damage_creates_claim(self):
        kit_id = self.enroll_and_kit("E1")
        self.full_outbound(kit_id)
        wo = svc.create_work_order(self.conn, "E1", "firing", "2026-10-20")
        inb = svc.submit_work(self.conn, wo["id"])
        svc.deliver_shipment(self.conn, inb["tracking_no"])
        svc.update_work_status(self.conn, wo["id"], "done")
        ret = svc.return_finished(self.conn, wo["id"])
        res = svc.deliver_shipment(self.conn, ret["tracking_no"], damaged=True,
                                   damage_note="木框角部凹陷")
        self.assertEqual(res["claim"]["scene"], "finished_transit")

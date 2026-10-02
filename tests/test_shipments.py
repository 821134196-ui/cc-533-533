"""双运单链路：去程材料包 / 作品回寄 / 成品返寄为三张独立运单。"""
from tests.base import StudioCase
from studio import services as svc


class TestWaybillChain(StudioCase):
    def _prepare_work_order(self, eid="E1", due="2026-10-20"):
        kit_id = self.enroll_and_kit(eid)
        self.full_outbound(kit_id)
        return svc.create_work_order(self.conn, eid, "firing", due)

    def test_three_distinct_shipments_full_chain(self):
        wo = self._prepare_work_order()
        inb = svc.submit_work(self.conn, wo["id"])
        svc.deliver_shipment(self.conn, inb["tracking_no"])
        for st in ("in_progress", "firing", "qc", "done"):
            svc.update_work_status(self.conn, wo["id"], st)
        ret = svc.return_finished(self.conn, wo["id"])
        svc.deliver_shipment(self.conn, ret["tracking_no"])

        rows = self.conn.execute(
            "SELECT direction, tracking_no, status FROM shipments ORDER BY created_at, id").fetchall()
        dirs = [r["direction"] for r in rows]
        self.assertEqual(dirs, ["outbound_kit", "inbound_work", "outbound_finished"])
        nos = [r["tracking_no"] for r in rows]
        self.assertEqual(len(set(nos)), 3)  # 三张运单号互不相同
        self.assertTrue(all(n.startswith("MOCK-") for n in nos))
        # 号段区分方向
        self.assertTrue(nos[0].startswith("MOCK-CK"))
        self.assertTrue(nos[1].startswith("MOCK-RC"))
        self.assertTrue(nos[2].startswith("MOCK-FC"))
        self.assertTrue(all(r["status"] == "delivered" for r in rows))

    def test_work_submission_requires_delivered_kit(self):
        svc.create_enrollment(self.conn, "E2", "乙", "online", "v1", "地址")
        kit_id = svc.generate_kit(self.conn, "E2")["id"]
        wo = svc.create_work_order(self.conn, "E2", "firing", "2026-10-20")
        with self.assertRaises(svc.DomainError) as cm:
            svc.submit_work(self.conn, wo["id"])
        self.assertEqual(cm.exception.code, "kit_not_delivered")

    def test_cannot_return_before_processing_done(self):
        wo = self._prepare_work_order()
        inb = svc.submit_work(self.conn, wo["id"])
        svc.deliver_shipment(self.conn, inb["tracking_no"])
        with self.assertRaises(svc.DomainError) as cm:
            svc.return_finished(self.conn, wo["id"])
        self.assertEqual(cm.exception.code, "not_done")

    def test_onsite_kit_has_no_shipment(self):
        kit_id = self.enroll_and_kit("E9", channel="onsite", addr="")
        self.pack_all(kit_id)
        svc.confirm_packing(self.conn, kit_id, "阿川", "晓岚")
        kit = svc.hand_over_onsite(self.conn, kit_id, by="前台")
        self.assertEqual(kit["status"], "delivered")
        n = self.conn.execute("SELECT COUNT(*) c FROM shipments WHERE ref_id=?",
                              (kit_id,)).fetchone()["c"]
        self.assertEqual(n, 0)

    def test_onsite_handover_blocked_for_online_student(self):
        kit_id = self.enroll_and_kit(channel="online")
        self.pack_all(kit_id)
        svc.confirm_packing(self.conn, kit_id, "阿川", "晓岚")
        with self.assertRaises(svc.DomainError) as cm:
            svc.hand_over_onsite(self.conn, kit_id)
        self.assertEqual(cm.exception.code, "not_onsite")

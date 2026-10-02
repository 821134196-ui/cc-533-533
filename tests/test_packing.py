"""重复打包 / 两人确认 / 缺件 / 替代色 / 并发互斥。"""
import threading

from tests.base import StudioCase
from studio import services as svc


class TestPacking(StudioCase):
    def test_shortage_blocks_sealing(self):
        kit_id = self.enroll_and_kit()
        svc.pack_item(self.conn, kit_id, "tool-a", 1)
        svc.pack_item(self.conn, kit_id, "con-clay", 0)  # 缺件
        svc.pack_item(self.conn, kit_id, "glaze-indigo", 1)
        with self.assertRaises(svc.DomainError) as cm:
            svc.confirm_packing(self.conn, kit_id, "阿川", "晓岚")
        self.assertEqual(cm.exception.code, "packing_incomplete")
        self.assertIn("陶土", cm.exception.message)
        kinds = {r["kind"] for r in self.conn.execute(
            "SELECT kind FROM incidents WHERE ref_id=?", (kit_id,))}
        self.assertIn("shortage", kinds)

    def test_unconfirmed_substitute_blocks_sealing(self):
        kit_id = self.enroll_and_kit()
        self.pack_all(kit_id)
        svc.pack_item(self.conn, kit_id, "glaze-indigo", 1, substitute_color="slate")
        with self.assertRaises(svc.DomainError) as cm:
            svc.confirm_packing(self.conn, kit_id, "阿川", "晓岚")
        self.assertEqual(cm.exception.code, "packing_incomplete")
        self.assertIn("替代颜色未确认", cm.exception.message)

    def test_confirmed_substitute_allows_sealing(self):
        kit_id = self.enroll_and_kit()
        self.pack_all(kit_id)
        svc.pack_item(self.conn, kit_id, "glaze-indigo", 1,
                      substitute_color="slate", confirmed=True)
        kit = svc.confirm_packing(self.conn, kit_id, "阿川", "晓岚")
        self.assertEqual(kit["status"], "packed")

    def test_same_packer_rejected(self):
        kit_id = self.enroll_and_kit()
        self.pack_all(kit_id)
        with self.assertRaises(svc.DomainError) as cm:
            svc.confirm_packing(self.conn, kit_id, "阿川", "阿川")
        self.assertEqual(cm.exception.code, "same_packer")

    def test_missing_packer_rejected(self):
        kit_id = self.enroll_and_kit()
        self.pack_all(kit_id)
        with self.assertRaises(svc.DomainError) as cm:
            svc.confirm_packing(self.conn, kit_id, "", "晓岚")
        self.assertEqual(cm.exception.code, "need_two_packers")

    def test_duplicate_confirm_rejected(self):
        kit_id = self.enroll_and_kit()
        self.pack_all(kit_id)
        svc.confirm_packing(self.conn, kit_id, "阿川", "晓岚")
        with self.assertRaises(svc.DomainError) as cm:
            svc.confirm_packing(self.conn, kit_id, "晓岚", "阿川")
        self.assertEqual(cm.exception.code, "already_packed")

    def test_concurrent_confirm_only_one_wins(self):
        """两名打包员在两个线程里同时确认同一包：恰好一人成功。"""
        kit_id = self.enroll_and_kit()
        self.pack_all(kit_id)
        results: list[str] = []
        barrier = threading.Barrier(2)

        def confirm(packer: str, checker: str) -> None:
            # 每个线程使用独立连接（模拟两个工作台）
            from studio.db import connect
            conn = connect(self.db)
            barrier.wait()
            try:
                svc.confirm_packing(conn, kit_id, packer, checker)
                results.append("ok")
            except svc.DomainError as e:
                results.append(e.code)
            finally:
                conn.close()

        t1 = threading.Thread(target=confirm, args=("阿川", "晓岚"))
        t2 = threading.Thread(target=confirm, args=("晓岚", "阿川"))
        t1.start(); t2.start(); t1.join(); t2.join()
        self.assertEqual(sorted(results), ["already_packed", "ok"])
        kit = svc.get_kit(self.conn, kit_id)
        # 获胜者不确定，但两人不同且只有一人主确认
        self.assertNotEqual(kit["packed_by"], kit["checked_by"])
        self.assertIn((kit["packed_by"], kit["checked_by"]),
                      [("阿川", "晓岚"), ("晓岚", "阿川")])

    def test_packed_kit_items_frozen(self):
        kit_id = self.enroll_and_kit()
        self.pack_all(kit_id)
        svc.confirm_packing(self.conn, kit_id, "阿川", "晓岚")
        with self.assertRaises(svc.DomainError) as cm:
            svc.pack_item(self.conn, kit_id, "con-clay", 1)
        self.assertEqual(cm.exception.code, "kit_locked")

    def test_cannot_ship_unpacked_kit(self):
        kit_id = self.enroll_and_kit()
        with self.assertRaises(svc.DomainError) as cm:
            svc.ship_kit(self.conn, kit_id)
        self.assertEqual(cm.exception.code, "not_packed")

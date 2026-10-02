"""版本锁定：课程改版不改变已生成材料包的清单。"""
import json

from tests.base import StudioCase
from studio import services as svc


class TestVersionLock(StudioCase):
    def test_kit_is_unique_per_enrollment(self):
        svc.create_enrollment(self.conn, "E1", "甲", "online", "v1", "地址")
        svc.generate_kit(self.conn, "E1")
        with self.assertRaises(svc.DomainError) as cm:
            svc.generate_kit(self.conn, "E1")
        self.assertEqual(cm.exception.code, "kit_exists")

    def test_snapshot_keeps_v1_list_after_v2_published(self):
        kit_id = self.enroll_and_kit("E1", version="v1")
        snap = json.loads(svc.get_kit(self.conn, kit_id)["version_snapshot"])
        self.assertEqual(len(snap), 3)
        self.assertEqual({i["sku"] for i in snap},
                         {"tool-a", "con-clay", "glaze-indigo"})
        self.assertEqual(next(i for i in snap if i["sku"] == "con-clay")["qty"], 2)

    def test_snapshot_actually_contains_v1_values(self):
        kit_id = self.enroll_and_kit("E1", version="v1")
        kit = svc.get_kit(self.conn, kit_id)
        self.assertEqual(len(kit["items"]), 3)
        clay = next(i for i in kit["items"] if i["sku"] == "con-clay")
        self.assertEqual(clay["qty_required"], 2)
        slates = [i for i in kit["items"] if i["sku"] == "glaze-slate"]
        self.assertEqual(slates, [])

    def test_new_enrollment_after_revision_uses_v2(self):
        self.enroll_and_kit("E1", version="v1")
        kit2_id = self.enroll_and_kit("E2", version="v2")
        kit2 = svc.get_kit(self.conn, kit2_id)
        self.assertEqual(len(kit2["items"]), 4)
        self.assertTrue(any(i["sku"] == "glaze-slate" for i in kit2["items"]))

    def test_revising_v2_items_does_not_touch_existing_kit_rows(self):
        kit_id = self.enroll_and_kit("E1", version="v1")
        # 在 v2 中再改一次数量（模拟后续改版），旧包行数据不变
        self.conn.execute(
            "UPDATE course_version_items SET qty=9 WHERE version_id='v2' AND sku='con-clay'")
        kit = svc.get_kit(self.conn, kit_id)
        clay = next(i for i in kit["items"] if i["sku"] == "con-clay")
        self.assertEqual(clay["qty_required"], 2)

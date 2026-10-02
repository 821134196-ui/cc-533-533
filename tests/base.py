"""测试基类：每个用例使用全新临时数据库与精简课程版本。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from studio import services as svc  # noqa: E402
from studio.db import connect, init_db  # noqa: E402

ITEMS_V1 = [
    {"sku": "tool-a", "name": "转盘", "category": "tool", "icon": "/x/a.svg", "qty": 1},
    {"sku": "con-clay", "name": "陶土", "category": "consumable", "icon": "/x/c.svg", "qty": 2},
    {"sku": "glaze-indigo", "name": "靛蓝釉", "category": "color", "icon": "/x/g.svg",
     "qty": 1, "color_code": "indigo"},
]
ITEMS_V2 = ITEMS_V1 + [
    {"sku": "glaze-slate", "name": "灰蓝釉", "category": "color", "icon": "/x/s.svg",
     "qty": 1, "color_code": "slate"},
]


class StudioCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "test.db"
        self.conn = connect(self.db)
        init_db(self.conn)
        svc.create_version(self.conn, "v1", "初版", "", ITEMS_V1)
        svc.create_version(self.conn, "v2", "改版", "", ITEMS_V2)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    # ---- 辅助 ----
    def enroll_and_kit(self, eid="E1", version="v1", channel="online",
                       addr="测试地址 1 号") -> str:
        svc.create_enrollment(self.conn, eid, f"学员{eid}", channel, version, addr)
        kit = svc.generate_kit(self.conn, eid)
        return kit["id"]

    def pack_all(self, kit_id: str, items=None) -> None:
        items = items or ITEMS_V1
        for it in items:
            svc.pack_item(self.conn, kit_id, it["sku"], it["qty"])

    def full_outbound(self, kit_id: str) -> str:
        """打包→封箱→发运→签收，返回去程运单号。"""
        self.pack_all(kit_id)
        svc.confirm_packing(self.conn, kit_id, "阿川", "晓岚")
        shp = svc.ship_kit(self.conn, kit_id)
        svc.deliver_shipment(self.conn, shp["tracking_no"])
        return shp["tracking_no"]

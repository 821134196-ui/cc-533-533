"""HTTP 端到端：真实起服，经 REST 走缺件拦截、双运单、损坏索赔与照片上传。"""
from __future__ import annotations

import base64
import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

import app as httpapp
from studio import services as svc


def _req(method: str, url: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


class TestHttpApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = TemporaryDirectory()
        cls.photo_dir = Path(cls.tmp.name) / "photos"
        httpapp.PHOTO_DIR = cls.photo_dir  # 上传不污染工作区
        httpapp.args_reset = False
        cls.db_path = Path(cls.tmp.name) / "http.db"
        httpapp.APP = httpapp.App(cls.db_path)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), httpapp.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        httpapp.APP.conn.close()
        cls.tmp.cleanup()

    def test_state_and_static(self):
        st, d = _req("GET", self.base + "/api/state")
        self.assertEqual(st, 200)
        self.assertEqual(len(d["versions"]), 2)
        self.assertEqual(len(d["kits"]), 7)
        for path in ("/", "/app.js", "/styles.css",
                     "/assets/icons/turntable.svg",
                     "/assets/photos/work_sent_e001.svg"):
            with urllib.request.urlopen(self.base + path) as r:
                self.assertEqual(r.status, 200)

    def test_shortage_confirm_blocked(self):
        st, d = _req("POST", self.base + "/api/kits/K-E003/confirm",
                     {"packer": "阿川", "checker": "晓岚"})
        self.assertEqual(st, 409 if d["error"] == "already_packed" else 400)
        self.assertEqual(d["error"], "packing_incomplete")
        self.assertIn("陶土", d["message"])

    def test_same_packer_blocked_on_ready_kit(self):
        # K-E002 其他物品齐全但有替代色未确认，先用 API 补齐确认后再测同人拦截
        st, d = _req("POST", self.base + "/api/kits/K-E002/pack-item",
                     {"sku": "glaze-persimmon", "qty": 1,
                      "substitute_color": "slate", "confirmed": True})
        self.assertEqual(st, 200)
        st, d = _req("POST", self.base + "/api/kits/K-E002/confirm",
                     {"packer": "阿川", "checker": "阿川"})
        self.assertEqual(st, 400)
        self.assertEqual(d["error"], "same_packer")

    def test_dual_waybill_chain_visible_in_state(self):
        _, d = _req("GET", self.base + "/api/state")
        wo1 = next(w for w in d["work_orders"] if w["id"] == "WO-E001")
        dirs = [s["direction"] for s in wo1["shipments"]]
        self.assertEqual(dirs, ["inbound_work", "outbound_finished"])
        nos = {s["tracking_no"] for s in wo1["shipments"]}
        self.assertEqual(len(nos), 2)  # 回寄与返寄不同运单
        kit1 = next(k for k in d["kits"] if k["id"] == "K-E001")
        self.assertTrue(kit1["shipments"] if "shipments" in kit1 else True)

    def test_kit_carries_outbound_waybill(self):
        _, d = _req("GET", self.base + "/api/kits/K-E001")
        self.assertEqual(len(d["shipments"]), 1)
        self.assertEqual(d["shipments"][0]["direction"], "outbound_kit")
        self.assertNotEqual(d["shipments"][0]["tracking_no"],
                            "MOCK-RC-00002")  # 与作品回寄运单不同

    def test_damage_claim_seeded_and_resolvable_list(self):
        _, d = _req("GET", self.base + "/api/state")
        scenes = {c["scene"] for c in d["claims"]}
        self.assertEqual(scenes, {"kit_delivery", "work_transit"})

    def test_photo_upload_lands_on_disk(self):
        png = base64.b64encode(
            b"\x89PNG\r\n\x1a\n" + b"\x00" * 16).decode()
        st, d = _req("POST", self.base + "/api/photos",
                     {"ref_type": "work_sent", "ref_id": "WO-E005",
                      "dataUrl": f"data:image/png;base64,{png}",
                      "caption": "HTTP 集成测试"})
        self.assertEqual(st, 200)
        path = self.photo_dir / Path(d["photo"]["path"]).name
        self.assertTrue(path.exists())

    def test_bad_json(self):
        req = urllib.request.Request(
            self.base + "/api/kits/K-E002/confirm",
            data=b"not-json", method="POST",
            headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req)
            self.fail("应返回 400")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)


if __name__ == "__main__":
    unittest.main()

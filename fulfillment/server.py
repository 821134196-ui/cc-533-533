"""本地 Web 服务：JSON API + 静态页面，仅用标准库。

启动：python -m fulfillment.server  (或 ./run.sh)
"""
from __future__ import annotations

import base64
import json
import mimetypes
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .db import connect
from .services import FulfillmentService, ServiceError

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "fulfillment" / "web"
PHOTO_DIR = ROOT / "fulfillment" / "photos"
DB_PATH = ROOT / "fulfillment" / "fulfillment.db"

BOM_KIND_LABEL = {"tool": "工具", "consumable": "消耗品", "color": "颜色选择"}
SHIP_LABEL = {"kit_outbound": "材料包去程", "work_inbound": "作品回寄",
              "finish_outbound": "成品返寄"}


def _service() -> FulfillmentService:
    conn = connect(DB_PATH)
    return FulfillmentService(conn, photo_dir=PHOTO_DIR)


class Handler(BaseHTTPRequestHandler):
    server_version = "Fulfillment/1.0"

    # ------------------------------------------------------------ 工具方法
    def _json(self, obj, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode())

    def _run(self, fn):
        """执行服务调用，把 ServiceError 映射为 409。"""
        try:
            result = fn()
            return self._json({"ok": True, "data": result})
        except ServiceError as e:
            return self._json({"ok": False, "error": str(e)}, 409)
        except Exception as e:  # 兜底：返回 JSON 而非断开连接
            return self._json({"ok": False, "error": f"服务器错误: {e}"}, 500)

    def _static(self, path: Path) -> None:
        if not path.is_file():
            self.send_error(404)
            return
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):  # 安静一点
        pass

    # ------------------------------------------------------------ 路由：读
    def do_GET(self) -> None:
        url = urlparse(self.path)
        p = url.path

        if p == "/api/state":
            return self._run(self._state)
        m = re.fullmatch(r"/api/kits/([^/]+)/photos", p)
        if m:
            svc = _service()
            return self._json({"ok": True, "data": svc.list_photos(m.group(1))})

        # 静态资源
        if p.startswith("/photos/"):
            return self._static(ROOT / "fulfillment" / p.lstrip("/"))
        if p.startswith("/static/"):
            return self._static(WEB_DIR / p.removeprefix("/"))
        if p == "/" or p == "/index.html":
            return self._static(WEB_DIR / "index.html")
        self.send_error(404)

    def _state(self):
        svc = _service()
        courses = [dict(r) for r in svc.conn.execute(
            "SELECT * FROM courses ORDER BY course_id")]
        versions = []
        for v in svc.conn.execute(
                "SELECT * FROM course_versions ORDER BY created_at"):
            v = dict(v)
            v["items"] = [dict(r) for r in svc.get_bom(v["version_id"])]
            versions.append(v)
        audit_rows = [dict(r) for r in svc.conn.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT 60")]
        return {"courses": courses, "versions": versions,
                "kits": svc.list_kits(), "claims": svc.list_claims(),
                "audit": audit_rows,
                "labels": {"category": BOM_KIND_LABEL, "ship": SHIP_LABEL}}

    # ------------------------------------------------------------ 路由：写
    def do_POST(self) -> None:
        url = urlparse(self.path)
        p = url.path
        payload = self._read_json()
        svc = _service()

        def K(no):
            return no

        m = re.fullmatch(r"/api/kits/([^/]+)/lines/([^/]+)/check", p)
        if m:
            return self._run(lambda: svc.check_line(
                m.group(1), m.group(2), int(payload["packed_qty"]),
                payload.get("actor", "staff")))

        m = re.fullmatch(r"/api/kits/([^/]+)/confirm", p)
        if m:
            return self._run(lambda: svc.confirm_packing(
                m.group(1), payload["packer"], payload["role"],
                payload.get("actor")))

        m = re.fullmatch(r"/api/kits/([^/]+)/substitute", p)
        if m:
            return self._run(lambda: svc.substitute_color(
                m.group(1), payload["item_id"], payload["name"],
                payload.get("thumbnail", "color-indigo.svg"),
                payload.get("actor", "staff")))

        m = re.fullmatch(r"/api/kits/([^/]+)/color-confirm", p)
        if m:
            return self._run(lambda: svc.confirm_color(
                m.group(1), payload["item_id"], bool(payload["accepted"]),
                payload.get("actor", "student")))

        m = re.fullmatch(r"/api/exceptions/(\d+)/resolve", p)
        if m:
            return self._run(lambda: svc.resolve_exception(
                int(m.group(1)), payload.get("resolution", "backfill"),
                payload.get("actor", "staff")))

        m = re.fullmatch(r"/api/kits/([^/]+)/ship-kit", p)
        if m:
            return self._run(lambda: svc.ship_kit(
                m.group(1), payload.get("actor", "staff")))

        m = re.fullmatch(r"/api/shipments/(\d+)/deliver", p)
        if m:
            return self._run(lambda: svc.deliver_shipment(
                int(m.group(1)), payload.get("actor", "courier"),
                bool(payload.get("damaged", False))))

        m = re.fullmatch(r"/api/kits/([^/]+)/send-work", p)
        if m:
            return self._run(lambda: svc.send_work_back(
                m.group(1), bool(payload.get("late", False)),
                payload.get("actor", "student")))

        m = re.fullmatch(r"/api/kits/([^/]+)/receive-work", p)
        if m:
            return self._run(lambda: svc.receive_work(
                m.group(1), payload.get("service_type", "firing"),
                payload.get("actor", "studio")))

        m = re.fullmatch(r"/api/kits/([^/]+)/advance-job", p)
        if m:
            return self._run(lambda: svc.advance_job(
                m.group(1), payload["stage"], payload.get("note", ""),
                payload.get("actor", "studio")))

        m = re.fullmatch(r"/api/kits/([^/]+)/ship-finished", p)
        if m:
            return self._run(lambda: svc.ship_finished(
                m.group(1), payload.get("actor", "studio")))

        m = re.fullmatch(r"/api/shipments/(\d+)/claims", p)
        if m:
            return self._run(lambda: svc.file_claim(
                int(m.group(1)), payload["title"],
                payload.get("description", ""), int(payload.get("amount", 0)),
                payload.get("actor", "student")))

        m = re.fullmatch(r"/api/claims/(\d+)/resolve", p)
        if m:
            return self._run(lambda: svc.resolve_claim(
                int(m.group(1)), bool(payload["approved"]),
                payload.get("resolution", ""), payload.get("actor", "admin")))

        m = re.fullmatch(r"/api/enrollments/([^/]+)/address", p)
        if m:
            return self._run(lambda: svc.change_address(
                m.group(1), payload["address"], payload.get("actor", "student"),
                payload.get("reason", "")))

        if p == "/api/photos":
            return self._run(lambda: self._add_photo(svc, payload))

        self._json({"ok": False, "error": f"未知接口 {p}"}, 404)

    def _add_photo(self, svc: FulfillmentService, payload: dict):
        raw = payload["data"].split(",", 1)[-1]
        data = base64.b64decode(raw)
        mime = payload["data"].split(";", 1)[0]
        suffix = "png" if "png" in mime else ("jpg" if "jpeg" in mime else "svg")
        return svc.add_photo(
            payload["kind"], data, suffix,
            kit_no=payload.get("kit_no"),
            shipment_id=payload.get("shipment_id"),
            caption=payload.get("caption", ""),
            actor=payload.get("actor", "studio"))


def main() -> None:
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"手作履约系统已启动: http://127.0.0.1:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()

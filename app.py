"""本地一条命令启动的 HTTP 服务：REST API + 静态页面 + 本地照片落盘。

用法：python3 app.py [--reset] [--port 8000]
首次启动自动建库并播种缺件案例；--reset 清空重来。
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from studio import services as svc  # noqa: E402
from studio.db import connect, init_db  # noqa: E402
from studio.shipping import get_carrier  # noqa: E402
from scripts.seed import seed  # noqa: E402

WEB_ROOT = ROOT / "web"
DATA_DIR = ROOT / "data"
PHOTO_DIR = DATA_DIR / "photos"
DB_PATH = DATA_DIR / "fulfillment.db"

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".json": "application/json; charset=utf-8",
    ".ico": "image/x-icon",
}

_lock = threading.Lock()  # 串行化写操作，配合 BEGIN IMMEDIATE 行锁防止重复打包


class App:
    def __init__(self, db_path: Path):
        PHOTO_DIR.mkdir(parents=True, exist_ok=True)
        fresh = not db_path.exists()
        if args_reset and db_path.exists():
            for p in (db_path, Path(str(db_path) + "-wal"), Path(str(db_path) + "-shm")):
                if p.exists():
                    p.unlink()
            fresh = True
        self.conn = connect(db_path)
        init_db(self.conn)
        if fresh or args_reset:
            self.conn.close()
            seed(db_path, WEB_ROOT, force=True)
            self.conn = connect(db_path)
        self._sync_carrier_seq()

    def _sync_carrier_seq(self) -> None:
        row = self.conn.execute("SELECT MAX(tracking_no) m FROM shipments").fetchone()
        if row["m"]:
            m = re.search(r"-(\d+)$", row["m"])
            if m:
                get_carrier().sync(int(m.group(1)))


APP: App | None = None
args_reset = False


# ---------------------------------------------------------------- 请求处理

class Handler(BaseHTTPRequestHandler):
    server_version = "StudioFulfillment/1.0"

    # ---- 工具 ----
    def _json(self, obj, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, code: str, message: str, status: int = 400) -> None:
        self._json({"error": code, "message": message}, status)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            raise svc.DomainError("bad_json", "请求体不是合法 JSON")
        return data if isinstance(data, dict) else {}

    def _static(self, path: Path, root: Path = WEB_ROOT) -> None:
        try:
            path.resolve().relative_to(root.resolve())
        except (OSError, ValueError):
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = path.read_bytes()
        self.send_response(200)
        ctype = MIME.get(path.suffix, "application/octet-stream")
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # 安静一点
        sys.stderr.write("[http] %s\n" % (fmt % args))

    # ---- 路由 ----
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path.startswith("/api/"):
                self._api_get(path)
            elif path.startswith("/assets/"):
                self._static(WEB_ROOT / path.lstrip("/"))
            elif path.startswith("/photos/"):
                self._static(PHOTO_DIR / Path(path).name, root=PHOTO_DIR)
            elif path in ("/", "/index.html"):
                self._static(WEB_ROOT / "index.html")
            elif path in ("/app.js", "/styles.css"):
                self._static(WEB_ROOT / path.lstrip("/"))
            elif path == "/favicon.ico":
                self._static(WEB_ROOT / "assets" / "icons" / "turntable.svg")
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except svc.DomainError as e:
            self._error(e.code, e.message, 404 if "not_found" in e.code else 400)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            with _lock:
                self._api_post(path)
        except svc.DomainError as e:
            status = 404 if "not_found" in e.code else 409 if e.code in (
                "already_packed", "already_shipped", "already_delivered", "kit_exists",
                "already_returned", "wo_exists", "same_address") else 400
            self._error(e.code, e.message, status)

    # ---- GET API ----
    def _api_get(self, path: str) -> None:
        c = APP.conn
        if path == "/api/state":
            work_orders = svc.list_work_orders(c)
            for w in work_orders:  # 列表视图需要三段运单与照片
                w["shipments"] = [dict(r) for r in c.execute(
                    "SELECT * FROM shipments WHERE ref_id=? ORDER BY created_at",
                    (w["id"],)).fetchall()]
                w["photos"] = [dict(r) for r in c.execute(
                    "SELECT * FROM photos WHERE ref_id=? ORDER BY created_at",
                    (w["id"],)).fetchall()]
            self._json({
                "versions": [svc.get_version(c, v["id"]) for v in svc.list_versions(c)],
                "kits": svc.list_kits(c),
                "work_orders": work_orders,
                "claims": svc.list_claims(c),
                "incidents": svc.list_incidents(c),
            })
        elif path == "/api/incidents":
            self._json({"incidents": svc.list_incidents(c)})
        else:
            m = re.fullmatch(r"/api/versions/([\w-]+)", path)
            if m:
                self._json(svc.get_version(c, m.group(1))); return
            m = re.fullmatch(r"/api/kits/([\w-]+)", path)
            if m:
                self._json(svc.get_kit(c, m.group(1))); return
            m = re.fullmatch(r"/api/work-orders/([\w-]+)", path)
            if m:
                d = svc.get_work_order(c, m.group(1))
                d["incidents"] = svc.list_incidents(c, d["id"])
                d["claim"] = self._claim_for(c, d["id"])
                if d["claim"]:  # 索赔凭证照片按 claim 挂在照片表，一并带出
                    d["photos"] += [dict(r) for r in c.execute(
                        "SELECT * FROM photos WHERE ref_type='claim' AND ref_id=? "
                        "ORDER BY created_at", (d["claim"]["id"],)).fetchall()]
                self._json(d); return
            self._error("not_found", "未知接口", 404)

    @staticmethod
    def _claim_for(c, wo_id: str) -> dict | None:
        row = c.execute(
            """SELECT cl.* FROM claims cl JOIN shipments s ON s.id=cl.shipment_id
               WHERE s.ref_id=? ORDER BY cl.created_at DESC LIMIT 1""",
            (wo_id,)).fetchone()
        return dict(row) if row else None

    # ---- POST API ----
    def _api_post(self, path: str) -> None:
        c = APP.conn
        body = self._read_json()

        m = re.fullmatch(r"/api/kits/([\w-]+)/pack-item", path)
        if m:
            svc.pack_item(c, m.group(1), body["sku"], int(body.get("qty", 0)),
                          body.get("substitute_color", ""),
                          bool(body.get("confirmed", False)), body.get("by", ""))
            self._json({"ok": True, "kit": svc.get_kit(c, m.group(1))}); return

        m = re.fullmatch(r"/api/kits/([\w-]+)/confirm", path)
        if m:
            self._json({"ok": True, "kit": svc.confirm_packing(
                c, m.group(1), body.get("packer", ""), body.get("checker", ""))}); return

        m = re.fullmatch(r"/api/kits/([\w-]+)/ship", path)
        if m:
            self._json({"ok": True, "shipment": svc.ship_kit(c, m.group(1))}); return

        m = re.fullmatch(r"/api/kits/([\w-]+)/handover", path)
        if m:
            self._json({"ok": True, "kit": svc.hand_over_onsite(
                c, m.group(1), body.get("by", ""))}); return

        m = re.fullmatch(r"/api/shipments/([\w-]+)/deliver", path)
        if m:
            res = svc.deliver_shipment(
                c, m.group(1), bool(body.get("damaged", False)),
                body.get("note", ""), body.get("damaged_items", []))
            self._json({"ok": True, **res}); return

        m = re.fullmatch(r"/api/work-orders/([\w-]+)/submit", path)
        if m:
            self._json({"ok": True, "shipment": svc.submit_work(c, m.group(1))}); return

        m = re.fullmatch(r"/api/work-orders/([\w-]+)/status", path)
        if m:
            self._json({"ok": True, "work_order": svc.update_work_status(
                c, m.group(1), body["status"], body.get("note", ""))}); return

        m = re.fullmatch(r"/api/work-orders/([\w-]+)/address", path)
        if m:
            self._json({"ok": True, "work_order": svc.change_return_address(
                c, m.group(1), body.get("address", ""), body.get("by", ""))}); return

        m = re.fullmatch(r"/api/work-orders/([\w-]+)/return", path)
        if m:
            self._json({"ok": True, "shipment": svc.return_finished(c, m.group(1))}); return

        m = re.fullmatch(r"/api/claims/([\w-]+)/resolve", path)
        if m:
            self._json({"ok": True, "claim": svc.resolve_claim(
                c, m.group(1), body["status"], body.get("resolution", ""),
                body.get("by", ""))}); return

        if path == "/api/photos":
            self._json({"ok": True, "photo": self._save_photo(body)}); return

        if path == "/api/versions":
            self._json({"ok": True, "version": svc.create_version(
                c, body["id"], body["title"], body.get("description", ""),
                body["items"])}); return

        self._error("not_found", "未知接口", 404)

    def _save_photo(self, body: dict) -> dict:
        data_url = body.get("dataUrl", "")
        mt = re.match(r"data:(image/\w+);base64,(.*)", data_url, re.S)
        if not mt:
            raise svc.DomainError("bad_photo", "仅支持 base64 图片上传")
        ext = {"image/png": ".png", "image/jpeg": ".jpg",
               "image/jpg": ".jpg", "image/svg+xml": ".svg"}.get(mt.group(1), ".bin")
        raw = base64.b64decode(mt.group(2))
        if len(raw) > 8 * 1024 * 1024:
            raise svc.DomainError("photo_too_large", "照片不能超过 8MB")
        last = APP.conn.execute("SELECT COALESCE(MAX(id),0)+1 n FROM photos").fetchone()["n"]
        name = f"photo_{last:04d}{ext}"
        (PHOTO_DIR / name).write_bytes(raw)
        return svc.add_photo(APP.conn, body.get("ref_type", "work_sent"),
                             body.get("ref_id", ""), f"/photos/{name}",
                             body.get("caption", ""))


# ---------------------------------------------------------------- 启动


def main() -> None:
    global APP, args_reset
    ap = argparse.ArgumentParser(description="手作工作室履约系统")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--reset", action="store_true", help="清空数据库并重新播种")
    ap.add_argument("--db", default=str(DB_PATH))
    args = ap.parse_args()
    args_reset = args.reset

    APP = App(Path(args.db))
    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"手作工作室履约系统已启动： http://127.0.0.1:{args.port}")
    print("（Ctrl+C 停止；--reset 可重建数据并重新播种缺件案例）")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")


if __name__ == "__main__":
    main()

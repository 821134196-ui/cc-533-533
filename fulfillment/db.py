"""SQLite 持久层：履约全流程建表与连接管理。

仅依赖标准库。每张业务表都带时间戳；需要留痕的事件另写入 audit_log。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

SCHEMA = """
PRAGMA foreign_keys = ON;

-- 课程与版本 ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS courses (
    course_id   TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS course_versions (
    version_id    TEXT PRIMARY KEY,
    course_id     TEXT NOT NULL REFERENCES courses(course_id),
    version_label TEXT NOT NULL,          -- 如 v1 / v2
    published     INTEGER NOT NULL DEFAULT 1,
    notes         TEXT,
    created_at    TEXT NOT NULL,
    UNIQUE(course_id, version_label)
);

-- 版本 BOM 模板：工具 / 消耗品 / 颜色选择
CREATE TABLE IF NOT EXISTS course_items (
    version_id  TEXT NOT NULL REFERENCES course_versions(version_id),
    item_id     TEXT NOT NULL,
    name        TEXT NOT NULL,
    category    TEXT NOT NULL CHECK(category IN ('tool','consumable','color')),
    color_role  TEXT,                     -- 颜色角色：釉色/织线色…
    unit        TEXT NOT NULL DEFAULT '件',
    qty         INTEGER NOT NULL CHECK(qty > 0),
    thumbnail   TEXT NOT NULL,            -- /static/img 下的 SVG 文件名
    sort_order  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (version_id, item_id)
);

-- 报名（含收货地址快照字段，变更走 address_changes 留痕）---------------------
CREATE TABLE IF NOT EXISTS enrollments (
    enrollment_id TEXT PRIMARY KEY,
    student_name  TEXT NOT NULL,
    phone         TEXT,
    course_id     TEXT NOT NULL REFERENCES courses(course_id),
    version_id    TEXT NOT NULL REFERENCES course_versions(version_id),
    address       TEXT NOT NULL,
    work_due_date TEXT,                   -- 作品应交回日期
    created_at    TEXT NOT NULL
);

-- 材料包：每个报名唯一生成；version_id 即版本锁定锚点 -----------------------
CREATE TABLE IF NOT EXISTS material_kits (
    kit_no            TEXT PRIMARY KEY,
    enrollment_id     TEXT NOT NULL UNIQUE REFERENCES enrollments(enrollment_id),
    version_id        TEXT NOT NULL REFERENCES course_versions(version_id),
    status            TEXT NOT NULL DEFAULT 'created'
                      CHECK(status IN ('created','packing','packed','shipped','delivered')),
    outbound_waybill  TEXT UNIQUE,        -- 材料包去程运单
    shortage_shipped  INTEGER NOT NULL DEFAULT 0, -- 缺件挂账补发标记
    notes             TEXT,
    created_at        TEXT NOT NULL,
    packed_at         TEXT,
    shipped_at        TEXT,
    delivered_at      TEXT
);

-- 材料包清单：建包时从 BOM 快照，课程改版不影响已生成行
CREATE TABLE IF NOT EXISTS kit_lines (
    kit_no         TEXT NOT NULL REFERENCES material_kits(kit_no),
    item_id        TEXT NOT NULL,
    name           TEXT NOT NULL,
    category       TEXT NOT NULL,
    color_role     TEXT,
    unit           TEXT NOT NULL,
    planned_qty    INTEGER NOT NULL,      -- 计划数量（快照）
    packed_qty     INTEGER NOT NULL DEFAULT 0, -- 实际装箱勾选数量
    thumbnail      TEXT NOT NULL,
    sort_order     INTEGER NOT NULL DEFAULT 0,
    is_substitute  INTEGER NOT NULL DEFAULT 0, -- 是否替代颜色
    substitute_for  TEXT,                 -- 被替代的原 item_id
    color_confirmed INTEGER NOT NULL DEFAULT 0, -- 替代色是否经学员确认
    checked        INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (kit_no, item_id)
);

-- 装箱核对：打包员 + 复核员两个角色，各只能确认一次，且不能为同一人
CREATE TABLE IF NOT EXISTS packing_confirmations (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kit_no       TEXT NOT NULL REFERENCES material_kits(kit_no),
    packer       TEXT NOT NULL,
    role         TEXT NOT NULL CHECK(role IN ('packer','checker')),
    confirmed_at TEXT NOT NULL,
    UNIQUE(kit_no, role),
    UNIQUE(kit_no, packer)   -- 同一人不能在同一包上占两个角色
);

-- 异常留痕：缺件 / 替代色未确认 / 配送破损 等
CREATE TABLE IF NOT EXISTS exceptions (
    exception_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    kit_no          TEXT NOT NULL REFERENCES material_kits(kit_no),
    type            TEXT NOT NULL CHECK(type IN
                    ('missing','color_substitute','transit_damage','late_submission')),
    item_id         TEXT,
    title           TEXT NOT NULL,
    description     TEXT,
    status          TEXT NOT NULL DEFAULT 'open'
                    CHECK(status IN ('open','resolved','rejected')),
    resolution      TEXT,                 -- backfill 补发 / accept 学员接受 / cancel
    created_by      TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    resolved_at     TEXT,
    resolved_by     TEXT
);

-- 作品照片（本地存储，元数据入库）
CREATE TABLE IF NOT EXISTS photos (
    photo_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL CHECK(kind IN
                ('kit_outbound','return_inbound','finish_outbound','finish_returned','damage')),
    kit_no      TEXT REFERENCES material_kits(kit_no),
    shipment_id INTEGER REFERENCES shipments(shipment_id),
    file_path   TEXT NOT NULL,
    caption     TEXT,
    uploaded_at TEXT NOT NULL
);

-- 运单：作品回寄 inbound 与成品返寄 outbound 是不同运单
CREATE TABLE IF NOT EXISTS shipments (
    shipment_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    kit_no         TEXT NOT NULL REFERENCES material_kits(kit_no),
    direction      TEXT NOT NULL CHECK(direction IN ('kit_outbound','work_inbound','finish_outbound')),
    waybill        TEXT NOT NULL UNIQUE,
    carrier        TEXT NOT NULL DEFAULT '模拟物流',
    status         TEXT NOT NULL DEFAULT 'created'
                   CHECK(status IN ('created','in_transit','delivered','damaged')),
    from_addr      TEXT,
    to_addr        TEXT,
    late           INTEGER NOT NULL DEFAULT 0,  -- 迟交标记（作品回寄）
    created_at     TEXT NOT NULL,
    shipped_at     TEXT,
    delivered_at   TEXT
);

-- 加工单（烧制 / 装裱）
CREATE TABLE IF NOT EXISTS processing_jobs (
    job_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    kit_no       TEXT NOT NULL UNIQUE REFERENCES material_kits(kit_no),
    inbound_sid  INTEGER NOT NULL REFERENCES shipments(shipment_id),
    outbound_sid INTEGER REFERENCES shipments(shipment_id),
    service_type TEXT NOT NULL CHECK(service_type IN ('firing','framing')),
    status       TEXT NOT NULL DEFAULT 'received'
                 CHECK(status IN ('received','queued','in_progress','qa','done')),
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS job_events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id  INTEGER NOT NULL REFERENCES processing_jobs(job_id),
    stage   TEXT NOT NULL,
    note    TEXT,
    at      TEXT NOT NULL
);

-- 损坏索赔
CREATE TABLE IF NOT EXISTS claims (
    claim_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    shipment_id  INTEGER NOT NULL REFERENCES shipments(shipment_id),
    kit_no       TEXT NOT NULL REFERENCES material_kits(kit_no),
    title        TEXT NOT NULL,
    description  TEXT,
    status       TEXT NOT NULL DEFAULT 'open'
                 CHECK(status IN ('open','approved','rejected')),
    resolution   TEXT,
    amount       INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    resolved_at  TEXT,
    resolved_by  TEXT
);

-- 返寄地址变更
CREATE TABLE IF NOT EXISTS address_changes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    enrollment_id TEXT NOT NULL REFERENCES enrollments(enrollment_id),
    old_address  TEXT NOT NULL,
    new_address  TEXT NOT NULL,
    reason       TEXT,
    requested_by TEXT NOT NULL,
    requested_at TEXT NOT NULL
);

-- 通用审计日志
CREATE TABLE IF NOT EXISTS audit_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    at        TEXT NOT NULL,
    actor     TEXT NOT NULL,
    action    TEXT NOT NULL,
    entity    TEXT NOT NULL,
    entity_id TEXT,
    detail    TEXT
);

CREATE INDEX IF NOT EXISTS idx_kit_lines_kit ON kit_lines(kit_no);
CREATE INDEX IF NOT EXISTS idx_shipments_kit ON shipments(kit_no);
CREATE INDEX IF NOT EXISTS idx_photos_kit ON photos(kit_no);
"""


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def audit(conn: sqlite3.Connection, actor: str, action: str, entity: str,
          entity_id: str | None = None, detail: dict | None = None) -> None:
    conn.execute(
        "INSERT INTO audit_log(at, actor, action, entity, entity_id, detail) "
        "VALUES (?,?,?,?,?,?)",
        (now(), actor, action, entity, entity_id,
         json.dumps(detail, ensure_ascii=False) if detail else None),
    )

"""课程版本 / 报名 / 材料包快照 / 装箱 / 运单 / 加工 / 索赔 / 留痕。"""

SCHEMA = """
PRAGMA foreign_keys = ON;

-- 课程版本（清单为 1..n 明细；改版即新建版本，旧包不受影响）
CREATE TABLE course_versions (
    id              TEXT PRIMARY KEY,          -- 如 kintsugi-v1
    title           TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    published_at    TEXT NOT NULL,
    is_active       INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE course_version_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    version_id      TEXT NOT NULL REFERENCES course_versions(id),
    sku             TEXT NOT NULL,             -- tool/clay/brush...
    name            TEXT NOT NULL,
    category        TEXT NOT NULL,             -- tool 工具 / consumable 消耗品 / color 颜色
    icon            TEXT NOT NULL DEFAULT '',  -- /assets/icons/xxx.svg
    qty             INTEGER NOT NULL CHECK (qty > 0),
    color_code      TEXT NOT NULL DEFAULT '',  -- category=color 时的色号
    UNIQUE(version_id, sku)
);

-- 学员报名（一次报名唯一生成一个材料包）
CREATE TABLE enrollments (
    id              TEXT PRIMARY KEY,          -- E001 ...
    student_name    TEXT NOT NULL,
    channel         TEXT NOT NULL,             -- online 线上 / onsite 现场
    version_id      TEXT NOT NULL REFERENCES course_versions(id),
    address         TEXT NOT NULL DEFAULT '',
    phone           TEXT NOT NULL DEFAULT '',
    enrolled_at     TEXT NOT NULL
);

-- 材料包：清单在生成时从版本快照复制，之后版本改版不影响本包
CREATE TABLE kits (
    id              TEXT PRIMARY KEY,          -- K-E001
    enrollment_id   TEXT NOT NULL UNIQUE REFERENCES enrollments(id),
    version_id      TEXT NOT NULL,               -- 生成时锁定的版本（仅存证）
    version_snapshot TEXT NOT NULL,             -- JSON: 生成时完整清单快照
    status          TEXT NOT NULL DEFAULT 'generated'
                    CHECK (status IN ('generated','packed','shipped','delivered')),
    created_at      TEXT NOT NULL,
    packed_at       TEXT,
    packed_by       TEXT,                        -- 主确认打包员
    checked_by      TEXT,                        -- 复核打包员（必须与主确认不同）
    shipped_at      TEXT,
    delivered_at    TEXT
);

-- 包内明细行（快照副本，可记录替代颜色与逐件核对）
CREATE TABLE kit_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    kit_id          TEXT NOT NULL REFERENCES kits(id),
    sku             TEXT NOT NULL,
    name            TEXT NOT NULL,
    category        TEXT NOT NULL,
    icon            TEXT NOT NULL DEFAULT '',
    qty_required    INTEGER NOT NULL,
    qty_packed      INTEGER NOT NULL DEFAULT 0,
    color_code      TEXT NOT NULL DEFAULT '',
    substitute_color TEXT NOT NULL DEFAULT '',   -- 实际替代色号
    substitute_confirmed INTEGER NOT NULL DEFAULT 0, -- 替代色是否已经学员确认
    item_status     TEXT NOT NULL DEFAULT 'pending'
                    CHECK (item_status IN ('pending','packed','short','substitute_pending','substitute_confirmed')),
    UNIQUE(kit_id, sku)
);

-- 留痕：缺件 / 替代色 / 破损 / 迟交 / 地址变更 等
CREATE TABLE incidents (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    kind            TEXT NOT NULL,             -- shortage/color_substitute/transit_damage/late_submission/address_change
    ref_type        TEXT NOT NULL,             -- kit / inbound / claim
    ref_id          TEXT NOT NULL,
    detail          TEXT NOT NULL,
    created_by      TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL
);

-- 运单（去程寄包 / 作品回寄 / 成品返寄 各自独立运单）
CREATE TABLE shipments (
    id              TEXT PRIMARY KEY,          -- SHP-...
    direction       TEXT NOT NULL CHECK (direction IN ('outbound_kit','inbound_work','outbound_finished')),
    ref_id          TEXT NOT NULL,             -- kit_id 或 work_order_id
    carrier         TEXT NOT NULL DEFAULT '模拟物流',
    tracking_no     TEXT NOT NULL UNIQUE,
    status          TEXT NOT NULL DEFAULT 'created'
                    CHECK (status IN ('created','picked_up','in_transit','delivered','damaged','lost')),
    from_addr       TEXT NOT NULL DEFAULT '',
    to_addr         TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    picked_up_at    TEXT,
    delivered_at    TEXT,
    damaged         INTEGER NOT NULL DEFAULT 0,
    damage_note     TEXT NOT NULL DEFAULT ''
);

-- 作品加工单（线上学员寄回烧制/装裱）
CREATE TABLE work_orders (
    id              TEXT PRIMARY KEY,          -- WO-E001
    enrollment_id   TEXT NOT NULL UNIQUE REFERENCES enrollments(id),
    due_date        TEXT NOT NULL,
    received_at     TEXT,                       -- 工作室签收作品时间
    late            INTEGER NOT NULL DEFAULT 0,
    service         TEXT NOT NULL DEFAULT 'firing', -- firing 烧制 / framing 装裱
    status          TEXT NOT NULL DEFAULT 'awaiting_submission'
                    CHECK (status IN ('awaiting_submission','in_transit','received',
                                      'in_progress','firing','framing','qc','done','returned')),
    return_addr     TEXT NOT NULL DEFAULT '',   -- 返寄地址（可变更，变更留痕）
    finished_at     TEXT
);

-- 照片（本地磁盘 data/photos，数据库只存相对路径）
CREATE TABLE photos (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ref_type        TEXT NOT NULL,             -- kit_sent / work_sent / work_received / finished / claim
    ref_id          TEXT NOT NULL,
    path            TEXT NOT NULL,
    caption         TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL
);

-- 损坏索赔（配送破损 / 作品运输损坏，可关联运单与照片）
CREATE TABLE claims (
    id              TEXT PRIMARY KEY,          -- CLM-...
    shipment_id     TEXT NOT NULL REFERENCES shipments(id),
    scene           TEXT NOT NULL CHECK (scene IN ('kit_delivery','work_transit','finished_transit')),
    description     TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'open'
                    CHECK (status IN ('open','investigating','approved','rejected','resolved')),
    resolution      TEXT NOT NULL DEFAULT '',
    created_at      TEXT,
    resolved_at     TEXT
);
"""

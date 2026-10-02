/* 手作工作室履约台 —— 六视图纯原生前端 */
"use strict";

const STATUS_TEXT = {
  generated: "待装箱", packed: "已封箱", shipped: "去程运输中", delivered: "已交付",
  awaiting_submission: "待学员寄回", in_transit: "回寄运输中", received: "已收件",
  in_progress: "加工中", firing: "烧制中", framing: "装裱中", qc: "质检中",
  done: "加工完成·待返寄", returned: "成品已返寄",
};
const ITEM_STATE_TEXT = {
  pending: "未装箱", packed: "已装箱", short: "缺件",
  substitute_pending: "替代色待确认", substitute_confirmed: "替代色已确认",
};
const DIR_TEXT = {
  outbound_kit: "去程·材料包", inbound_work: "回程·作品回寄", outbound_finished: "去程·成品返寄",
};
const SHIP_STATUS = {
  created: "已建单", picked_up: "已揽收", in_transit: "运输中",
  delivered: "已签收", damaged: "签收异常·损坏", lost: "丢失",
};
const STAGES = [
  ["received", "收件"], ["in_progress", "开工"], ["firing", "烧制"],
  ["framing", "装裱"], ["qc", "质检"], ["done", "完成"],
];

let state = null;
let currentTab = "versions";

const $ = (sel, root = document) => root.querySelector(sel);
const el = (tag, cls, html) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (html !== undefined) n.innerHTML = html;
  return n;
};
const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtTime = (t) => t ? t.replace("T", " ").replace(/\+.*$/, "").slice(0, 16) : "—";

async function api(path, body) {
  const opt = body ? {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  } : {};
  const res = await fetch(path, opt);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.message || `请求失败 ${res.status}`);
  return data;
}

function toast(msg, isErr) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (isErr ? " err" : "");
  t.hidden = false;
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => { t.hidden = true; }, 2600);
}

async function refresh(msg) {
  state = await api("/api/state");
  render();
  if (msg) toast(msg);
}

function badge(status) {
  return `<span class="badge ${esc(status)}">${esc(STATUS_TEXT[status] || status)}</span>`;
}

/* ============================================================== 课程版本 */

function renderVersions(view) {
  view.appendChild(el("h2", "section-title", "课程版本与清单锁定"));
  view.appendChild(el("p", "lead",
    "改版只对新报名生效；每个材料包在生成时复制完整清单快照，之后课程改版不会改变已发材料包。"));

  const grid = el("div", "cards");
  for (const v of state.versions) {
    const usingKits = state.kits.filter((k) => k.version_id === v.id);
    const card = el("div", "card");
    card.innerHTML = `
      <div class="cardhead">
        <div>
          <h3>${esc(v.title)} <span class="mono muted" style="font-size:12px">${esc(v.id)}</span></h3>
          <div class="sub">发布于 ${fmtTime(v.published_at)} · ${v.item_count} 项物料</div>
        </div>
        <span class="badge wood">${usingKits.length} 个材料包在用</span>
      </div>
      <p class="muted" style="font-size:12.5px;margin:8px 0">${esc(v.description)}</p>
      <ul class="itemlist">
        ${v.items.map((it) => `
          <li>
            <img src="${esc(it.icon)}" alt="">
            <div><div class="item-name">${esc(it.name)}</div>
              <div class="item-meta">${({ tool: "工具", consumable: "消耗品", color: "颜色选择" })[it.category]}
                ${it.color_code ? "· 色号 " + esc(it.color_code) : ""}</div></div>
            <div class="item-state"><span class="pill packed">× ${it.qty}</span></div>
          </li>`).join("")}
      </ul>
      <div class="muted" style="font-size:12px;margin-top:8px">
        锁定此版本的材料包：${usingKits.length
          ? usingKits.map((k) => k.id).join("、") : "（暂无）"}
      </div>`;
    grid.appendChild(card);
  }
  view.appendChild(grid);

  view.appendChild(el("h2", "section-title", "改版影响对照"));
  const v1 = state.versions.find((v) => v.id.endsWith("v1"));
  const v2 = state.versions.find((v) => v.id.endsWith("v2"));
  const note = el("div", "card");
  note.innerHTML = `
    <p><b>v1 → v2 变化：</b>陶土包 2 包 → 3 包；新增「釉料·灰蓝 slate」。</p>
    <p class="muted" style="font-size:12.5px">已按 v1 生成的 K-E001～K-E005 清单保持 ${v1.item_count} 项不变；
    v2 之后报名的 K-E006、K-E007 为 ${v2.item_count} 项。可在「材料包清单」中核对各自快照。</p>`;
  view.appendChild(note);
}

/* ============================================================== 材料包清单 */

function itemRowHtml(it) {
  return `
  <li>
    <img src="${esc(it.icon)}" alt="">
    <div>
      <div class="item-name">${esc(it.name)}</div>
      <div class="item-meta">应装 ×${it.qty_required} · 实装 ×${it.qty_packed}
        ${it.substitute_color ? ` · 替代色 ${esc(it.substitute_color)}` : ""}</div>
    </div>
    <div class="item-state">
      <span class="pill ${esc(it.item_status)}">${ITEM_STATE_TEXT[it.item_status] || it.item_status}</span>
    </div>
  </li>`;
}

function renderKits(view) {
  view.appendChild(el("h2", "section-title", "材料包清单（一次报名唯一生成）"));
  view.appendChild(el("p", "lead",
    "每个材料包保留生成时的版本快照；点击卡片查看缩略图清单、数量勾选、运单与留痕。"));
  const grid = el("div", "cards");
  for (const k of state.kits) {
    const issues = [];
    if (k.missing_count) issues.push(`${k.missing_count} 项缺件`);
    if (k.pending_color_count) issues.push(`${k.pending_color_count} 项替代色待确认`);
    const card = el("div", "card");
    card.style.cursor = "pointer";
    card.innerHTML = `
      <div class="cardhead">
        <div><h3>${esc(k.id)} · ${esc(k.student_name)}</h3>
          <div class="sub">${esc(k.course_title)}（${esc(k.version_id)}）
            · ${k.channel === "online" ? "线上·快递收货" : "现场·到课自取"}</div></div>
        ${badge(k.status)}
      </div>
      <div class="muted" style="font-size:12px;margin:8px 0">
        ${k.address ? "📍 " + esc(k.address) : "📍 工作室现场交付"}
        ${k.packed_by ? `<br>✅ 主确认 ${esc(k.packed_by)} / 复核 ${esc(k.checked_by)}` : ""}
      </div>
      ${issues.length ? `<div class="notice block">⚠ ${issues.join("；")}，封箱被拦截</div>` : ""}
      <div class="sub">物料 ${k.items.length} 项，点击查看清单与运单 →</div>`;
    card.onclick = () => openKitModal(k.id);
    grid.appendChild(card);
  }
  view.appendChild(grid);
}

/* ============================================================== 装箱核对 */

function renderPacking(view) {
  view.appendChild(el("h2", "section-title", "装箱核对"));
  view.appendChild(el("p", "lead",
    "逐件勾选实装数量；缺件或未确认的替代颜色会阻止封箱。封箱需两名不同打包员分别确认，系统拒绝重复确认。"));

  const todo = state.kits.filter((k) => k.status === "generated");
  const done = state.kits.filter((k) => k.status !== "generated");

  for (const k of todo) {
    const card = el("div", "card");
    card.innerHTML = `
      <div class="cardhead">
        <div><h3>${esc(k.id)} · ${esc(k.student_name)}</h3>
          <div class="sub">${esc(k.course_title)} · 快照版本 ${esc(k.version_id)}</div></div>
        <span class="badge generated">待装箱</span>
      </div>
      <ul class="itemlist" data-kit="${esc(k.id)}">
        ${k.items.map((it) => packRowHtml(it)).join("")}
      </ul>
      <div class="notice block blockers" hidden></div>
      <div class="row" style="margin-top:12px">
        <div><label class="fld">主确认打包员</label>
          <input type="text" class="packer" value="阿川"></div>
        <div><label class="fld">复核打包员（须不同）</label>
          <input type="text" class="checker" value="晓岚"></div>
        <div style="flex:0 0 auto">
          <button class="btn" data-act="confirm">两人确认封箱</button></div>
      </div>`;

    card.querySelectorAll("[data-act=save]").forEach((btn) => {
      btn.onclick = async () => {
        const row = btn.closest("li");
        const body = {
          sku: row.dataset.sku,
          qty: Number($(".qty", row).value),
          substitute_color: $(".sub", row) ? $(".sub", row).value : "",
          confirmed: $(".conf", row) ? $(".conf", row).checked : false,
          by: "打包员",
        };
        try { await api(`/api/kits/${k.id}/pack-item`, body); await refresh("已登记该物品"); }
        catch (e) { toast(e.message, true); }
      };
    });
    $(".blockers", card).hidden = true;
    refreshBlockers(card, k);
    $("[data-act=confirm]", card).onclick = async () => {
      try {
        await api(`/api/kits/${k.id}/confirm`, {
          packer: $(".packer", card).value.trim(),
          checker: $(".checker", card).value.trim(),
        });
        await refresh("封箱完成，两名打包员均已确认");
      } catch (e) { toast(e.message, true); }
    };
    view.appendChild(card);
  }

  view.appendChild(el("h2", "section-title", "已完成 / 已发运"));
  const wrap = el("div", "cards");
  for (const k of done) {
    const c = el("div", "card");
    c.style.cursor = "pointer";
    c.innerHTML = `
      <div class="cardhead"><div><h3>${esc(k.id)} · ${esc(k.student_name)}</h3>
        <div class="sub">主确认 ${esc(k.packed_by || "—")} / 复核 ${esc(k.checked_by || "—")}</div></div>
        ${badge(k.status)}</div>`;
    c.onclick = () => openKitModal(k.id);
    wrap.appendChild(c);
  }
  view.appendChild(wrap);
}

function packRowHtml(it) {
  const isColor = it.category === "color";
  return `
  <li data-sku="${esc(it.sku)}">
    <img src="${esc(it.icon)}" alt="">
    <div>
      <div class="item-name">${esc(it.name)} <span class="muted" style="font-weight:400;font-size:12px">应装 ×${it.qty_required}</span></div>
      <div class="row" style="margin-top:4px">
        <div style="flex:0 0 90px"><label class="fld">实装数量</label>
          <input type="number" min="0" max="9" class="qty" value="${it.qty_packed}"></div>
        ${isColor ? `
        <div style="flex:1"><label class="fld">替代颜色（留空=原色 ${esc(it.color_code)}）</label>
          <select class="sub">
            <option value="">原色 ${esc(it.color_code)}</option>
            ${["indigo", "rice", "persimmon", "loess", "slate"].map((c) =>
              `<option value="${c}" ${it.substitute_color === c ? "selected" : ""}>${c}</option>`).join("")}
          </select></div>
        <div style="flex:0 0 auto;padding-top:22px"><label>
          <input type="checkbox" class="conf" ${it.substitute_confirmed ? "checked" : ""}>
          学员已确认</label></div>` : `<div></div>`}
        <div style="flex:0 0 auto"><button class="btn small secondary" data-act="save">更新</button></div>
      </div>
    </div>
    <div class="item-state"><span class="pill ${esc(it.item_status)}">${ITEM_STATE_TEXT[it.item_status] || it.item_status}</span></div>
  </li>`;
}

function computeBlockers(k) {
  const out = [];
  for (const it of k.items) {
    if (it.item_status === "short" || it.qty_packed < it.qty_required)
      out.push(`缺件：${it.name} 需 ${it.qty_required}，实装 ${it.qty_packed}`);
    else if (it.item_status === "substitute_pending")
      out.push(`替代颜色未确认：${it.name} → ${it.substitute_color}`);
    else if (it.item_status === "pending") out.push(`尚未装箱：${it.name}`);
  }
  return out;
}

function refreshBlockers(card, k) {
  const blockers = computeBlockers(k);
  const box = $(".blockers", card);
  if (blockers.length) {
    box.hidden = false;
    box.innerHTML = "⛔ " + blockers.map(esc).join("<br>⛔ ");
  }
}

/* ============================================================== 作品回寄 */

function renderInbound(view) {
  view.appendChild(el("h2", "section-title", "作品回寄（独立运单，与材料包运单分开）"));
  view.appendChild(el("p", "lead",
    "线上学员完成作品后寄回烧制或装裱；迟交自动留痕，运输损坏开箱即留痕并生成索赔单。"));

  for (const w of state.work_orders) {
    const card = el("div", "card");
    card.innerHTML = `
      <div class="cardhead">
        <div><h3>${esc(w.id)} · ${esc(w.student_name)}</h3>
          <div class="sub">${w.service === "firing" ? "烧制" : "装裱"} · 约定交件 ${esc(w.due_date)}
            · ${w.channel === "online" ? "线上学员" : "现场学员"}
            ${w.late ? ' <span class="badge danger">迟交已留痕</span>' : ""}</div></div>
        ${badge(w.status)}
      </div>
      <div data-here></div>`;
    const here = $("[data-here]", card);
    here.appendChild(waybillsBlock(w));
    const kit = state.kits.find((k) => k.enrollment_id === w.enrollment_id);
    if (kit) {
      here.appendChild(el("p", "muted",
        `材料包 ${kit.id} 状态：${STATUS_TEXT[kit.status]}`));
    }
    const actions = el("div", "row");
    if (w.status === "awaiting_submission") {
      const b = el("button", "btn", "① 学员寄出作品（生成回寄运单）");
      b.onclick = async () => {
        try { await api(`/api/work-orders/${w.id}/submit`, {}); await refresh("回寄运单已生成"); }
        catch (e) { toast(e.message, true); }
      };
      actions.appendChild(b);
    }
    const inbShip = (w.shipments || []).find((s) => s.direction === "inbound_work");
    if (inbShip && ["picked_up", "in_transit"].includes(inbShip.status)) {
      const ok = el("button", "btn secondary", "② 工作室正常签收");
      ok.onclick = async () => {
        try {
          await api(`/api/shipments/${inbShip.tracking_no}/deliver`, { damaged: false });
          await refresh("作品已签收");
        } catch (e) { toast(e.message, true); }
      };
      const bad = el("button", "btn danger", "签收异常：开箱损坏");
      bad.onclick = () => openDamageModal(inbShip.tracking_no);
      actions.append(ok, bad);
    }
    if (actions.children.length) here.appendChild(actions);

    const detail = el("button", "link", "查看照片 / 留痕 →");
    detail.style.marginTop = "8px";
    detail.onclick = () => openWorkModal(w.id);
    here.appendChild(detail);
    view.appendChild(card);
  }

  view.appendChild(el("h2", "section-title", "损坏索赔"));
  view.appendChild(claimsTable());
  view.appendChild(el("h2", "section-title", "最近留痕"));
  view.appendChild(incidentsList(state.incidents.slice(0, 12)));
}

function waybillsBlock(w) {
  const box = el("div");
  if (!w.shipments || !w.shipments.length) {
    box.appendChild(el("div", "muted", "暂无运单"));
    return box;
  }
  for (const s of w.shipments) {
    const d = el("div", "waybill");
    d.innerHTML = `<span class="dir">${DIR_TEXT[s.direction]}</span>
      <span class="no">${esc(s.tracking_no)}</span>
      <span class="badge ${s.status === "damaged" ? "damaged" : s.status === "delivered" ? "delivered" : "shipped"}">
        ${SHIP_STATUS[s.status] || s.status}</span>
      <span class="muted" style="margin-left:auto">${esc(s.carrier)}</span>`;
    box.appendChild(d);
  }
  return box;
}

function claimsTable() {
  const wrap = el("div", "card");
  if (!state.claims.length) { wrap.textContent = "暂无索赔。"; return wrap; }
  wrap.innerHTML = `<table><thead><tr>
    <th>索赔单</th><th>场景</th><th>运单号</th><th>描述</th><th>状态</th><th>处理</th>
    </tr></thead><tbody>
    ${state.claims.map((c) => `
      <tr>
        <td class="mono">${esc(c.id)}</td>
        <td>${({ kit_delivery: "材料包配送破损", work_transit: "作品运输损坏",
                 finished_transit: "成品返寄损坏" })[c.scene]}</td>
        <td class="mono">${esc(c.tracking_no)}</td>
        <td>${esc(c.description)}</td>
        <td><span class="badge ${c.status === "open" ? "danger" : "delivered"}">${esc(c.status)}</span>
            ${c.resolution ? `<div class="muted" style="font-size:11.5px">${esc(c.resolution)}</div>` : ""}</td>
        <td>${c.status === "open" || c.status === "investigating"
          ? `<button class="btn small" data-claim="${esc(c.id)}">处理</button>` : "—"}</td>
      </tr>`).join("")}
    </tbody></table>`;
  wrap.querySelectorAll("[data-claim]").forEach((btn) => {
    btn.onclick = () => openClaimModal(btn.dataset.claim);
  });
  return wrap;
}

function incidentsList(list) {
  const ul = el("ul", "trail");
  for (const i of list) {
    ul.innerHTML += `<li class="${esc(i.kind)}">
      <span class="t-time">${fmtTime(i.created_at)}</span>
      <span class="kindtag">${esc(i.kind)}</span>${esc(i.detail)}
      <span class="muted">@${esc(i.ref_type)} ${esc(i.ref_id)}</span></li>`;
  }
  return ul;
}

/* ============================================================== 加工进度 */

function renderProcessing(view) {
  view.appendChild(el("h2", "section-title", "加工进度：去程 · 加工 · 返程"));
  view.appendChild(el("p", "lead", "三段链路互相独立：材料包去程运单、作品回寄运单、成品返寄运单各不相同。"));

  for (const w of state.work_orders) {
    const card = el("div", "card");
    const kit = state.kits.find((k) => k.enrollment_id === w.enrollment_id);
    const inb = (w.shipments || []).find((s) => s.direction === "inbound_work");
    const out = (w.shipments || []).find((s) => s.direction === "outbound_finished");
    card.innerHTML = `<div class="cardhead">
        <div><h3>${esc(w.id)} · ${esc(w.student_name)} ${w.late ? '<span class="badge danger">迟交</span>' : ""}</h3>
        <div class="sub">${w.service === "firing" ? "烧制" : "装裱"} · 交件 ${esc(w.due_date)}</div></div>
        ${badge(w.status)}</div>`;

    // 三段进度
    const leg1State = !kit ? "" : kit.status === "delivered" ? "done"
      : kit.status === "shipped" ? "active" : "";
    const damageInb = inb && inb.status === "damaged";
    const prog = el("div", "journey");
    prog.innerHTML = `
      <div class="leg ${leg1State}"><span class="dot"></span>
        <div class="leg-label">去程 · 材料包</div>
        <div class="leg-sub">${kit ? esc(kit.id) + " " + STATUS_TEXT[kit.status] : "—"}</div></div>
      <div class="leg ${damageInb ? "danger"
        : ["received", "in_progress", "firing", "framing", "qc", "done", "returned"].includes(w.status)
          ? "done" : w.status === "in_transit" ? "active" : ""}">
        <span class="dot"></span>
        <div class="leg-label">回寄 + 加工</div>
        <div class="leg-sub">${damageInb ? "回寄损坏" : STATUS_TEXT[w.status]}</div></div>
      <div class="leg ${w.status === "returned" ? "done" : out ? "active" : ""}">
        <span class="dot"></span>
        <div class="leg-label">返程 · 成品</div>
        <div class="leg-sub">${w.status === "returned" ? "学员已签收" : out ? "返寄运输中" : "待返寄"}</div></div>`;
    card.appendChild(prog);
    card.appendChild(waybillsBlock(w));

    // 加工阶段按钮
    if (["received", "in_progress", "firing", "framing", "qc"].includes(w.status)) {
      const row = el("div", "row");
      for (const [st, label] of STAGES) {
        if (st === "received" || (st === "firing" && w.service === "framing")
            || (st === "framing" && w.service !== "framing")) continue;
        const b = el("button", "btn small ghost", label);
        b.onclick = async () => {
          try { await api(`/api/work-orders/${w.id}/status`, { status: st }); await refresh(`已更新：${label}`); }
          catch (e) { toast(e.message, true); }
        };
        row.appendChild(b);
      }
      card.appendChild(row);
    }

    const cmp = el("div"); cmp.style.marginTop = "10px";
    cmp.appendChild(photoCompare(w, "work_sent", "work_received", "寄出时", "收回时（开箱核对）"));
    const link = el("button", "link", "查看完整照片与留痕 →");
    link.style.marginTop = "8px";
    link.onclick = () => openWorkModal(w.id);
    cmp.appendChild(link);
    card.appendChild(cmp);
    view.appendChild(card);
  }
}

function photoBlock(photo, title, damage) {
  if (!photo) {
    return `<div class="photo ${damage ? "damage" : ""}">
      <div style="height:120px;display:flex;align-items:center;justify-content:center"
        class="muted">${title}：暂无照片</div>
      <div class="cap">${title}</div></div>`;
  }
  return `<div class="photo ${damage ? "damage" : ""}">
    <img src="${esc(photo.path)}" alt="${esc(title)}" onclick="zoomPhoto('${esc(photo.path)}')">
    <div class="cap"><b>${title}</b><br>${esc(photo.caption || "")}
      <span class="muted">${fmtTime(photo.created_at)}</span></div></div>`;
}

function photoCompare(w, typeA, typeB, labelA, labelB) {
  const photos = w.photos || [];
  const a = photos.filter((p) => p.ref_type === typeA).slice(-1)[0];
  const b = photos.filter((p) => p.ref_type === typeB).slice(-1)[0];
  const inb = (w.shipments || []).find((s) => s.direction === "inbound_work");
  const damage = inb && inb.status === "damaged";
  const div = el("div");
  div.innerHTML = `<div class="compare">
      ${photoBlock(a, labelA, false)}
      <div class="vs">VS</div>
      ${photoBlock(b, labelB, !!damage)}
    </div>`;
  return div;
}

/* ============================================================== 成品返寄 */

function renderReturns(view) {
  view.appendChild(el("h2", "section-title", "成品返寄"));
  view.appendChild(el("p", "lead",
    "成品返寄使用与作品回寄不同的新运单；返寄地址变更全程留痕，已发出后不可直接改址。"));

  for (const w of state.work_orders) {
    const out = (w.shipments || []).find((s) => s.direction === "outbound_finished");
    const card = el("div", "card");
    card.innerHTML = `
      <div class="cardhead">
        <div><h3>${esc(w.id)} · ${esc(w.student_name)}</h3>
          <div class="sub">返寄地址：${esc(w.return_addr || "（未登记）")}</div></div>
        ${badge(w.status)}</div>`;

    if (w.status === "done" && !out) {
      const row = el("div", "row");
      const addr = el("input", null); addr.type = "text"; addr.value = w.return_addr;
      const addrWrap = el("div");
      addrWrap.innerHTML = `<label class="fld">变更返寄地址（不改可直接发出）</label>`;
      addrWrap.appendChild(addr);
      const changeBtn = el("button", "btn ghost", "登记地址变更");
      changeBtn.onclick = async () => {
        try { await api(`/api/work-orders/${w.id}/address`, { address: addr.value, by: "客服" }); await refresh("地址变更已留痕"); }
        catch (e) { toast(e.message, true); }
      };
      const sendBtn = el("button", "btn", "成品返寄发出（新运单）");
      sendBtn.onclick = async () => {
        try { await api(`/api/work-orders/${w.id}/return`, {}); await refresh("成品已交运"); }
        catch (e) { toast(e.message, true); }
      };
      row.append(addrWrap, wrapBtn(changeBtn), wrapBtn(sendBtn));
      card.appendChild(row);
    }
    if (out && ["picked_up", "in_transit"].includes(out.status)) {
      const row = el("div", "row");
      const ok = el("button", "btn secondary", "学员正常签收");
      ok.onclick = async () => {
        try { await api(`/api/shipments/${out.tracking_no}/deliver`, { damaged: false }); await refresh("返寄完成"); }
        catch (e) { toast(e.message, true); }
      };
      const bad = el("button", "btn danger", "签收异常：成品损坏");
      bad.onclick = () => openDamageModal(out.tracking_no);
      row.append(wrapBtn(ok), wrapBtn(bad));
      card.appendChild(row);
    }
    card.appendChild(waybillsBlock(w));
    const cmp = el("div");
    cmp.appendChild(photoCompare(w, "finished_sent", "finished_received", "寄出装箱", "学员收回"));
    card.appendChild(cmp);
    view.appendChild(card);
  }
}

function wrapBtn(b) { const d = el("div"); d.style.flex = "0 0 auto"; d.style.paddingTop = "22px"; d.appendChild(b); return d; }

/* ============================================================== 弹窗 */

function modal(title, bodyNode) {
  $("#modalTitle").textContent = title;
  const b = $("#modalBody");
  b.innerHTML = "";
  b.appendChild(bodyNode);
  $("#modal").hidden = false;
}
$("#modalClose").onclick = () => { $("#modal").hidden = true; };
$("#modal").onclick = (e) => { if (e.target.id === "modal") $("#modal").hidden = true; };

async function openKitModal(kitId) {
  const k = await api(`/api/kits/${kitId}`);
  const body = el("div");
  const head = el("dl", "kv");
  head.innerHTML = `
    <dt>学员</dt><dd>${esc(k.student_name)}（${k.channel === "online" ? "线上" : "现场"}）</dd>
    <dt>课程版本</dt><dd class="mono">${esc(k.version_id)} · ${esc(k.course_title)}</dd>
    <dt>快照物料数</dt><dd>${k.snapshot.length} 项（生成时锁定，不随后续改版变化）</dd>
    <dt>收货方式</dt><dd>${k.channel === "online" ? esc(k.address || "地址待补") : "到课自取（无运单）"}</dd>
    <dt>打包员</dt><dd>${esc(k.packed_by || "—")} / 复核 ${esc(k.checked_by || "—")}</dd>`;
  body.appendChild(head);

  body.appendChild(el("h4", "section-title", "物品缩略图与数量勾选"));
  const ul = el("ul", "itemlist");
  ul.innerHTML = k.items.map(itemRowHtml).join("");
  body.appendChild(ul);

  body.appendChild(el("h4", "section-title", "运单"));
  if (!k.shipments.length) body.appendChild(el("p", "muted", k.channel === "onsite" ? "现场自取，无运单。" : "尚未发运。"));
  else body.appendChild(waybillsBlock(k));

  if (k.status === "packed") {
    const row = el("div", "row");
    if (k.channel === "online") {
      const b = el("button", "btn", "发运材料包（去程运单）");
      b.onclick = async () => {
        try { await api(`/api/kits/${k.id}/ship`, {}); $("#modal").hidden = true; await refresh("材料包已发运"); }
        catch (e) { toast(e.message, true); }
      };
      row.appendChild(wrapBtn(b));
    } else {
      const b = el("button", "btn secondary", "现场学员到课自取交付");
      b.onclick = async () => {
        try { await api(`/api/kits/${k.id}/handover`, { by: "前台" }); $("#modal").hidden = true; await refresh("已自取交付"); }
        catch (e) { toast(e.message, true); }
      };
      row.appendChild(wrapBtn(b));
    }
    body.appendChild(row);
  }
  const outShip = (k.shipments || []).find((s) => s.direction === "outbound_kit");
  if (outShip && ["picked_up", "in_transit"].includes(outShip.status)) {
    const row = el("div", "row");
    const ok = el("button", "btn secondary", "正常签收");
    ok.onclick = async () => {
      try { await api(`/api/shipments/${outShip.tracking_no}/deliver`, { damaged: false }); $("#modal").hidden = true; await refresh("材料包已签收"); }
      catch (e) { toast(e.message, true); }
    };
    const bad = el("button", "btn danger", "配送破损签收");
    bad.onclick = () => openDamageModal(outShip.tracking_no);
    row.append(wrapBtn(ok), wrapBtn(bad));
    body.appendChild(row);
  }

  const incs = (await api("/api/incidents")).incidents.filter((i) => i.ref_id === k.id);
  if (incs.length) {
    body.appendChild(el("h4", "section-title", "留痕"));
    body.appendChild(incidentsList(incs));
  }
  modal(`${k.id} · ${k.student_name}`, body);
}

async function openWorkModal(woId) {
  const w = await api(`/api/work-orders/${woId}`);
  const body = el("div");
  body.appendChild(waybillsBlock(w));

  // 照片上传
  body.appendChild(el("h4", "section-title", "上传作品照片（本地保存）"));
  const up = el("div", "row");
  const typeSel = el("select");
  for (const [v, t] of [["work_sent", "寄出时"], ["work_received", "收回开箱"],
                        ["finished_sent", "成品装箱"], ["finished_received", "学员收回"]]) {
    typeSel.innerHTML += `<option value="${v}">${t}</option>`;
  }
  const file = el("input"); file.type = "file"; file.accept = "image/*";
  const cap = el("input"); cap.type = "text"; cap.placeholder = "照片说明（如包装方式/磕碰位置）";
  const upBtn = el("button", "btn small secondary", "上传");
  upBtn.onclick = async () => {
    if (!file.files[0]) return toast("请先选择照片", true);
    try {
      const dataUrl = await fileToDataUrl(file.files[0]);
      await api("/api/photos", { ref_type: typeSel.value, ref_id: w.id, dataUrl, caption: cap.value });
      $("#modal").hidden = true;
      await refresh("照片已保存到本地");
    } catch (e) { toast(e.message, true); }
  };
  up.append(wrapField("照片阶段", typeSel), wrapField("选择文件", file),
            wrapField("说明", cap), wrapBtn(upBtn));
  body.appendChild(up);

  body.appendChild(el("h4", "section-title", "寄出 vs 收回 对比"));
  body.appendChild(photoCompare(w, "work_sent", "work_received", "作品寄出", "工作室收回"));
  body.appendChild(photoCompare(w, "finished_sent", "finished_received", "成品寄出", "学员收回"));
  const claimPhotos = (w.photos || []).filter((p) => p.ref_type === "claim");
  if (claimPhotos.length || w.claim) {
    body.appendChild(el("h4", "section-title", "损坏与索赔"));
    if (w.claim) body.appendChild(el("div", "notice block",
      `${w.claim.id}：${w.claim.description}（${w.claim.status}）${w.claim.resolution ? " — " + w.claim.resolution : ""}`));
    const box = el("div", "photos");
    box.innerHTML = claimPhotos.map((p) => photoBlock(p, "索赔凭证", true)).join("");
    body.appendChild(box);
  }

  body.appendChild(el("h4", "section-title", "留痕"));
  body.appendChild(incidentsList(w.incidents || []));
  modal(`${w.id} · ${w.student_name}`, body);
}

function wrapField(label, node) {
  const d = el("div");
  d.innerHTML = `<label class="fld">${label}</label>`;
  d.appendChild(node);
  return d;
}

function fileToDataUrl(file) {
  return new Promise((resolve, reject) => {
    const fr = new FileReader();
    fr.onload = () => resolve(fr.result);
    fr.onerror = reject;
    fr.readAsDataURL(file);
  });
}

function openDamageModal(trackingNo) {
  const body = el("div");
  body.innerHTML = `<p class="muted">运单 <span class="mono">${esc(trackingNo)}</span> 签收时记录损坏，系统将自动留痕并生成索赔单。</p>`;
  const ta = el("textarea"); ta.rows = 3; ta.placeholder = "损坏情况，如：外箱一角塌陷、瓶身碎裂…";
  const items = el("input"); items.type = "text"; items.placeholder = "受损物品（顿号分隔）";
  body.appendChild(wrapField("损坏描述", ta));
  body.appendChild(wrapField("受损物品", items));
  const b = el("button", "btn danger", "确认损坏并开索赔单");
  b.style.marginTop = "12px";
  b.onclick = async () => {
    try {
      await api(`/api/shipments/${trackingNo}/deliver`, {
        damaged: true, note: ta.value,
        damaged_items: items.value.split(/[、,，]/).map((s) => s.trim()).filter(Boolean),
      });
      $("#modal").hidden = true;
      await refresh("已登记运输损坏并生成索赔单");
    } catch (e) { toast(e.message, true); }
  };
  body.appendChild(b);
  modal("签收异常登记", body);
}

function openClaimModal(claimId) {
  const c = state.claims.find((x) => x.id === claimId);
  const body = el("div");
  body.innerHTML = `<div class="notice block">${esc(c.description)}</div>`;
  const sel = el("select");
  sel.innerHTML = `<option value="investigating">调查中</option>
    <option value="approved">批准赔付/补发</option>
    <option value="rejected">驳回</option>
    <option value="resolved">协商解决并结案</option>`;
  const ta = el("textarea"); ta.rows = 3; ta.placeholder = "处理方案与备注";
  body.appendChild(wrapField("处理结果", sel));
  body.appendChild(wrapField("处理说明", ta));
  const b = el("button", "btn", "提交处理");
  b.style.marginTop = "12px";
  b.onclick = async () => {
    try {
      await api(`/api/claims/${c.id}/resolve`, { status: sel.value, resolution: ta.value, by: "客服" });
      $("#modal").hidden = true;
      await refresh("索赔已处理");
    } catch (e) { toast(e.message, true); }
  };
  body.appendChild(b);
  modal(`索赔 ${c.id}`, body);
}

window.zoomPhoto = (src) => {
  const body = el("div");
  body.innerHTML = `<img src="${esc(src)}" style="width:100%;border-radius:8px">`;
  modal("照片放大", body);
};

/* ============================================================== 启动 */

const RENDERERS = {
  versions: renderVersions,
  kits: renderKits,
  packing: renderPacking,
  inbound: renderInbound,
  processing: renderProcessing,
  returns: renderReturns,
};

function render() {
  $("#tabs").querySelectorAll(".tab").forEach((t) =>
    t.classList.toggle("active", t.dataset.tab === currentTab));
  const view = $("#view");
  view.innerHTML = "";
  RENDERERS[currentTab](view);
}

$("#tabs").addEventListener("click", (e) => {
  const t = e.target.closest(".tab");
  if (!t) return;
  currentTab = t.dataset.tab;
  render();
});

(async function init() {
  try {
    await refresh();
  } catch (e) {
    $("#view").innerHTML = `<div class="notice block">加载失败：${esc(e.message)}</div>`;
  }
})();

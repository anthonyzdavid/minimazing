"use strict";

const state = {
  summary: null,
  datasets: [],
  counts: {},       // key -> number
  currentKey: "overview",
  cache: {},        // key -> {records, total}
  activeChat: null, // chat_id for messages/whatsapp thread view
  viewToken: 0,     // bumps on every navigation so stale async renders bail out
};

function esc(s) {
  if (s === null || s === undefined) return "";
  return String(s).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

function fmtBytes(n) {
  if (!n) return "0 B";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i === 0 ? 0 : 1)} ${u[i]}`;
}

function fmtDate(iso, { time = true } = {}) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  const sameYear = d.getFullYear() === new Date().getFullYear();
  return d.toLocaleString(undefined, {
    month: "short", day: "numeric",
    year: sameYear ? undefined : "numeric",
    ...(time ? { hour: "numeric", minute: "2-digit" } : {}),
  });
}

function fmtDuration(s) {
  s = Math.round(Number(s) || 0);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  const mm = h ? String(m).padStart(2, "0") : String(m);
  return `${h ? h + ":" : ""}${mm}:${String(sec).padStart(2, "0")}`;
}

function fmtNum(n) { return Number(n || 0).toLocaleString(); }
function plural(n, word) { return `${fmtNum(n)} ${word}${n === 1 ? "" : "s"}`; }

function debounce(fn, ms = 200) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    const body = await res.json().catch(() => ({ error: res.statusText }));
    const err = new Error(body.error || `Request failed: ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return res.json();
}

function post(path, body) {
  return api(path, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
}

const $ = (id) => document.getElementById(id);
const content = () => $("content");

function startView() { return ++state.viewToken; }
function stale(token) { return token !== state.viewToken; }

const IMG_EXT = /\.(jpe?g|png|gif|webp|heic|heif|tiff?)$/i;
const VID_EXT = /\.(mov|mp4|m4v)$/i;
const AUD_EXT = /\.(m4a|m4r|caf|mp3|amr|aac|wav|opus)$/i;

// ---------------------------------------------------------------- gate: pick / unlock

function showGate(html) {
  $("app").hidden = true;
  $("gate").hidden = false;
  $("gate-card").innerHTML = html;
}

async function renderPicker(message = "") {
  showGate(`<div class="gate-logo">${icon("device")}</div>
    <h2>Choose a backup</h2>
    <div id="picker-list" class="muted">Looking for backups…</div>
    <form id="picker-form" class="gate-form">
      <input id="picker-path" placeholder="…or paste a backup folder path (the one with Manifest.plist)">
      <button class="btn primary">Open</button>
    </form>
    <div id="gate-error" class="gate-error">${esc(message)}</div>`);
  $("picker-form").onsubmit = (e) => { e.preventDefault(); openBackup($("picker-path").value.trim()); };
  let backups = [];
  try { backups = await api("/api/backups"); } catch (e) { /* listing is best-effort */ }
  const list = $("picker-list");
  if (!list) return;
  if (!backups.length) {
    list.innerHTML = "No backups found in the Finder / iTunes / iMazing folders.";
    return;
  }
  list.classList.remove("muted");
  list.innerHTML = backups.map((b, i) => `
    <div class="picker-row" data-i="${i}">
      ${icon(b.encrypted ? "lock" : "device")}
      <div><div class="name">${esc(b.device_name || b.udid || b.path)}</div>
      <div class="muted">${esc([b.product_type, b.ios_version && "iOS " + b.ios_version,
        b.last_backup && fmtDate(b.last_backup)].filter(Boolean).join(" · "))}</div>
      <div class="muted path">${esc(b.path)}</div></div>
    </div>`).join("");
  list.querySelectorAll(".picker-row").forEach((row) =>
    row.addEventListener("click", () => openBackup(backups[Number(row.dataset.i)].path)));
}

async function openBackup(path) {
  if (!path) return;
  try {
    const r = await post("/api/open", { path });
    route(r.stage);
  } catch (e) {
    renderPicker(e.message);
  }
}

function renderUnlock(summary, message = "") {
  showGate(`<div class="gate-logo">${icon("lock")}</div>
    <h2>${esc(summary.device_name || "Encrypted backup")}</h2>
    <div class="muted">${esc([summary.product_type, summary.ios_version && "iOS " + summary.ios_version,
      summary.last_backup && "backed up " + fmtDate(summary.last_backup)].filter(Boolean).join(" · "))}</div>
    <p>This backup is encrypted. Enter the backup password set in Finder / iTunes / iMazing.</p>
    <form id="unlock-form" class="gate-form">
      <input id="unlock-pw" type="password" placeholder="Backup password" autocomplete="current-password" autofocus>
      <button class="btn primary" id="unlock-btn">Unlock</button>
    </form>
    <div id="gate-error" class="gate-error">${esc(message)}</div>
    <button class="link-btn" id="gate-back">Choose a different backup</button>`);
  $("gate-back").onclick = async () => { await post("/api/close"); renderPicker(); };
  $("unlock-form").onsubmit = async (e) => {
    e.preventDefault();
    const btn = $("unlock-btn");
    btn.disabled = true;
    btn.textContent = "Unlocking…";  // key derivation takes a few seconds by design
    try {
      const r = await post("/api/unlock", { password: $("unlock-pw").value });
      route(r.stage);
    } catch (err) {
      renderUnlock(summary, err.message);
    }
  };
  setTimeout(() => $("unlock-pw") && $("unlock-pw").focus(), 30);
}

async function route(stage) {
  const st = await api("/api/state");
  stage = st.stage;
  if (stage === "pick") return renderPicker();
  if (stage === "locked") return renderUnlock(st.summary);
  $("gate").hidden = true;
  $("app").hidden = false;
  await bootApp();
}

// ---------------------------------------------------------------- sidebar

const STORAGE_COLORS = {
  Photos: "#4e9bf0", Messages: "#34b36b", WhatsApp: "#2bb67d",
  "App Data": "#f0a93e", Other: "#b5b5bc",
};

function renderDeviceCard() {
  const s = state.summary;
  $("device-icon").innerHTML = icon("device");
  $("device-name").textContent = s.device_name || "Unknown device";
  const bits = [s.product_type, s.ios_version ? `iOS ${s.ios_version}` : null,
    s.encrypted ? "Encrypted" : "Unencrypted"].filter(Boolean);
  $("device-sub").textContent = bits.join(" · ");
}

function renderStorage(storage) {
  const bar = $("storage-bar");
  const legend = $("storage-legend");
  bar.innerHTML = "";
  legend.innerHTML = "";
  if (!storage.length) {
    bar.style.display = "none";
    legend.style.display = "none";
    return;
  }
  bar.style.display = "flex";
  legend.style.display = "flex";
  let totalBytes = 0;
  for (const seg of storage) {
    const div = document.createElement("div");
    div.style.width = seg.pct + "%";
    div.style.background = STORAGE_COLORS[seg.label] || "#b5b5bc";
    bar.appendChild(div);
    totalBytes += seg.bytes;
  }
  bar.title = `${fmtBytes(totalBytes)} across ${storage.length} categories`;
  for (const seg of storage) {
    const span = document.createElement("span");
    span.innerHTML = `<i style="background:${STORAGE_COLORS[seg.label] || "#b5b5bc"}"></i>` +
      `${esc(seg.label)} ${fmtBytes(seg.bytes)}`;
    legend.appendChild(span);
  }
}

function renderSidebar() {
  const nav = $("nav");
  nav.innerHTML = "";

  const overviewRow = document.createElement("div");
  overviewRow.className = "nav-item" + (state.currentKey === "overview" ? " active" : "");
  overviewRow.innerHTML = `${icon("device")}<span class="label">Overview</span>`;
  overviewRow.addEventListener("click", () => navigate("overview"));
  nav.appendChild(overviewRow);

  const sectionLabel = document.createElement("div");
  sectionLabel.className = "nav-section-label";
  sectionLabel.textContent = "Backup";
  nav.appendChild(sectionLabel);

  for (const d of state.datasets) {
    const row = document.createElement("div");
    row.className = "nav-item" + (!d.implemented || !d.available ? " disabled" : "");
    row.dataset.key = d.key;
    if (d.key === state.currentKey) row.classList.add("active");
    let right = "";
    if (!d.implemented) {
      right = '<span class="soon">not in backups</span>';
    } else if (d.available) {
      const c = state.counts[d.key];
      right = `<span class="count">${c === undefined ? "…" : fmtNum(c)}</span>`;
    } else {
      right = '<span class="count">—</span>';
    }
    row.innerHTML = `${icon(d.icon)}<span class="label">${esc(d.label)}</span>${right}`;
    if (d.implemented && d.available) {
      row.addEventListener("click", () => navigate(d.key));
    } else {
      row.title = d.reason || "Not present in this backup.";
    }
    nav.appendChild(row);
  }
}

function setCount(key, n) {
  state.counts[key] = n;
  const el = document.querySelector(`.nav-item[data-key="${key}"] .count`);
  if (el) el.textContent = fmtNum(n);
  const card = document.querySelector(`.ds-card[data-key="${key}"] .n`);
  if (card) card.textContent = fmtNum(n);
}

async function loadCounts() {
  // the slow ones (they decrypt big databases) go last so the rest fill in fast
  const order = state.datasets.filter((d) => d.implemented && d.available)
    .sort((a, b) => ["apps", "photos", "messages"].indexOf(a.key) - ["apps", "photos", "messages"].indexOf(b.key));
  for (const d of order) {
    api(`/api/count/${d.key}`).then((r) => setCount(d.key, r.count)).catch(() => setCount(d.key, 0));
  }
}

// ---------------------------------------------------------------- top bar

function setTopbar(title, { search = false, placeholder = "Search", exports = null, tools = "" } = {}) {
  $("view-title").textContent = title;
  const searchEl = $("search");
  searchEl.hidden = !search;
  searchEl.value = "";
  searchEl.placeholder = placeholder;
  searchEl.oninput = null;
  $("view-tools").innerHTML = tools;
  const group = $("export-group");
  group.hidden = !exports;
  group.innerHTML = "";
  for (const [label, url] of exports || []) {
    const btn = document.createElement("button");
    btn.className = "btn";
    btn.innerHTML = `${icon("download")} ${esc(label)}`;
    btn.onclick = () => { window.location.href = typeof url === "function" ? url() : url; };
    group.appendChild(btn);
  }
}

function exportsFor(key, extra = []) {
  return [["CSV", `/api/export/${key}.csv`], ["JSON", `/api/export/${key}.json`], ...extra];
}

// ---------------------------------------------------------------- navigation

function navigate(key) {
  state.currentKey = key;
  state.activeChat = null;
  if (location.hash !== "#" + key) history.replaceState(null, "", "#" + key);
  document.querySelectorAll(".nav-item").forEach((r) =>
    r.classList.toggle("active", (r.dataset.key || "overview") === key));
  const token = startView();
  if (key === "overview") return renderOverview();
  const d = state.datasets.find((x) => x.key === key);
  if (!d || !d.available) return renderOverview();
  content().innerHTML = `<div class="loading">Loading ${esc(d.label)}…</div>`;
  const views = {
    threads: renderThreadView, photos: renderPhotoGrid, notes: renderNotes,
    audio: renderAudio, apps: renderApps, files: renderFiles, table: renderTableView,
  };
  (views[d.view] || renderTableView)(d, token).catch((e) => {
    if (!stale(token)) content().innerHTML = errorState(e.message);
  });
}

// ---------------------------------------------------------------- overview

function renderOverview() {
  setTopbar("Overview");
  const s = state.summary;
  const rows = [
    ["Device", s.device_name || "—"],
    ["Model", s.product_type || "—"],
    ["iOS version", s.ios_version || "—"],
    ["Last backup", fmtDate(s.last_backup) || "—"],
    ["Backup type", s.full_backup ? "Full backup" : "Partial / data-access"],
    ["Encryption", s.encrypted ? "Encrypted backup" : "Not encrypted"],
    ["Snapshot state", s.snapshot_state || "—"],
    ["UDID", s.udid || "—"],
  ];
  const cells = rows.map(([k, v]) => `
    <div class="overview-cell"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div></div>
  `).join("");
  const cards = state.datasets.filter((d) => d.implemented && d.available).map((d) => `
    <div class="ds-card" data-key="${d.key}">${icon(d.icon)}
      <div class="n">${state.counts[d.key] === undefined ? "…" : fmtNum(state.counts[d.key])}</div>
      <div class="l">${esc(d.label)}</div></div>`).join("");
  content().innerHTML = `
    <div class="overview">
      <div style="margin-bottom:16px">
        <span class="badge">${icon(s.encrypted ? "lock" : "device")} ${
          s.encrypted ? "Encrypted backup, unlocked" : "Unencrypted backup"}</span>
      </div>
      <div class="overview-grid">${cells}</div>
      <div class="overview-path muted">${esc(s.path || "")}</div>
      <h3>Contents</h3>
      <div class="ds-grid">${cards}</div>
    </div>`;
  content().querySelectorAll(".ds-card").forEach((c) =>
    c.addEventListener("click", () => navigate(c.dataset.key)));
}

// ---------------------------------------------------------------- loading records

async function loadDataset(key) {
  if (!state.cache[key]) {
    state.cache[key] = await api(`/api/dataset/${key}`);
    setCount(key, state.cache[key].total);
  }
  return state.cache[key];
}

function emptyState(label, iconKey, detail = "Nothing was found for this dataset.") {
  return `<div class="empty-state">${icon(iconKey)}
    <div class="title">No ${esc(label)}</div>
    <div>${esc(detail)}</div></div>`;
}

function errorState(msg) {
  return `<div class="empty-state"><div class="title">Something went wrong</div><div>${esc(msg)}</div></div>`;
}

function filterRecords(records, q) {
  if (!q) return records;
  return records.filter((r) => Object.values(r).some((v) => {
    if (v === null || v === undefined || typeof v === "boolean") return false;
    if (Array.isArray(v)) return v.join(" ").toLowerCase().includes(q);
    return String(v).toLowerCase().includes(q);
  }));
}

// Render big lists in slices so 10k rows don't freeze the page.
function progressive(container, items, renderItem, { batch = 300, wrap = (h) => h } = {}) {
  let shown = 0;
  const sentinel = document.createElement("div");
  sentinel.className = "sentinel";
  const more = () => {
    const html = items.slice(shown, shown + batch).map((it, k) => renderItem(it, shown + k)).join("");
    shown = Math.min(shown + batch, items.length);
    sentinel.insertAdjacentHTML("beforebegin", wrap(html));
    if (shown >= items.length) { obs.disconnect(); sentinel.remove(); }
  };
  const obs = new IntersectionObserver((entries) => {
    if (entries.some((e) => e.isIntersecting)) more();
  }, { root: content(), rootMargin: "800px" });
  container.appendChild(sentinel);
  more();
  if (shown < items.length) obs.observe(sentinel);
}

// ---------------------------------------------------------------- generic table

const TABLE_COLUMNS = {
  contacts: [["name", "Name"], ["organization", "Organization"],
    ["phones", "Phone"], ["emails", "Email"]],
  calls: [["name", "Contact"], ["number", "Number"], ["service", "Type"], ["direction", "Direction"],
    ["answered", "Answered"], ["duration_s", "Duration"], ["date", "Date"]],
  calendar: [["title", "Title"], ["calendar", "Calendar"], ["start", "Start"], ["end", "End"],
    ["location", "Location"]],
  safari: [["title", "Title"], ["url", "URL"], ["visited", "Visited"]],
};
const DATE_COLS = new Set(["date", "start", "end", "modified", "visited", "created"]);

function cellValue(col, row) {
  const v = row[col];
  if (DATE_COLS.has(col)) {
    return esc(fmtDate(v, { time: !(col !== "date" && row.all_day) }));
  }
  if (Array.isArray(v)) return v.map(esc).join("<br>");
  if (col === "duration_s") return v ? fmtDuration(v) : "—";
  if (col === "answered") return v ? "Yes" : '<span class="missed">Missed</span>';
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (col === "url" && v) return `<a href="${esc(v)}" target="_blank" rel="noopener noreferrer">${esc(v)}</a>`;
  return esc(v);
}

async function renderTableView(d, token) {
  setTopbar(d.label, { search: true, exports: exportsFor(d.key) });
  const data = await loadDataset(d.key);
  if (stale(token)) return;
  const draw = (records) => {
    const cols = TABLE_COLUMNS[d.key] || Object.keys(data.records[0] || {}).map((c) => [c, c]);
    if (!records.length) {
      content().innerHTML = emptyState(d.label.toLowerCase(), d.icon,
        data.records.length ? "No matches for that search." : "Nothing was found for this dataset.");
      return;
    }
    const head = cols.map(([, label]) => `<th>${esc(label)}</th>`).join("");
    content().innerHTML = `<div class="table-note muted">${fmtNum(records.length)} ${
      records.length === 1 ? "item" : "items"}</div>
      <table class="data"><thead><tr>${head}</tr></thead><tbody></tbody></table>`;
    const tbody = content().querySelector("tbody");
    // rows go straight into <tbody>, so the sentinel lives after the table
    let shown = 0;
    const more = () => {
      tbody.insertAdjacentHTML("beforeend", records.slice(shown, shown + 400).map((row) =>
        `<tr>${cols.map(([col]) => `<td>${cellValue(col, row)}</td>`).join("")}</tr>`).join(""));
      shown += 400;
      if (shown >= records.length) obs.disconnect();
    };
    const sentinel = document.createElement("div");
    sentinel.className = "sentinel";
    content().appendChild(sentinel);
    const obs = new IntersectionObserver((e) => { if (e.some((x) => x.isIntersecting)) more(); },
      { root: content(), rootMargin: "600px" });
    more();
    if (shown < records.length) obs.observe(sentinel);
  };
  draw(data.records);
  $("search").oninput = debounce((e) => draw(filterRecords(data.records, e.target.value.trim().toLowerCase())), 150);
}

// ---------------------------------------------------------------- messages / whatsapp thread view

async function renderThreadView(d, token) {
  const chatExports = () => state.activeChat
    ? `/api/export/${d.key}.html?chat=${encodeURIComponent(state.activeChat)}` : `/api/export/${d.key}.html`;
  setTopbar(d.label, {
    search: true, placeholder: "Search conversations",
    exports: [["This chat (HTML)", chatExports], ...exportsFor(d.key)],
  });
  const chats = await api(`/api/chats/${d.key}`);
  if (stale(token)) return;
  if (!chats.length) {
    content().innerHTML = emptyState(d.label.toLowerCase(), d.icon);
    return;
  }
  content().innerHTML = `<div class="thread-layout"><div id="chat-list"></div><div id="thread-pane"></div></div>`;
  renderChatList(d, chats);
  selectChat(d, chats[0].chat_id);

  $("search").oninput = debounce(async (e) => {
    const q = e.target.value.trim();
    const filtered = await api(`/api/chats/${d.key}?q=${encodeURIComponent(q)}`);
    if (stale(token)) return;
    renderChatList(d, filtered, q);
    if (filtered.length && !filtered.some((c) => c.chat_id === state.activeChat)) {
      selectChat(d, filtered[0].chat_id, q);
    }
  }, 250);
}

function renderChatList(d, chats, q = "") {
  const list = $("chat-list");
  if (!chats.length) {
    list.innerHTML = `<div class="muted pad">No conversations match.</div>`;
    return;
  }
  list.innerHTML = "";
  progressive(list, chats, (c) => `
    <div class="chat-row ${c.chat_id === state.activeChat ? "active" : ""}" data-chat="${esc(c.chat_id)}">
      <span class="time">${esc(fmtDate(c.last_date, { time: false }))}</span>
      <div class="name">${esc(c.name)}</div>
      <div class="preview">${esc(c.preview)}</div>
      <div class="meta muted">${plural(c.count, "message")}</div>
    </div>`, { batch: 200 });
  list.onclick = (e) => {
    const row = e.target.closest(".chat-row");
    if (row) selectChat(d, row.dataset.chat, q);
  };
}

function attachmentHtml(a) {
  const name = a.name || "attachment";
  if (!a.file_id) return `<div class="att-missing">📎 ${esc(name)} <span class="muted">(not in backup)</span></div>`;
  const mime = a.mime || "";
  if (mime.startsWith("image/") || IMG_EXT.test(name)) {
    return `<img class="att-img" loading="lazy" src="/api/thumb/${a.file_id}" data-view="${a.file_id}"
      data-name="${esc(name)}" alt="${esc(name)}">`;
  }
  if (mime.startsWith("video/") || VID_EXT.test(name)) {
    return `<video class="att-img" controls preload="none" src="/api/file/${a.file_id}"></video>`;
  }
  if (mime.startsWith("audio/") || AUD_EXT.test(name)) {
    return `<audio controls preload="none" src="/api/view/${a.file_id}"></audio>`;
  }
  return `<a class="att-file" href="/api/file/${a.file_id}?download=1">📎 ${esc(name)} <span>${fmtBytes(a.size)}</span></a>`;
}

function messageHtml(m, isGroup, prev) {
  let sep = "";
  const day = (m.date || "").slice(0, 10);
  if (!prev || (prev.date || "").slice(0, 10) !== day) {
    sep = `<div class="bubble-time">${esc(fmtDate(m.date, { time: false }))}</div>`;
  }
  const atts = (m.attachments || []).map(attachmentHtml).join("");
  const text = m.text ? `<div class="txt">${esc(m.text)}</div>` : "";
  const body = text || atts ? text + atts : (m.has_attachment ? "📎 Attachment" : "<i>(empty)</i>");
  const who = isGroup && !m.from_me && m.sender ? `<div class="sender">${esc(m.sender)}</div>` : "";
  return `${sep}<div class="bubble-row ${m.from_me ? "me" : "them"}">
    <div class="bubble ${atts && !text ? "media-only" : ""}">${who}${body}
      <span class="attach-tag">${esc(new Date(m.date).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" }))}${
        m.service && m.service !== "iMessage" ? " · " + esc(m.service) : ""}</span>
    </div></div>`;
}

async function selectChat(d, chatId, q = "") {
  state.activeChat = chatId;
  document.querySelectorAll(".chat-row").forEach((r) =>
    r.classList.toggle("active", r.dataset.chat === chatId));
  const pane = $("thread-pane");
  pane.innerHTML = `<div class="loading">Loading…</div>`;
  const token = state.viewToken;
  const data = await api(`/api/chat/${d.key}/${encodeURIComponent(chatId)}`);
  if (stale(token) || state.activeChat !== chatId) return;
  const msgs = data.messages;
  const isGroup = new Set(msgs.filter((m) => !m.from_me).map((m) => m.sender)).size > 1;
  const PAGE = 400;
  let start = Math.max(0, msgs.length - PAGE);

  const draw = (keepScroll) => {
    const before = pane.scrollHeight - pane.scrollTop;
    const head = start > 0
      ? `<div class="load-earlier"><button class="btn" id="load-earlier">Load earlier messages (${fmtNum(start)} more)</button></div>`
      : `<div class="thread-head muted">${esc(data.chat)} · ${fmtNum(msgs.length)} messages</div>`;
    pane.innerHTML = head + msgs.slice(start).map((m, i) =>
      messageHtml(m, isGroup, i ? msgs[start + i - 1] : null)).join("");
    pane.scrollTop = keepScroll ? pane.scrollHeight - before : pane.scrollHeight;
    const btn = $("load-earlier");
    if (btn) btn.onclick = () => { start = Math.max(0, start - PAGE); draw(true); };
  };
  draw(false);
  if (q) {  // jump to the first match of the search term
    const needle = q.toLowerCase();
    const idx = msgs.findIndex((m) => (m.text || "").toLowerCase().includes(needle));
    if (idx >= 0 && idx < start) { start = Math.max(0, idx - 20); draw(false); }
    const hit = [...pane.querySelectorAll(".txt")].find((el) => el.textContent.toLowerCase().includes(needle));
    if (hit) { hit.closest(".bubble").classList.add("hit"); hit.scrollIntoView({ block: "center" }); }
  }
  pane.onclick = (e) => {
    const img = e.target.closest("img[data-view]");
    if (img) openViewer([{ view: `/api/view/${img.dataset.view}`, name: img.dataset.name,
      download: `/api/file/${img.dataset.view}?download=1` }], 0);
  };
}

// ---------------------------------------------------------------- notes

async function renderNotes(d, token) {
  setTopbar(d.label, { search: true, placeholder: "Search notes", exports: exportsFor(d.key) });
  const data = await loadDataset(d.key);
  if (stale(token)) return;
  if (!data.records.length) { content().innerHTML = emptyState("notes", d.icon); return; }
  content().innerHTML = `<div class="thread-layout"><div id="chat-list"></div><div id="note-pane"></div></div>`;
  let current = data.records;
  const show = (i) => {
    const n = current[i];
    document.querySelectorAll("#chat-list .chat-row").forEach((r) =>
      r.classList.toggle("active", Number(r.dataset.i) === i));
    $("note-pane").innerHTML = !n ? "" : `<article class="note">
      <h2>${esc(n.title)}</h2>
      <div class="muted">${esc([n.folder, n.modified && "Edited " + fmtDate(n.modified),
        n.created && "Created " + fmtDate(n.created)].filter(Boolean).join(" · "))}</div>
      <div class="note-body">${n.locked ? "<i>🔒 This note is locked with a password.</i>"
        : esc(n.body || n.snippet || "")}</div></article>`;
  };
  const draw = (records) => {
    current = records;
    const list = $("chat-list");
    list.innerHTML = records.length ? records.map((n, i) => `
      <div class="chat-row" data-i="${i}">
        <span class="time">${esc(fmtDate(n.modified, { time: false }))}</span>
        <div class="name">${n.locked ? "🔒 " : ""}${esc(n.title)}</div>
        <div class="preview">${esc(n.snippet || (n.body || "").slice(0, 120))}</div>
        ${n.folder ? `<div class="meta muted">${esc(n.folder)}</div>` : ""}
      </div>`).join("") : `<div class="muted pad">No notes match.</div>`;
    list.onclick = (e) => { const r = e.target.closest(".chat-row"); if (r) show(Number(r.dataset.i)); };
    show(0);
  };
  draw(data.records);
  $("search").oninput = debounce((e) => draw(filterRecords(data.records, e.target.value.trim().toLowerCase())), 150);
}

// ---------------------------------------------------------------- photos

const QUALITY_LABEL = {
  original: "Original", "edited render": "Edited render", thumbnail: "Preview",
  preview: "Preview", "poster frame": "Poster frame", "tiny thumbnail": "Tiny preview",
};

async function renderPhotoGrid(d, token) {
  const filters = [["all", "All"], ["photos", "Photos"], ["videos", "Videos"],
    ["favorites", "Favorites"], ["originals", "Originals"]];
  setTopbar(d.label, {
    search: true, placeholder: "Search file names", exports: exportsFor(d.key),
    tools: `<div class="seg" id="photo-filter">${filters.map(([k, l], i) =>
      `<button data-f="${k}" class="${i ? "" : "on"}">${l}</button>`).join("")}</div>`,
  });
  const data = await loadDataset(d.key);
  if (stale(token)) return;
  const all = data.records;
  let filter = "all";
  let q = "";
  const apply = () => {
    const recs = all.filter((r) => {
      if (filter === "photos" && r.is_video) return false;
      if (filter === "videos" && !r.is_video) return false;
      if (filter === "favorites" && !r.favorite) return false;
      if (filter === "originals" && !r.file_id) return false;
      return !q || (r.filename || "").toLowerCase().includes(q);
    });
    drawPhotoGrid(recs, all);
  };
  $("photo-filter").onclick = (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    filter = b.dataset.f;
    document.querySelectorAll("#photo-filter button").forEach((x) => x.classList.toggle("on", x === b));
    apply();
  };
  $("search").oninput = debounce((e) => { q = e.target.value.trim().toLowerCase(); apply(); }, 150);
  apply();
}

function photoViewItem(r) {
  const playable = r.is_video && r.file_id;
  const name = r.filename;
  return {
    name,
    video: playable ? `/api/file/${r.file_id}` : null,
    view: r.best_id ? `/api/view/${r.best_id}` : null,
    poster: r.thumb_id ? `/api/thumb/${r.thumb_id}` : null,
    download: r.file_id ? `/api/file/${r.file_id}?download=1`
      : r.best_id ? `/api/file/${r.best_id}?download=1` : null,
    downloadLabel: r.file_id ? "Download original" : "Download best available",
    meta: [r.width && r.height ? `${r.width}×${r.height}` : null,
      r.size ? fmtBytes(r.size) : null, fmtDate(r.created),
      r.is_video && r.duration_s ? fmtDuration(r.duration_s) : null].filter(Boolean).join(" · "),
    note: r.file_id ? "" : r.best_id
      ? `The original isn't in this backup. Showing the best copy it kept: ${
        (QUALITY_LABEL[r.quality] || r.quality || "preview").toLowerCase()} (${fmtBytes(r.best_size)}).`
      : "No image for this item was kept in the backup.",
  };
}

function drawPhotoGrid(records, all) {
  const c = content();
  if (!records.length) {
    c.innerHTML = emptyState("photos", "photos", all.length ? "No photos match." : "Nothing was found for this dataset.");
    return;
  }
  const originals = records.filter((r) => r.file_id).length;
  c.innerHTML = `<div class="table-note muted">${fmtNum(records.length)} items · ${fmtNum(originals)} originals in backup${
    originals < records.length ? " · others shown at the best quality the backup kept" : ""}</div>`;
  const grid = document.createElement("div");
  grid.className = "photo-grid";
  c.appendChild(grid);
  progressive(grid, records, (r, i) => {
    return `<div class="photo-cell ${r.thumb_id ? "" : "no-image"}" data-idx="${i}" title="${esc(r.filename)}">
      ${r.thumb_id ? `<img loading="lazy" decoding="async" src="/api/thumb/${r.thumb_id}" alt="${esc(r.filename)}">`
        : `<div class="ph">${icon(r.is_video ? "video" : "photos")}<span>Not in backup</span></div>`}
      ${r.favorite ? '<span class="fav">★</span>' : ""}
      ${r.is_video ? `<span class="video-badge">▶ ${r.duration_s ? fmtDuration(r.duration_s) : "Video"}</span>` : ""}
    </div>`;
  }, { batch: 240 });
  grid.onclick = (e) => {
    const cell = e.target.closest(".photo-cell");
    if (cell) openViewer(records.map(photoViewItem), Number(cell.dataset.idx));
  };
}

// ---------------------------------------------------------------- lightbox / viewer

const viewer = { items: [], i: 0 };

function openViewer(items, i) {
  viewer.items = items;
  viewer.i = i;
  $("lightbox").classList.remove("hidden");
  drawViewer();
}

function drawViewer() {
  const it = viewer.items[viewer.i];
  const body = $("lightbox-body");
  $("lightbox-prev").hidden = viewer.i <= 0;
  $("lightbox-next").hidden = viewer.i >= viewer.items.length - 1;
  let media;
  if (it.video) {
    media = `<video src="${esc(it.video)}" ${it.poster ? `poster="${esc(it.poster)}"` : ""} controls autoplay></video>`;
  } else if (it.view) {
    // show the small thumbnail instantly, swap in the full image once decoded
    media = `<img id="lb-img" src="${esc(it.poster || it.view)}" alt="${esc(it.name)}">`;
  } else {
    media = `<div class="lb-missing">${icon("photos")}<div>Not in this backup</div></div>`;
  }
  body.innerHTML = `${media}<div class="meta"><b>${esc(it.name)}</b>${it.meta ? " · " + esc(it.meta) : ""}
    ${it.download ? `&nbsp;·&nbsp;<a href="${esc(it.download)}">${esc(it.downloadLabel || "Download")}</a>` : ""}
    ${it.note ? `<div class="lb-note">${esc(it.note)}</div>` : ""}</div>`;
  if (!it.video && it.view && it.poster && it.poster !== it.view) {
    const full = new Image();
    const want = viewer.i;
    full.onload = () => {
      const img = $("lb-img");
      if (img && viewer.i === want) img.src = full.src;
    };
    full.src = it.view;
  }
}

function closeViewer() {
  $("lightbox").classList.add("hidden");
  $("lightbox-body").innerHTML = "";
}

$("lightbox-close").addEventListener("click", closeViewer);
$("lightbox-prev").addEventListener("click", (e) => { e.stopPropagation(); if (viewer.i > 0) { viewer.i--; drawViewer(); } });
$("lightbox-next").addEventListener("click", (e) => {
  e.stopPropagation();
  if (viewer.i < viewer.items.length - 1) { viewer.i++; drawViewer(); }
});
$("lightbox").addEventListener("click", (e) => { if (e.target.id === "lightbox") closeViewer(); });
document.addEventListener("keydown", (e) => {
  if ($("lightbox").classList.contains("hidden")) return;
  if (e.key === "Escape") closeViewer();
  if (e.key === "ArrowLeft") $("lightbox-prev").click();
  if (e.key === "ArrowRight") $("lightbox-next").click();
});

// ---------------------------------------------------------------- audio lists (voice memos, voicemail, ringtones)

async function renderAudio(d, token) {
  setTopbar(d.label, { search: true, exports: exportsFor(d.key) });
  const data = await loadDataset(d.key);
  if (stale(token)) return;
  const draw = (records) => {
    if (!records.length) {
      content().innerHTML = emptyState(d.label.toLowerCase(), d.icon,
        data.records.length ? "No matches." : "Nothing was found for this dataset.");
      return;
    }
    content().innerHTML = `<div class="audio-list">${records.map((r) => `
      <div class="audio-row">
        <div class="audio-main">
          <div class="name">${esc(r.title)}</div>
          <div class="muted">${esc([r.number && r.number !== r.title ? r.number : null, fmtDate(r.date),
            r.duration_s ? fmtDuration(r.duration_s) : null, r.size ? fmtBytes(r.size) : null].filter(Boolean).join(" · "))}</div>
        </div>
        ${r.file_id ? `<audio controls preload="none" src="/api/view/${r.file_id}"></audio>
          <a class="btn" href="/api/file/${r.file_id}?download=1" title="Download">${icon("download")}</a>`
          : `<span class="muted">audio not in backup</span>`}
      </div>`).join("")}</div>`;
  };
  draw(data.records);
  $("search").oninput = debounce((e) => draw(filterRecords(data.records, e.target.value.trim().toLowerCase())), 150);
}

// ---------------------------------------------------------------- apps

async function renderApps(d, token) {
  setTopbar(d.label, {
    search: true, placeholder: "Search apps", exports: exportsFor(d.key),
    tools: `<div class="seg" id="app-sort"><button data-s="name" class="on">Name</button><button data-s="size">Data size</button></div>`,
  });
  content().innerHTML = `<div class="loading">Measuring app data…</div>`;
  const data = await loadDataset(d.key);
  if (stale(token)) return;
  let sort = "name", q = "";
  const draw = () => {
    let recs = filterRecords(data.records, q);
    if (sort === "size") recs = [...recs].sort((a, b) => b.data_size - a.data_size);
    content().innerHTML = recs.length ? `<div class="app-grid">${recs.map((a) => `
      <div class="app-card" title="${esc(a.bundle_id)}">
        ${a.has_icon ? `<img src="/api/appicon/${encodeURIComponent(a.bundle_id)}" alt="">` : `<div class="app-ph">${icon("apps")}</div>`}
        <div class="app-text">
          <div class="name">${esc(a.name)}</div>
          <div class="muted">${esc(a.developer || a.bundle_id)}</div>
          <div class="muted">${esc([a.version && "v" + a.version, a.genre].filter(Boolean).join(" · "))}</div>
          <div class="app-size">${a.data_size ? fmtBytes(a.data_size) + " of data" : "no data in backup"}${
            a.offloaded ? ' · <span class="muted">offloaded</span>' : ""}</div>
        </div>
      </div>`).join("")}</div>` : emptyState("apps", "apps", "No matches.");
  };
  $("app-sort").onclick = (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    sort = b.dataset.s;
    document.querySelectorAll("#app-sort button").forEach((x) => x.classList.toggle("on", x === b));
    draw();
  };
  $("search").oninput = debounce((e) => { q = e.target.value.trim().toLowerCase(); draw(); }, 150);
  draw();
}

// ---------------------------------------------------------------- file system browser

const FILES_APP_DOMAIN = "AppDomainGroup-group.com.apple.FileProvider.LocalStorage";

function domainGroup(domain) {
  if (domain.startsWith("AppDomainGroup-")) return "App Groups";
  if (domain.startsWith("AppDomainPlugin-")) return "App Extensions";
  if (domain.startsWith("AppDomain-")) return "Apps";
  return "System";
}

async function renderFiles(d, token, loc = null) {
  setTopbar(d.label, { search: true, placeholder: loc ? "Filter this folder" : "Filter domains" });
  if (!loc) {
    const domains = await api("/api/domains");
    if (stale(token)) return;
    const appNames = {};
    for (const a of (state.cache.apps || {}).records || []) appNames[a.bundle_id] = a.name;
    const draw = (q) => {
      const groups = {};
      for (const x of domains) {
        if (q && !x.domain.toLowerCase().includes(q)) continue;
        (groups[domainGroup(x.domain)] ||= []).push(x);
      }
      const shortcut = domains.some((x) => x.domain === FILES_APP_DOMAIN)
        ? `<div class="fs-row dir shortcut" data-domain="${FILES_APP_DOMAIN}" data-path="File Provider Storage">
            ${icon("files")}<span class="name">On My iPhone <span class="muted">(Files app)</span></span></div>` : "";
      content().innerHTML = `<div class="fs">${q ? "" : shortcut}${["System", "Apps", "App Groups", "App Extensions"]
        .filter((g) => groups[g]).map((g) => `<div class="fs-group">${g}</div>${groups[g].map((x) => {
          const bid = x.domain.replace(/^AppDomain(Group|Plugin)?-/, "");
          const label = appNames[bid] ? `${esc(appNames[bid])} <span class="muted">${esc(x.domain)}</span>` : esc(x.domain);
          return `<div class="fs-row dir" data-domain="${esc(x.domain)}" data-path="">${icon("files")}
            <span class="name">${label}</span><span class="muted">${plural(x.files, "file")}</span></div>`;
        }).join("")}`).join("")}</div>`;
    };
    draw("");
    $("search").oninput = debounce((e) => draw(e.target.value.trim().toLowerCase()), 120);
    content().onclick = (e) => {
      const row = e.target.closest(".fs-row.dir");
      if (row) renderFiles(d, token, { domain: row.dataset.domain, path: row.dataset.path });
    };
    return;
  }

  const listing = await api(`/api/fs?domain=${encodeURIComponent(loc.domain)}&path=${encodeURIComponent(loc.path)}`);
  if (stale(token)) return;
  const segs = loc.path ? loc.path.split("/") : [];
  const crumbs = [`<a data-domain="" data-path="">All domains</a>`,
    `<a data-domain="${esc(loc.domain)}" data-path="">${esc(loc.domain)}</a>`,
    ...segs.map((s, i) => `<a data-domain="${esc(loc.domain)}" data-path="${esc(segs.slice(0, i + 1).join("/"))}">${esc(s)}</a>`)];
  const draw = (q) => {
    const dirs = listing.dirs.filter((x) => !q || x.name.toLowerCase().includes(q));
    const files = listing.files.filter((x) => !q || x.name.toLowerCase().includes(q));
    content().innerHTML = `<div class="crumbs">${crumbs.join('<span class="sep">›</span>')}</div>
      <div class="fs">${dirs.map((x) => `<div class="fs-row dir" data-domain="${esc(loc.domain)}"
          data-path="${esc((loc.path ? loc.path + "/" : "") + x.name)}">${icon("files")}
          <span class="name">${esc(x.name)}</span><span class="muted">${x.files ? plural(x.files, "file") : ""}</span></div>`).join("")}
        ${files.map((f, i) => `<div class="fs-row file" data-i="${listing.files.indexOf(f)}">${icon(fileIcon(f.name))}
          <span class="name">${esc(f.name)}</span><span class="muted">${esc(fmtDate(f.mtime ? new Date(f.mtime * 1000).toISOString() : null))}</span>
          <span class="muted size">${fmtBytes(f.size)}</span>
          <a class="btn small" href="/api/file/${f.file_id}?download=1" title="Download">${icon("download")}</a></div>`).join("")}
        ${!dirs.length && !files.length ? `<div class="muted pad">Empty folder.</div>` : ""}</div>`;
  };
  draw("");
  $("search").oninput = debounce((e) => draw(e.target.value.trim().toLowerCase()), 120);
  content().onclick = (e) => {
    const crumb = e.target.closest(".crumbs a");
    if (crumb) {
      return renderFiles(d, token, crumb.dataset.domain ? { domain: crumb.dataset.domain, path: crumb.dataset.path } : null);
    }
    const row = e.target.closest(".fs-row.dir");
    if (row) return renderFiles(d, token, { domain: row.dataset.domain, path: row.dataset.path });
    const file = e.target.closest(".fs-row.file");
    if (file && !e.target.closest("a")) {
      const f = listing.files[Number(file.dataset.i)];
      if (IMG_EXT.test(f.name)) {
        const imgs = listing.files.filter((x) => IMG_EXT.test(x.name));
        openViewer(imgs.map((x) => ({ name: x.name, view: `/api/view/${x.file_id}`, meta: fmtBytes(x.size),
          download: `/api/file/${x.file_id}?download=1` })), imgs.indexOf(f));
      } else if (VID_EXT.test(f.name)) {
        openViewer([{ name: f.name, video: `/api/file/${f.file_id}`, meta: fmtBytes(f.size),
          download: `/api/file/${f.file_id}?download=1` }], 0);
      } else {
        window.open(`/api/view/${f.file_id}`, "_blank");
      }
    }
  };
}

function fileIcon(name) {
  if (IMG_EXT.test(name)) return "photos";
  if (VID_EXT.test(name)) return "video";
  if (AUD_EXT.test(name)) return "music";
  return "file";
}

// ---------------------------------------------------------------- boot

async function bootApp() {
  state.cache = {};
  state.counts = {};
  state.summary = await api("/api/summary");
  state.datasets = await api("/api/datasets");
  renderDeviceCard();
  renderSidebar();
  const initial = location.hash.slice(1);
  navigate(state.datasets.some((d) => d.key === initial && d.available) ? initial : "overview");
  loadCounts();
  api("/api/storage").then(renderStorage).catch(() => renderStorage([]));
}

$("switch-backup").addEventListener("click", async () => {
  await post("/api/close");
  history.replaceState(null, "", "#");
  renderPicker();
});

route().catch((e) => showGate(`<h2>Couldn't start</h2><div class="gate-error">${esc(e.message)}</div>`));

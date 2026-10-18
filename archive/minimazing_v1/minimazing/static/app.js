"use strict";

const state = {
  summary: null,
  datasets: [],
  currentKey: "overview",
  cache: {},        // key -> {records, total, truncated}
  filtered: null,   // records after search filter, for current view
  activeChat: null, // for messages/whatsapp thread view
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

function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  const now = new Date();
  const sameYear = d.getFullYear() === now.getFullYear();
  return d.toLocaleString(undefined, {
    month: "short", day: "numeric",
    year: sameYear ? undefined : "numeric",
    hour: "numeric", minute: "2-digit",
  });
}

async function api(path) {
  const res = await fetch(path);
  if (!res.ok) {
    const body = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(body.error || `Request failed: ${res.status}`);
  }
  return res.json();
}

// ---------------------------------------------------------------- sidebar

const STORAGE_COLORS = {
  Photos: "#4e9bf0", Messages: "#34b36b", WhatsApp: "#2bb67d",
  "App Data": "#f0a93e", Other: "#b5b5bc",
};

function renderSidebar() {
  const s = state.summary;
  document.getElementById("device-icon").innerHTML = icon("device");
  document.getElementById("device-name").textContent = s.device_name || "Unknown device";
  const bits = [s.product_type, s.ios_version ? `iOS ${s.ios_version}` : null,
    s.encrypted ? "Encrypted" : "Unencrypted"].filter(Boolean);
  document.getElementById("device-sub").textContent = bits.join(" · ");

  const bar = document.getElementById("storage-bar");
  const legend = document.getElementById("storage-legend");
  bar.innerHTML = "";
  legend.innerHTML = "";
  const storage = s.storage || [];
  if (!storage.length) {
    bar.style.display = "none";
    legend.style.display = "none";
  } else {
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

  const nav = document.getElementById("nav");
  nav.innerHTML = "";

  const overviewRow = document.createElement("div");
  overviewRow.className = "nav-item" + (state.currentKey === "overview" ? " active" : "");
  overviewRow.innerHTML = `${icon("device")}<span class="label">Overview</span>`;
  overviewRow.addEventListener("click", renderOverview);
  nav.appendChild(overviewRow);

  const sectionLabel = document.createElement("div");
  sectionLabel.className = "nav-section-label";
  sectionLabel.textContent = "Backup";
  nav.appendChild(sectionLabel);

  for (const d of state.datasets) {
    const row = document.createElement("div");
    row.className = "nav-item" + (!d.implemented || !d.available ? " disabled" : "");
    if (d.key === state.currentKey) row.classList.add("active");
    let right = "";
    if (!d.implemented) {
      right = '<span class="soon">not in mini version</span>';
    } else if (d.available) {
      right = `<span class="count">${d.count}</span>`;
    } else {
      right = '<span class="count">—</span>';
    }
    row.innerHTML = `${icon(d.icon)}<span class="label">${esc(d.label)}</span>${right}`;
    if (d.implemented && d.available) {
      row.addEventListener("click", () => selectDataset(d));
    } else {
      row.title = !d.implemented
        ? "This dataset isn't implemented in the mini version."
        : "Not present in this backup.";
    }
    nav.appendChild(row);
  }
}

// ---------------------------------------------------------------- top bar

function setTopbar(title, { search = false, exportKey = null } = {}) {
  document.getElementById("view-title").textContent = title;
  const searchEl = document.getElementById("search");
  searchEl.hidden = !search;
  searchEl.value = "";
  const exportGroup = document.getElementById("export-group");
  exportGroup.hidden = !exportKey;
  if (exportKey) {
    document.getElementById("export-csv").onclick = () =>
      window.open(`/api/export/${exportKey}.csv`, "_blank");
    document.getElementById("export-json").onclick = () =>
      window.open(`/api/export/${exportKey}.json`, "_blank");
  }
}

// ---------------------------------------------------------------- overview

function renderOverview() {
  state.currentKey = "overview";
  renderSidebar();
  setTopbar("Overview");
  const s = state.summary;
  const rows = [
    ["Device", s.device_name || "—"],
    ["Model", s.product_type || "—"],
    ["iOS version", s.ios_version || "—"],
    ["Backup type", s.full_backup ? "Full backup" : "Partial / data-access"],
    ["Encryption", s.encrypted ? "Encrypted backup" : "Not encrypted"],
    ["Snapshot state", s.snapshot_state || "—"],
    ["UDID", s.udid || "—"],
    ["Backup path", s.path || "—"],
  ];
  const cells = rows.map(([k, v]) => `
    <div class="overview-cell"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div></div>
  `).join("");
  document.getElementById("content").innerHTML = `
    <div class="overview">
      <div style="margin-bottom:16px">
        <span class="badge">${icon(s.encrypted ? "lock" : "device")} ${
          s.encrypted ? "Encrypted backup, unlocked" : "Unencrypted backup"}</span>
      </div>
      <div class="overview-grid">${cells}</div>
    </div>`;
}

// ---------------------------------------------------------------- loading records

async function loadDataset(key) {
  if (!state.cache[key]) {
    state.cache[key] = await api(`/api/dataset/${key}`);
  }
  return state.cache[key];
}

function emptyState(label, iconKey) {
  return `<div class="empty-state">${icon(iconKey)}
    <div class="title">No ${esc(label)} in this backup</div>
    <div>Nothing was found for this dataset.</div></div>`;
}

// ---------------------------------------------------------------- generic table

const TABLE_COLUMNS = {
  contacts: [["name", "Name"], ["organization", "Organization"],
    ["values", "Phone / Email"]],
  calls: [["number", "Number"], ["direction", "Direction"],
    ["answered", "Answered"], ["duration_s", "Duration (s)"], ["date", "Date"]],
  calendar: [["title", "Title"], ["start", "Start"], ["end", "End"], ["all_day", "All day"]],
  notes: [["title", "Title"], ["snippet", "Preview"], ["modified", "Modified"]],
  safari: [["title", "Title"], ["url", "URL"], ["visited", "Visited"]],
};

function cellValue(key, col, row) {
  let v = row[col];
  if (col === "date" || col === "start" || col === "end" || col === "modified" || col === "visited") {
    return esc(fmtDate(v));
  }
  if (col === "values" && Array.isArray(v)) return v.map(esc).join("<br>");
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (col === "url" && v) return `<a href="${esc(v)}" target="_blank" rel="noopener">${esc(v)}</a>`;
  return esc(v);
}

async function selectDataset(d) {
  state.currentKey = d.key;
  state.activeChat = null;
  renderSidebar();

  if (d.key === "messages" || d.key === "whatsapp") {
    return renderThreadView(d);
  }
  if (d.key === "photos") {
    return renderPhotoGrid(d);
  }
  setTopbar(d.label, { search: true, exportKey: d.key });
  const data = await loadDataset(d.key);
  state.filtered = data.records;
  renderTable(d, data);
  wireSearch(d, () => renderTable(d, { ...data, records: state.filtered }));
}

function renderTable(d, data) {
  const cols = TABLE_COLUMNS[d.key] || Object.keys(data.records[0] || {}).map((c) => [c, c]);
  const content = document.getElementById("content");
  if (!data.records.length) {
    content.innerHTML = emptyState(d.label.toLowerCase(), d.icon);
    return;
  }
  const head = cols.map(([, label]) => `<th>${esc(label)}</th>`).join("");
  const body = data.records.map((row) => {
    const tds = cols.map(([col]) => `<td>${cellValue(d.key, col, row)}</td>`).join("");
    return `<tr>${tds}</tr>`;
  }).join("");
  const notice = data.truncated
    ? `<div class="truncate-note">Showing the first 5,000 of ${data.total} records. Use Export for all of them.</div>`
    : "";
  content.innerHTML = `${notice}<table class="data"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

function wireSearch(d, onFilter) {
  const el = document.getElementById("search");
  el.oninput = async () => {
    const q = el.value.trim().toLowerCase();
    const data = await loadDataset(d.key);
    state.filtered = !q ? data.records : data.records.filter((r) =>
      Object.values(r).some((v) => {
        if (v === null || v === undefined) return false;
        if (Array.isArray(v)) return v.join(" ").toLowerCase().includes(q);
        return String(v).toLowerCase().includes(q);
      })
    );
    onFilter();
  };
}

// ---------------------------------------------------------------- messages / whatsapp thread view

function groupByChat(records) {
  const map = new Map();
  for (const r of records) {
    const key = r.chat || "Unknown";
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(r);
  }
  return [...map.entries()].map(([chat, msgs]) => ({
    chat, msgs, last: msgs[msgs.length - 1],
  })).sort((a, b) => new Date(b.last.date || 0) - new Date(a.last.date || 0));
}

async function renderThreadView(d) {
  setTopbar(d.label, { search: true, exportKey: d.key });
  const data = await loadDataset(d.key);
  const chats = groupByChat(data.records);
  state.filtered = chats;

  const content = document.getElementById("content");
  if (!chats.length) {
    content.innerHTML = emptyState(d.label.toLowerCase(), d.icon);
    return;
  }
  content.innerHTML = `<div class="thread-layout"><div id="chat-list"></div><div id="thread-pane"></div></div>`;
  renderChatList(chats);
  selectChat(chats[0].chat, chats);

  document.getElementById("search").oninput = (e) => {
    const q = e.target.value.trim().toLowerCase();
    const filtered = !q ? chats : chats.filter((c) =>
      c.chat.toLowerCase().includes(q) ||
      c.msgs.some((m) => (m.text || "").toLowerCase().includes(q))
    );
    renderChatList(filtered);
    if (filtered.length && (!state.activeChat || !filtered.some((c) => c.chat === state.activeChat))) {
      selectChat(filtered[0].chat, chats);
    }
  };
}

function renderChatList(chats) {
  const list = document.getElementById("chat-list");
  list.innerHTML = chats.map((c) => `
    <div class="chat-row ${c.chat === state.activeChat ? "active" : ""}" data-chat="${esc(c.chat)}">
      <span class="time">${esc(fmtDate(c.last.date))}</span>
      <div class="name">${esc(c.chat)}</div>
      <div class="preview">${esc(c.last.text || (c.last.has_attachment ? "📎 Attachment" : ""))}</div>
    </div>`).join("");
  list.querySelectorAll(".chat-row").forEach((row) => {
    row.addEventListener("click", () => selectChat(row.dataset.chat, chats));
  });
}

function selectChat(chatName, chats) {
  state.activeChat = chatName;
  document.querySelectorAll(".chat-row").forEach((r) =>
    r.classList.toggle("active", r.dataset.chat === chatName));
  const entry = chats.find((c) => c.chat === chatName);
  const pane = document.getElementById("thread-pane");
  if (!entry) { pane.innerHTML = ""; return; }
  pane.innerHTML = entry.msgs.map((m) => `
    <div class="bubble-row ${m.from_me ? "me" : "them"}">
      <div class="bubble">${esc(m.text) || (m.has_attachment ? "📎 Attachment" : "<i>(empty)</i>")}
        <span class="attach-tag">${esc(fmtDate(m.date))}</span>
      </div>
    </div>`).join("");
}

// ---------------------------------------------------------------- photos

async function renderPhotoGrid(d) {
  setTopbar(d.label, { search: true, exportKey: d.key });
  const data = await loadDataset(d.key);
  state.filtered = data.records;
  drawPhotoGrid(data.records);

  document.getElementById("search").oninput = (e) => {
    const q = e.target.value.trim().toLowerCase();
    const filtered = !q ? data.records : data.records.filter((r) =>
      (r.filename || "").toLowerCase().includes(q));
    drawPhotoGrid(filtered);
  };
}

function drawPhotoGrid(records) {
  const content = document.getElementById("content");
  if (!records.length) {
    content.innerHTML = emptyState("photos", "photos");
    return;
  }
  content.innerHTML = `<div class="photo-grid">${records.map((r, i) => `
    <div class="photo-cell" data-idx="${i}">
      ${r.file_id ? `<img loading="lazy" src="/api/file/${esc(r.file_id)}" alt="${esc(r.filename)}">` : ""}
      ${r.favorite ? '<span class="fav">★</span>' : ""}
      ${r.is_video ? '<span class="video-badge">Video</span>' : ""}
    </div>`).join("")}</div>`;

  content.querySelectorAll(".photo-cell").forEach((cell) => {
    cell.addEventListener("click", () => openLightbox(records[Number(cell.dataset.idx)]));
  });
}

function openLightbox(rec) {
  const box = document.getElementById("lightbox");
  const body = document.getElementById("lightbox-body");
  const src = rec.file_id ? `/api/file/${rec.file_id}` : "";
  const media = rec.is_video
    ? `<video src="${esc(src)}" controls autoplay></video>`
    : `<img src="${esc(src)}" alt="${esc(rec.filename)}">`;
  body.innerHTML = `${media}<div class="meta">${esc(rec.filename)} · ${fmtBytes(rec.size)} · ${esc(fmtDate(rec.created))}
    &nbsp;·&nbsp; <a style="color:#fff" href="${esc(src)}?download=1">Download original</a></div>`;
  box.classList.remove("hidden");
}

document.getElementById("lightbox-close").addEventListener("click", () => {
  document.getElementById("lightbox").classList.add("hidden");
  document.getElementById("lightbox-body").innerHTML = "";
});
document.getElementById("lightbox").addEventListener("click", (e) => {
  if (e.target.id === "lightbox") {
    document.getElementById("lightbox-close").click();
  }
});

// ---------------------------------------------------------------- boot

async function boot() {
  try {
    state.summary = await api("/api/summary");
    state.datasets = await api("/api/datasets");
    renderOverview();
  } catch (e) {
    document.getElementById("content").innerHTML =
      `<div class="empty-state"><div class="title">Couldn't load this backup</div><div>${esc(e.message)}</div></div>`;
  }
}

boot();

const camerasEl = document.getElementById("cameras");
const countEl = document.getElementById("count");
const healthEl = document.getElementById("health");
const emptyEl = document.getElementById("empty");
const detailEl = document.getElementById("detail");
const statusEl = document.getElementById("status");

let selectedId = null;
let cameras = [];
let firmware = null;
let fwPoll = null;
let lastFirmwareLibraryKey = "";
let openFirmwareFamilies = new Set();
let fwImagesBound = false;
let lastSelectedFwKey = "";
let previewTimer = null;
let previewCamId = null;
let previewGen = 0;
let previewShownCamId = null;
let previewObjectUrl = null;
let previewAbort = null;
let settingsTimer = null;

async function api(path, options) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = data.detail;
    const message =
      typeof detail === "string"
        ? detail
        : detail?.message || JSON.stringify(data);
    throw new Error(message);
  }
  return data;
}

function showStatus(text, isError) {
  statusEl.hidden = false;
  statusEl.textContent = text;
  statusEl.style.borderColor = isError ? "#c43c32" : "#2c2c2c";
}

function sameCamera(a, b) {
  if (!a || !b) return false;
  if (a.id && a.id === b.id) return true;
  const aAliases = a.aliases || [];
  const bAliases = b.aliases || [];
  return (a.id && bAliases.includes(a.id)) || (b.id && aAliases.includes(b.id));
}

function firmwareFor(cam) {
  return (firmware?.cameras || []).find((item) => sameCamera(item, cam));
}

function batteryFromCam(cam) {
  const fromApi = cam?.battery?.percent;
  if (fromApi != null && Number.isFinite(Number(fromApi))) {
    return {
      percent: Math.max(0, Math.min(100, Math.round(Number(fromApi)))),
      charging: Boolean(cam.battery.charging),
    };
  }
  const raw = cam?.txt?.camera_charge;
  const percent = raw != null && raw !== "" ? Number(raw) : NaN;
  if (!Number.isFinite(percent)) return null;
  const status = Number(cam?.txt?.power_status);
  return {
    percent: Math.max(0, Math.min(100, Math.round(percent))),
    charging: Number.isFinite(status) && status !== 0,
  };
}

function batteryTone(batt) {
  if (!batt) return "unknown";
  if (batt.charging) return "charging";
  if (batt.percent <= 15) return "low";
  if (batt.percent <= 40) return "mid";
  return "high";
}

function batteryMarkup(batt) {
  if (!batt) return "";
  const tone = batteryTone(batt);
  const title = batt.charging ? `${batt.percent}% charging` : `${batt.percent}%`;
  return `<span class="cam-batt ${tone}" title="${escapeHtml(title)}">
      <span class="cam-batt-meter" aria-hidden="true"><span class="cam-batt-fill" style="height:${batt.percent}%"></span></span>
      ${batt.charging ? `<span class="cam-batt-bolt" aria-hidden="true"></span>` : ""}
      <span class="cam-batt-pct">${batt.percent}%</span>
    </span>`;
}

function jobPercent(job) {
  const value = Number(job?.progress);
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(100, Math.round(value)));
}

function indicatorFor(fwCam) {
  return fwCam?.indicator || (fwCam?.update ? "pending" : fwCam?.available ? "latest" : "unknown");
}

function indicatorLabel(indicator) {
  return (
    {
      latest: "Latest firmware",
      pending: "Update available",
      updating: "Update in progress",
      error: "Update failed",
      offline: "Camera offline",
      unknown: "Firmware unknown",
    }[indicator] || "Firmware unknown"
  );
}

function canUpdateFirmware(cam) {
  const fwCam = firmwareFor(cam);
  if (!fwCam?.update || cam.offline || fwCam.offline) return false;
  if (jobPending(fwCam.job) || jobShowsDone(fwCam)) return false;
  return true;
}

function jobBusy(job) {
  const status = job?.status;
  return status === "queued" || status === "updating";
}

function jobPending(job) {
  return jobBusy(job) || job?.phase === "rebooting";
}

function jobShowsDone(fwCam) {
  const job = fwCam?.job;
  if (!job || job.status !== "done" || job.phase === "stale") return false;
  if (fwCam.offline || job.phase === "rebooting") return true;
  if (fwCam.current && job.to_version && fwCam.current === job.to_version) return true;
  return !fwCam.update || job.to_version === fwCam.available;
}

function revokePreviewUrl() {
  if (previewObjectUrl) {
    URL.revokeObjectURL(previewObjectUrl);
    previewObjectUrl = null;
  }
}

function stopPreview() {
  previewCamId = null;
  previewGen += 1;
  if (previewAbort) {
    previewAbort.abort();
    previewAbort = null;
  }
  if (previewTimer) {
    clearInterval(previewTimer);
    previewTimer = null;
  }
}

function startPreview(camId) {
  if (previewCamId === camId && previewTimer) return;
  previewCamId = camId;
  const gen = ++previewGen;
  if (previewAbort) previewAbort.abort();
  previewAbort = new AbortController();
  const signal = previewAbort.signal;
  if (previewTimer) {
    clearInterval(previewTimer);
    previewTimer = null;
  }
  const img = document.getElementById("cam-preview");
  const status = document.getElementById("cam-preview-status");
  if (!img || !status) return;
  status.hidden = false;
  status.classList.remove("ok");
  status.textContent = "Connecting preview…";
  if (previewShownCamId !== camId) {
    revokePreviewUrl();
    img.removeAttribute("src");
    img.style.opacity = "";
  }
  fetch(`/api/cameras/${encodeURIComponent(camId)}/preview?follow=1`, { signal }).catch(() => {});
  let inFlight = false;
  const tick = () => {
    if (previewGen !== gen || previewCamId !== camId || inFlight) return;
    inFlight = true;
    fetch(`/api/cameras/${encodeURIComponent(camId)}/preview.jpg?t=${Date.now()}`, { signal })
      .then((res) => {
        if (!res.ok) throw new Error("not ready");
        return res.blob();
      })
      .then((blob) => {
        inFlight = false;
        if (previewGen !== gen || previewCamId !== camId) return;
        revokePreviewUrl();
        previewObjectUrl = URL.createObjectURL(blob);
        img.src = previewObjectUrl;
        img.style.opacity = "";
        previewShownCamId = camId;
        status.textContent = "Live preview";
        status.classList.add("ok");
        status.hidden = true;
      })
      .catch((err) => {
        inFlight = false;
        if (previewGen !== gen || previewCamId !== camId) return;
        if (err && err.name === "AbortError") return;
        fetch(`/api/cameras/${encodeURIComponent(camId)}/preview`, { signal })
          .then((res) => res.json())
          .then((data) => {
            if (previewGen !== gen || previewCamId !== camId) return;
            status.hidden = false;
            status.classList.toggle("ok", Boolean(data.ready));
            status.textContent = data.message || "Waiting for preview…";
          })
          .catch(() => {
            if (previewGen !== gen || previewCamId !== camId) return;
            status.classList.remove("ok");
            status.textContent = "Preview unavailable";
          });
      });
  };
  tick();
  previewTimer = setInterval(tick, 500);
}

function jobsActive(data) {
  return (data?.cameras || []).some((item) => {
    const status = item.job?.status;
    return status === "queued" || status === "updating" || item.job?.phase === "rebooting";
  });
}

function identityOf(cam) {
  const serial = String(cam?.txt?.serial_number || "").trim().toUpperCase();
  if (serial) return `serial:${serial}`;
  const name = String(cam?.name || cam?.txt?.camera_name || "").trim().toLowerCase();
  if (name) return `name:${name}`;
  return `id:${cam?.id || ""}`;
}

function listedCameras() {
  const rows = cameras.slice();
  for (const fwCam of firmware?.cameras || []) {
    if (!fwCam?.id || rows.some((cam) => sameCamera(cam, fwCam))) continue;
    rows.push({
      id: fwCam.id,
      name: fwCam.name,
      host: "",
      port: "",
      offline: true,
      powered_off: Boolean(fwCam.powered_off),
      aliases: fwCam.aliases || [],
      txt: { camera_name: fwCam.name },
    });
  }
  const merged = new Map();
  for (const cam of rows) {
    const key = identityOf(cam);
    const prev = merged.get(key);
    if (!prev) {
      merged.set(key, cam);
      continue;
    }
    const preferNew = Boolean(prev.offline) && !cam.offline;
    const keep = preferNew ? cam : prev;
    const drop = preferNew ? prev : cam;
    const aliases = new Set([...(keep.aliases || []), ...(drop.aliases || [])]);
    if (drop.id && drop.id !== keep.id) aliases.add(drop.id);
    keep.aliases = [...aliases];
    merged.set(key, keep);
  }
  return [...merged.values()];
}

function changelogHtml(entries) {
  return (entries || [])
    .map((entry) => {
      const items = (entry.items || [])
        .map((item) => `<li>${escapeHtml(item)}</li>`)
        .join("");
      return `<section class="change"><h4>${escapeHtml(entry.heading || "")}</h4>${
        items ? `<ul>${items}</ul>` : ""
      }</section>`;
    })
    .join("");
}

function renderChangelog(container, entries, showHeading) {
  if (!container) return;
  if (!entries?.length) {
    if (!container.classList.contains("hidden") || container.innerHTML) {
      container.classList.add("hidden");
      container.innerHTML = "";
    }
    return;
  }
  const html = `${showHeading ? `<p class="whats-new">What's new:</p>` : ""}${changelogHtml(entries)}`;
  if (!container.classList.contains("hidden") && container.innerHTML === html) return;
  container.classList.remove("hidden");
  container.innerHTML = html;
}

function showFwProgress(job) {
  const el = document.getElementById("fw-progress");
  const text = document.getElementById("fw-progress-text");
  const fill = document.getElementById("fw-bar-fill");
  const pctEl = document.getElementById("fw-progress-pct");
  if (!el || !text) return;
  const pending = jobPending(job);
  el.classList.toggle("hidden", !pending);
  el.setAttribute("aria-hidden", pending ? "false" : "true");
  if (pending) {
    text.textContent = job.message || "Updating Mevo firmware. Please wait.";
    const value = jobPercent(job);
    if (fill) fill.style.width = `${value}%`;
    if (pctEl) pctEl.textContent = `${value}%`;
  }
}

function renderList() {
  const rows = listedCameras();
  countEl.textContent = String(rows.length);
  camerasEl.innerHTML = "";
  for (const cam of rows) {
    const li = document.createElement("li");
    if (cam.id === selectedId) li.classList.add("active");
    const label = cam.txt?.camera_name || cam.name;
    const session = cam.session?.authorized ? "connected" : "";
    const fwCam = firmwareFor(cam);
    const job = fwCam?.job;
    const offline = Boolean(cam.offline || fwCam?.offline);
    if (offline) li.classList.add("offline");
    if (jobBusy(job)) li.classList.add("updating");
    const indicator = indicatorFor(fwCam);
    const batt = batteryFromCam(cam);
    let action = "";
    if (jobBusy(job) || job?.phase === "rebooting") {
      const pct = jobPercent(job);
      action = `<span class="cam-job">${escapeHtml(job.message || "Updating Mevo firmware. Please wait.")} (${pct}%)</span>`;
    } else if (job && job.status === "error") {
      action = `<span class="addr">${escapeHtml(job.error || "Update failed")}</span>
        <button type="button" class="cam-update" data-update="${cam.id}">Retry update</button>`;
    } else if (jobShowsDone(fwCam)) {
      action = `<span class="addr">${escapeHtml(job.message || `Updated to ${job.to_version || fwCam?.available || ""}`)}</span>`;
    } else if (fwCam?.update && !offline) {
      action = `<button type="button" class="cam-update" data-update="${cam.id}">Update to ${escapeHtml(fwCam.available)}</button>`;
    }
    const addr = offline
      ? job?.phase === "rebooting" && !cam.powered_off && !fwCam?.powered_off
        ? "Waiting for camera…"
        : "Camera is off"
      : `${cam.host}:${cam.port}${session ? " · connected" : ""}`;
    li.innerHTML = `<span class="name"><span class="fw-dot ${escapeHtml(indicator)}" title="${escapeHtml(indicatorLabel(indicator))}"></span><span class="cam-label">${escapeHtml(label)}</span>${batteryMarkup(batt)}</span>
      <span class="addr">${escapeHtml(addr)}</span>
      ${action}`;
    li.addEventListener("click", (event) => {
      if (event.target.closest("[data-update]")) return;
      select(cam.id);
    });
    camerasEl.appendChild(li);
  }
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function findListed(id) {
  return listedCameras().find((cam) => cam.id === id || (cam.aliases || []).includes(id));
}

async function refresh(loadSettings = false) {
  const data = await api("/api/cameras");
  cameras = data.cameras || [];
  if (selectedId) {
    const match = findListed(selectedId);
    if (match) selectedId = match.id;
  }
  if (!selectedId) {
    const firstLive = cameras.find((item) => !item.offline);
    const pick = firstLive || cameras[0];
    if (pick) {
      selectedId = pick.id;
      loadSettings = true;
    }
  }
  renderList();
  if (selectedId) select(selectedId, loadSettings);
}

function select(id, loadSettings = true) {
  const resolved = findListed(id);
  selectedId = resolved?.id || id;
  const cam = resolved;
  const fwCam = firmwareFor({ id });
  renderList();
  if (!cam) {
    emptyEl.classList.remove("hidden");
    detailEl.classList.add("hidden");
    showFwProgress(null);
    stopPreview();
    return;
  }
  emptyEl.classList.add("hidden");
  detailEl.classList.remove("hidden");
  const label = cam.txt?.camera_name || cam.name;
  const job = fwCam?.job;
  const offline = Boolean(cam.offline || fwCam?.offline);
  document.getElementById("cam-name").textContent = label;
  document.getElementById("cam-addr").textContent = offline
    ? job?.phase === "rebooting" && !cam.powered_off && !fwCam?.powered_off
      ? "Waiting for camera to return"
      : "Camera is off"
    : `${cam.host}:${cam.port} · ${cam.service_type || ""}`;
  const session = cam.session || {};
  const sessionEl = document.getElementById("cam-session");
  sessionEl.textContent = offline
    ? ""
    : session.authorized
      ? `session authorized · protocol ${session.protocol_version ?? "?"}`
      : "not connected — Ping / Reboot / Shutdown will authorize automatically";
  const fwEl = document.getElementById("cam-fw");
  if (fwEl) {
    if (jobPending(job)) {
      fwEl.textContent = `${job.message || "Updating Mevo firmware. Please wait."} (${jobPercent(job)}%)`;
    } else if (job && job.status === "error") {
      fwEl.textContent = job.error || "Firmware update failed";
    } else if (jobShowsDone(fwCam)) {
      fwEl.textContent = job.message || `Updated to ${job.to_version}`;
    } else if (fwCam?.available) {
      const current = fwCam.current || "unknown";
      fwEl.textContent = fwCam.update
        ? `${fwCam.label} firmware ${current} → ${fwCam.available} available`
        : `${fwCam.label} firmware ${current} (library ${fwCam.available})`;
    } else {
      fwEl.textContent = "No firmware in the local library yet. Check for updates.";
    }
  }
  const notes = Boolean(fwCam?.update || jobPending(job) || (job && job.status === "error"));
  renderChangelog(document.getElementById("cam-changelog"), notes ? (fwCam?.changelog || []) : [], notes);
  const updateBtn = document.querySelector('[data-action="update-firmware"]');
  if (updateBtn) {
    const allowed = canUpdateFirmware(cam);
    updateBtn.disabled = !allowed;
    updateBtn.title = jobPending(job)
      ? "Firmware update in progress"
      : allowed
        ? ""
        : "Already on the latest firmware";
  }
  showFwProgress(job);
  const meta = document.getElementById("cam-meta");
  meta.innerHTML = "";
  const rows = [
    ["id", cam.id],
    ["source", cam.source || (offline ? "offline" : "")],
    ["model", cam.model || "—"],
  ];
  for (const [key, value] of Object.entries(cam.txt || {})) {
    rows.push([key, value]);
  }
  for (const [k, v] of rows) {
    const dt = document.createElement("dt");
    const dd = document.createElement("dd");
    dt.textContent = k;
    dd.textContent = v;
    meta.append(dt, dd);
  }
  document.getElementById("deck-url").textContent =
    `POST http://127.0.0.1:8787/api/cameras/${cam.id}/actions/shutdown`;
  if (offline) {
    stopPreview();
    const previewStatus = document.getElementById("cam-preview-status");
    if (previewStatus) {
      previewStatus.hidden = false;
      previewStatus.classList.remove("ok");
      previewStatus.textContent = "Camera offline";
    }
    const settingsEl = document.getElementById("cam-settings");
    if (settingsEl) {
      settingsEl.textContent =
        job?.phase === "rebooting" && !cam.powered_off && !fwCam?.powered_off
          ? "Camera is rebooting…"
          : "Camera is off";
    }
    return;
  }
  startPreview(cam.id);
  if (!loadSettings) return;
  const settingsEl = document.getElementById("cam-settings");
  if (!settingsEl) return;
  settingsEl.textContent = session.authorized ? "loading…" : "connect to load settings";
  if (!session.authorized) return;
  if (settingsTimer) clearTimeout(settingsTimer);
  settingsTimer = setTimeout(() => {
    if (selectedId !== cam.id) return;
    api(`/api/cameras/${cam.id}/settings`)
      .then((data) => {
        if (selectedId !== cam.id) return;
        settingsEl.textContent = JSON.stringify(data.settings, null, 2);
      })
      .catch((err) => {
        if (selectedId !== cam.id) return;
        settingsEl.textContent = err.message;
      });
  }, 350);
}

async function health() {
  try {
    const data = await api("/api/health");
    healthEl.textContent = `ok · v${data.version}`;
    healthEl.classList.add("ok");
  } catch {
    healthEl.textContent = "offline";
    healthEl.classList.remove("ok");
  }
}

document.getElementById("refresh").addEventListener("click", () => {
  refresh(true).catch((err) => showStatus(err.message, true));
});

document.getElementById("manual").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  try {
    const data = await api("/api/cameras/manual", {
      method: "POST",
      body: JSON.stringify({
        host: form.get("host"),
        port: Number(form.get("port") || 38000),
        name: form.get("name") || null,
      }),
    });
    await refresh(true);
    select(data.camera.id);
  } catch (err) {
    showStatus(err.message, true);
  }
});

document.querySelector(".actions").addEventListener("click", async (event) => {
  const button = event.target.closest("button");
  if (!button || !selectedId) return;
  const action = button.dataset.action;
  try {
    if (action === "connect") {
      const data = await api(`/api/cameras/${selectedId}/connect`, { method: "POST" });
      showStatus(JSON.stringify(data.result, null, 2), false);
      await refresh(true);
      return;
    }
    if (action === "probe") {
      const data = await api(`/api/cameras/${selectedId}/probe`, { method: "POST" });
      showStatus(JSON.stringify(data.probe, null, 2), !data.probe.ok);
      return;
    }
    if (action === "update-firmware") {
      if (!canUpdateFirmware(listedCameras().find((item) => item.id === selectedId) || { id: selectedId })) {
        return;
      }
      await startCameraUpdate(selectedId);
      return;
    }
    if (["ping", "reboot", "shutdown"].includes(action)) {
      if (action === "shutdown" && !confirm("Shut down this camera?")) return;
      if (action === "reboot" && !confirm("Reboot this camera?")) return;
      const data = await api(`/api/cameras/${selectedId}/actions/${action}`, {
        method: "POST",
      });
      if (action === "shutdown") {
        showStatus("Camera is shutting down.", false);
      } else if (action === "reboot") {
        showStatus("Camera is rebooting.", false);
      } else {
        showStatus(JSON.stringify(data.result || data, null, 2), false);
      }
      await refresh(true);
    }
  } catch (err) {
    showStatus(err.message, true);
  }
});

health();
refresh(true).catch((err) => showStatus(err.message, true));
refreshFirmware().catch(() => {});
setInterval(() => {
  refresh(false).catch(() => {});
  health();
  refreshFirmware().catch(() => {});
}, 4000);

function firmwareLibraryKey(data) {
  return JSON.stringify(
    (data.images || []).map((image) => [
      image.family,
      image.version,
      image.label,
      image.changelog,
    ]),
  );
}

function selectedFirmwareKey(data) {
  const fwCam = (data.cameras || []).find((item) => item.id === selectedId);
  if (!fwCam) return "";
  const job = fwCam.job || {};
  return [
    fwCam.indicator,
    fwCam.current,
    fwCam.available,
    fwCam.update,
    fwCam.offline,
    job.status,
    job.phase,
    job.progress,
    job.message,
    job.error,
  ].join("|");
}

function bindFirmwareImages(imagesEl) {
  if (fwImagesBound || !imagesEl) return;
  fwImagesBound = true;
  imagesEl.addEventListener(
    "toggle",
    (event) => {
      const details = event.target;
      if (!(details instanceof HTMLDetailsElement)) return;
      const key = details.dataset.family;
      if (!key) return;
      if (details.open) openFirmwareFamilies.add(key);
      else openFirmwareFamilies.delete(key);
    },
    true,
  );
}

function rememberOpenFirmware(imagesEl) {
  if (!imagesEl) return;
  for (const details of imagesEl.querySelectorAll("details")) {
    const key = details.dataset.family;
    if (!key) continue;
    if (details.open) openFirmwareFamilies.add(key);
    else openFirmwareFamilies.delete(key);
  }
}

function renderFirmwareImages(imagesEl, data) {
  const key = firmwareLibraryKey(data);
  rememberOpenFirmware(imagesEl);
  if (key === lastFirmwareLibraryKey && imagesEl.children.length) return;
  lastFirmwareLibraryKey = key;
  imagesEl.innerHTML = "";
  for (const image of data.images || []) {
    const li = document.createElement("li");
    const family = String(image.family || image.version || "");
    const notes = changelogHtml(image.changelog);
    const open = openFirmwareFamilies.has(family);
    li.innerHTML = `<details data-family="${escapeHtml(family)}"${open ? " open" : ""}>
      <summary>
        <span class="name">${escapeHtml(image.label || image.family)}</span>
        <span class="addr"><span class="ver">${escapeHtml(image.version)}</span></span>
      </summary>
      <div class="changelog">${notes || `<p class="muted">No release notes.</p>`}</div>
    </details>`;
    imagesEl.appendChild(li);
  }
}

function renderFirmware(data) {
  firmware = data;
  const statusElFw = document.getElementById("fw-status");
  const imagesEl = document.getElementById("fw-images");
  const checkBtn = document.getElementById("fw-check");
  const checking = data.status === "checking";
  const updating = jobsActive(data);
  const outdated = (data.cameras || []).filter(
    (item) => item.update && !jobShowsDone(item) && !jobPending(item.job)
  ).length;
  const parts = [];
  if (data.app?.version) parts.push(`app ${data.app.version}`);
  if (data.message) parts.push(data.message);
  else parts.push("Checks once after sign-in, when online");
  statusElFw.textContent = parts.join(" · ") || "No firmware library yet";
  bindFirmwareImages(imagesEl);
  renderFirmwareImages(imagesEl, data);
  checkBtn.disabled = checking;
  checkBtn.textContent = checking ? "Checking…" : "Check for updates";
  const updateAllBtn = document.getElementById("fw-update-all");
  if (updateAllBtn) {
    updateAllBtn.disabled = outdated === 0;
    updateAllBtn.textContent =
      outdated > 0 ? `Update all outdated (${outdated})` : "All cameras current";
  }
  const nextSelectedKey = selectedFirmwareKey(data);
  if (selectedId && nextSelectedKey !== lastSelectedFwKey) {
    lastSelectedFwKey = nextSelectedKey;
    select(selectedId, false);
  }
  if ((checking || updating) && !fwPoll) {
    fwPoll = setInterval(() => {
      refreshFirmware().catch(() => {});
    }, 1000);
  }
  if (!checking && !updating && fwPoll) {
    clearInterval(fwPoll);
    fwPoll = null;
  }
}

async function refreshFirmware() {
  const data = await api("/api/firmware");
  renderFirmware(data);
}

let fwDialogResolver = null;

function openFirmwareDialog({ lead, changelog }) {
  const dialog = document.getElementById("fw-dialog");
  document.getElementById("fw-dialog-lead").textContent = lead;
  document.getElementById("fw-dialog-notes").innerHTML = changelogHtml(changelog);
  dialog.classList.remove("hidden");
  dialog.setAttribute("aria-hidden", "false");
  return new Promise((resolve) => {
    fwDialogResolver = resolve;
  });
}

function closeFirmwareDialog(result) {
  const dialog = document.getElementById("fw-dialog");
  if (dialog) {
    dialog.classList.add("hidden");
    dialog.setAttribute("aria-hidden", "true");
  }
  const resolve = fwDialogResolver;
  fwDialogResolver = null;
  if (resolve) resolve(result);
}

async function startCameraUpdate(camId) {
  const cam = listedCameras().find((item) => item.id === camId);
  const fwCam = firmwareFor(cam || { id: camId });
  if (!canUpdateFirmware(cam || { id: camId })) return;
  const current = fwCam.current || "unknown";
  const available = fwCam.available || "the library version";
  const ok = await openFirmwareDialog({
    lead: `New version ${available} is available. Current version is ${current}.`,
    changelog: fwCam.changelog || [],
  });
  if (!ok) return;
  const data = await api(`/api/cameras/${camId}/actions/update-firmware`, {
    method: "POST",
    body: JSON.stringify({ force: false }),
  });
  renderFirmware(data);
  showStatus(data.cameras?.find((item) => item.id === camId)?.job?.message || "Firmware update started.", false);
}

camerasEl.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-update]");
  if (!button) return;
  event.preventDefault();
  event.stopPropagation();
  try {
    await startCameraUpdate(button.dataset.update);
  } catch (err) {
    showStatus(err.message, true);
  }
});

document.getElementById("fw-update-all").addEventListener("click", async () => {
  const outdated = (firmware?.cameras || []).filter(
    (item) => item.update && !jobShowsDone(item) && !jobPending(item.job)
  );
  if (!outdated.length) return;
  if (
    !confirm(
      `Update ${outdated.length} camera${outdated.length === 1 ? "" : "s"} in parallel? They will reboot.`
    )
  ) {
    return;
  }
  try {
    const data = await api("/api/firmware/update", {
      method: "POST",
      body: JSON.stringify({
        camera_ids: outdated.map((item) => item.id),
        force: false,
      }),
    });
    renderFirmware(data);
    showStatus("Firmware updates started in parallel.", false);
  } catch (err) {
    showStatus(err.message, true);
  }
});

document.getElementById("fw-check").addEventListener("click", async () => {
  try {
    const data = await api("/api/firmware/check", {
      method: "POST",
      body: JSON.stringify({ force: true }),
    });
    renderFirmware(data);
  } catch (err) {
    showStatus(err.message, true);
  }
});

document.getElementById("fw-dialog-update").addEventListener("click", () => closeFirmwareDialog(true));
document.getElementById("fw-dialog-cancel").addEventListener("click", () => closeFirmwareDialog(false));
document.getElementById("fw-dialog").addEventListener("click", (event) => {
  if (event.target.id === "fw-dialog") closeFirmwareDialog(false);
});

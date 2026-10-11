import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import streamDeck, {
  SingletonAction,
  type JsonObject,
  type KeyAction,
  type KeyDownEvent,
  type WillAppearEvent,
  type WillDisappearEvent,
  type DidReceiveSettingsEvent,
} from "@elgato/streamdeck";

const API = "http://127.0.0.1:8787";
const FACE_URL = new Map<string, string>();

const DOT = {
  latest: "#3DDC84",
  pending: "#F5A623",
  updating: "#F5A623",
  error: "#E53935",
  offline: "#8A8A8A",
  unknown: "#8A8A8A",
} as const;

type CameraSettings = JsonObject & {
  cameraId?: string;
};

type FirmwareJob = {
  status?: string;
  phase?: string;
  message?: string;
  progress?: number;
  error?: string;
};

type FirmwareCamera = {
  id: string;
  name?: string;
  update?: boolean;
  current?: string;
  available?: string;
  offline?: boolean;
  aliases?: string[];
  indicator?: keyof typeof DOT | string;
  job?: FirmwareJob;
};

async function postCameraAction(cameraId: string, name: string): Promise<void> {
  const response = await fetch(`${API}/api/cameras/${cameraId}/actions/${name}`, {
    method: "POST",
  });
  if (!response.ok) {
    const data = (await response.json().catch(() => ({}))) as {
      detail?: { message?: string } | string;
    };
    const detail = data.detail;
    const message =
      typeof detail === "string" ? detail : detail?.message || response.statusText;
    throw new Error(message);
  }
}

async function fetchFirmware(): Promise<{ cameras: FirmwareCamera[] } | null> {
  try {
    const response = await fetch(`${API}/api/firmware?light=1`, {
      signal: AbortSignal.timeout(8000),
    });
    if (!response.ok) return null;
    return (await response.json()) as { cameras: FirmwareCamera[] };
  } catch (err) {
    streamDeck.logger.warn(`firmware fetch failed: ${String(err)}`);
    return null;
  }
}

function faceFile(indicator: string): string | null {
  const names = [`firmware-${indicator}@2x.png`, `firmware-${indicator}.png`];
  const roots = [join(__dirname, "..", "imgs"), join(__dirname, "imgs"), join(process.cwd(), "imgs")];
  for (const root of roots) {
    for (const name of names) {
      const path = join(root, name);
      if (existsSync(path)) return path;
    }
  }
  return null;
}

function faceDataUrl(indicator: string, progress: number | null): string {
  const color = DOT[indicator as keyof typeof DOT] || DOT.unknown;
  if (progress != null || indicator === "offline") {
    const radius = 46;
    const circ = 2 * Math.PI * radius;
    const pct = progress == null ? 0 : Math.max(0, Math.min(100, progress));
    const dash = (pct / 100) * circ;
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="144" height="144" viewBox="0 0 144 144">
      <rect width="144" height="144" rx="22" fill="#1a1a1a"/>
      <circle cx="72" cy="52" r="${radius}" fill="none" stroke="#2a2a2a" stroke-width="8"/>
      <circle cx="72" cy="52" r="${radius}" fill="none" stroke="${color}" stroke-width="8" stroke-linecap="round" stroke-dasharray="${dash.toFixed(2)} ${circ.toFixed(2)}" transform="rotate(-90 72 52)"/>
      <circle cx="72" cy="52" r="16" fill="${color}"/>
    </svg>`;
    return `data:image/svg+xml;charset=utf8,${encodeURIComponent(svg)}`;
  }
  const cached = FACE_URL.get(indicator);
  if (cached) return cached;
  const path = faceFile(indicator) || faceFile("unknown");
  const url = path
    ? `data:image/png;base64,${readFileSync(path).toString("base64")}`
    : "";
  if (url) FACE_URL.set(indicator, url);
  return url;
}

type CameraInfo = {
  id: string;
  name: string;
  offline?: boolean;
  aliases?: string[];
  txt?: { camera_name?: string; camera_charge?: string; power_status?: string };
  battery?: { percent?: number | null; charging?: boolean };
};

function findCamera<T extends { id: string; aliases?: string[] }>(
  cameras: T[] | undefined,
  cameraId: string,
): T | undefined {
  if (!cameras || !cameraId) return undefined;
  return (
    cameras.find((item) => item.id === cameraId) ||
    cameras.find((item) => (item.aliases || []).includes(cameraId))
  );
}

function retargetCamera(
  action: { id: string; setSettings: (settings: CameraSettings) => Promise<void> },
  map: Map<string, string>,
  requestedId: string,
  canonicalId: string | undefined,
): void {
  if (!canonicalId || canonicalId === requestedId) return;
  map.set(action.id, canonicalId);
  void action.setSettings({ cameraId: canonicalId }).catch((err: unknown) => {
    streamDeck.logger.warn(`could not retarget camera: ${String(err)}`);
  });
}

async function fetchCameras(): Promise<CameraInfo[] | null> {
  try {
    const response = await fetch(`${API}/api/cameras`, {
      signal: AbortSignal.timeout(5000),
    });
    if (!response.ok) return null;
    const data = (await response.json()) as { cameras?: CameraInfo[] };
    return data.cameras || [];
  } catch {
    return null;
  }
}

function cameraLabel(cam: CameraInfo | undefined): string {
  return cam?.txt?.camera_name || cam?.name || "Camera";
}

function batteryFrom(cam: CameraInfo | undefined): { percent: number | null; charging: boolean } {
  const fromApi = cam?.battery?.percent;
  if (fromApi != null && Number.isFinite(Number(fromApi))) {
    return {
      percent: Math.max(0, Math.min(100, Math.round(Number(fromApi)))),
      charging: Boolean(cam?.battery?.charging),
    };
  }
  const raw = cam?.txt?.camera_charge;
  const percent = raw != null && raw !== "" ? Number(raw) : NaN;
  const status = Number(cam?.txt?.power_status);
  return {
    percent: Number.isFinite(percent) ? Math.max(0, Math.min(100, Math.round(percent))) : null,
    charging: Number.isFinite(status) && status !== 0,
  };
}

function batteryColor(percent: number | null, charging: boolean): string {
  if (percent == null) return "#8A8A8A";
  if (charging) return "#4FC3F7";
  if (percent <= 15) return "#E53935";
  if (percent <= 40) return "#F5A623";
  return "#3DDC84";
}

function batterySvg(percent: number | null, charging: boolean, offline = false): string {
  const color = offline ? "#8A8A8A" : batteryColor(percent, charging);
  const radius = 42;
  const circumference = 2 * Math.PI * radius;
  const filled = percent == null ? 0 : Math.max(0, Math.min(100, percent));
  const dash = (circumference * filled) / 100;
  const gap = Math.max(0, circumference - dash);
  const label = percent == null ? "--" : String(percent);
  const fontSize = label.length >= 3 ? 26 : 32;
  const bolt = charging
    ? `<path d="M78 10 L70 32 L76 32 L69 52 L84 28 L77 28 L84 10 Z" fill="#FFD54F"/>`
    : "";
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="144" height="144" viewBox="0 0 144 144">
<rect width="144" height="144" rx="22" fill="#1a1a1a"/>
<circle cx="72" cy="52" r="${radius}" fill="none" stroke="#2a2a2a" stroke-width="9"/>
<circle cx="72" cy="52" r="${radius}" fill="none" stroke="${color}" stroke-width="9" stroke-linecap="round" stroke-dasharray="${dash.toFixed(2)} ${gap.toFixed(2)}" transform="rotate(-90 72 52)"/>
<text x="72" y="58" text-anchor="middle" fill="#f2efe8" font-family="Arial, sans-serif" font-size="${fontSize}" font-weight="700">${label}</text>
${percent == null ? "" : `<text x="72" y="76" text-anchor="middle" fill="#9a958c" font-family="Arial, sans-serif" font-size="11" font-weight="600">%</text>`}
${bolt}
</svg>`;
  return `data:image/svg+xml;charset=utf8,${encodeURIComponent(svg)}`;
}

function shortName(name: string): string {
  const trimmed = name.trim() || "Camera";
  return trimmed.length > 12 ? `${trimmed.slice(0, 11)}...` : trimmed;
}

function statusLine(view: FirmwareCamera | undefined, companionOff: boolean): string {
  if (companionOff) return "Offline";
  if (!view) return "Off";
  const indicator = view.indicator || "unknown";
  const job = view.job;
  const pct = Math.max(0, Math.min(100, Math.round(Number(job?.progress) || 0)));
  if (indicator === "updating") {
    if (job?.phase === "rebooting") return "Reboot";
    if (job?.phase === "flashing") return "Flash";
    if (job?.phase === "uploading") return `${pct}%`;
    if (job?.phase === "preparing" || job?.phase === "queued") return "Prep";
    return `${pct}%`;
  }
  if (indicator === "pending") return "Update";
  if (indicator === "error") return "Failed";
  if (indicator === "latest") return "Latest";
  if (indicator === "offline") return "Off";
  return "Check";
}

class PolledTitle extends SingletonAction<CameraSettings> {
  private timer?: ReturnType<typeof setInterval>;
  private inflight = false;
  private snapshot: CameraInfo[] | null = null;
  protected cameraByContext = new Map<string, string>();
  private lastTitle = new Map<string, string>();

  override async onWillAppear(ev: WillAppearEvent<CameraSettings>): Promise<void> {
    this.cameraByContext.set(ev.action.id, ev.payload.settings.cameraId || "");
    this.lastTitle.delete(ev.action.id);
    this.ensureTimer();
    await this.paint(ev.action, this.snapshot);
    void this.refreshAll();
  }

  override async onWillDisappear(ev: WillDisappearEvent<CameraSettings>): Promise<void> {
    this.cameraByContext.delete(ev.action.id);
    this.lastTitle.delete(ev.action.id);
    setTimeout(() => {
      if (this.actions.length === 0 && this.timer) {
        clearInterval(this.timer);
        this.timer = undefined;
      }
    }, 0);
  }

  override async onDidReceiveSettings(ev: DidReceiveSettingsEvent<CameraSettings>): Promise<void> {
    this.cameraByContext.set(ev.action.id, ev.payload.settings.cameraId || "");
    this.lastTitle.delete(ev.action.id);
    await this.paint(ev.action, this.snapshot);
    void this.refreshAll();
  }

  private ensureTimer(): void {
    if (this.timer) return;
    this.timer = setInterval(() => {
      void this.refreshAll();
    }, 3000);
  }

  protected async refreshAll(): Promise<void> {
    if (this.inflight) return;
    this.inflight = true;
    try {
      const cameras = await fetchCameras();
      if (cameras) this.snapshot = cameras;
      const face = cameras ?? this.snapshot;
      for (const action of this.actions) {
        try {
          await this.paint(action, face);
        } catch (err) {
          streamDeck.logger.warn(`camera title paint failed: ${String(err)}`);
        }
      }
    } finally {
      this.inflight = false;
    }
  }

  private async paint(
    action: KeyAction<CameraSettings> | WillAppearEvent<CameraSettings>["action"],
    cameras: CameraInfo[] | null | undefined,
  ): Promise<void> {
    if (!action.isKey()) return;
    const cameraId = this.cameraByContext.get(action.id) ?? "";
    if (!cameraId) {
      await this.applyTitle(action, "Pick camera");
      return;
    }
    if (!cameras) {
      await this.applyTitle(action, "Camera\n...");
      return;
    }
    const cam = findCamera(cameras, cameraId);
    retargetCamera(action, this.cameraByContext, cameraId, cam?.id);
    if (!cam) {
      await this.applyTitle(action, "Camera\nOff");
      return;
    }
    const name = shortName(cameraLabel(cam));
    await this.applyTitle(action, cam.offline ? `${name}\nOff` : name);
  }

  private async applyTitle(action: KeyAction<CameraSettings>, title: string): Promise<void> {
    if (this.lastTitle.get(action.id) === title) return;
    this.lastTitle.set(action.id, title);
    await action.setTitle(title);
  }
}

class PowerOff extends PolledTitle {
  manifestId = "com.mevo-companion.shutdown";

  override async onKeyDown(ev: KeyDownEvent<CameraSettings>): Promise<void> {
    const cameraId = ev.payload.settings.cameraId;
    if (!cameraId) {
      await ev.action.showAlert();
      return;
    }
    try {
      await postCameraAction(cameraId, "shutdown");
      await ev.action.showOk();
      await this.refreshAll();
    } catch {
      await ev.action.showAlert();
    }
  }
}

class Reboot extends PolledTitle {
  manifestId = "com.mevo-companion.reboot";

  override async onKeyDown(ev: KeyDownEvent<CameraSettings>): Promise<void> {
    const cameraId = ev.payload.settings.cameraId;
    if (!cameraId) {
      await ev.action.showAlert();
      return;
    }
    try {
      await postCameraAction(cameraId, "reboot");
      await ev.action.showOk();
      await this.refreshAll();
    } catch {
      await ev.action.showAlert();
    }
  }
}

class FirmwareUpdate extends SingletonAction<CameraSettings> {
  manifestId = "com.mevo-companion.firmware";
  private timer?: ReturnType<typeof setInterval>;
  private lastFace = new Map<string, string>();
  private starting = new Set<string>();
  private inflight = false;
  private snapshot: { cameras: FirmwareCamera[] } | null = null;
  private cameraByContext = new Map<string, string>();

  override async onWillAppear(ev: WillAppearEvent<CameraSettings>): Promise<void> {
    const cameraId = ev.payload.settings.cameraId || "";
    this.cameraByContext.set(ev.action.id, cameraId);
    this.lastFace.delete(ev.action.id);
    streamDeck.logger.info(`firmware appear ${ev.action.id} camera=${cameraId || "(none)"}`);
    this.ensureTimer();
    await this.paint(ev.action, this.snapshot);
    void this.refreshAll();
  }

  override async onWillDisappear(ev: WillDisappearEvent<CameraSettings>): Promise<void> {
    this.cameraByContext.delete(ev.action.id);
    this.lastFace.delete(ev.action.id);
    setTimeout(() => {
      if (this.actions.length === 0 && this.timer) {
        clearInterval(this.timer);
        this.timer = undefined;
      }
    }, 0);
  }

  override async onDidReceiveSettings(ev: DidReceiveSettingsEvent<CameraSettings>): Promise<void> {
    this.cameraByContext.set(ev.action.id, ev.payload.settings.cameraId || "");
    this.lastFace.delete(ev.action.id);
    await this.paint(ev.action, this.snapshot);
    void this.refreshAll();
  }

  override async onKeyDown(ev: KeyDownEvent<CameraSettings>): Promise<void> {
    const cameraId = ev.payload.settings.cameraId;
    this.cameraByContext.set(ev.action.id, cameraId || "");
    if (!cameraId) {
      await ev.action.showAlert();
      return;
    }
    if (this.starting.has(cameraId)) return;
    const data = await fetchFirmware();
    if (data) this.snapshot = data;
    if (!data) {
      await ev.action.showAlert();
      return;
    }
    const view = findCamera(data.cameras, cameraId);
    const indicator = view?.indicator;
    if (view?.offline || indicator === "offline") {
      await this.paint(ev.action, data);
      return;
    }
    if (indicator === "updating") {
      await this.paint(ev.action, data);
      return;
    }
    if (indicator === "latest") {
      await ev.action.showOk();
      return;
    }
    if (indicator !== "pending" && indicator !== "error") {
      await ev.action.showAlert();
      return;
    }
    this.starting.add(cameraId);
    try {
      await postCameraAction(cameraId, "update-firmware");
      this.lastFace.delete(ev.action.id);
      await this.refreshAll();
    } catch {
      await ev.action.showAlert();
    } finally {
      this.starting.delete(cameraId);
    }
  }

  private ensureTimer(): void {
    if (this.timer) return;
    this.timer = setInterval(() => {
      void this.refreshAll();
    }, 2000);
  }

  private async refreshAll(): Promise<void> {
    if (this.inflight) return;
    this.inflight = true;
    try {
      const data = await fetchFirmware();
      if (data) this.snapshot = data;
      streamDeck.logger.debug(
        `firmware poll ${data ? `${data.cameras.length} cameras` : "no data"}; actions=${this.actions.length}`,
      );
      const face = data ?? this.snapshot;
      for (const action of this.actions) {
        try {
          await this.paint(action, face);
        } catch (err) {
          streamDeck.logger.warn(`firmware paint failed: ${String(err)}`);
        }
      }
    } catch (err) {
      streamDeck.logger.warn(`firmware refresh failed: ${String(err)}`);
    } finally {
      this.inflight = false;
    }
  }

  private async paint(
    action: KeyAction<CameraSettings> | WillAppearEvent<CameraSettings>["action"],
    cached: { cameras: FirmwareCamera[] } | null | undefined,
  ): Promise<void> {
    if (!action.isKey()) return;
    const cameraId = this.cameraByContext.get(action.id) ?? "";
    if (!cameraId) {
      await this.applyFace(action, "Pick camera", "unknown", null);
      return;
    }
    if (!cached) {
      await this.applyFace(action, "Camera\n...", "unknown", null);
      return;
    }
    const view = findCamera(cached.cameras, cameraId);
    retargetCamera(action, this.cameraByContext, cameraId, view?.id);
    if (!view) {
      await this.applyFace(action, "Camera\nOff", "offline", null);
      return;
    }
    const name = view?.name || "Camera";
    const line = statusLine(view, false);
    const indicator = (view?.indicator && view.indicator in DOT ? view.indicator : "unknown") as keyof typeof DOT;
    const updating = indicator === "updating";
    const progress = updating ? Math.max(0, Math.min(100, Math.round(Number(view?.job?.progress) || 0))) : null;
    await this.applyFace(action, `${shortName(name)}\n${line}`, indicator, progress);
  }

  private async applyFace(
    action: KeyAction<CameraSettings>,
    title: string,
    indicator: keyof typeof DOT,
    progress: number | null,
  ): Promise<void> {
    const key = `${title}|${indicator}|${progress ?? ""}`;
    if (this.lastFace.get(action.id) === key) return;
    this.lastFace.set(action.id, key);
    const image = faceDataUrl(indicator, progress);
    streamDeck.logger.info(`firmware paint ${action.id} ${indicator} ${title.replace(/\n/g, " / ")} bytes=${image.length}`);
    if (image) {
      await action.setImage(image);
    } else {
      await action.setImage(`imgs/firmware-${indicator}`);
    }
    await action.setTitle(title);
  }
}

class Battery extends SingletonAction<CameraSettings> {
  manifestId = "com.mevo-companion.battery";
  private timer?: ReturnType<typeof setInterval>;
  private lastFace = new Map<string, string>();
  private inflight = false;
  private snapshot: CameraInfo[] | null = null;
  private cameraByContext = new Map<string, string>();

  override async onWillAppear(ev: WillAppearEvent<CameraSettings>): Promise<void> {
    this.cameraByContext.set(ev.action.id, ev.payload.settings.cameraId || "");
    this.ensureTimer();
    await this.paint(ev.action, this.snapshot);
    void this.refreshAll();
  }

  override async onWillDisappear(ev: WillDisappearEvent<CameraSettings>): Promise<void> {
    this.cameraByContext.delete(ev.action.id);
    this.lastFace.delete(ev.action.id);
    setTimeout(() => {
      if (this.actions.length === 0 && this.timer) {
        clearInterval(this.timer);
        this.timer = undefined;
      }
    }, 0);
  }

  override async onDidReceiveSettings(ev: DidReceiveSettingsEvent<CameraSettings>): Promise<void> {
    this.cameraByContext.set(ev.action.id, ev.payload.settings.cameraId || "");
    this.lastFace.delete(ev.action.id);
    await this.paint(ev.action, this.snapshot);
    void this.refreshAll();
  }

  override async onKeyDown(ev: KeyDownEvent<CameraSettings>): Promise<void> {
    this.cameraByContext.set(ev.action.id, ev.payload.settings.cameraId || "");
    await this.refreshAll();
  }

  private ensureTimer(): void {
    if (this.timer) return;
    this.timer = setInterval(() => {
      void this.refreshAll();
    }, 3000);
  }

  private async refreshAll(): Promise<void> {
    if (this.inflight) return;
    this.inflight = true;
    try {
      const cameras = await fetchCameras();
      if (cameras) this.snapshot = cameras;
      const face = cameras ?? this.snapshot;
      for (const action of this.actions) {
        try {
          await this.paint(action, face);
        } catch (err) {
          streamDeck.logger.warn(`battery paint failed: ${String(err)}`);
        }
      }
    } catch (err) {
      streamDeck.logger.warn(`battery refresh failed: ${String(err)}`);
    } finally {
      this.inflight = false;
    }
  }

  private async paint(
    action: KeyAction<CameraSettings> | WillAppearEvent<CameraSettings>["action"],
    cameras: CameraInfo[] | null | undefined,
  ): Promise<void> {
    if (!action.isKey()) return;
    const cameraId = this.cameraByContext.get(action.id) ?? "";
    if (!cameraId) {
      await this.applyFace(action, "Pick camera", null, false);
      return;
    }
    if (!cameras) {
      await this.applyFace(action, "Camera\n...", null, false);
      return;
    }
    const cam = findCamera(cameras, cameraId);
    retargetCamera(action, this.cameraByContext, cameraId, cam?.id);
    if (!cam) {
      await this.applyFace(action, "Camera\nOff", null, false, true);
      return;
    }
    const { percent, charging } = batteryFrom(cam);
    const name = shortName(cameraLabel(cam));
    if (cam.offline) {
      const line = percent == null ? "Off" : `${percent}% off`;
      await this.applyFace(action, `${name}\n${line}`, percent, false, true);
      return;
    }
    const line = percent == null ? "No data" : charging ? `${percent}% AC` : `${percent}%`;
    await this.applyFace(action, `${name}\n${line}`, percent, charging, false);
  }

  private async applyFace(
    action: KeyAction<CameraSettings>,
    title: string,
    percent: number | null,
    charging: boolean,
    offline = false,
  ): Promise<void> {
    const key = `${title}|${percent ?? ""}|${charging ? 1 : 0}|${offline ? 1 : 0}`;
    if (this.lastFace.get(action.id) === key) return;
    this.lastFace.set(action.id, key);
    await action.setImage(batterySvg(percent, charging, offline));
    await action.setTitle(title);
  }
}

streamDeck.logger.info("Mevo Companion plugin starting");
streamDeck.actions.registerAction(new PowerOff());
streamDeck.actions.registerAction(new Reboot());
streamDeck.actions.registerAction(new FirmwareUpdate());
streamDeck.actions.registerAction(new Battery());
streamDeck.connect();

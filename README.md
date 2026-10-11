# Mevo Companion

Desktop companion for Mevo Start, Core, and Plus cameras. It finds cameras on the local network, shows a web UI, and exposes a local API used by the included Stream Deck plugin (power, reboot, firmware, and battery).

The app listens on `http://127.0.0.1:8787`.

## Requirements

- Windows 10 or later
- Python 3.11 or newer, with the `py` launcher
- The PC and the cameras on the same LAN
- Optional: [NDI Runtime](https://ndi.video/tools/) for live preview
- Optional: Elgato Stream Deck app 6.9 or newer
- Optional: Node.js 20 or newer, only if you rebuild the Stream Deck plugin

## Install

From this folder:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

That installs the app into `.venv` and registers the `mevo_companion` module.

## Launch

Double-click `start-companion-api.bat`, or run:

```powershell
.\.venv\Scripts\python.exe -m mevo_companion
```

The API starts at [http://127.0.0.1:8787](http://127.0.0.1:8787) and a Mevo icon appears in the system tray. Right-click that icon and choose **Open web UI**. Choosing **Quit** stops the app.

If a copy is already running, the launcher opens the existing instance instead of starting a second one.

Other launch modes:

```powershell
.\.venv\Scripts\python.exe -m mevo_companion --window
.\.venv\Scripts\python.exe -m mevo_companion --no-window
.\.venv\Scripts\python.exe -m mevo_companion --open-ui
```

- `--window` opens the desktop window instead of the tray.
- `--no-window` runs the API only. Press Ctrl+C to stop it.
- `--open-ui` opens the web UI in the browser after startup.

### Start at sign-in

```powershell
.\install-startup.bat
```

That is the same as:

```powershell
.\.venv\Scripts\python.exe -m mevo_companion --install-startup
```

Windows will start the tray app at sign-in. Remove it with:

```powershell
.\.venv\Scripts\python.exe -m mevo_companion --uninstall-startup
```

## Stream Deck

Start Mevo Companion first. The plugin calls the local API and does nothing useful while the app is stopped.

Copy the plugin into the Stream Deck plugins folder:

```powershell
$dest = Join-Path $env:APPDATA "Elgato\StreamDeck\Plugins\com.mevo-companion.sdPlugin"
Copy-Item -Recurse -Force .\streamdeck\com.mevo-companion.sdPlugin $dest
```

Quit and reopen the Stream Deck app. The **Mevo Companion** category then has:

| Action | What it does |
| --- | --- |
| Power off | Shut down the selected camera |
| Reboot | Reboot the selected camera |
| Firmware update | Shows whether an update is available; press to start it |
| Battery | Live battery level for the selected camera |

Pick the camera in each key’s property inspector.

Rebuild the plugin only after changing `streamdeck/src/plugin.ts`:

```powershell
cd streamdeck
npm install
npm run build
```

Then copy `com.mevo-companion.sdPlugin` into the plugins folder again.

## Firmware library

The companion keeps a local firmware library in `firmware-store` (created on first use, not part of the git checkout). Check or refresh it with:

```powershell
.\.venv\Scripts\python.exe -m mevo_companion.firmware status
.\.venv\Scripts\python.exe -m mevo_companion.firmware check
```

`install-firmware-task.bat` registers a scheduled task that checks for new firmware. `check-firmware.bat` runs one check and appends to `firmware-store\check.log`.

## Network

Cameras advertise `_mevo-cameraman._tcp` and `_ls-cameraman._tcp`. If discovery is empty, add a camera by IP in the web UI. The phone app and this companion should not control the same camera at the same time.

Live preview uses the camera’s NDI output. Turn NDI on in the camera settings, and install the NDI Runtime on this PC if preview reports that it is unavailable.

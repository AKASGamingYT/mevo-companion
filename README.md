# Mevo Companion

Mevo Companion is a small desktop app for Mevo Start, Core, and Plus cameras. It finds the cameras on your network, opens a control panel on this computer, and can drive them from an Elgato Stream Deck.

Power, reboot, firmware, battery, and a live preview all stay on your own machine. Nothing about the cameras is sent to a cloud service.

## Download

Grab the installer for your computer from the [latest release](https://github.com/AKASGamingYT/mevo-companion/releases/latest).

| Your computer | File to download |
| --- | --- |
| Windows 10 or later, 64-bit | [MevoCompanion-Setup-x64.exe](https://github.com/AKASGamingYT/mevo-companion/releases/latest/download/MevoCompanion-Setup-x64.exe) |
| Mac with Apple silicon (M1 or newer) | [MevoCompanion-macOS.dmg](https://github.com/AKASGamingYT/mevo-companion/releases/latest/download/MevoCompanion-macOS.dmg) |
| Linux, Debian or Ubuntu, 64-bit | [MevoCompanion-linux-amd64.deb](https://github.com/AKASGamingYT/mevo-companion/releases/latest/download/MevoCompanion-linux-amd64.deb) |

Each release also includes a checksum file, `SHA256SUMS.txt`, plus portable builds if you would rather not use an installer.

## Install

The computer and the cameras need to be on the same network.

### Windows

1. Open `MevoCompanion-Setup-x64.exe`.
2. If Windows SmartScreen says the app is unrecognized, choose **More info**, then **Run anyway**. The installer is not signed with a paid certificate.
3. Leave **Install the Stream Deck plugin** selected if you use a Stream Deck.
4. Finish the installer and launch Mevo Companion.

A Mevo icon appears in the system tray, next to the clock. Right-click it and choose **Open web UI**. The control panel is at [http://127.0.0.1:8787](http://127.0.0.1:8787).

Mevo Companion also starts on its own the next time you sign in to Windows. To turn that off, open a terminal in the install folder and run:

```powershell
MevoCompanion.exe --uninstall-startup
```

The usual install folder is `%LOCALAPPDATA%\Programs\Mevo Companion`.

### Mac

1. Open `MevoCompanion-macOS.dmg`.
2. Drag **Mevo Companion** onto **Applications**.
3. The first time you open it, macOS may block it. Control-click the app, choose **Open**, then choose **Open** again.

The app stays in the menu bar. Choose **Open web UI** from that icon. It starts again the next time you sign in. This disk image runs on Apple silicon. To turn sign-in launch off:

```bash
"/Applications/Mevo Companion.app/Contents/MacOS/MevoCompanion" --uninstall-startup
```

This build is for Apple silicon. Intel Macs are not included in the release.

### Linux

On Debian or Ubuntu:

```bash
sudo dpkg -i MevoCompanion-linux-amd64.deb
```

Open **Mevo Companion** from the app menu. The control panel opens in your browser. The app starts again the next time you sign in. Quit from the app menu with **Quit Mevo Companion**, or from a terminal:

```bash
mevo-companion --quit
```

Turn off the sign-in launch with `mevo-companion --uninstall-startup`.

A portable copy is in `MevoCompanion-linux-x64.tar.gz` if you do not want a package. Unpack it and run `MevoCompanion/MevoCompanion`.

On Linux the app opens in your browser. Windows and Mac use a system tray icon, and can also open a desktop window with `--window`.

## Use the control panel

1. Turn the camera on and give it a moment to join Wi-Fi.
2. Choose **Scan LAN**.
3. Select the camera, then **Connect**.

If the list stays empty, add the camera by its IP address. The port is usually `38000`.

Close the Mevo phone app while Mevo Companion is connected to a camera. The camera talks to one controller at a time.

From the camera’s page you can connect, ping, reboot, shut down, change settings, and update firmware.

## Stream Deck

The plugin adds four keys: **Power off**, **Reboot**, **Firmware update**, and **Battery**. Pick the camera in each key’s property inspector.

On Windows, the installer copies the plugin into the Stream Deck plugins folder when that task is left on. Quit the Stream Deck app and open it again so the **Mevo Companion** category shows up.

You can also install the plugin yourself from [MevoCompanion-StreamDeck.zip](https://github.com/AKASGamingYT/mevo-companion/releases/latest/download/MevoCompanion-StreamDeck.zip). Unzip it so this folder exists:

```text
%APPDATA%\Elgato\StreamDeck\Plugins\com.mevo-companion.sdPlugin
```

The plugin needs Stream Deck 6.9 or newer, and it needs Mevo Companion to be running. The plugin itself is for Windows.

## Firmware

Mevo Companion keeps a firmware library on this computer and checks for a newer version once after you sign in, when the network is up. In the control panel, **Check for updates** looks again, and **Update firmware** sends the saved file to the selected camera.

The library is stored in:

| System | Folder |
| --- | --- |
| Windows | `%LOCALAPPDATA%\Mevo Companion\firmware-store` |
| Mac | `~/Library/Application Support/Mevo Companion/firmware-store` |
| Linux | `~/.local/share/mevo-companion/firmware-store` |

## Live preview

Preview uses the camera’s NDI output. Turn NDI on in the camera’s own settings. The picture in Mevo Companion is a low-resolution preview, so it does not replace your program output.

NDI® is a trademark of Vizrt NDI AB. The Windows and Mac downloads include the NDI libraries used for that preview. The Linux package is the control panel; preview is in the Windows and Mac builds.

## If something looks wrong

- **No cameras.** Confirm the computer and the camera are on the same network, then add the camera by IP.
- **The camera will not connect.** Close the Mevo phone app and try Connect again.
- **The page will not load.** Mevo Companion has to be running. Open it from the Start menu, the Applications folder, or the Linux app menu, then go to [http://127.0.0.1:8787](http://127.0.0.1:8787).
- **A second copy does not start.** That is expected. The one already running is the one to use. Opening it again brings that copy forward.
- **Preview stays blank.** Turn NDI on in the camera settings. If the panel says the NDI runtime is missing, install the [NDI Tools](https://ndi.video/tools/) runtime on this computer.

## Build from source

You need Python 3.11 or newer. From this folder:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m mevo_companion
```

On macOS or Linux, use `python3` instead of `py -3.11`, and run `.venv/bin/python -m mevo_companion`.

`--window` opens a desktop window. `--no-window` runs the local API until you press Ctrl+C. `--open-ui` opens the control panel in your browser. `--install-startup` and `--uninstall-startup` turn sign-in launch on or off.

The Stream Deck plugin is already built. Rebuild it only after changing `streamdeck/src/plugin.ts`:

```powershell
cd streamdeck
npm install
npm run build
```

## How releases are built

A version tag such as `v0.1.0` runs the [Release workflow](https://github.com/AKASGamingYT/mevo-companion/actions). That workflow builds an installer on Windows, macOS, and Linux, then publishes the files on the Releases page.

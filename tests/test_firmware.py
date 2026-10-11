import hashlib
import json
import os
import tempfile
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mevo_companion.firmware.apkmirror import (
    extract_final_download_url,
    extract_redirect_download_url,
    extract_variants,
    extract_versions,
    is_stable,
    pick_variant,
)
from mevo_companion.firmware.archive import (
    camera_firmware_view,
    family_from_camera,
    ingest_apk,
    version_newer,
    walk_firmware_json,
)
from mevo_companion.firmware.check import should_skip_check


VERSIONS_HTML = """
<div>
<h5 class="appRowTitle wrapText marginZero">
<a href="/apk/logitech-europe-s-a/mevo-the-live-event-camera/mevo-the-live-event-camera-5-2-28-release/" class="fontBlack">Logitech Mevo 5.2.28</a>
</h5>
<h5 class="appRowTitle wrapText marginZero">
<a href="/apk/logitech-europe-s-a/mevo-the-live-event-camera/mevo-the-live-event-camera-5-2-28-beta-release/" class="fontBlack">Logitech Mevo 5.2.28 beta</a>
</h5>
</div>
"""

VARIANTS_HTML = """
<div class="table topmargin variants-table">
<div class="table-row header-row"><div class="table-cell">Variant</div></div>
<div class="table-row">
  <div class="table-cell">
    <a class="accent_color" href="/apk/logitech/mevo/5-2-28/download/"> 5.2.28 </a>
    <span class="colorLightBlack">2155</span>
    <span class="apkm-badge success">BUNDLE</span>
  </div>
  <div class="table-cell">universal</div>
  <div class="table-cell">Android 8.0+</div>
  <div class="table-cell">120-640dpi</div>
</div>
</div>
<a name="all_releases"></a>
"""

DOWNLOAD_HTML = '<a class="accent_color btn btn-flat downloadButton" href="/apk/foo/download/?key=abc">Download APK</a>'
FINAL_HTML = '<a id="download-link" href="/wp-content/themes/APKMirror/download.php?id=1&key=abc">here</a>'

FIRMWARE_JSON = {
    "version": 1,
    "protocol": {
        "47": {
            "mevo_type": {
                "004": {
                    "min_required_version": "2.4.21",
                    "url": "mevo_fw_start.tar.xz",
                    "version": "5.3.9",
                    "signature": "8935a781",
                    "release_notes_url": "release_notes_mevo_start.txt",
                },
                "001": {
                    "min_required_version": "1.18.1",
                    "url": "mevo_fw_plus.tar.xz",
                    "version": "4.5.5",
                    "sha512": None,
                    "release_notes_url": "release_notes_mevo_plus.txt",
                },
            }
        }
    },
}


class APKMirrorParseTests(unittest.TestCase):
    def test_extract_versions_skips_beta(self) -> None:
        versions = extract_versions(VERSIONS_HTML)
        self.assertEqual(versions[0].version, "5.2.28")
        self.assertTrue(is_stable(versions[0]))
        self.assertFalse(is_stable(versions[1]))

    def test_extract_bundle_variant(self) -> None:
        variants = extract_variants(VARIANTS_HTML)
        chosen = pick_variant(variants)
        self.assertEqual(chosen.type, "bundle")
        self.assertEqual(chosen.arch, "universal")
        self.assertEqual(chosen.dpi, "120-640dpi")
        self.assertEqual(chosen.version_code, "2155")

    def test_download_urls(self) -> None:
        self.assertIn("download/?key=abc", extract_redirect_download_url(DOWNLOAD_HTML))
        self.assertIn("download.php?id=1", extract_final_download_url(FINAL_HTML))


class FirmwareArchiveTests(unittest.TestCase):
    def test_walk_and_family(self) -> None:
        rows = walk_firmware_json(FIRMWARE_JSON)
        families = {row["family"] for row in rows}
        self.assertEqual(families, {"start", "plus"})
        self.assertTrue(version_newer("5.3.9", "5.2.23"))
        self.assertFalse(version_newer("5.2.23", "5.3.9"))
        camera = {"txt": {"model": "Mevo Start", "fw_version": "5.2.23"}, "name": "Balcony"}
        self.assertEqual(family_from_camera(camera), "start")
        view = camera_firmware_view(camera, {"images": [{"family": "start", "version": "5.3.9"}]})
        self.assertTrue(view["update"])
        start = next(row for row in rows if row["family"] == "start")
        self.assertEqual(start["signature"], "8935a781")

    def test_ingest_apk(self) -> None:
        blob = b"fake-firmware-blob"
        digest = hashlib.sha512(blob).hexdigest()
        payload = json.loads(json.dumps(FIRMWARE_JSON))
        payload["protocol"]["47"]["mevo_type"]["004"]["sha512"] = digest
        payload["protocol"]["47"]["mevo_type"]["001"]["sha512"] = digest
        payload["protocol"]["47"]["mevo_type"]["001"]["url"] = "mevo_fw_start.tar.xz"
        with tempfile.TemporaryDirectory() as tmp:
            previous = os.environ.get("MEVO_COMPANION_STORE")
            os.environ["MEVO_COMPANION_STORE"] = tmp
            self.addCleanup(self._restore_store, previous)
            apk = Path(tmp) / "Mevo_test.apk"
            with zipfile.ZipFile(apk, "w") as archive:
                archive.writestr("assets/firmware/firmware.json", json.dumps(payload))
                archive.writestr("assets/firmware/mevo_fw_start.tar.xz", blob)
                archive.writestr("assets/firmware/release_notes_mevo_start.txt", "Start 5.3.9 notes")
                archive.writestr("assets/firmware/release_notes_mevo_plus.txt", "Plus notes")
            result = ingest_apk(apk, app_version="5.2.28", source="test")
            self.assertEqual(result["copied"], 1)
            start = next(item for item in result["images"] if item["family"] == "start")
            self.assertEqual(start["version"], "5.3.9")
            self.assertTrue((Path(tmp) / start["path"]).exists())
            again = ingest_apk(apk, app_version="5.2.28", source="test")
            self.assertEqual(again["copied"], 0)

    def _restore_store(self, previous: str | None) -> None:
        if previous is None:
            os.environ.pop("MEVO_COMPANION_STORE", None)
        else:
            os.environ["MEVO_COMPANION_STORE"] = previous


class SignInSkipTests(unittest.TestCase):
    tz = timezone(timedelta(hours=-5))

    def test_skips_only_after_this_sign_in(self) -> None:
        logon = datetime(2026, 10, 11, 9, 25, tzinfo=self.tz)
        self.assertFalse(should_skip_check(None, logon))
        earlier = datetime(2026, 10, 10, 18, 0, tzinfo=self.tz).isoformat()
        self.assertFalse(should_skip_check(earlier, logon))
        self.assertTrue(should_skip_check(logon.isoformat(), logon))
        later = datetime(2026, 10, 11, 9, 40, tzinfo=self.tz).isoformat()
        self.assertTrue(should_skip_check(later, logon))

    def test_missing_logon_time_does_not_skip(self) -> None:
        from mevo_companion.firmware import check

        last = datetime(2026, 10, 11, 9, 40, tzinfo=self.tz).isoformat()
        orig = check.current_logon_time
        check.current_logon_time = lambda: None  # type: ignore[method-assign]
        try:
            self.assertFalse(should_skip_check(last))
        finally:
            check.current_logon_time = orig  # type: ignore[method-assign]


class FirmwarePushTests(unittest.TestCase):
    def test_progress_body_is_httpx_content(self) -> None:
        import httpx
        from mevo_companion.firmware.push import _ProgressBody

        seen: list[tuple[int, int]] = []
        body = _ProgressBody(b"hello world", lambda sent, total: seen.append((sent, total)))
        request = httpx.Request("PUT", "http://192.168.1.111/upload/mevo_fw.tar.xz", content=body)
        self.assertEqual(request.headers["content-length"], "11")
        self.assertEqual(b"".join(request.stream), b"hello world")
        self.assertEqual(seen[-1], (11, 11))

    def test_resolve_upload_url(self) -> None:
        from mevo_companion.firmware.push import resolve_upload_url

        self.assertEqual(
            resolve_upload_url("", "192.168.1.234"),
            "http://192.168.1.234/upload/mevo_fw.tar.xz",
        )
        self.assertEqual(
            resolve_upload_url("file:///firmware/", "192.168.1.234"),
            "http://192.168.1.234/upload/mevo_fw.tar.xz",
        )
        self.assertEqual(
            resolve_upload_url("/upload/mevo_fw.tar.xz", "192.168.1.234"),
            "http://192.168.1.234/upload/mevo_fw.tar.xz",
        )
        self.assertEqual(
            resolve_upload_url("http://192.168.1.234:8080/upload/mevo_fw.tar.xz", "192.168.1.234"),
            "http://192.168.1.234:8080/upload/mevo_fw.tar.xz",
        )

    def test_receive_firmware_wire_name(self) -> None:
        from mevo_companion.protocol import wire_command

        self.assertEqual(wire_command("ReceiveFirmware"), "receive_firmware")
        self.assertEqual(wire_command("UpdateFirmware"), "update_firmware")

    def test_friendly_progress_matches_official_copy(self) -> None:
        from mevo_companion.firmware.push import friendly_progress

        self.assertEqual(
            friendly_progress("PUT http://192.168.1.60/upload/mevo_fw.tar.xz (12 bytes)")[0],
            "Uploading Mevo firmware. Please wait.",
        )
        self.assertEqual(
            friendly_progress("firmware applied; camera is flashing")[0],
            "Updating Mevo firmware. Please wait.",
        )
        self.assertEqual(friendly_progress("Asking camera to receive firmware")[1], "preparing")


class ChangelogFormatTests(unittest.TestCase):
    SAMPLE = """August 12, 2026(Mevo Start 5.3.9)
NDI library updated to version 6.3.2
SRT library updated to version 1.5.6


June 25, 2026(Mevo Start FW 5.3.6)
New: white balance tint option
New: ISO priority exposure mode


February 11, 2026(Mevo Start FW 5.2.23)
Stability improvements
"""

    def test_parse_matches_official_app_layout(self) -> None:
        from mevo_companion.firmware.archive import parse_changelog

        entries = parse_changelog(self.SAMPLE)
        self.assertEqual(entries[0]["heading"], "August 12, 2026 (Start FW 5.3.9)")
        self.assertEqual(
            entries[0]["items"],
            [
                "NDI library updated to version 6.3.2",
                "SRT library updated to version 1.5.6",
            ],
        )
        self.assertEqual(entries[1]["heading"], "June 25, 2026 (Start FW 5.3.6)")
        self.assertEqual(entries[0]["versions"]["start"], "5.3.9")

    def test_gif_heading_and_dashes(self) -> None:
        from mevo_companion.firmware.archive import parse_changelog

        entries = parse_changelog(
            "March 26, 2021 (Start FW 2.4.7, Plus FW 2.4.4)\n- Bug fixes\n"
        )
        self.assertEqual(
            entries[0]["heading"],
            "March 26, 2021 (Start FW 2.4.7, Plus FW 2.4.4)",
        )
        self.assertEqual(entries[0]["items"], ["Bug fixes"])
        self.assertEqual(entries[0]["versions"]["plus"], "2.4.4")

    def test_changelog_since_current_version(self) -> None:
        from mevo_companion.firmware.archive import changelog_since, parse_changelog

        entries = parse_changelog(self.SAMPLE)
        newer = changelog_since(entries, "5.2.23", "start")
        self.assertEqual([item["versions"]["start"] for item in newer], ["5.3.9", "5.3.6"])
        self.assertEqual(changelog_since(entries, "5.3.9", "start"), [])

    def test_public_job_status_after_flash(self) -> None:
        from datetime import datetime, timedelta, timezone

        from mevo_companion.firmware.check import _public_job

        recent = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        job = {
            "status": "done",
            "to_version": "5.3.9",
            "message": "Updated to 5.3.9. Camera is rebooting.",
            "finished_at": recent,
        }
        done = _public_job(job, online=True, current="5.3.9")
        self.assertEqual(done["message"], "Updated to 5.3.9")
        self.assertEqual(done["phase"], "complete")
        rebooting = _public_job(job, online=False, current=None)
        self.assertEqual(rebooting["phase"], "rebooting")
        self.assertIn("Waiting for camera to return", rebooting["message"])
        self.assertEqual(rebooting["progress"], 92)
        powered_off = _public_job(job, online=False, current=None, powered_off=True)
        self.assertEqual(powered_off["phase"], "complete")
        stale_offline = _public_job(
            {**job, "finished_at": "2026-01-01T00:00:00+00:00"},
            online=False,
            current=None,
        )
        self.assertEqual(stale_offline["phase"], "complete")

    def test_job_percent_uses_upload_bytes(self) -> None:
        from mevo_companion.firmware.push import camera_indicator, job_percent

        self.assertEqual(job_percent({"phase": "preparing", "status": "updating"}), 8)
        self.assertEqual(
            job_percent(
                {
                    "phase": "uploading",
                    "status": "updating",
                    "bytes_sent": 50,
                    "bytes_total": 100,
                }
            ),
            47,
        )
        self.assertEqual(job_percent({"phase": "complete", "status": "done"}), 100)
        self.assertEqual(
            camera_indicator({"update": True, "available": "5.3.9"}),
            "pending",
        )
        self.assertEqual(
            camera_indicator({"update": True, "offline": True, "available": "5.3.9"}),
            "offline",
        )
        self.assertEqual(
            camera_indicator(
                {
                    "offline": True,
                    "powered_off": True,
                    "job": {"status": "done", "phase": "rebooting"},
                }
            ),
            "offline",
        )
        self.assertEqual(
            camera_indicator(
                {"offline": True, "job": {"status": "done", "phase": "rebooting"}}
            ),
            "updating",
        )
        self.assertEqual(
            camera_indicator({"update": False, "available": "5.3.9"}),
            "latest",
        )
        self.assertEqual(
            camera_indicator(
                {"update": False, "job": {"status": "updating", "phase": "uploading"}}
            ),
            "updating",
        )


class ScheduleCacheTests(unittest.TestCase):
    def test_schedule_info_does_not_shell_out(self) -> None:
        from mevo_companion.firmware import schedule

        def boom(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("schedule status must not run schtasks")

        orig = schedule._run_schtasks
        schedule._run_schtasks = boom  # type: ignore[method-assign]
        try:
            info = schedule.schedule_info(force=True)
        finally:
            schedule._run_schtasks = orig  # type: ignore[method-assign]
        self.assertEqual(
            info["triggers"],
            ["Once after sign-in, when the network is connected"],
        )
        self.assertFalse(info["weekly"])

    def test_public_status_light_skips_schedule(self) -> None:
        import tempfile

        from mevo_companion.firmware import check

        def boom(**_kwargs: object) -> dict[str, object]:
            raise AssertionError("schedule_info should not run for light status")

        orig = check.schedule_info
        previous = os.environ.get("MEVO_COMPANION_STORE")
        check.schedule_info = boom  # type: ignore[method-assign]
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["MEVO_COMPANION_STORE"] = tmp
                status = check.public_status(None, light=True)
            self.assertIsNone(status["schedule"])
        finally:
            check.schedule_info = orig  # type: ignore[method-assign]
            if previous is None:
                os.environ.pop("MEVO_COMPANION_STORE", None)
            else:
                os.environ["MEVO_COMPANION_STORE"] = previous

    def test_recover_stale_check_clears_a_dead_run(self) -> None:
        import tempfile

        from mevo_companion.firmware.check import recover_stale_check
        from mevo_companion.firmware.store import load_state, save_state

        previous = os.environ.get("MEVO_COMPANION_STORE")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["MEVO_COMPANION_STORE"] = tmp
                save_state(
                    {
                        "status": "checking",
                        "message": "Starting firmware check",
                        "last_started": "2026-10-04T13:00:02+00:00",
                        "last_success": None,
                        "last_error": None,
                    }
                )
                self.assertTrue(recover_stale_check())
                state = load_state()
                self.assertEqual(state["status"], "error")
                self.assertIn("stopped", str(state["message"]).lower())
                self.assertFalse(recover_stale_check())
        finally:
            if previous is None:
                os.environ.pop("MEVO_COMPANION_STORE", None)
            else:
                os.environ["MEVO_COMPANION_STORE"] = previous


if __name__ == "__main__":
    unittest.main()

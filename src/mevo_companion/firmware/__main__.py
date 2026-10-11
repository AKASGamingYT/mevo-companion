from __future__ import annotations

import argparse
import sys
from pathlib import Path

from mevo_companion.firmware.archive import ingest_apk
from mevo_companion.firmware.check import public_status, run_check
from mevo_companion.firmware.schedule import ScheduleError, install_task, uninstall_task


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mevo firmware library checker")
    sub = parser.add_subparsers(dest="command", required=True)

    check_parser = sub.add_parser("check", help="Download new Mevo APKs and refresh the firmware library")
    check_parser.add_argument(
        "--force",
        action="store_true",
        help="Run even if this Sunday cycle already succeeded",
    )

    ingest_parser = sub.add_parser("ingest", help="Extract firmware from a local APK or APKS bundle")
    ingest_parser.add_argument("path", type=Path)

    sub.add_parser("status", help="Print library and Sunday-task status")
    sub.add_parser("install-task", help="Install the Sunday + logon scheduled task")
    sub.add_parser("uninstall-task", help="Remove the scheduled task")

    args = parser.parse_args(argv)
    if args.command == "check":
        try:
            result = run_check(force=args.force)
        except Exception as exc:
            print(str(exc), file=sys.stderr)
            return 1
        return 0 if result.get("ok") else 1
    if args.command == "ingest":
        try:
            result = ingest_apk(args.path, source="manual")
        except Exception as exc:
            print(str(exc), file=sys.stderr)
            return 1
        for image in result.get("images") or []:
            print(f"{image['label']} {image['version']} -> {image['path']}")
        return 0
    if args.command == "status":
        status = public_status()
        print(f"status: {status['status']}")
        print(f"message: {status['message']}")
        print(f"last_success: {status['last_success']}")
        print(f"store: {status['store']}")
        app = status.get("app") or {}
        if app:
            print(f"app: {app.get('version')} ({app.get('source')})")
        for image in status.get("images") or []:
            print(f"  {image.get('label')} {image.get('version')}")
        schedule = status.get("schedule") or {}
        print(f"sunday_task: {'installed' if schedule.get('installed') else 'not installed'}")
        return 0
    if args.command == "install-task":
        try:
            info = install_task()
        except ScheduleError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(f"Installed {info.get('name')}")
        return 0
    if args.command == "uninstall-task":
        try:
            uninstall_task()
        except ScheduleError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print("Removed Sunday checker task")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())

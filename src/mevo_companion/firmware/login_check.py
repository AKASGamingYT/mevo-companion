"""Run one firmware check after sign-in, once the network is connected."""

from __future__ import annotations

import logging
import threading
import time

from mevo_companion.firmware.check import run_check, should_skip_check
from mevo_companion.firmware.schedule import retire_legacy_checker
from mevo_companion.firmware.session import network_connected
from mevo_companion.firmware.store import load_state

log = logging.getLogger("mevo_companion.firmware")

_WAIT_SECONDS = 5.0
_start_lock = threading.Lock()
_started = False


def start_login_check() -> None:
    """Start the one-shot checker. Further calls in this process do nothing."""
    global _started
    with _start_lock:
        if _started:
            return
        _started = True
    threading.Thread(
        target=_login_check,
        daemon=True,
        name="firmware-login-check",
    ).start()


def _login_check() -> None:
    try:
        retire_legacy_checker()
    except Exception:
        log.exception("could not remove the old firmware scheduled task")
    while True:
        state = load_state()
        if should_skip_check(state.get("last_success")):
            log.info("firmware check already finished for this sign-in")
            return
        if network_connected():
            try:
                run_check(force=False)
            except Exception:
                log.exception("firmware check after sign-in failed")
                if network_connected():
                    return
            else:
                return
        time.sleep(_WAIT_SECONDS)

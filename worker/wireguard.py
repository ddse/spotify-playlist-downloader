import os
import subprocess
import threading
import time


CONFIG = os.getenv("WG_CONFIG", "/etc/wireguard/wg0.conf")
INTERFACE = os.getenv("WG_INTERFACE", "wg0")
_lock = threading.RLock()
_state = False


def _run(*args):
    return subprocess.run(args, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def is_up():
    return bool(subprocess.run(
        ["ip", "link", "show", "dev", INTERFACE],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode == 0)


def set_enabled(enabled: bool):
    global _state
    with _lock:
        if enabled:
            if not os.path.exists(CONFIG):
                raise RuntimeError(f"WireGuard config not found: {CONFIG}")
            if not is_up():
                _run("wg-quick", "up", CONFIG)
            _state = True
        else:
            if is_up():
                _run("wg-quick", "down", CONFIG)
            _state = False


def setting_enabled():
    try:
        from database import db
        c = db()
        row = c.execute(
            "SELECT value FROM app_settings WHERE key='wireguard_enabled'"
        ).fetchone()
        c.close()
        return bool(row and row["value"] == "1")
    except Exception:
        return False


def run(enabled, func):
    with _lock:
        set_enabled(bool(enabled))
        return func()


def run_download(enabled, func):
    return run(enabled, func)


def status():
    with _lock:
        up = is_up()
        return {"enabled": up, "interface": INTERFACE}

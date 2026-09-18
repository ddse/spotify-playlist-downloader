import os
import subprocess
import threading
import time
import urllib.request


CONFIG = os.getenv("WG_CONFIG", "/etc/wireguard/wg0.conf")
INTERFACE = os.getenv("WG_INTERFACE", "wg0")
_lock = threading.RLock()
_state = False


def _run(*args):
    return subprocess.run(args, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def is_up():
    # Use the WireGuard userspace tool instead of relying on the ip(8)
    # command being present in the minimal Python image.
    result = subprocess.run(
        ["wg", "show", INTERFACE],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


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


def _public_ip():
    try:
        with urllib.request.urlopen("https://api.ipify.org", timeout=5) as response:
            return response.read().decode().strip()
    except Exception:
        return ""

def _route_status():
    try:
        routes = subprocess.run(
            ["ip", "-4", "route", "show", "0.0.0.0/0"],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        ).stdout.splitlines()
        return any(INTERFACE in route for route in routes), routes
    except Exception:
        return False, []

def _handshake_status():
    try:
        output = subprocess.run(
            ["wg", "show", INTERFACE, "latest-handshakes"],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        ).stdout.strip()
        now = int(time.time())
        peers = []
        recent = False
        for line in output.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                ts = int(parts[1])
                age = now - ts if ts else None
                peers.append({"public_key": parts[0], "age_seconds": age})
                if age is not None and age <= 180:
                    recent = True
        return recent, peers
    except Exception:
        return False, []

def status():
    with _lock:
        up = is_up()
        route_active, routes = _route_status() if up else (False, [])
        handshake_recent, peers = _handshake_status() if up else (False, [])
        public_ip = _public_ip() if up and route_active else ""
        vpn_route = up and route_active and handshake_recent and bool(public_ip)
        transfer = {"receive_bytes": 0, "send_bytes": 0}
        try:
            raw = subprocess.run(
                ["wg", "show", INTERFACE, "transfer"],
                check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            ).stdout.strip()
            for line in raw.splitlines():
                parts = line.split()
                if len(parts) >= 3:
                    transfer["receive_bytes"] += int(parts[1])
                    transfer["send_bytes"] += int(parts[2])
        except Exception:
            pass
        return {
            "enabled": up,
            "interface": INTERFACE,
            "route_active": route_active,
            "handshake_recent": handshake_recent,
            "vpn_route": vpn_route,
            "public_ip": public_ip,
            "peer_count": len(peers),
            "peers": peers,
            "receive_bytes": transfer["receive_bytes"],
            "send_bytes": transfer["send_bytes"],
            "default_routes": routes,
        }

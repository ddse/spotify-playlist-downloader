import os
import subprocess
import threading
import time
import urllib.request


CONFIG = os.getenv("WG_CONFIG", "/etc/wireguard/wg0.conf")
DB_CONFIG_KEY = "wireguard_config"
INTERFACE = os.getenv("WG_INTERFACE", "wg0")
# wg-quick derives the interface name from the config filename. Keep the
# runtime config basename aligned with WG_INTERFACE so `wg-quick up` creates
# and manages the same interface used by status/down operations.
RUNTIME_CONFIG = f"/tmp/{INTERFACE}.conf"
_lock = threading.RLock()
_state = False
_operation = None
_operation_error = ""


def _run(*args):
    return subprocess.run(args, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def is_up():
    """Return whether the WireGuard interface exists and is currently up.

    CI and development environments may not have the WireGuard userspace
    tools installed. Missing "wg" means the interface cannot be up; it
    should not make diagnostics or search fail.
    """
    try:
        result = subprocess.run(
            ["wg", "show", INTERFACE],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return result.returncode == 0
    except (FileNotFoundError, OSError):
        return False


def _db_config():
    """Return the WireGuard config stored in the shared application DB."""
    try:
        from database import db
        c = db()
        row = c.execute("SELECT value FROM app_settings WHERE key=?", (DB_CONFIG_KEY,)).fetchone()
        c.close()
        return (row["value"] or "").strip() if row else ""
    except Exception:
        return ""


def _legacy_config():
    """Read the legacy mounted config as a migration fallback."""
    try:
        if os.path.isfile(CONFIG):
            with open(CONFIG, "r", encoding="utf-8") as handle:
                return handle.read().strip()
    except OSError:
        pass
    return ""


def config_content():
    return _db_config() or _legacy_config()


def config_configured():
    return bool(config_content())


def _materialize_config():
    content = config_content()
    if not content:
        raise RuntimeError("WireGuard configuration is not saved in the database")
    with open(RUNTIME_CONFIG, "w", encoding="utf-8") as handle:
        handle.write(content.rstrip() + "\n")
    os.chmod(RUNTIME_CONFIG, 0o600)
    return RUNTIME_CONFIG


def _persist_enabled(enabled: bool):
    try:
        from database import db
        c = db()
        c.execute("INSERT INTO app_settings(key,value) VALUES('wireguard_enabled',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", ("1" if enabled else "0",))
        c.commit(); c.close()
    except Exception:
        pass


def set_enabled(enabled: bool):
    global _state
    with _lock:
        if enabled:
            if not config_configured():
                raise RuntimeError("WireGuard configuration is not saved in the database")
            if not is_up():
                _run("wg-quick", "up", _materialize_config())
            _state = True
            _persist_enabled(True)
        else:
            if is_up():
                _run("wg-quick", "down", INTERFACE)
            try:
                os.remove(RUNTIME_CONFIG)
            except OSError:
                pass
            _state = False
            _persist_enabled(False)


def setting_enabled():
    """Return the persisted desired state, falling back to runtime state."""
    with _lock:
        try:
            from database import db
            c = db()
            row = c.execute("SELECT value FROM app_settings WHERE key='wireguard_enabled'").fetchone()
            c.close()
            if row is not None:
                return row["value"] == "1"
        except Exception:
            pass
        return bool(_state)


def restore_persisted_state():
    """Restore the last enabled state after worker/server restart."""
    if not setting_enabled():
        return False
    def restore():
        for attempt in range(10):
            try:
                set_enabled(True)
                return
            except Exception as exc:
                with _lock:
                    global _operation_error
                    _operation_error = f"startup restore attempt {attempt + 1}: {type(exc).__name__}: {exc}"
                time.sleep(min(2 ** attempt, 30))
    threading.Thread(target=restore, name="wireguard-startup-restore", daemon=True).start()
    return True


def run(enabled, func):
    """Run network work without changing the live WireGuard interface.

    WireGuard is a worker/container-wide network setting. The Settings toggle
    is the only operation allowed to bring the interface up or down. Search
    and download jobs inherit the current network namespace routing.
    The legacy ``enabled`` argument is retained for API compatibility.
    """
    return func()


def run_download(enabled, func):
    """Run a download without toggling the global WireGuard interface."""
    return func()

def apply_enabled_async(enabled: bool):
    """Start a WireGuard transition without blocking the HTTP request."""
    global _operation, _operation_error
    target = "connecting" if enabled else "disconnecting"
    with _lock:
        if _operation:
            return False
        _operation = target
        _operation_error = ""

    def worker():
        global _operation, _operation_error
        try:
            set_enabled(enabled)
        except Exception as exc:
            with _lock:
                _operation_error = f"{type(exc).__name__}: {exc}"
        finally:
            with _lock:
                _operation = None

    threading.Thread(target=worker, name="wireguard-transition", daemon=True).start()
    return True


def debug_status():
    """Lightweight WireGuard diagnostics for search/debug flows."""
    try:
        current = status()
        return {
            "requested_enabled": requested_enabled,
            "interface": current.get("interface", INTERFACE),
            "config_exists": bool(current.get("config_exists")),
            "interface_up": bool(current.get("enabled")),
            "route_active": bool(current.get("route_active")),
            "handshake_recent": bool(current.get("handshake_recent")),
            "vpn_route": bool(current.get("vpn_route")),
            "status": current.get("status"),
            "status_detail": current.get("status_detail", ""),
            "public_ip": current.get("public_ip", ""),
            "peer_count": int(current.get("peer_count", 0)),
        }
    except Exception as exc:
        return {
            "requested_enabled": bool(_state),
            "interface": INTERFACE,
            "config_exists": config_configured(),
            "interface_up": False,
            "route_active": False,
            "handshake_recent": False,
            "vpn_route": False,
            "status": "error",
            "status_detail": f"{type(exc).__name__}: {exc}",
            "public_ip": "",
            "peer_count": 0,
        }



def _public_ip():
    try:
        with urllib.request.urlopen("https://api.ipify.org", timeout=5) as response:
            return response.read().decode().strip()
    except Exception:
        return ""

def _route_status():
    """Detect a WireGuard default route, including wg-quick policy routing."""
    try:
        routes = subprocess.run(
            ["ip", "-4", "route", "show", "table", "all"],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        ).stdout.splitlines()
        default_routes = [route for route in routes if route.startswith("default ")]
        route_active = any(
            f" dev {INTERFACE}" in route or f" {INTERFACE}" in route
            for route in default_routes
        )
        if not route_active:
            rules = subprocess.run(
                ["ip", "-4", "rule", "show"],
                check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            ).stdout.splitlines()
            has_policy_rule = any(
                "lookup" in rule or "table" in rule
                for rule in rules
            )
            route_active = has_policy_rule and any(
                f" dev {INTERFACE}" in route or f" {INTERFACE}" in route
                for route in routes
            )
        return route_active, default_routes
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
        operation = _operation
        operation_error = _operation_error
        up = is_up()
        route_active, routes = _route_status() if up else (False, [])
        handshake_recent, peers = _handshake_status() if up else (False, [])
        # Public-IP detection is diagnostic only. A slow/unreachable ipify
        # endpoint must never turn an otherwise established WireGuard tunnel
        # into a false "connecting" state.
        public_ip = _public_ip() if up and route_active else ""
        vpn_route = up and route_active and handshake_recent

        # During an asynchronous transition, expose the requested target rather
        # than the last persisted preference.
        requested_enabled = (operation == "connecting") if operation else setting_enabled()
        if operation:
            status = operation
            status_detail = (
                "WireGuard is connecting; waiting for the tunnel handshake"
                if operation == "connecting"
                else "WireGuard is disconnecting; waiting for the interface to go down"
            )
            if operation_error:
                status_detail += f": {operation_error}"
        elif not up:
            status = "disconnected"
            status_detail = "WireGuard interface is down"
        elif not route_active:
            status = "connecting"
            status_detail = "Interface is up; waiting for WireGuard routing"
        elif not handshake_recent:
            # An active route without a recent handshake is not an established VPN.
            status = "connecting"
            status_detail = "Route is active; waiting for a recent peer handshake"
        else:
            status = "connected"
            status_detail = "WireGuard interface, route, and peer handshake are active"
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
            "requested_enabled": setting_enabled(),
            "interface": INTERFACE,
            "config_path": "database://wireguard_config",
            "config_source": "database" if config_configured() else ("live_interface" if up else "missing"),
            "config_exists": config_configured() or up,
            "route_active": route_active,
            "handshake_recent": handshake_recent,
            "vpn_route": vpn_route,
            "status": status,
            "status_detail": status_detail,
            "operation": operation,
            "operation_error": operation_error,
            "public_ip": public_ip,
            "peer_count": len(peers),
            "peers": peers,
            "receive_bytes": transfer["receive_bytes"],
            "send_bytes": transfer["send_bytes"],
            "default_routes": routes,
        }

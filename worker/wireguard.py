import os
import queue
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

_MONITOR_INTERVAL = float(os.getenv("WIREGUARD_MONITOR_INTERVAL", "0.5"))
_state_lock = threading.RLock()
_subscribers = set()
_last_snapshot = None
_last_fingerprint = None
_monitor_thread = None
_monitor_stop = threading.Event()


def _run(*args):
    return subprocess.run(args, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _wg_show(*args):
    """Read WireGuard state directly from the wg userspace tool."""
    return subprocess.run(
        ["wg", "show", INTERFACE, *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def is_up():
    """Return whether wg show confirms the interface exists."""
    try:
        _wg_show()
        return True
    except (FileNotFoundError, OSError, subprocess.CalledProcessError):
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
                try:
                    _run("wg-quick", "down", RUNTIME_CONFIG)
                except subprocess.CalledProcessError:
                    # wg-quick down is not idempotent: the interface may have
                    # disappeared between is_up() and the down command. Treat
                    # that race as an already-disconnected state, but preserve
                    # real failures while the interface is still present.
                    if is_up():
                        raise
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
        # Persist the requested target before the background transition starts.
        # This prevents startup-restore or another status reader from observing
        # the previous enabled preference and bringing WireGuard back up while
        # a user-initiated disable is still in progress.
        _persist_enabled(enabled)

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



_PUBLIC_IP_TTL = 60
_public_ip_cache = ""
_public_ip_cache_at = 0.0
_public_ip_refreshing = False


def _refresh_public_ip():
    global _public_ip_cache, _public_ip_cache_at, _public_ip_refreshing
    try:
        with urllib.request.urlopen("https://api.ipify.org", timeout=3) as response:
            value = response.read().decode().strip()
        with _lock:
            _public_ip_cache = value
            _public_ip_cache_at = time.time()
    except Exception:
        pass
    finally:
        with _lock:
            _public_ip_refreshing = False


def _public_ip():
    """Best-effort public IP lookup, used by explicit status requests."""
    try:
        with urllib.request.urlopen("https://api.ipify.org", timeout=2) as response:
            return response.read().decode().strip()
    except Exception:
        return ""


def _ensure_public_ip_refresh():
    global _public_ip_refreshing
    with _lock:
        if _public_ip_refreshing:
            return
        if _public_ip_cache and time.time() - _public_ip_cache_at < _PUBLIC_IP_TTL:
            return
        _public_ip_refreshing = True
    threading.Thread(target=_refresh_public_ip, name="wireguard-public-ip", daemon=True).start()


def _snapshot_fingerprint(snapshot):
    import json
    return json.dumps(snapshot, sort_keys=True, separators=(",", ":"))


def _publish_snapshot(snapshot):
    global _last_snapshot, _last_fingerprint
    fingerprint = _snapshot_fingerprint(snapshot)
    with _state_lock:
        if fingerprint == _last_fingerprint:
            return False
        _last_fingerprint = fingerprint
        _last_snapshot = dict(snapshot)
        subscribers = list(_subscribers)
    for subscriber in subscribers:
        try:
            subscriber.put_nowait(dict(snapshot))
        except queue.Full:
            try:
                subscriber.get_nowait()
            except queue.Empty:
                pass
            try:
                subscriber.put_nowait(dict(snapshot))
            except queue.Full:
                pass
    return True


def wireguard_state_snapshot():
    with _state_lock:
        return dict(_last_snapshot) if _last_snapshot else None


def subscribe_wireguard():
    subscriber = queue.Queue(maxsize=4)
    with _state_lock:
        _subscribers.add(subscriber)
        snapshot = dict(_last_snapshot) if _last_snapshot else None
    return subscriber, snapshot


def unsubscribe_wireguard(subscriber):
    with _state_lock:
        _subscribers.discard(subscriber)


def _monitor_loop():
    global _public_ip_cache, _public_ip_cache_at
    while not _monitor_stop.is_set():
        try:
            snapshot = status(resolve_public_ip=False)
            if snapshot.get("status") == "connected":
                _ensure_public_ip_refresh()
                with _lock:
                    snapshot["public_ip"] = _public_ip_cache
            else:
                with _lock:
                    _public_ip_cache = ""
                    _public_ip_cache_at = 0.0
            _publish_snapshot(snapshot)
        except Exception as exc:
            _publish_snapshot({
                "enabled": False,
                "requested_enabled": setting_enabled(),
                "interface": INTERFACE,
                "status": "unavailable",
                "status_detail": f"WireGuard monitor error: {type(exc).__name__}: {exc}",
                "public_ip": "",
                "peer_count": 0,
                "receive_bytes": 0,
                "send_bytes": 0,
            })
        _monitor_stop.wait(_MONITOR_INTERVAL)


def start_monitor():
    global _monitor_thread
    with _state_lock:
        if _monitor_thread and _monitor_thread.is_alive():
            return
        _monitor_stop.clear()
        _monitor_thread = threading.Thread(target=_monitor_loop, name="wireguard-monitor", daemon=True)
        _monitor_thread.start()


def stop_monitor():
    _monitor_stop.set()


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
        output = _wg_show("latest-handshakes").stdout.strip()
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



def status(resolve_public_ip=True):
    global _public_ip_cache, _public_ip_cache_at
    with _lock:
        operation = _operation
        operation_error = _operation_error
        up = is_up()
        route_active, routes = _route_status() if up else (False, [])
        handshake_recent, peers = _handshake_status() if up else (False, [])
        vpn_route = up and route_active and handshake_recent

        requested_enabled = (operation == "connecting") if operation else setting_enabled()
        if operation:
            current_status = operation
            status_detail = (
                "WireGuard is connecting; waiting for the tunnel handshake"
                if operation == "connecting"
                else "WireGuard is disconnecting; waiting for the interface to go down"
            )
            if operation_error:
                status_detail += f": {operation_error}"
        elif not up:
            current_status = "disconnected"
            status_detail = "WireGuard interface is down"
        elif not route_active:
            current_status = "connecting"
            status_detail = "Interface is up; waiting for WireGuard routing"
        elif not handshake_recent:
            current_status = "connecting"
            status_detail = "Route is active; waiting for a recent peer handshake"
        else:
            current_status = "connected"
            status_detail = "WireGuard interface, route, and peer handshake are active"

        if current_status == "connected":
            with _lock:
                public_ip = _public_ip_cache
            if resolve_public_ip and not public_ip:
                public_ip = _public_ip()
                if public_ip:
                    with _lock:
                        _public_ip_cache = public_ip
                        _public_ip_cache_at = time.time()
        else:
            public_ip = ""

        transfer = {"receive_bytes": 0, "send_bytes": 0}
        try:
            raw = _wg_show("transfer").stdout.strip()
            for line in raw.splitlines():
                parts = line.split()
                if len(parts) >= 3:
                    transfer["receive_bytes"] += int(parts[1])
                    transfer["send_bytes"] += int(parts[2])
        except Exception:
            pass
        return {
            "enabled": up,
            "requested_enabled": requested_enabled,
            "interface": INTERFACE,
            "config_path": "database://wireguard_config",
            "config_source": "database" if config_configured() else ("live_interface" if up else "missing"),
            "config_exists": config_configured() or up,
            "route_active": route_active,
            "handshake_recent": handshake_recent,
            "vpn_route": vpn_route,
            "status": current_status,
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

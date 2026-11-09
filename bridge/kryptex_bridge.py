"""Kryptex bridge: a small HTTP API on the mining PC for Home Assistant.

Reads income, balance and exchange rates from the Kryptex app's local SQLite database (read-only), reads mining
devices, version, update and pause state from the Kryptex service's local API (127.0.0.1 only), starts or stops the
app and pauses or resumes mining. Python 3.10+ standard library only. Run it in the user session that runs Kryptex
(see install.ps1).

Money: Kryptex keeps the account balance in bitcoin and shows other currencies as a conversion. Its database stores
amounts in USD with a USD-based rate table, so every amount is returned in BTC (converted with Kryptex's own BTC rate)
and, for reference, in USD.
"""

from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit
import urllib.error
import urllib.request

VERSION = "1.0.0"
LOG = logging.getLogger("kryptex_bridge")

MAX_MEASUREMENT_AGE_SECONDS = 120
# Profitability coefficients update less often than the five-second hashrate measurements.
MAX_COEFFICIENT_AGE_SECONDS = 24 * 60 * 60
# The estimate uses the last mining hashrates and coefficients for up to a week, e.g. over a long pause.
MAX_ESTIMATE_AGE_SECONDS = 7 * 24 * 60 * 60
# The estimate is the median of this many last mining readings (Kryptex writes one every 5 seconds).
ESTIMATE_READINGS = 120
FUTURE_TOLERANCE_SECONDS = 30
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
SERVICE_TIMEOUT_SECONDS = 5
# How long /start and /stop wait for Kryptex to start or close.
CONTROL_WAIT_SECONDS = 20
BTC_PLACES = Decimal("0.00000001")


def default_config_path() -> Path:
    return Path(os.environ.get("APPDATA", Path.home())) / "kryptex-bridge" / "config.json"


DEFAULTS = {
    "host": "0.0.0.0",
    "port": 5500,
    "api_key": "",
    "database": str(Path(os.environ.get("APPDATA", "")) / "Kryptex" / "kryptex.db"),
    # The Kryptex service's own API. It has no authentication, so the bridge only talks to it on this computer.
    "service_url": "http://127.0.0.1:8107",
    "process_name": "Kryptex.exe",
    # Kryptex runs as administrator. The bridge does not, so it starts and stops Kryptex through two scheduled tasks
    # that install.ps1 registers with the highest privileges (they only start Kryptex.exe or end it).
    "start_command": ["schtasks", "/run", "/tn", "Kryptex bridge start"],
    "stop_command": ["schtasks", "/run", "/tn", "Kryptex bridge stop"],
}


def load_config(path: Path) -> dict:
    config = dict(DEFAULTS)
    if path.is_file():
        # utf-8-sig: Windows PowerShell 5.1 writes a BOM.
        config.update(json.loads(path.read_text(encoding="utf-8-sig")))
    if not config["api_key"]:
        raise SystemExit(f"api_key is missing from {path}. Run install.ps1 or add a long random key.")
    for key in ("start_command", "stop_command"):
        if not isinstance(config[key], list) or not all(isinstance(part, str) for part in config[key]):
            raise SystemExit(f"{key} must be a list of strings.")
    url = urlsplit(str(config["service_url"]))
    if url.scheme != "http" or url.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit("service_url must be an http address on this computer (127.0.0.1).")
    return config


class ReadingError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


# --- Database ---------------------------------------------------------------------------------------------------

def number(value, label: str, positive: bool = False) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ReadingError("invalid_measurement", f"Invalid {label}.") from None
    if not result.is_finite() or result < 0 or (positive and result == 0):
        raise ReadingError("invalid_measurement", f"Invalid {label}.")
    return result


def timestamp_ms(value, label: str) -> int:
    result = number(value, label, positive=True)
    if result != result.to_integral_value() or result > Decimal("253402300799999"):
        raise ReadingError("invalid_measurement", f"Invalid {label}.")
    return int(result)


def iso_time(milliseconds: int) -> str:
    return datetime.fromtimestamp(milliseconds / 1000, timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def check_age(timestamp: int, now_ms: int, max_seconds: int, code: str, label: str) -> float:
    age = (now_ms - timestamp) / 1000
    if age < -FUTURE_TOLERANCE_SECONDS:
        raise ReadingError("future_timestamp", f"{label} is in the future; check the computer clock.")
    if age > max_seconds:
        raise ReadingError(code, f"{label} is older than {max_seconds} seconds.")
    return max(0.0, age)


def money(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def btc(value: Decimal) -> float:
    return float(value.quantize(BTC_PLACES, rounding=ROUND_HALF_UP))


def connect(database: Path) -> sqlite3.Connection:
    if not database.is_file():
        raise ReadingError("database_missing", "The Kryptex database was not found.")
    # mode=ro sees committed WAL data and can neither create nor change the database. Do not use immutable=1:
    # the running app keeps writing to it.
    connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    connection.execute("BEGIN")  # One consistent snapshot for all SELECTs.
    return connection


def newest_rates(connection: sqlite3.Connection) -> dict[str, tuple[Decimal, int]]:
    """Newest rate per currency, as units of that currency per 1 USD (Kryptex's own rate table)."""
    rows = connection.execute(
        """
        SELECT cu.name, cr.rate, cr.updated_at FROM currency_rate cr
        JOIN currency cu ON cu.id = cr.currency_id
        WHERE cr.id = (SELECT id FROM currency_rate WHERE currency_id = cr.currency_id
                       ORDER BY updated_at DESC, id DESC LIMIT 1)
        """
    ).fetchall()
    return {
        str(row["name"]).lower(): (number(row["rate"], "exchange rate", positive=True), timestamp_ms(row["updated_at"], "rate timestamp"))
        for row in rows
    }


def btc_per_usd(connection: sqlite3.Connection) -> Decimal:
    rate = newest_rates(connection).get("btc")
    if rate is None:
        raise ReadingError("no_btc_rate", "The database contains no BTC rate.")
    return rate[0]


INCOME_QUERY = """
    SELECT pd.device_id, d.name AS device_name, pdr.coin_algorithm_id,
           co.name AS coin, a.name AS algorithm, pdr.hashrate,
           cp.profitability, cp.updated_at AS coefficient_updated_at
    FROM process_device_reading pdr
    LEFT JOIN process_device pd ON pd.id = pdr.process_device_id
    LEFT JOIN device d ON d.id = pd.device_id
    LEFT JOIN coin_algorithm ca ON ca.id = pdr.coin_algorithm_id
    LEFT JOIN coin co ON co.id = ca.coin_id
    LEFT JOIN algorithm a ON a.id = ca.algorithm_id
    LEFT JOIN coin_algorithm_profitability cp ON cp.id = (
        SELECT id FROM coin_algorithm_profitability
        WHERE coin_algorithm_id = ca.id
        ORDER BY updated_at DESC, id DESC LIMIT 1
    )
    WHERE pdr.reading_id = ?
    ORDER BY pd.device_id, pdr.coin_algorithm_id
"""


def reading_income(connection: sqlite3.Connection, reading_id: int, now_ms: int, max_coefficient_age: int) -> dict:
    """Income of one hashrate reading with Kryptex's newest profitability coefficients."""
    rows = connection.execute(INCOME_QUERY, (reading_id,)).fetchall()
    rate = btc_per_usd(connection)
    if not rows:
        raise ReadingError("no_hashrates", "The measurement has no mining hashrates.")
    required = ("device_id", "device_name", "coin_algorithm_id", "coin", "algorithm", "profitability", "coefficient_updated_at")
    seen = set()
    devices = []
    daily_total = Decimal(0)
    coefficient_times = []
    for row in rows:
        if any(row[key] is None for key in required):
            raise ReadingError("incomplete_measurement", "A device, algorithm or profitability coefficient is missing.")
        key = (row["device_id"], row["coin_algorithm_id"])
        if key in seen:
            raise ReadingError("duplicate_measurement", "The measurement has duplicate device/algorithm rows.")
        seen.add(key)
        hashrate = number(row["hashrate"], "hashrate")
        coefficient_time = timestamp_ms(row["coefficient_updated_at"], "coefficient timestamp")
        check_age(coefficient_time, now_ms, max_coefficient_age, "stale_coefficient", "A profitability coefficient")
        coefficient_times.append(coefficient_time)
        daily = hashrate * number(row["profitability"], "profitability coefficient")
        daily_total += daily
        devices.append({
            "id": str(row["device_id"]), "name": row["device_name"], "coin": row["coin"], "algorithm": row["algorithm"],
            "hashrate": float(hashrate), "daily_btc": btc(daily * rate), "daily_usd": money(daily),
        })
    if daily_total <= 0:
        raise ReadingError("no_income", "There is no positive mining income in the measurement.")
    return {
        "daily_btc": btc(daily_total * rate), "monthly_btc": btc(daily_total * 30 * rate),
        "daily_usd": money(daily_total), "monthly_usd": money(daily_total * 30),
        "coefficient_updated_at": iso_time(min(coefficient_times)),
        "device_count": len({row["device_id"] for row in rows}), "devices": devices,
    }


def read_profitability(database: Path, now_ms: int | None = None) -> dict:
    """Income now: the newest hashrate reading, which must be at most two minutes old."""
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    with closing(connect(database)) as connection:
        reading = connection.execute("SELECT id, timestamp FROM reading ORDER BY timestamp DESC, id DESC LIMIT 1").fetchone()
        if reading is None:
            raise ReadingError("no_measurement", "The database contains no measurements.")
        measured_at_ms = timestamp_ms(reading["timestamp"], "measurement timestamp")
        age = check_age(measured_at_ms, now_ms, MAX_MEASUREMENT_AGE_SECONDS, "stale_measurement", "The hashrate measurement")
        income = reading_income(connection, reading["id"], now_ms, MAX_COEFFICIENT_AGE_SECONDS)
    return {**income, "measured_at": iso_time(measured_at_ms), "measurement_age_seconds": round(age, 3)}


ESTIMATE_TOTALS_QUERY = """
    SELECT pdr.reading_id AS id, r.timestamp AS timestamp, SUM(pdr.hashrate * cp.profitability) AS total
    FROM process_device_reading pdr
    JOIN reading r ON r.id = pdr.reading_id
    LEFT JOIN coin_algorithm_profitability cp ON cp.id = (
        SELECT id FROM coin_algorithm_profitability
        WHERE coin_algorithm_id = pdr.coin_algorithm_id
        ORDER BY updated_at DESC, id DESC LIMIT 1
    )
    WHERE pdr.reading_id IN (
        SELECT r2.id FROM reading r2
        WHERE EXISTS (SELECT 1 FROM process_device_reading p2 WHERE p2.reading_id = r2.id AND p2.hashrate > 0)
        ORDER BY r2.timestamp DESC, r2.id DESC LIMIT ?
    )
    GROUP BY pdr.reading_id
"""


def read_estimate(database: Path, now_ms: int | None = None) -> dict:
    """Income if mining started now, for automations that decide when to mine (works while paused or closed).

    Uses the last ESTIMATE_READINGS mining readings (about 10 minutes at Kryptex's 5-second interval) with
    Kryptex's newest coefficients, and takes the reading with the median income. The newest reading alone is not
    enough: while Kryptex starts or stops, a reading can contain only some of the devices.
    """
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    with closing(connect(database)) as connection:
        totals = connection.execute(ESTIMATE_TOTALS_QUERY, (ESTIMATE_READINGS,)).fetchall()
        if not totals:
            raise ReadingError("no_measurement", "Kryptex has not mined yet.")
        newest_ms = max(timestamp_ms(row["timestamp"], "measurement timestamp") for row in totals)
        check_age(newest_ms, now_ms, MAX_ESTIMATE_AGE_SECONDS, "stale_measurement", "The last mining measurement")
        ranked = sorted(totals, key=lambda row: (row["total"] is None, row["total"] or 0, row["id"]))
        middle = ranked[(len(ranked) - 1) // 2]
        income = reading_income(connection, middle["id"], now_ms, MAX_ESTIMATE_AGE_SECONDS)
        measured_at_ms = timestamp_ms(middle["timestamp"], "measurement timestamp")
    return {**income, "hashrate_measured_at": iso_time(measured_at_ms), "last_mining_at": iso_time(newest_ms),
            "readings_used": len(totals)}


def read_balance(database: Path, now_ms: int | None = None) -> dict:
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    with closing(connect(database)) as connection:
        rows = connection.execute("SELECT total, withdrawable, updated_at FROM balance ORDER BY updated_at DESC, id DESC").fetchall()
        rate = btc_per_usd(connection) if rows else None

    if not rows:
        raise ReadingError("no_balance", "The database contains no balance.")
    if len(rows) > 1:
        raise ReadingError("multiple_balances", "The database contains more than one balance.")
    total = number(rows[0]["total"], "balance total")
    withdrawable = number(rows[0]["withdrawable"], "withdrawable balance")
    updated_at_ms = timestamp_ms(rows[0]["updated_at"], "balance timestamp")
    age = (now_ms - updated_at_ms) / 1000
    if age < -FUTURE_TOLERANCE_SECONDS:
        raise ReadingError("future_timestamp", "The balance timestamp is in the future; check the computer clock.")
    return {
        "total_btc": btc(total * rate), "withdrawable_btc": btc(withdrawable * rate),
        "total_usd": money(total), "withdrawable_usd": money(withdrawable),
        "updated_at": iso_time(updated_at_ms), "age_seconds": round(max(0.0, age), 3),
    }


def read_rates(database: Path, now_ms: int | None = None) -> dict:
    """Price of one bitcoin in every currency Kryptex knows, from Kryptex's own rate table."""
    with closing(connect(database)) as connection:
        rates = newest_rates(connection)
    if "btc" not in rates:
        raise ReadingError("no_btc_rate", "The database contains no BTC rate.")
    per_usd = rates["btc"][0]
    prices = {name: float((rate / per_usd).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))
              for name, (rate, _) in sorted(rates.items()) if name != "btc"}
    return {"btc_price": prices, "updated_at": iso_time(min(updated for _, updated in rates.values()))}


def safe_read(reader, database: Path) -> dict:
    """Never returns numbers from a failed read, so a caller cannot store a wrong value."""
    try:
        return {"ok": True, **reader(database)}
    except ReadingError as error:
        return {"ok": False, "error": error.code, "message": str(error)}
    except (sqlite3.Error, OSError):
        return {"ok": False, "error": "database_unavailable",
                "message": "The Kryptex database could not be read; check its path, permissions and schema."}
    except (InvalidOperation, OverflowError, ValueError):
        return {"ok": False, "error": "invalid_measurement", "message": "The database contains invalid numeric data."}


# --- Kryptex service API ----------------------------------------------------------------------------------------

class ServiceError(Exception):
    pass


class KryptexService:
    """The local API the Kryptex app's own window uses. Only fixed paths are ever requested."""

    def __init__(self, base_url: str):
        self.base = base_url.rstrip("/")

    def request(self, method: str, path: str, body: dict | None = None):
        data = None if body is None else json.dumps(body).encode()
        headers = {"Content-Type": "application/json"} if data is not None else {}
        request = urllib.request.Request(self.base + path, method=method, data=data, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=SERVICE_TIMEOUT_SECONDS) as response:
                raw = response.read()
        except (urllib.error.URLError, OSError) as error:
            raise ServiceError(f"The Kryptex service did not answer: {error}") from None
        if not raw.strip():
            return None
        try:
            return json.loads(raw)
        except ValueError:
            raise ServiceError("The Kryptex service returned an unexpected answer.") from None

    def get(self, path: str):
        return self.request("GET", path)


def optional_number(value) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def normalize_device(item: dict, btc_rate: Decimal | None) -> dict | None:
    """One mining device from the service's /devices answer, in a stable shape. None if it has no id."""
    if not isinstance(item, dict):
        return None
    device = item.get("device") or {}
    if device.get("id") is None:
        return None
    reading = item.get("reading") or {}
    process = item.get("process") or {}
    combination = process.get("algorithm_combination") or {}
    miner_version = process.get("miner_version") or {}
    algorithms = []
    for algorithm in combination.get("algorithms") or []:
        algorithm_reading = algorithm.get("reading") or {}
        algorithms.append({
            "coin": algorithm.get("coin"), "algorithm": algorithm.get("algorithm"),
            "hashrate": optional_number(algorithm_reading.get("hashrate")),
        })
    daily_usd = optional_number(combination.get("profitability"))
    daily_btc = None
    if daily_usd is not None and daily_usd >= 0 and btc_rate is not None:
        daily_btc = btc(Decimal(str(daily_usd)) * btc_rate)
    return {
        "id": str(device["id"]),
        "name": device.get("name") or f"Device {device['id']}",
        "type": device.get("type"), "vendor": device.get("vendor"), "architecture": device.get("architecture"),
        "memory_mb": optional_number(device.get("memory_size")),
        "enabled": not (device.get("configuration") or {}).get("disabled", False),
        "state": (item.get("state") or {}).get("state"),
        "temperature": optional_number(reading.get("core_temperature")),
        "memory_temperature": optional_number(reading.get("memory_temperature")),
        "power": optional_number(reading.get("power_usage")),
        "fan": optional_number(reading.get("fan_speed")),
        "core_clock": optional_number(reading.get("core_clock")),
        "memory_clock": optional_number(reading.get("memory_clock")),
        "miner": (miner_version.get("miner") or {}).get("name") if process else None,
        "miner_version": miner_version.get("version") if process else None,
        "algorithms": algorithms,
        "coin": "+".join(a["coin"] for a in algorithms if a["coin"]) or None,
        "algorithm": "+".join(a["algorithm"] for a in algorithms if a["algorithm"]) or None,
        "hashrate": algorithms[0]["hashrate"] if len(algorithms) == 1 else None,
        "daily_btc": daily_btc,
    }


def read_service(service: KryptexService, database: Path) -> dict:
    """Version, update, pause state and mining devices. Each part is None when its answer was not understood."""
    try:
        version = service.get("/version")
        update = service.get("/update")
        pause = service.get("/pause")
        devices = service.get("/devices")
    except ServiceError as error:
        return {"ok": False, "error": "service_unavailable", "message": str(error)}
    try:
        with closing(connect(database)) as connection:
            rate = btc_per_usd(connection)
    except (ReadingError, sqlite3.Error, OSError, InvalidOperation, ValueError):
        rate = None  # Devices are still shown, without income.
    if update is None:
        update_info = {"available": False, "version": None}
    elif isinstance(update, dict):
        # Shape seen only as null (no update). Any answer with content means an update is offered.
        update_info = {"available": True, "version": update.get("version") or update.get("name")}
    else:
        update_info = {"available": True, "version": None}
    return {
        "ok": True,
        "version": version.get("version") if isinstance(version, dict) else None,
        "update": update_info,
        "paused": pause.get("pause") if isinstance(pause, dict) and isinstance(pause.get("pause"), bool) else None,
        "devices": [d for d in (normalize_device(item, rate) for item in (devices if isinstance(devices, list) else [])) if d],
    }


# --- Process control --------------------------------------------------------------------------------------------

def is_running(process_name: str) -> bool | None:
    if os.name != "nt":
        return None
    try:
        output = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {process_name}", "/NH", "/FO", "CSV"],
            capture_output=True, text=True, timeout=10, creationflags=NO_WINDOW,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return f'"{process_name.lower()}"' in output.lower()


def run_command(command: list[str], detached: bool) -> dict:
    try:
        if detached:
            flags = NO_WINDOW | getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            subprocess.Popen(command, creationflags=flags, close_fds=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return {"ok": True}
        result = subprocess.run(command, capture_output=True, text=True, timeout=30, creationflags=NO_WINDOW)
        return {"ok": result.returncode == 0, "returncode": result.returncode}
    except (OSError, subprocess.SubprocessError) as error:
        LOG.warning("Command failed: %s", error)
        return {"ok": False, "error": "command_failed", "message": "The command could not be run; check its path."}


# --- HTTP -------------------------------------------------------------------------------------------------------

class Bridge:
    def __init__(self, config: dict):
        self.config = config
        self.database = Path(config["database"])
        self.service = KryptexService(config["service_url"])
        self.lock = threading.Lock()  # One start, stop, pause or resume at a time.

    def profitability(self, paused: bool | None = None) -> dict:
        """Income now. While paused Kryptex keeps reporting the last hashrates for a while, so check the pause."""
        if paused is None:
            try:
                pause = self.service.get("/pause")
                paused = pause.get("pause") is True if isinstance(pause, dict) else False
            except ServiceError:
                paused = False
        if paused:
            return {"ok": False, "error": "paused", "message": "Kryptex is paused."}
        return safe_read(read_profitability, self.database)

    def status(self) -> dict:
        service = read_service(self.service, self.database)
        return {
            "ok": True, "version": VERSION, "running": is_running(self.config["process_name"]),
            "profitability": self.profitability(paused=service.get("paused") is True),
            "estimate": safe_read(read_estimate, self.database),
            "balance": safe_read(read_balance, self.database),
            "rates": safe_read(read_rates, self.database),
            "service": service,
        }

    def control(self, action: str) -> dict:
        """Start or close the Kryptex app, and wait until it has (the scheduled tasks return at once)."""
        with self.lock:
            running = is_running(self.config["process_name"])
            if action == "start":
                if running:
                    return {"ok": True, "changed": False}
                result = run_command(self.config["start_command"], detached=False)
            else:
                if running is False:
                    return {"ok": True, "changed": False}
                result = run_command(self.config["stop_command"], detached=False)
            if not result["ok"]:
                return {**result, "changed": False}
            wanted = action == "start"
            deadline = time.monotonic() + CONTROL_WAIT_SECONDS
            state = is_running(self.config["process_name"])
            while state is not None and state != wanted and time.monotonic() < deadline:
                time.sleep(1)
                state = is_running(self.config["process_name"])
            if state is not None and state != wanted:
                return {"ok": False, "changed": False, "error": "not_applied",
                        "message": f"Kryptex did not {'start' if wanted else 'close'} within {CONTROL_WAIT_SECONDS} seconds."}
            return {**result, "changed": True}

    def set_pause(self, pause: bool) -> dict:
        """Kryptex's own pause: the app keeps running, mining stops. Checked by reading the state back."""
        with self.lock:
            try:
                before = self.service.get("/pause")
                if isinstance(before, dict) and before.get("pause") is pause:
                    return {"ok": True, "changed": False, "paused": pause}
                self.service.request("PUT", "/pause", {"pause": pause})
                after = self.service.get("/pause")
            except ServiceError as error:
                return {"ok": False, "error": "service_unavailable", "message": str(error)}
            if not isinstance(after, dict) or after.get("pause") is not pause:
                return {"ok": False, "error": "pause_not_applied",
                        "message": "Kryptex did not change its pause state; check that the app is running and logged in."}
            return {"ok": True, "changed": True, "paused": pause}


def make_handler(bridge: Bridge):
    key = bridge.config["api_key"].encode()

    class Handler(BaseHTTPRequestHandler):
        server_version = f"KryptexBridge/{VERSION}"

        def log_message(self, fmt, *args):
            LOG.info("%s %s", self.address_string(), fmt % args)

        def send_json(self, status: int, body: dict) -> None:
            data = json.dumps(body, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def authorized(self) -> bool:
            header = self.headers.get("Authorization", "")
            token = header[7:].encode() if header.startswith("Bearer ") else b""
            if hmac.compare_digest(token, key):
                return True
            self.send_json(401, {"ok": False, "error": "unauthorized"})
            return False

        def do_GET(self):
            if not self.authorized():
                return
            routes = {
                "/health": lambda: {"ok": True, "version": VERSION},
                "/status": bridge.status,
                "/profitability": bridge.profitability,
                "/estimate": lambda: safe_read(read_estimate, bridge.database),
                "/balance": lambda: safe_read(read_balance, bridge.database),
                "/rates": lambda: safe_read(read_rates, bridge.database),
                "/service": lambda: read_service(bridge.service, bridge.database),
            }
            route = routes.get(self.path.split("?")[0])
            if route is None:
                return self.send_json(404, {"ok": False, "error": "not_found"})
            body = route()
            self.send_json(200 if body["ok"] else 503, body)

        def do_POST(self):
            if not self.authorized():
                return
            routes = {
                "/start": lambda: bridge.control("start"),
                "/stop": lambda: bridge.control("stop"),
                "/pause": lambda: bridge.set_pause(True),
                "/resume": lambda: bridge.set_pause(False),
            }
            route = routes.get(self.path.split("?")[0])
            if route is None:
                return self.send_json(404, {"ok": False, "error": "not_found"})
            body = route()
            self.send_json(200 if body["ok"] else 500, body)

    return Handler


class BridgeServer(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR lets a second program bind a port that is already in use, so the bridge could
    # silently share a port with another program. Without it, a taken port fails at startup as it should.
    allow_reuse_address = sys.platform != "win32"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=default_config_path())
    parser.add_argument("--log", type=Path, help="Log file (default: next to the config file).")
    args = parser.parse_args(argv)
    log_file = args.log or args.config.with_name("bridge.log")
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=log_file, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # The bridge runs hidden, so a startup error is visible only in the log.
    try:
        config = load_config(args.config)
    except SystemExit as error:
        LOG.error("Not started: %s", error)
        return 1
    try:
        server = BridgeServer((config["host"], int(config["port"])), make_handler(Bridge(config)))
    except OSError as error:
        LOG.error("Not started: port %s is already in use or not allowed (%s). Run install.cmd again: it picks "
                  "a free port.", config["port"], error)
        return 1
    LOG.info("Kryptex bridge %s listening on %s:%s", VERSION, config["host"], config["port"])
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())

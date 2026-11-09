from http.server import BaseHTTPRequestHandler
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest import mock
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import kryptex_bridge as kb  # noqa: E402
from fixture_db import NOW_MS, build  # noqa: E402

DEVICES = json.loads((Path(__file__).parent / "service_devices.json").read_text(encoding="utf-8"))


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "kryptex.db"
        build(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def execute(self, sql, *params):
        with sqlite3.connect(self.db) as connection:
            connection.execute(sql, params)

    def test_profitability_uses_newest_reading_and_coefficient(self):
        result = kb.read_profitability(self.db, NOW_MS)
        self.assertEqual(result["daily_usd"], 0.8)
        self.assertEqual(result["monthly_usd"], 24.0)
        # Newest BTC rate 0.00001 BTC per USD.
        self.assertEqual(result["daily_btc"], 0.000008)
        self.assertEqual(result["monthly_btc"], 0.00024)
        self.assertEqual(result["device_count"], 2)
        self.assertEqual([d["daily_btc"] for d in result["devices"]], [0.000003, 0.000005])
        self.assertEqual([d["id"] for d in result["devices"]], ["1", "2"])
        self.assertEqual(result["measurement_age_seconds"], 5.0)

    def test_estimate_uses_last_mining_reading(self):
        # Paused: newer readings have no hashrates. The estimate still uses the last mining reading (id 2).
        with sqlite3.connect(self.db) as connection:
            connection.execute("INSERT INTO reading VALUES (3, ?)", (NOW_MS - 1_000,))
            connection.execute("INSERT INTO process_device_reading VALUES (4, 3, 10, 100, 0.0)")
        estimate = kb.read_estimate(self.db, NOW_MS + 3_600_000)
        self.assertEqual(estimate["daily_usd"], 0.8)
        self.assertEqual(estimate["daily_btc"], 0.000008)
        self.assertEqual(estimate["hashrate_measured_at"], kb.iso_time(NOW_MS - 5_000))
        with self.assertRaises(kb.ReadingError) as error:
            kb.read_profitability(self.db, NOW_MS)
        self.assertEqual(error.exception.code, "no_income")

    def test_estimate_ignores_a_partial_reading_when_mining_stops(self):
        # Ten full readings (0.80 USD/day each), then one where only the CPU still mined (0.50 USD/day).
        with sqlite3.connect(self.db) as connection:
            for n in range(10):
                reading_id, row_id = 10 + n, 10 + 2 * n
                connection.execute("INSERT INTO reading VALUES (?, ?)", (reading_id, NOW_MS + 5_000 * (n + 1)))
                connection.execute("INSERT INTO process_device_reading VALUES (?, ?, 10, 100, 30.0)", (row_id, reading_id))
                connection.execute("INSERT INTO process_device_reading VALUES (?, ?, 12, 101, 5000.0)", (row_id + 1, reading_id))
            connection.execute("INSERT INTO reading VALUES (30, ?)", (NOW_MS + 60_000,))
            connection.execute("INSERT INTO process_device_reading VALUES (40, 30, 12, 101, 5000.0)")
        estimate = kb.read_estimate(self.db, NOW_MS + 120_000)
        self.assertEqual(estimate["daily_usd"], 0.8)
        self.assertEqual(estimate["last_mining_at"], kb.iso_time(NOW_MS + 60_000))
        self.assertEqual(estimate["readings_used"], 13)  # the 10 above, the fixture's 2 and the partial one

    def test_estimate_uses_at_most_the_last_readings(self):
        with mock.patch.object(kb, "ESTIMATE_READINGS", 1):
            estimate = kb.read_estimate(self.db, NOW_MS)
        self.assertEqual((estimate["readings_used"], estimate["daily_usd"]), (1, 0.8))

    def test_estimate_lasts_longer_than_the_live_income(self):
        two_days = 2 * 24 * 3_600_000
        self.assertEqual(kb.read_estimate(self.db, NOW_MS + two_days)["daily_usd"], 0.8)
        with self.assertRaises(kb.ReadingError) as error:
            kb.read_profitability(self.db, NOW_MS + two_days)
        self.assertEqual(error.exception.code, "stale_measurement")
        with self.assertRaises(kb.ReadingError) as error:
            kb.read_estimate(self.db, NOW_MS + 8 * 24 * 3_600_000)
        self.assertIn(error.exception.code, ("stale_measurement", "stale_coefficient"))

    def test_balance_in_btc(self):
        result = kb.read_balance(self.db, NOW_MS)
        self.assertEqual(result["total_btc"], 0.00012345)
        self.assertEqual(result["withdrawable_btc"], 0.0001)
        self.assertEqual(result["total_usd"], 12.35)
        self.assertEqual(result["age_seconds"], 600.0)

    def test_rates_are_bitcoin_prices(self):
        result = kb.read_rates(self.db, NOW_MS)
        self.assertEqual(result["btc_price"], {"eur": 90000.0, "usd": 100000.0})

    def test_missing_btc_rate(self):
        self.execute("DELETE FROM currency WHERE name = 'btc'")
        self.assert_error(kb.read_profitability, "no_btc_rate")
        self.assert_error(kb.read_balance, "no_btc_rate")
        self.assert_error(kb.read_rates, "no_btc_rate")

    def test_database_is_not_modified(self):
        before = self.db.read_bytes()
        kb.read_profitability(self.db, NOW_MS)
        kb.read_balance(self.db, NOW_MS)
        kb.read_rates(self.db, NOW_MS)
        self.assertEqual(self.db.read_bytes(), before)

    def assert_error(self, reader, code, now_ms=NOW_MS):
        with self.assertRaises(kb.ReadingError) as context:
            reader(self.db, now_ms)
        self.assertEqual(context.exception.code, code)

    def test_stale_and_future_measurements(self):
        self.assert_error(kb.read_profitability, "stale_measurement", NOW_MS + 200_000)
        self.assert_error(kb.read_profitability, "future_timestamp", NOW_MS - 100_000)
        self.assert_error(kb.read_balance, "future_timestamp", NOW_MS - 700_000)

    def test_stale_coefficient(self):
        self.execute("UPDATE coin_algorithm_profitability SET updated_at = ?", NOW_MS - 2 * 86_400_000)
        self.assert_error(kb.read_profitability, "stale_coefficient")

    def test_incomplete_and_duplicate_rows(self):
        self.execute("INSERT INTO process_device_reading VALUES (4, 2, 10, 100, 1.0)")
        self.assert_error(kb.read_profitability, "duplicate_measurement")
        self.execute("DELETE FROM process_device_reading WHERE id = 4")
        self.execute("DELETE FROM device WHERE id = 2")
        self.assert_error(kb.read_profitability, "incomplete_measurement")

    def test_no_income(self):
        self.execute("UPDATE process_device_reading SET hashrate = 0")
        self.assert_error(kb.read_profitability, "no_income")
        self.execute("DELETE FROM process_device_reading")
        self.assert_error(kb.read_profitability, "no_hashrates")
        self.execute("DELETE FROM reading")
        self.assert_error(kb.read_profitability, "no_measurement")

    def test_balance_rows(self):
        self.execute("INSERT INTO balance VALUES (2, 1, 1, ?)", NOW_MS)
        self.assert_error(kb.read_balance, "multiple_balances")
        self.execute("DELETE FROM balance")
        self.assert_error(kb.read_balance, "no_balance")

    def test_invalid_number(self):
        self.execute("UPDATE balance SET total = 'abc'")
        self.assert_error(kb.read_balance, "invalid_measurement")

    def test_safe_read_hides_numbers_on_failure(self):
        self.assertEqual(kb.safe_read(kb.read_balance, Path(self.tmp.name) / "missing.db")["error"], "database_missing")
        self.execute("DROP TABLE balance")
        result = kb.safe_read(kb.read_balance, self.db)
        self.assertEqual(result, {"ok": False, "error": "database_unavailable", "message": result["message"]})


class FakeService:
    """Stands in for the Kryptex service API on 127.0.0.1 (answers recorded from Kryptex 5.10.7, ids anonymized)."""

    def __init__(self):
        self.paused = False
        self.update = None
        self.ignore_put = False
        self.requests = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def answer(self, body):
                data = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                fake.requests.append(("GET", self.path))
                answers = {"/version": {"version": "5.10.7"}, "/update": fake.update, "/pause": {"pause": fake.paused},
                           "/devices": DEVICES}
                if self.path not in answers:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.answer(answers[self.path])

            def do_PUT(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.requests.append(("PUT", self.path, body))
                if not fake.ignore_put:
                    fake.paused = body["pause"]
                self.answer({"pause": fake.paused})

        self.server = kb.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "kryptex.db"
        build(self.db)
        self.fake = FakeService()
        self.service = kb.KryptexService(self.fake.url)

    def tearDown(self):
        self.fake.close()
        self.tmp.cleanup()

    def test_devices_version_update_and_pause(self):
        result = kb.read_service(self.service, self.db)
        self.assertTrue(result["ok"])
        self.assertEqual(result["version"], "5.10.7")
        self.assertEqual(result["update"], {"available": False, "version": None})
        self.assertIs(result["paused"], False)
        self.assertEqual([d["id"] for d in result["devices"]], ["1", "2", "4"])
        cpu, gpu = result["devices"][0], result["devices"][1]
        self.assertEqual((cpu["type"], cpu["miner"], cpu["miner_version"], cpu["coin"]), ("cpu", "xmrig", "v6.26.0", "xmr"))
        self.assertIsNone(cpu["temperature"])  # Kryptex does not report CPU temperature or power.
        self.assertIsNone(cpu["power"])
        self.assertEqual((gpu["temperature"], gpu["power"], gpu["fan"], gpu["memory_mb"]), (83.0, 339.0, 100.0, 12288.0))
        self.assertEqual(gpu["algorithm"], "pearlhash")
        self.assertEqual(gpu["state"], "mining")
        self.assertEqual(gpu["daily_btc"], 0.0000255)  # 2.55 USD/day x 0.00001 BTC/USD
        self.assertNotIn("uuid", json.dumps(result))  # Hardware ids are not passed on.

    def test_update_offered(self):
        self.fake.update = {"version": "5.11.0"}
        self.assertEqual(kb.read_service(self.service, self.db)["update"], {"available": True, "version": "5.11.0"})
        self.fake.update = {"url": "x"}
        self.assertEqual(kb.read_service(self.service, self.db)["update"], {"available": True, "version": None})

    def test_devices_without_btc_rate_have_no_income(self):
        with sqlite3.connect(self.db) as connection:
            connection.execute("DELETE FROM currency WHERE name = 'btc'")
        result = kb.read_service(self.service, self.db)
        self.assertEqual(len(result["devices"]), 3)
        self.assertIsNone(result["devices"][0]["daily_btc"])

    def test_service_down(self):
        result = kb.read_service(kb.KryptexService("http://127.0.0.1:9"), self.db)
        self.assertEqual(result["error"], "service_unavailable")
        self.assertNotIn("devices", result)

    def test_pause_and_resume_are_read_back(self):
        bridge = kb.Bridge({**kb.DEFAULTS, "api_key": "k", "database": str(self.db), "service_url": self.fake.url})
        self.assertEqual(bridge.set_pause(True), {"ok": True, "changed": True, "paused": True})
        self.assertEqual(bridge.set_pause(True), {"ok": True, "changed": False, "paused": True})
        self.assertEqual(bridge.set_pause(False), {"ok": True, "changed": True, "paused": False})
        puts = [r for r in self.fake.requests if r[0] == "PUT"]
        self.assertEqual(puts, [("PUT", "/pause", {"pause": True}), ("PUT", "/pause", {"pause": False})])
        self.fake.ignore_put = True
        self.assertEqual(bridge.set_pause(True)["error"], "pause_not_applied")

    def test_normalize_skips_unknown_items(self):
        self.assertIsNone(kb.normalize_device({"device": {}}, None))
        self.assertIsNone(kb.normalize_device("x", None))
        idle = kb.normalize_device({"device": {"id": 9, "name": "GPU"}, "state": {"state": "idle"}}, None)
        self.assertEqual((idle["state"], idle["miner"], idle["coin"], idle["daily_btc"]), ("idle", None, None, None))


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db = Path(self.tmp.name) / "kryptex.db"
        build(db)
        self.fake = FakeService()
        config = {**kb.DEFAULTS, "api_key": "secret-key", "database": str(db), "port": 0, "host": "127.0.0.1",
                  "service_url": self.fake.url}
        self.bridge = kb.Bridge(config)
        self.server = kb.ThreadingHTTPServer(("127.0.0.1", 0), kb.make_handler(self.bridge))
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        # Freeze the clock used by the readers.
        patcher = mock.patch.object(kb.time, "time", return_value=NOW_MS / 1000)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.fake.close()
        self.tmp.cleanup()

    def request(self, path, method="GET", key="secret-key"):
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        request = urllib.request.Request(self.base + path, method=method, headers=headers, data=b"" if method == "POST" else None)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def test_requires_api_key(self):
        self.assertEqual(self.request("/health", key=None)[0], 401)
        self.assertEqual(self.request("/health", key="wrong")[0], 401)
        self.assertEqual(self.request("/start", "POST", key="wrong")[0], 401)
        self.assertEqual(self.request("/pause", "POST", key="wrong")[0], 401)
        self.assertFalse(self.fake.requests)

    def test_status(self):
        with mock.patch.object(kb, "is_running", return_value=True):
            status, body = self.request("/status")
        self.assertEqual(status, 200)
        self.assertTrue(body["running"])
        self.assertEqual(body["profitability"]["monthly_btc"], 0.00024)
        self.assertEqual(body["balance"]["total_btc"], 0.00012345)
        self.assertEqual(body["rates"]["btc_price"]["eur"], 90000.0)
        self.assertEqual(body["service"]["version"], "5.10.7")
        self.assertEqual(len(body["service"]["devices"]), 3)
        self.assertEqual(body["estimate"]["daily_usd"], 0.8)

    def test_no_income_while_paused(self):
        # Kryptex keeps the last hashrates in its database for a while after a pause: the bridge must not report
        # them as income now. The estimate stays.
        self.fake.paused = True
        with mock.patch.object(kb, "is_running", return_value=True):
            body = self.request("/status")[1]
        self.assertEqual(body["profitability"], {"ok": False, "error": "paused", "message": "Kryptex is paused."})
        self.assertEqual(body["estimate"]["daily_usd"], 0.8)
        self.assertEqual(self.request("/profitability"), (503, body["profitability"]))
        self.assertEqual(self.request("/estimate")[1]["daily_usd"], 0.8)

    def test_single_readers_and_errors(self):
        self.assertEqual(self.request("/profitability")[1]["daily_usd"], 0.8)
        self.assertEqual(self.request("/balance")[1]["withdrawable_btc"], 0.0001)
        self.assertEqual(self.request("/rates")[1]["btc_price"]["usd"], 100000.0)
        self.assertEqual(self.request("/service")[1]["paused"], False)
        self.assertEqual(self.request("/nope")[0], 404)
        self.assertEqual(self.request("/start")[0], 404)  # GET does not control anything.
        self.assertEqual(self.request("/pause")[0], 404)
        Path(self.bridge.database).unlink()
        status, body = self.request("/balance")
        self.assertEqual((status, body["error"]), (503, "database_missing"))

    def test_pause_and_resume(self):
        self.assertEqual(self.request("/pause", "POST"), (200, {"ok": True, "changed": True, "paused": True}))
        self.assertTrue(self.fake.paused)
        self.assertEqual(self.request("/resume", "POST"), (200, {"ok": True, "changed": True, "paused": False}))
        self.fake.close()
        self.assertEqual(self.request("/pause", "POST")[0], 500)

    def test_start_and_stop(self):
        # States read by is_running: before the command, then after it (the tasks return at once).
        with mock.patch.object(kb, "is_running", side_effect=[False, True]), \
                mock.patch.object(kb, "run_command", return_value={"ok": True}) as run:
            self.assertEqual(self.request("/start", "POST"), (200, {"ok": True, "changed": True}))
            run.assert_called_once_with(self.bridge.config["start_command"], detached=False)
        with mock.patch.object(kb, "is_running", return_value=False):
            self.assertEqual(self.request("/stop", "POST"), (200, {"ok": True, "changed": False}))
        with mock.patch.object(kb, "is_running", side_effect=[True, True, False]), mock.patch.object(kb.time, "sleep"), \
                mock.patch.object(kb, "run_command", return_value={"ok": True}) as run:
            self.assertEqual(self.request("/stop", "POST"), (200, {"ok": True, "changed": True}))
            run.assert_called_once_with(self.bridge.config["stop_command"], detached=False)
        with mock.patch.object(kb, "is_running", return_value=True):
            self.assertEqual(self.request("/start", "POST"), (200, {"ok": True, "changed": False}))
        failure = {"ok": False, "error": "command_failed", "message": "x"}
        with mock.patch.object(kb, "is_running", return_value=False), mock.patch.object(kb, "run_command", return_value=failure):
            self.assertEqual(self.request("/start", "POST")[0], 500)

    def test_stop_that_does_not_apply_is_an_error(self):
        # E.g. Kryptex runs as administrator and the stop task is missing: report it instead of claiming success.
        with mock.patch.object(kb, "is_running", return_value=True), mock.patch.object(kb, "CONTROL_WAIT_SECONDS", 0), \
                mock.patch.object(kb, "run_command", return_value={"ok": True}):
            status, body = self.request("/stop", "POST")
        self.assertEqual((status, body["error"]), (500, "not_applied"))

    def test_default_commands_use_the_elevated_tasks(self):
        self.assertEqual(kb.DEFAULTS["start_command"], ["schtasks", "/run", "/tn", "Kryptex bridge start"])
        self.assertEqual(kb.DEFAULTS["stop_command"], ["schtasks", "/run", "/tn", "Kryptex bridge stop"])


class ConfigTests(unittest.TestCase):
    def test_config_with_bom_and_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text('{"api_key": "k", "port": 5600}', encoding="utf-8-sig")
            config = kb.load_config(path)
            self.assertEqual((config["api_key"], config["port"]), ("k", 5600))
            self.assertEqual(config["service_url"], "http://127.0.0.1:8107")
            path.write_text('{"port": 5600}', encoding="utf-8")
            with self.assertRaises(SystemExit):
                kb.load_config(path)
            path.write_text('{"api_key": "k", "stop_command": "taskkill"}', encoding="utf-8")
            with self.assertRaises(SystemExit):
                kb.load_config(path)

    def test_service_url_must_be_local(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            for url in ("http://192.168.1.5:8107", "https://127.0.0.1:8107", "file:///c:/x"):
                path.write_text(json.dumps({"api_key": "k", "service_url": url}), encoding="utf-8")
                with self.assertRaises(SystemExit):
                    kb.load_config(path)


if __name__ == "__main__":
    unittest.main()

"""Builds a SQLite file with the tables and columns the bridge reads from Kryptex's kryptex.db.

The schema covers only what the bridge queries. Values are synthetic: two devices, three algorithm readings.
"""

import sqlite3

SCHEMA = """
CREATE TABLE reading (id INTEGER PRIMARY KEY, timestamp INTEGER);
CREATE TABLE device (id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE process_device (id INTEGER PRIMARY KEY, device_id INTEGER);
CREATE TABLE coin (id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE algorithm (id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE coin_algorithm (id INTEGER PRIMARY KEY, coin_id INTEGER, algorithm_id INTEGER);
CREATE TABLE coin_algorithm_profitability (id INTEGER PRIMARY KEY, coin_algorithm_id INTEGER, profitability REAL, updated_at INTEGER);
CREATE TABLE process_device_reading (id INTEGER PRIMARY KEY, reading_id INTEGER, process_device_id INTEGER, coin_algorithm_id INTEGER, hashrate REAL);
CREATE TABLE balance (id INTEGER PRIMARY KEY, total REAL, withdrawable REAL, updated_at INTEGER);
CREATE TABLE currency (id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE currency_rate (id INTEGER PRIMARY KEY, currency_id INTEGER, rate REAL, updated_at INTEGER);
"""

NOW_MS = 1_790_000_000_000  # Fixed clock for the tests.


def build(path, now_ms=NOW_MS):
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA)
    connection.executemany("INSERT INTO device VALUES (?, ?)", [(1, "GPU 0"), (2, "CPU")])
    connection.executemany("INSERT INTO process_device VALUES (?, ?)", [(10, 1), (11, 1), (12, 2)])
    connection.executemany("INSERT INTO coin VALUES (?, ?)", [(1, "etc"), (2, "xmr")])
    connection.executemany("INSERT INTO algorithm VALUES (?, ?)", [(1, "etchash"), (2, "randomx")])
    connection.executemany("INSERT INTO coin_algorithm VALUES (?, ?, ?)", [(100, 1, 1), (101, 2, 2)])
    # Old and new coefficient for 100: the newest one must be used.
    connection.executemany(
        "INSERT INTO coin_algorithm_profitability VALUES (?, ?, ?, ?)",
        [(1, 100, 0.5, now_ms - 7_200_000), (2, 100, 0.01, now_ms - 3_600_000), (3, 101, 0.0001, now_ms - 3_600_000)],
    )
    connection.executemany("INSERT INTO reading VALUES (?, ?)", [(1, now_ms - 60_000), (2, now_ms - 5_000)])
    connection.executemany(
        "INSERT INTO process_device_reading VALUES (?, ?, ?, ?, ?)",
        [
            (1, 1, 10, 100, 999.0),  # old reading, ignored
            (2, 2, 10, 100, 30.0),  # 30 x 0.01 = 0.30 USD/day
            (3, 2, 12, 101, 5000.0),  # 5000 x 0.0001 = 0.50 USD/day
        ],
    )
    connection.execute("INSERT INTO balance VALUES (1, 12.345, 10.0, ?)", (now_ms - 600_000,))
    connection.executemany("INSERT INTO currency VALUES (?, ?)", [(1, "btc"), (2, "usd"), (3, "eur")])
    # Rates are units of the currency per 1 USD; the newest row per currency must be used.
    connection.executemany(
        "INSERT INTO currency_rate VALUES (?, ?, ?, ?)",
        [
            (1, 1, 0.00002, now_ms - 9_000_000), (2, 1, 0.00001, now_ms - 60_000),  # 1 BTC = 100 000 USD
            (3, 2, 1.0, now_ms - 60_000), (4, 3, 0.9, now_ms - 120_000),  # 1 BTC = 90 000 EUR
        ],
    )
    connection.commit()
    connection.close()

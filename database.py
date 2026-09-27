"""
database.py
-----------
Persistence layer for SmartPark KE.

Implements the dynamic relational schema designed in Task One:
    SLOT            - every physical parking bay and its live status
    VEHICLE_SESSION - one row per vehicle visit, from entry to exit
    TRANSACTION     - one payment record per completed session
    RATE_TIER       - the fee bands, kept as DATA so management can
                       change prices without touching the code

Using SQLite keeps the project dependency-free and easy to mark/run,
while the schema itself is ordinary relational design that would
port unchanged to PostgreSQL/MySQL in production.
"""

import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "smartpark.db")

# Default number of bays created the first time the database is built.
DEFAULT_SLOT_COUNT = 20

# Default fee tiers, taken directly from the client's terms of reference.
DEFAULT_RATE_TIERS = [
    (30, 0),      # up to 30 minutes: free
    (120, 50),    # up to 2 hours: Kshs. 50
    (240, 100),   # up to 4 hours: Kshs. 100
    (360, 300),   # up to 6 hours: Kshs. 300
    (999999, 500),  # over 6 hours: Kshs. 500
]


def get_conn():
    """Return a SQLite connection with row access by column name."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """
    Create tables if they do not exist yet, and seed default slots
    and rate tiers on first run only. Safe to call on every app start.
    """
    conn = get_conn()
    cur = conn.cursor()

    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS slot (
            slot_id     INTEGER PRIMARY KEY AUTOINCREMENT,
            slot_code   TEXT NOT NULL UNIQUE,
            zone        TEXT NOT NULL DEFAULT 'A',
            status      TEXT NOT NULL DEFAULT 'FREE'
                        CHECK (status IN ('FREE', 'OCCUPIED', 'OUT_OF_SERVICE'))
        );

        CREATE TABLE IF NOT EXISTS vehicle_session (
            ticket_id     INTEGER PRIMARY KEY AUTOINCREMENT,
            plate_number  TEXT NOT NULL,
            slot_id       INTEGER NOT NULL REFERENCES slot(slot_id),
            entry_time    TEXT NOT NULL,
            exit_time     TEXT,
            status        TEXT NOT NULL DEFAULT 'ACTIVE'
                          CHECK (status IN ('ACTIVE', 'CLOSED'))
        );

        CREATE TABLE IF NOT EXISTS "transaction" (
            transaction_id  INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id       INTEGER NOT NULL REFERENCES vehicle_session(ticket_id),
            amount          REAL NOT NULL,
            payment_method  TEXT NOT NULL DEFAULT 'CASH',
            paid_at         TEXT NOT NULL,
            phone_number    TEXT,
            mpesa_receipt   TEXT
        );

        CREATE TABLE IF NOT EXISTS rate_tier (
            tier_id      INTEGER PRIMARY KEY AUTOINCREMENT,
            max_minutes  INTEGER NOT NULL,
            fee          REAL NOT NULL
        );
        """
    )

    # Migration for databases created before the M-Pesa columns existed:
    # add them if a marker's existing smartpark.db predates this change.
    existing_cols = {row["name"] for row in cur.execute('PRAGMA table_info("transaction")')}
    if "phone_number" not in existing_cols:
        cur.execute('ALTER TABLE "transaction" ADD COLUMN phone_number TEXT')
    if "mpesa_receipt" not in existing_cols:
        cur.execute('ALTER TABLE "transaction" ADD COLUMN mpesa_receipt TEXT')

    # Seed slots only if the table is empty (first run).
    cur.execute("SELECT COUNT(*) AS n FROM slot")
    if cur.fetchone()["n"] == 0:
        for i in range(1, DEFAULT_SLOT_COUNT + 1):
            cur.execute(
                "INSERT INTO slot (slot_code, zone, status) VALUES (?, 'A', 'FREE')",
                (f"A{i:02d}",),
            )

    # Seed rate tiers only if the table is empty (first run).
    cur.execute("SELECT COUNT(*) AS n FROM rate_tier")
    if cur.fetchone()["n"] == 0:
        cur.executemany(
            "INSERT INTO rate_tier (max_minutes, fee) VALUES (?, ?)",
            DEFAULT_RATE_TIERS,
        )

    conn.commit()
    conn.close()

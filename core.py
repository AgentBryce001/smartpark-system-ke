"""
core.py
-------
The algorithmic heart of SmartPark KE. This module is a direct
implementation of the design produced in Task One:

  * Slot Management Module   -> SlotManager (min-heap of free slot ids)
  * Vehicle Entry Module     -> register_entry() (hash map of active sessions)
  * Fee Calculation Module   -> calculate_fee() (rate tiers loaded from DB)
  * Payment & Barrier Module -> process_exit()

The free-slot heap and the active-vehicle hash map are kept in memory
for O(log n) / O(1) access on the hot path (every entry and every
exit), while SQLite remains the source of truth so nothing is lost
if the app restarts.
"""

import heapq
import random
import re
import string
import time
from datetime import datetime

from database import get_conn

# In-memory table of M-Pesa payments that have been "requested" (STK push
# sent) but not yet confirmed by the driver entering their PIN. Keyed by
# ticket_id. This models the real Safaricom Daraja flow (STK push -> customer
# enters PIN on their phone -> callback confirms payment) without needing
# live Daraja API credentials for a coursework deployment.
_pending_mpesa = {}

# In-memory hash map: ticket_id -> dict(plate_number, slot_id, entry_time)
# This is the "Hash map: ticketId -> Vehicle record" from Task One, Section 5.
active_vehicles = {}

# In-memory min-heap of free slot ids. Rebuilt from the database on startup
# so the lowest-numbered free slot is always allocated first (Section 4.1).
_free_slots_heap = []


class NoSlotAvailable(Exception):
    """Raised when the car park is full."""


class TicketNotFound(Exception):
    """Raised when an exit/payment is requested for an unknown ticket."""


class PaymentDeclined(Exception):
    """Raised when the amount tendered is less than the fee due."""


# ---------------------------------------------------------------------------
# Slot Management Module  (Task One, Section 4.1)
# ---------------------------------------------------------------------------

def load_free_slots():
    """Rebuild the free-slot min-heap from the database. Call on app startup."""
    global _free_slots_heap
    conn = get_conn()
    rows = conn.execute(
        "SELECT slot_id FROM slot WHERE status = 'FREE' ORDER BY slot_id"
    ).fetchall()
    conn.close()
    _free_slots_heap = [row["slot_id"] for row in rows]
    heapq.heapify(_free_slots_heap)

    # Also rebuild the active-vehicle hash map from any sessions left ACTIVE
    # (e.g. after a restart), so in-memory state matches the database.
    conn = get_conn()
    rows = conn.execute(
        "SELECT ticket_id, plate_number, slot_id, entry_time "
        "FROM vehicle_session WHERE status = 'ACTIVE'"
    ).fetchall()
    conn.close()
    active_vehicles.clear()
    for row in rows:
        active_vehicles[row["ticket_id"]] = {
            "plate_number": row["plate_number"],
            "slot_id": row["slot_id"],
            "entry_time": row["entry_time"],
        }


def allocate_slot(conn):
    """
    ALGORITHM AllocateSlot (Task One 4.1): pop the lowest-numbered free
    slot id from the heap in O(log n); mark it OCCUPIED in the database.
    """
    if not _free_slots_heap:
        raise NoSlotAvailable("No slot available")
    slot_id = heapq.heappop(_free_slots_heap)
    conn.execute("UPDATE slot SET status = 'OCCUPIED' WHERE slot_id = ?", (slot_id,))
    return slot_id


def release_slot(conn, slot_id):
    """
    ALGORITHM ReleaseSlot (Task One 4.1): push the slot back onto the
    heap in O(log n) and mark it FREE in the database.
    """
    conn.execute("UPDATE slot SET status = 'FREE' WHERE slot_id = ?", (slot_id,))
    heapq.heappush(_free_slots_heap, slot_id)


def get_all_slots():
    """Return every slot with its live status, for the visual display."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT slot_id, slot_code, zone, status FROM slot ORDER BY slot_id"
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Vehicle Entry Module  (Task One, Section 4.2)
# ---------------------------------------------------------------------------

def register_entry(plate_number):
    """
    ALGORITHM RegisterEntry (Task One 4.2): allocate a slot, timestamp the
    arrival, insert into the active-vehicle hash map (O(1) average) and
    persist the session row. Returns the new ticket_id.
    """
    conn = get_conn()
    try:
        slot_id = allocate_slot(conn)
        entry_time = datetime.now().isoformat(timespec="seconds")
        cur = conn.execute(
            "INSERT INTO vehicle_session (plate_number, slot_id, entry_time, status) "
            "VALUES (?, ?, ?, 'ACTIVE')",
            (plate_number.strip().upper(), slot_id, entry_time),
        )
        ticket_id = cur.lastrowid
        conn.commit()

        active_vehicles[ticket_id] = {
            "plate_number": plate_number.strip().upper(),
            "slot_id": slot_id,
            "entry_time": entry_time,
        }

        slot_code = conn.execute(
            "SELECT slot_code FROM slot WHERE slot_id = ?", (slot_id,)
        ).fetchone()["slot_code"]
        return {"ticket_id": ticket_id, "slot_code": slot_code, "entry_time": entry_time}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Fee Calculation Module  (Task One, Section 4.3)
# ---------------------------------------------------------------------------

def _get_rate_tiers(conn):
    """Load the tiered rate schedule, ordered ascending — data, not code."""
    rows = conn.execute(
        "SELECT max_minutes, fee FROM rate_tier ORDER BY max_minutes ASC"
    ).fetchall()
    return [(row["max_minutes"], row["fee"]) for row in rows]


def calculate_fee(ticket_id, at_time=None):
    """
    ALGORITHM CalculateFee (Task One 4.3): look the vehicle up in the
    hash map (O(1) average), find elapsed minutes, then scan the small
    ordered rate-tier list for the first band the duration fits into.
    """
    vehicle = active_vehicles.get(ticket_id)
    if vehicle is None:
        raise TicketNotFound(f"Ticket {ticket_id} not found or already closed")

    entry_time = datetime.fromisoformat(vehicle["entry_time"])
    now = at_time or datetime.now()
    minutes = max(0, (now - entry_time).total_seconds() / 60.0)

    conn = get_conn()
    try:
        tiers = _get_rate_tiers(conn)
    finally:
        conn.close()

    fee = tiers[-1][1]  # default to the top (over-the-limit) tier
    for max_minutes, tier_fee in tiers:
        if minutes <= max_minutes:
            fee = tier_fee
            break

    return {
        "ticket_id": ticket_id,
        "plate_number": vehicle["plate_number"],
        "slot_id": vehicle["slot_id"],
        "minutes_parked": round(minutes, 1),
        "fee": fee,
    }


# ---------------------------------------------------------------------------
# Payment & Barrier Control Module  (Task One, Section 4.4)
# ---------------------------------------------------------------------------

def process_exit(ticket_id, amount_tendered, payment_method="CASH",
                  phone_number=None, mpesa_receipt=None):
    """
    ALGORITHM ProcessExit (Task One 4.4): recompute the fee at the moment
    of payment, reject under-payment, otherwise close the session, record
    the transaction, release the slot back to the heap, and open the barrier.

    phone_number / mpesa_receipt are only populated for M-Pesa payments
    (see the M-Pesa Payment Module below) and stored on the transaction
    row for the receipt and admin reports.
    """
    bill = calculate_fee(ticket_id)

    if amount_tendered < bill["fee"]:
        raise PaymentDeclined(
            f"Amount tendered (Kshs. {amount_tendered}) is less than the fee due "
            f"(Kshs. {bill['fee']})"
        )

    conn = get_conn()
    try:
        vehicle = active_vehicles.pop(ticket_id)  # O(1) average removal
        exit_time = datetime.now().isoformat(timespec="seconds")

        conn.execute(
            "UPDATE vehicle_session SET exit_time = ?, status = 'CLOSED' "
            "WHERE ticket_id = ?",
            (exit_time, ticket_id),
        )
        conn.execute(
            "INSERT INTO \"transaction\" "
            "(ticket_id, amount, payment_method, paid_at, phone_number, mpesa_receipt) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ticket_id, bill["fee"], payment_method, exit_time, phone_number, mpesa_receipt),
        )
        release_slot(conn, vehicle["slot_id"])
        conn.commit()
    finally:
        conn.close()

    return {
        "ticket_id": ticket_id,
        "plate_number": vehicle["plate_number"],
        "fee_paid": bill["fee"],
        "minutes_parked": bill["minutes_parked"],
        "barrier": "OPEN",
        "payment_method": payment_method,
        "phone_number": phone_number,
        "mpesa_receipt": mpesa_receipt,
    }


# ---------------------------------------------------------------------------
# M-Pesa Payment Module
# ---------------------------------------------------------------------------
# Models the Safaricom Daraja "STK Push" (Lipa na M-Pesa Online) flow:
#   1. Driver enters their M-Pesa phone number for the exact fee due.
#   2. initiate_mpesa_payment() records a PENDING request — in production
#      this is the point at which Daraja would push a PIN prompt to the
#      driver's phone.
#   3. The driver "enters their PIN" (simulated here with a confirm step,
#      since coursework deployments have no live Daraja credentials).
#   4. confirm_mpesa_payment() plays the role of Daraja's payment callback:
#      it verifies the request is still pending, generates the M-Pesa
#      receipt code, and calls process_exit() to close the session and
#      open the barrier — identical to the cash/card path from there.

_SAFARICOM_PREFIXES = ("07", "01", "2547", "2541", "+2547", "+2541")


def validate_mpesa_phone(phone_number):
    """
    Accepts common Kenyan phone formats (07XXXXXXXX, 01XXXXXXXX,
    2547XXXXXXXX, +2547XXXXXXXX) and normalises to 2547XXXXXXXX /
    2541XXXXXXXX. Raises ValueError if the format is not recognised.
    """
    digits = re.sub(r"\D", "", phone_number or "")

    if digits.startswith("0") and len(digits) == 10:
        digits = "254" + digits[1:]
    elif digits.startswith("254") and len(digits) == 12:
        pass
    elif digits.startswith("7") or digits.startswith("1"):
        if len(digits) == 9:
            digits = "254" + digits

    if not re.fullmatch(r"254(7|1)\d{8}", digits):
        raise ValueError(
            "Enter a valid Safaricom number, e.g. 0712345678 or 254712345678."
        )
    return digits


def initiate_mpesa_payment(ticket_id, phone_number):
    """
    Simulated STK push: validates the phone number and the ticket, then
    records a PENDING M-Pesa request against the current fee due.
    """
    phone = validate_mpesa_phone(phone_number)
    bill = calculate_fee(ticket_id)  # also confirms the ticket is still active

    _pending_mpesa[ticket_id] = {
        "phone_number": phone,
        "amount": bill["fee"],
        "requested_at": time.time(),
    }
    return {"ticket_id": ticket_id, "phone_number": phone, "amount": bill["fee"]}


def confirm_mpesa_payment(ticket_id):
    """
    Simulated Daraja callback: the driver has "entered their PIN", so the
    push is confirmed successful. Generates an M-Pesa-style receipt code
    and finalises the exit through the normal payment/barrier module.
    """
    pending = _pending_mpesa.pop(ticket_id, None)
    if pending is None:
        raise TicketNotFound("No pending M-Pesa request for this ticket")

    receipt_code = "".join(random.choices(string.ascii_uppercase + string.digits, k=10))

    return process_exit(
        ticket_id,
        amount_tendered=pending["amount"],
        payment_method="MPESA",
        phone_number=pending["phone_number"],
        mpesa_receipt=receipt_code,
    )


def cancel_mpesa_payment(ticket_id):
    """Driver backed out before entering their PIN, or the push timed out."""
    _pending_mpesa.pop(ticket_id, None)


# ---------------------------------------------------------------------------
# Administration & Reporting Module  (Task One, Section 3.5 / 6)
# ---------------------------------------------------------------------------

def get_rate_tiers():
    conn = get_conn()
    rows = conn.execute(
        "SELECT tier_id, max_minutes, fee FROM rate_tier ORDER BY max_minutes ASC"
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def update_rate_tier(tier_id, max_minutes, fee):
    """Let management edit a fee band without any code change."""
    conn = get_conn()
    conn.execute(
        "UPDATE rate_tier SET max_minutes = ?, fee = ? WHERE tier_id = ?",
        (max_minutes, fee, tier_id),
    )
    conn.commit()
    conn.close()


def add_slot(slot_code, zone="A"):
    """Let management grow the car park without any code change."""
    conn = get_conn()
    conn.execute(
        "INSERT INTO slot (slot_code, zone, status) VALUES (?, ?, 'FREE')",
        (slot_code, zone),
    )
    new_id = conn.execute(
        "SELECT slot_id FROM slot WHERE slot_code = ?", (slot_code,)
    ).fetchone()["slot_id"]
    conn.commit()
    conn.close()
    heapq.heappush(_free_slots_heap, new_id)


def get_occupancy_summary():
    conn = get_conn()
    total = conn.execute("SELECT COUNT(*) AS n FROM slot").fetchone()["n"]
    occupied = conn.execute(
        "SELECT COUNT(*) AS n FROM slot WHERE status = 'OCCUPIED'"
    ).fetchone()["n"]
    revenue_today = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS total FROM \"transaction\" "
        "WHERE date(paid_at) = date('now')"
    ).fetchone()["total"]
    conn.close()
    return {"total_slots": total, "occupied": occupied, "free": total - occupied,
            "revenue_today": revenue_today}


def get_recent_transactions(limit=20):
    conn = get_conn()
    rows = conn.execute(
        "SELECT t.transaction_id, t.ticket_id, v.plate_number, t.amount, "
        "t.payment_method, t.mpesa_receipt, t.paid_at "
        "FROM \"transaction\" t JOIN vehicle_session v ON v.ticket_id = t.ticket_id "
        "ORDER BY t.transaction_id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]

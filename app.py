"""
app.py
------
SmartPark KE — a modern, web-based parking management system.

Built for: Data Structures and Algorithms, Actual System Development task.
Multimedia University of Kenya.
Author: Bryson Muthomi Mutugi (CIT-223-024/2025)

Run with:
    pip install -r requirements.txt
    python app.py
Then open http://127.0.0.1:5000 in a browser.
"""

from flask import Flask, render_template, request, redirect, url_for, flash
import os

import core
from database import init_db

app = Flask(__name__)
app.secret_key = "smartpark-dev-secret"  # fine for coursework; use env var in production


@app.before_request
def _ensure_startup_state():
    # Cheap idempotent call; makes sure the in-memory heap/hash map are
    # populated even if the module-level load_free_slots() below hasn't
    # run yet (e.g. under some WSGI servers that reload workers).
    if not hasattr(app, "_initialised"):
        init_db()
        core.load_free_slots()
        app._initialised = True


# ---------------------------------------------------------------------------
# Driver-facing pages
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    """Home page: visual display of parking slots (Use Case: View Available Slots)."""
    slots = core.get_all_slots()
    summary = core.get_occupancy_summary()
    return render_template("index.html", slots=slots, summary=summary)


@app.route("/entry", methods=["POST"])
def entry():
    """Handle vehicle arrival (Use Case: Register Vehicle Entry)."""
    plate_number = request.form.get("plate_number", "").strip()
    if not plate_number:
        flash("Please enter a plate number.", "error")
        return redirect(url_for("index"))

    try:
        result = core.register_entry(plate_number)
        flash(
            f"Welcome! Ticket #{result['ticket_id']} — slot {result['slot_code']} "
            f"assigned at {result['entry_time']}.",
            "success",
        )
    except core.NoSlotAvailable:
        flash("Sorry, the parking is full. Please try again later.", "error")

    return redirect(url_for("index"))


@app.route("/exit", methods=["GET"])
def exit_lookup():
    """Page where a driver enters their ticket number before paying."""
    return render_template("exit.html", bill=None)


@app.route("/exit", methods=["POST"])
def exit_calculate():
    """Compute the fee due for a ticket (Use Case: Compute Parking Fee)."""
    ticket_id = request.form.get("ticket_id", "").strip()
    try:
        bill = core.calculate_fee(int(ticket_id))
    except (ValueError, core.TicketNotFound):
        flash("Ticket not found. Please check the number and try again.", "error")
        return redirect(url_for("exit_lookup"))

    return render_template("exit.html", bill=bill)


@app.route("/pay", methods=["POST"])
def pay():
    """Process a cash/card payment and open the barrier."""
    ticket_id = int(request.form.get("ticket_id"))
    amount = float(request.form.get("amount", 0))
    method = request.form.get("payment_method", "CASH")

    try:
        receipt = core.process_exit(ticket_id, amount, method)
        return render_template("receipt.html", receipt=receipt)
    except core.PaymentDeclined as e:
        flash(str(e), "error")
        bill = core.calculate_fee(ticket_id)
        return render_template("exit.html", bill=bill)
    except core.TicketNotFound:
        flash("Ticket not found.", "error")
        return redirect(url_for("exit_lookup"))


# ---------------------------------------------------------------------------
# M-Pesa payment UI (simulated STK push flow)
# ---------------------------------------------------------------------------

@app.route("/pay/mpesa/initiate", methods=["POST"])
def mpesa_initiate():
    """Driver has entered their phone number — simulate sending the STK push."""
    ticket_id = int(request.form.get("ticket_id"))
    phone_number = request.form.get("phone_number", "")

    try:
        push = core.initiate_mpesa_payment(ticket_id, phone_number)
        return render_template("mpesa.html", push=push)
    except ValueError as e:
        flash(str(e), "error")
        bill = core.calculate_fee(ticket_id)
        return render_template("exit.html", bill=bill)
    except core.TicketNotFound:
        flash("Ticket not found.", "error")
        return redirect(url_for("exit_lookup"))


@app.route("/pay/mpesa/confirm", methods=["POST"])
def mpesa_confirm():
    """Driver has 'entered their M-Pesa PIN' — simulate Daraja's confirmation callback."""
    ticket_id = int(request.form.get("ticket_id"))
    try:
        receipt = core.confirm_mpesa_payment(ticket_id)
        return render_template("receipt.html", receipt=receipt)
    except core.TicketNotFound:
        flash("That M-Pesa request has expired. Please try again.", "error")
        return redirect(url_for("exit_lookup"))


@app.route("/pay/mpesa/cancel", methods=["POST"])
def mpesa_cancel():
    """Driver cancelled the STK push before entering their PIN."""
    ticket_id = int(request.form.get("ticket_id"))
    core.cancel_mpesa_payment(ticket_id)
    flash("M-Pesa payment cancelled.", "error")
    bill = core.calculate_fee(ticket_id)
    return render_template("exit.html", bill=bill)


# ---------------------------------------------------------------------------
# Live JSON feed, so the slot display can auto-refresh without a full reload
# ---------------------------------------------------------------------------

@app.route("/api/slots")
def api_slots():
    return {"slots": core.get_all_slots(), "summary": core.get_occupancy_summary()}


# ---------------------------------------------------------------------------
# Administration & Reporting Module
# ---------------------------------------------------------------------------

@app.route("/admin")
def admin():
    return render_template(
        "admin.html",
        rate_tiers=core.get_rate_tiers(),
        slots=core.get_all_slots(),
        summary=core.get_occupancy_summary(),
        transactions=core.get_recent_transactions(),
    )


@app.route("/admin/rates/<int:tier_id>", methods=["POST"])
def admin_update_rate(tier_id):
    max_minutes = int(request.form.get("max_minutes"))
    fee = float(request.form.get("fee"))
    core.update_rate_tier(tier_id, max_minutes, fee)
    flash("Rate tier updated.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/slots", methods=["POST"])
def admin_add_slot():
    slot_code = request.form.get("slot_code", "").strip()
    zone = request.form.get("zone", "A").strip() or "A"
    if slot_code:
        core.add_slot(slot_code, zone)
        flash(f"Slot {slot_code} added.", "success")
    return redirect(url_for("admin"))


if __name__ == "__main__":
    init_db()
    core.load_free_slots()
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "1") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)

# SmartPark KE

A modern, web-based parking management system built for the **Data Structures
and Algorithms** "Actual System Development" assignment (Multimedia University
of Kenya).

It implements the design from Task One:

| Task One Module | Code |
|---|---|
| Slot Management (min-heap) | `core.py` — `allocate_slot`, `release_slot` |
| Vehicle Entry (hash map) | `core.py` — `register_entry`, `active_vehicles` |
| Fee Calculation (tiered rates) | `core.py` — `calculate_fee` |
| Payment & Barrier Control | `core.py` — `process_exit` |
| Administration & Reporting | `core.py` — rate/slot management, `app.py` `/admin` routes |
| Dynamic database | `database.py` — `slot`, `vehicle_session`, `transaction`, `rate_tier` tables |

## Features

- **Live visual slot display** on the home page (green = free, red = occupied),
  auto-refreshing every 5 seconds.
- **Vehicle entry**: enter a plate number, get a slot allocated and a ticket
  number.
- **Exit & pay**: enter a ticket number, see the fee computed from elapsed
  time against the tiered rate schedule, pay, and see the barrier "open".
- **M-Pesa payment**: a dedicated M-Pesa tab on the exit page — driver enters
  their Safaricom number, receives a simulated STK push prompt, "enters their
  PIN" to confirm, and gets a receipt with an M-Pesa transaction code. This
  mirrors the real Safaricom Daraja (Lipa na M-Pesa Online) flow; wiring it to
  the live Daraja API just means replacing the confirm button with Daraja's
  callback webhook (see `core.py` — M-Pesa Payment Module).
- **Admin dashboard**: edit fee tiers, add slots, and view today's revenue and
  recent transactions — all without changing any code, since prices and slot
  count are stored in the database (`rate_tier` / `slot` tables).

## Fee schedule (default, editable in Admin)

| Duration | Fee |
|---|---|
| Up to 30 minutes | Free |
| Up to 2 hours | Kshs. 50 |
| Up to 4 hours | Kshs. 100 |
| Up to 6 hours | Kshs. 300 |
| Over 6 hours | Kshs. 500 |

## Running locally

```bash
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000 in a browser. A SQLite database file
(`smartpark.db`) is created automatically on first run with 20 slots (A01–A20)
and the default fee tiers above.

## Project structure

```
smartpark/
├── app.py            # Flask routes (the web layer)
├── core.py           # Algorithms: slot allocation, fee calc, payment/barrier
├── database.py       # SQLite schema + seeding (the dynamic database)
├── requirements.txt
├── templates/         # Jinja2 HTML pages
│   ├── base.html
│   ├── index.html      (slot display + entry)
│   ├── exit.html        (fee lookup + payment method selection)
│   ├── mpesa.html        (simulated STK push confirmation)
│   ├── receipt.html     (barrier confirmation)
│   └── admin.html       (rates, slots, reports)
└── static/style.css
```

## Publishing to GitHub

```bash
cd smartpark
git init
git add .
git commit -m "SmartPark KE: web-based parking system"
git branch -M main
git remote add origin https://github.com/<your-username>/<repo-name>.git
git push -u origin main
```

Then submit the repository link as required by the brief.

## Notes for the marker

- The free-slot allocator is a binary min-heap (`heapq`) as justified in
  Task One §5 — O(log n) allocate/release versus an O(n) array scan.
- Active vehicles are kept in an in-memory hash map keyed by ticket ID
  (O(1) average lookup) and mirrored to SQLite so no data is lost on
  restart (`core.load_free_slots()` rebuilds both on startup).
- `smartpark.db` is created automatically and is not tracked in git
  (see `.gitignore`) so each marker gets a fresh, empty car park.

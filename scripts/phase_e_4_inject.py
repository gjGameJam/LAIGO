"""Phase E.4 — Mid-saga restart test harness.

Injects a synthetic in-flight saga at `stripe_held` into Neon + creates a
matching real Stripe TEST PaymentIntent. Used to exercise the lifespan
`resume_in_flight_sagas()` flow without placing real marketplace orders.

Usage:
  .venv\\Scripts\\python.exe -m scripts.phase_e_4_inject inject
  .venv\\Scripts\\python.exe -m scripts.phase_e_4_inject verify <checkout_id>
  .venv\\Scripts\\python.exe -m scripts.phase_e_4_inject cleanup <checkout_id>

After `inject`:
  1. The script prints job_id / checkout_id / hold_id.
  2. Kill uvicorn (if running) and restart it.
  3. Boot log should show `[resume] examined 1 in-flight sagas (all routed cleanly)`.
  4. Run `verify <checkout_id>` to inspect the post-resume state in Neon + Stripe.
  5. Run `cleanup <checkout_id>` to delete the synthetic rows.

NEVER run with sk_live_ keys. The script refuses to start if the secret key
prefix is not sk_test_.

NOT a production code path — raw SQL INSERTs bypass the dispatcher's API.
Intentionally so: this is dev infrastructure for one specific test.
"""

import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import asyncpg
import stripe
from dotenv import load_dotenv

# Load env directly to avoid scripts.Util's bare-import antipattern (see
# CLAUDE.md). .env first, then .env.secrets — neither overrides the other,
# matching the behavior of scripts.Util.load_project_env().
_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_ROOT / ".env", override=False)
load_dotenv(_ROOT / ".env.secrets", override=False)

DATABASE_URL = os.environ.get("DATABASE_URL")
STRIPE_KEY = os.environ.get("STRIPE_SECRET_KEY")

if not DATABASE_URL:
    print("ERROR: DATABASE_URL missing. Check .env.secrets.", file=sys.stderr)
    sys.exit(1)

# Parse host portion only — DSN may have password containing '-pooler'.
_host = DATABASE_URL.split("@", 1)[-1].split("/", 1)[0].split(":", 1)[0]
if "-pooler" not in _host:
    print(f"ERROR: DATABASE_URL host {_host!r} has no -pooler suffix; "
          "use the pooler DSN.", file=sys.stderr)
    sys.exit(1)

if not STRIPE_KEY:
    print("ERROR: STRIPE_SECRET_KEY missing. Check .env.secrets.", file=sys.stderr)
    sys.exit(1)

if not STRIPE_KEY.startswith("sk_test_"):
    print(f"REFUSING TO RUN: STRIPE_SECRET_KEY prefix is {STRIPE_KEY[:8]!r}, "
          "expected sk_test_. This script must NEVER touch live keys.",
          file=sys.stderr)
    sys.exit(2)

stripe.api_key = STRIPE_KEY


async def _connect():
    return await asyncpg.connect(DATABASE_URL)


async def inject():
    # Real Stripe TEST PaymentIntent — authorizes a hold against pm_card_visa.
    pi = stripe.PaymentIntent.create(
        amount=1000,
        currency="usd",
        payment_method="pm_card_visa",
        payment_method_types=["card"],
        capture_method="manual",
        confirm=True,
        description="LAIGO Phase E.4 synthetic test hold",
    )
    if pi.status != "requires_capture":
        print(f"ERROR: PaymentIntent landed at status {pi.status!r}, "
              "expected 'requires_capture'.", file=sys.stderr)
        sys.exit(3)

    job_id = f"phase-e-4-job-{uuid.uuid4().hex[:8]}"
    checkout_id = f"phase-e-4-co-{uuid.uuid4().hex[:8]}"
    hold_id = pi.id

    conn = await _connect()
    try:
        now = datetime.now(timezone.utc)
        ttl = now + timedelta(hours=1)
        await conn.execute(
            """
            INSERT INTO jobs (job_id, status, mosaic_type, width_blocks, dither, ttl_expires_at)
            VALUES ($1, 'complete', '2d', 5, TRUE, $2)
            """,
            job_id, ttl,
        )
        await conn.execute(
            """
            INSERT INTO checkouts (checkout_id, job_id, shipping_country, shipping_zip,
                                   customer_email, allocation, expires_at)
            VALUES ($1, $2, 'US', '00000', 'phase-e-4@test.invalid',
                    '{"test": true, "phase": "E.4"}'::jsonb, $3)
            """,
            checkout_id, job_id, ttl,
        )
        await conn.execute(
            """
            INSERT INTO sagas (checkout_id, job_id, saga_status, payment_provider, payment_mode,
                               payment_hold_id, payment_authorized_cents)
            VALUES ($1, $2, 'stripe_held', 'stripe', 'test', $3, 1000)
            """,
            checkout_id, job_id, hold_id,
        )
        await conn.execute(
            """
            INSERT INTO payment_holds (hold_id, checkout_id, provider, mode,
                                       amount_authorized_cents, currency, last_known_status)
            VALUES ($1, $2, 'stripe', 'test', 1000, 'usd', 'requires_capture')
            """,
            hold_id, checkout_id,
        )
    finally:
        await conn.close()

    print()
    print("=" * 70)
    print("Phase E.4 — synthetic saga injected")
    print("=" * 70)
    print(f"  job_id      : {job_id}")
    print(f"  checkout_id : {checkout_id}")
    print(f"  hold_id     : {hold_id}")
    print(f"  amount      : $10.00 USD (test mode)")
    print()
    print("NEXT STEPS:")
    print("  1. Restart uvicorn:")
    print("       Ctrl+C in the uvicorn window if running,")
    print("       then re-run: uvicorn scripts.Main:app --reload")
    print("  2. Boot log should show:")
    print("       [resume] examined 1 in-flight sagas (all routed cleanly)")
    print(f"  3. Verify:")
    print(f"       .venv\\Scripts\\python.exe -m scripts.phase_e_4_inject verify {checkout_id}")
    print(f"  4. Clean up when done:")
    print(f"       .venv\\Scripts\\python.exe -m scripts.phase_e_4_inject cleanup {checkout_id}")
    print()


async def verify(checkout_id: str):
    conn = await _connect()
    try:
        saga = await conn.fetchrow(
            "SELECT saga_status, error_message, customer_message, "
            "payment_hold_id, last_transition_at "
            "FROM sagas WHERE checkout_id = $1",
            checkout_id,
        )
        if not saga:
            print(f"NO SAGA ROW: checkout_id={checkout_id!r}", file=sys.stderr)
            sys.exit(4)
        print("sagas row:")
        print(f"  saga_status        : {saga['saga_status']}")
        print(f"  payment_hold_id    : {saga['payment_hold_id']}")
        print(f"  error_message      : {saga['error_message']}")
        print(f"  customer_message   : {saga['customer_message']}")
        print(f"  last_transition_at : {saga['last_transition_at']}")

        hold_id = saga["payment_hold_id"]
        if hold_id:
            ph = await conn.fetchrow(
                "SELECT last_known_status, last_reconciled_at "
                "FROM payment_holds WHERE hold_id = $1",
                hold_id,
            )
            print("payment_holds row:")
            print(f"  last_known_status  : {ph['last_known_status']}")
            print(f"  last_reconciled_at : {ph['last_reconciled_at']}")

            pi = stripe.PaymentIntent.retrieve(hold_id)
            print("Stripe PaymentIntent:")
            print(f"  status             : {pi.status}")
            print(f"  amount             : {pi.amount}")

        audit_rows = await conn.fetch(
            "SELECT event, data, ts FROM audit_events "
            "WHERE checkout_id = $1 ORDER BY ts",
            checkout_id,
        )
        print(f"audit_events ({len(audit_rows)} rows):")
        for row in audit_rows:
            print(f"  {row['ts'].isoformat()}  {row['event']:30s}  {dict(row['data'])}")
    finally:
        await conn.close()


async def cleanup(checkout_id: str):
    conn = await _connect()
    try:
        saga = await conn.fetchrow(
            "SELECT job_id, payment_hold_id FROM sagas WHERE checkout_id = $1",
            checkout_id,
        )
        if not saga:
            print(f"Nothing to clean: checkout_id={checkout_id!r}", file=sys.stderr)
            sys.exit(5)
        hold_id = saga["payment_hold_id"]
        job_id = saga["job_id"]
        # Drop in FK reverse order.
        await conn.execute("DELETE FROM audit_events WHERE checkout_id = $1", checkout_id)
        await conn.execute("DELETE FROM payment_holds WHERE checkout_id = $1", checkout_id)
        await conn.execute("DELETE FROM sagas WHERE checkout_id = $1", checkout_id)
        await conn.execute("DELETE FROM checkouts WHERE checkout_id = $1", checkout_id)
        await conn.execute("DELETE FROM jobs WHERE job_id = $1", job_id)
        print(f"Cleaned: job_id={job_id}, checkout_id={checkout_id}, hold_id={hold_id}")
        print("(Stripe PaymentIntent left in canceled state; harmless in test mode.)")
    finally:
        await conn.close()


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "inject":
        asyncio.run(inject())
    elif cmd == "verify":
        if len(sys.argv) < 3:
            print("ERROR: verify requires a checkout_id argument", file=sys.stderr)
            sys.exit(1)
        asyncio.run(verify(sys.argv[2]))
    elif cmd == "cleanup":
        if len(sys.argv) < 3:
            print("ERROR: cleanup requires a checkout_id argument", file=sys.stderr)
            sys.exit(1)
        asyncio.run(cleanup(sys.argv[2]))
    else:
        print(f"Unknown command: {cmd!r}", file=sys.stderr)
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()

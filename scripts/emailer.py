"""Build-pack email delivery via Resend.

After a pay-what-you-want checkout (including $0), the customer is emailed
their build pack — the instructions PDF and the Pick-a-Brick order list — as
attachments. The files are read from outputs/{job_id}/ at send time and
nothing is retained beyond the existing job TTL: the email IS the delivery,
and email.json (which holds the recipient address) is purged with the job dir.

Leaf module: stdlib + httpx + checkout/_env.is_truthy only. Resend's API is a
single JSON POST, so no SDK dependency is needed. Do not import FastAPI,
stripe, or the jobs store here.

Trigger points (all in pay_router.py, via FastAPI BackgroundTasks):
  - POST /jobs/{id}/pay with amount_cents == 0  (free download)
  - POST /jobs/{id}/pay sync success            (immediate card charge)
  - POST /webhooks/stripe payment_intent.succeeded
    (3DS completions + charges whose sync response was lost; the address
     rides in the PaymentIntent metadata — no server-side storage)

The sync and webhook paths can BOTH fire for one payment; the email.json
sentinel (claimed with O_EXCL) makes the second attempt a no-op.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
import zipfile
from pathlib import Path

import httpx

from .checkout._env import is_truthy

logger = logging.getLogger("laigo")

RESEND_API_URL = "https://api.resend.com/emails"

# Resend rejects messages over 40 MB AFTER base64 encoding (which inflates
# raw bytes by 4/3). Cap the raw attachment total so the encoded message
# stays comfortably under, leaving headroom for the JSON envelope + HTML.
_ENCODED_CAP_BYTES = 35_000_000

# Large attachments over TLS; Resend can be slow to accept multi-MB bodies.
_SEND_TIMEOUT_S = 60.0

# The PDF exists ONLY inside artifact.zip (worker.py deletes the workspace
# after zipping). This is its member path; _load_attachments falls back to
# scanning the namelist in case the layout ever shifts.
_PDF_MEMBER = "Instructions/instructions.pdf"

_DEFAULT_FROM = "LAIGO <onboarding@resend.dev>"


def is_enabled() -> bool:
    """True when EMAIL_ENABLED is truthy AND RESEND_API_KEY is set.

    Read at call time (not import time) so tests and a mid-flight `.env.secrets`
    fix take effect without a restart — same pattern as the webhook's
    STRIPE_WEBHOOK_SECRET read in pay_router.py.
    """
    return (
        is_truthy(os.getenv("EMAIL_ENABLED"))
        and bool(os.getenv("RESEND_API_KEY", "").strip())
    )


def send_build_pack_email(
    *,
    job_id: str,
    to_email: str,
    amount_cents: int,
    job_dir: Path,
) -> str:
    """Send the build pack for `job_id` to `to_email`. NEVER raises.

    Returns one of:
      "sent"      — Resend accepted the message; email.json status "sent".
      "duplicate" — a prior attempt already sent (or is in flight); no-op.
      "skipped"   — email disabled / no API key; NO sentinel written, so a
                    later webhook redelivery can still send once configured.
      "failed"    — anything else; email.json status "failed", which permits
                    a retry (e.g. a Stripe webhook redelivery of the event).

    Runs in Starlette's threadpool (sync function handed to BackgroundTasks),
    strictly after the HTTP response is flushed — it can never fail or delay
    the customer's charge.
    """
    try:
        if not is_enabled():
            logger.info(
                "email.skipped job_id=%s (EMAIL_ENABLED off or RESEND_API_KEY unset)",
                job_id,
            )
            return "skipped"

        if not _claim_send(job_dir):
            logger.info("email.duplicate job_id=%s", job_id)
            return "duplicate"

        attachments, link_only = _load_attachments(job_dir)
        url = _download_url(job_id)

        if not attachments:
            _record(job_dir, {
                "job_id": job_id,
                "to": to_email,
                "status": "failed",
                "detail": "no attachable artifacts on disk",
                "attachments": [],
                "link_only_fallback": link_only,
                "recorded_at": time.time(),
            })
            logger.warning("email.no_artifacts job_id=%s dir=%s", job_id, job_dir)
            return "failed"

        body = {
            "from": os.getenv("EMAIL_FROM", "").strip() or _DEFAULT_FROM,
            "to": [to_email],
            "subject": "Your LEGO build pack is ready",
            "html": _build_html(
                job_id=job_id, amount_cents=amount_cents,
                link_only=link_only, url=url,
            ),
            "attachments": [
                {"filename": name, "content": base64.b64encode(data).decode("ascii")}
                for name, data in attachments
            ],
        }
        resp = _http_post(
            RESEND_API_URL,
            headers={
                "Authorization": f"Bearer {os.environ['RESEND_API_KEY'].strip()}",
                "Content-Type": "application/json",
            },
            json_body=body,
        )

        if resp.status_code // 100 != 2:
            detail = f"HTTP {resp.status_code}: {resp.text[:300]}"
            _record(job_dir, {
                "job_id": job_id,
                "to": to_email,
                "status": "failed",
                "detail": detail,
                "attachments": [name for name, _ in attachments],
                "link_only_fallback": link_only,
                "recorded_at": time.time(),
            })
            logger.warning("email.send_failed job_id=%s %s", job_id, detail)
            return "failed"

        try:
            resend_id = resp.json().get("id", "")
        except Exception:
            resend_id = ""
        _record(job_dir, {
            "job_id": job_id,
            "to": to_email,
            "status": "sent",
            "detail": resend_id,
            "attachments": [name for name, _ in attachments],
            "link_only_fallback": link_only,
            "recorded_at": time.time(),
        })
        logger.info(
            "email.sent job_id=%s to=%s link_only=%s resend_id=%s",
            job_id, to_email, link_only, resend_id,
        )
        return "sent"

    except Exception as exc:
        logger.warning("email.send_failed job_id=%s err=%s", job_id, exc)
        try:
            _record(job_dir, {
                "job_id": job_id,
                "to": to_email,
                "status": "failed",
                "detail": f"{type(exc).__name__}: {exc}",
                "attachments": [],
                "link_only_fallback": False,
                "recorded_at": time.time(),
            })
        except Exception:
            pass
        return "failed"


# ─────────────────────────────────────────────────────────────────────────────
# Internals
# ─────────────────────────────────────────────────────────────────────────────

def _claim_send(job_dir: Path) -> bool:
    """Atomically claim the right to send for this job.

    open(..., "x") is the test-and-set: exactly one of the racing callers
    (sync /pay success vs webhook delivery) creates email.json and proceeds;
    the other sees FileExistsError. A prior record with status "failed"
    releases the claim so a webhook redelivery can retry; "sent" (or an
    in-flight placeholder) blocks forever.
    """
    path = job_dir / "email.json"
    try:
        with open(path, "x", encoding="utf-8") as fh:
            # Placeholder claimed-but-in-flight record; the real outcome
            # overwrites it in _record(). Has no "status" key, so a
            # concurrent reader treats it as claimed (not retryable).
            fh.write("{}")
        return True
    except FileExistsError:
        try:
            prior = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return False  # unreadable — treat as claimed rather than double-send
        return prior.get("status") == "failed"


def _load_attachments(job_dir: Path) -> tuple[list[tuple[str, bytes]], bool]:
    """Gather (filename, bytes) attachments for the build pack.

    order_list.json comes from its stable copy at the job root; the
    instructions PDF is extracted in-memory from artifact.zip (its only home).
    Returns (attachments, link_only_fallback) — link_only is True when the PDF
    could not be included (missing, or would push the encoded message over
    Resend's cap), in which case the email body points at the download URL.
    An empty attachments list means nothing usable exists on disk.
    """
    attachments: list[tuple[str, bytes]] = []
    link_only = False

    order_path = job_dir / "order_list.json"
    if order_path.is_file():
        attachments.append(("order_list.json", order_path.read_bytes()))

    pdf_bytes: bytes | None = None
    zip_path = job_dir / "artifact.zip"
    if zip_path.is_file():
        try:
            with zipfile.ZipFile(zip_path) as zf:
                member = _PDF_MEMBER if _PDF_MEMBER in zf.namelist() else next(
                    (n for n in zf.namelist() if n.endswith("instructions.pdf")),
                    None,
                )
                if member is not None:
                    pdf_bytes = zf.read(member)
        except Exception as exc:
            logger.warning("email.pdf_extract_failed zip=%s err=%s", zip_path, exc)

    if pdf_bytes is None:
        link_only = True
    else:
        raw_total = len(pdf_bytes) + sum(len(d) for _, d in attachments)
        if raw_total * 4 / 3 > _ENCODED_CAP_BYTES:
            # Attach the small order list only; the body carries the download
            # link for the full pack. Base64 inflates by 4/3, and Resend's
            # 40 MB limit applies to the encoded message.
            link_only = True
        else:
            attachments.append(("instructions.pdf", pdf_bytes))

    return attachments, link_only


def _download_url(job_id: str) -> str | None:
    """Public URL for GET /jobs/{id}/download, or None if the app can't know
    its own origin. PUBLIC_API_BASE_URL wins; Render auto-sets
    RENDER_EXTERNAL_URL on web services as the deploy-time fallback."""
    base = (
        os.getenv("PUBLIC_API_BASE_URL", "").strip()
        or os.getenv("RENDER_EXTERNAL_URL", "").strip()
    )
    if not base:
        return None
    return f"{base.rstrip('/')}/jobs/{job_id}/download"


def _build_html(
    *, job_id: str, amount_cents: int, link_only: bool, url: str | None,
) -> str:
    parts = ["<h2>Your LEGO build pack is ready!</h2>"]

    if link_only:
        parts.append(
            "<p>Attached is your brick order list. Your step-by-step "
            "instructions PDF could not be attached (it may be too large "
            "for email)."
        )
        if url:
            parts.append(
                f' Download the full build pack here: <a href="{url}">{url}</a>'
                " &mdash; note the link expires about an hour after your "
                "mosaic was generated.</p>"
            )
        else:
            parts.append(
                " Please return to your browser tab and use the download "
                "button to get the full pack.</p>"
            )
    else:
        parts.append(
            "<p>Attached are your step-by-step building instructions and "
            "the brick order list for your mosaic.</p>"
        )
        if url:
            parts.append(
                f'<p>You can also <a href="{url}">download the full build pack</a> '
                "for about an hour after your mosaic was generated.</p>"
            )

    parts.append(
        "<p>The order list JSON can be uploaded straight to LEGO.com "
        "Pick-a-Brick to buy exactly the bricks you need.</p>"
    )
    if amount_cents > 0:
        parts.append(
            f"<p>Thank you for your contribution of "
            f"${amount_cents / 100:.2f} &mdash; it keeps LAIGO running!</p>"
        )
    parts.append(f'<p style="color:#888;font-size:12px">Job {job_id}</p>')
    return "\n".join(parts)


def _http_post(url: str, *, headers: dict, json_body: dict) -> httpx.Response:
    """The single network seam — tests replace this function."""
    return httpx.post(url, headers=headers, json=json_body, timeout=_SEND_TIMEOUT_S)


def _record(job_dir: Path, payload: dict) -> None:
    """Best-effort email.json write (mirrors _record_payment's philosophy:
    an observability write must never take down the send path)."""
    try:
        (job_dir / "email.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8",
        )
    except Exception as exc:
        logger.warning(
            "email.record_failed job_id=%s err=%s", payload.get("job_id"), exc,
        )

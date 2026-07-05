"""Tests for scripts/emailer.py — build-pack email delivery via Resend.

No network: emailer._http_post (the single network seam) is replaced with a
recorder. Job dirs are built in a tempdir with a real artifact.zip so the
zip-extraction path is exercised for real.

Run from project root:
    .venv\\Scripts\\python.exe -m scripts.test_emailer
"""

import base64
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

from scripts import emailer


# ─────────────────────────────────────────────────────────────────────────────
# Fake HTTP seam
# ─────────────────────────────────────────────────────────────────────────────

class _FakeResponse:
    def __init__(self, status_code=200, body=None, text=""):
        self.status_code = status_code
        self._body = body if body is not None else {"id": "re_fake_123"}
        self.text = text

    def json(self):
        return self._body


_POSTS = []            # every call: {"url", "headers", "json_body"}
_NEXT_RESPONSES = []   # queue of _FakeResponse | Exception; empty -> 200


def _fake_post(url, *, headers, json_body):
    _POSTS.append({"url": url, "headers": headers, "json_body": json_body})
    nxt = _NEXT_RESPONSES.pop(0) if _NEXT_RESPONSES else _FakeResponse()
    if isinstance(nxt, Exception):
        raise nxt
    return nxt


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

_PDF_BYTES = b"%PDF-1.4 fake instructions pdf bytes"
_ORDER_BYTES = b'{"3005": 42, "302426": 7}'

_TMP: Path = None  # set in main()


def _make_job(job_id, *, with_zip=True, with_pdf=True, with_order=True,
              pdf_bytes=_PDF_BYTES) -> Path:
    job_dir = _TMP / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    if with_order:
        (job_dir / "order_list.json").write_bytes(_ORDER_BYTES)
    if with_zip:
        with zipfile.ZipFile(job_dir / "artifact.zip", "w") as zf:
            zf.writestr("manifest.json", "{}")
            zf.writestr("OrderLists/order_list.json", _ORDER_BYTES)
            if with_pdf:
                zf.writestr("Instructions/instructions.pdf", pdf_bytes)
    return job_dir


def _send(job_id, job_dir, *, amount_cents=500, to="buyer@example.com"):
    return emailer.send_build_pack_email(
        job_id=job_id, to_email=to, amount_cents=amount_cents, job_dir=job_dir,
    )


def _email_json(job_dir: Path) -> dict:
    return json.loads((job_dir / "email.json").read_text(encoding="utf-8"))


def _attachment_map(post) -> dict:
    """filename -> decoded bytes for one recorded POST."""
    return {
        a["filename"]: base64.b64decode(a["content"])
        for a in post["json_body"]["attachments"]
    }


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────


def test_happy_path():
    job_dir = _make_job("job-happy")
    out = _send("job-happy", job_dir, amount_cents=750)
    assert out == "sent", out
    assert len(_POSTS) == 1, _POSTS
    post = _POSTS[0]
    assert post["url"] == emailer.RESEND_API_URL
    assert post["headers"]["Authorization"] == "Bearer re_test_key_123"
    body = post["json_body"]
    assert body["from"] == "LAIGO Test <onboarding@resend.dev>", body["from"]
    assert body["to"] == ["buyer@example.com"], body["to"]
    assert body["subject"] == "Your LEGO build pack is ready"
    atts = _attachment_map(post)
    assert atts["order_list.json"] == _ORDER_BYTES, "order list must round-trip"
    assert atts["instructions.pdf"] == _PDF_BYTES, "pdf must round-trip from zip"
    assert "$7.50" in body["html"], body["html"]
    rec = _email_json(job_dir)
    assert rec["status"] == "sent", rec
    assert rec["to"] == "buyer@example.com"
    assert rec["detail"] == "re_fake_123"
    assert sorted(rec["attachments"]) == ["instructions.pdf", "order_list.json"]
    assert rec["link_only_fallback"] is False
    print("OK: happy path -> sent, both attachments round-trip, email.json recorded")


def test_duplicate_suppressed():
    job_dir = _TMP / "job-happy"  # already sent above
    before = len(_POSTS)
    out = _send("job-happy", job_dir)
    assert out == "duplicate", out
    assert len(_POSTS) == before, "duplicate must not POST"
    assert _email_json(job_dir)["status"] == "sent", "sent record must survive"
    print("OK: second send for same job -> duplicate, no POST, record intact")


def test_failure_then_retry():
    job_dir = _make_job("job-retry")
    _NEXT_RESPONSES.append(_FakeResponse(status_code=500, text="boom"))
    out = _send("job-retry", job_dir)
    assert out == "failed", out
    rec = _email_json(job_dir)
    assert rec["status"] == "failed", rec
    assert "HTTP 500" in rec["detail"], rec
    posts_after_fail = len(_POSTS)
    # A failed record releases the claim: a retry (e.g. webhook redelivery)
    # goes through and overwrites the record.
    out = _send("job-retry", job_dir)
    assert out == "sent", out
    assert len(_POSTS) == posts_after_fail + 1
    assert _email_json(job_dir)["status"] == "sent"
    print("OK: HTTP 500 -> failed (retryable); retry succeeds and re-records")


def test_transport_exception_is_failed():
    job_dir = _make_job("job-exc")
    _NEXT_RESPONSES.append(ConnectionError("network down"))
    out = _send("job-exc", job_dir)
    assert out == "failed", out
    rec = _email_json(job_dir)
    assert rec["status"] == "failed" and "network down" in rec["detail"], rec
    print("OK: transport exception -> failed, never raises")


def test_oversize_falls_back_to_link():
    job_dir = _make_job("job-big")
    os.environ["PUBLIC_API_BASE_URL"] = "https://api.laigo.example/"
    saved_cap = emailer._ENCODED_CAP_BYTES
    emailer._ENCODED_CAP_BYTES = 10  # force the PDF over the cap
    try:
        out = _send("job-big", job_dir, amount_cents=0)
        assert out == "sent", out
        post = _POSTS[-1]
        atts = _attachment_map(post)
        assert list(atts) == ["order_list.json"], atts.keys()
        html = post["json_body"]["html"]
        assert "https://api.laigo.example/jobs/job-big/download" in html, html
        assert "hour" in html, "must warn the link expires"
        rec = _email_json(job_dir)
        assert rec["link_only_fallback"] is True, rec
        assert rec["attachments"] == ["order_list.json"], rec
        print("OK: oversize pack -> order list only + expiring download link")
    finally:
        emailer._ENCODED_CAP_BYTES = saved_cap
        os.environ.pop("PUBLIC_API_BASE_URL", None)


def test_disabled_or_keyless_skips():
    job_dir = _make_job("job-skip")
    before = len(_POSTS)

    os.environ["EMAIL_ENABLED"] = "false"
    out = _send("job-skip", job_dir)
    assert out == "skipped", out

    os.environ["EMAIL_ENABLED"] = "true"
    saved_key = os.environ.pop("RESEND_API_KEY")
    try:
        out = _send("job-skip", job_dir)
        assert out == "skipped", out
    finally:
        os.environ["RESEND_API_KEY"] = saved_key

    assert len(_POSTS) == before, "skipped must not POST"
    assert not (job_dir / "email.json").exists(), (
        "skipped must NOT claim the sentinel — a later configured retry "
        "(webhook redelivery) must still be able to send"
    )
    print("OK: disabled / missing key -> skipped, no POST, no sentinel")


def test_missing_pdf_member_sends_link_only():
    job_dir = _make_job("job-nopdf", with_pdf=False)
    os.environ["RENDER_EXTERNAL_URL"] = "https://laigo.onrender.com"
    try:
        out = _send("job-nopdf", job_dir)
        assert out == "sent", out
        post = _POSTS[-1]
        atts = _attachment_map(post)
        assert list(atts) == ["order_list.json"], atts.keys()
        # RENDER_EXTERNAL_URL is the fallback origin when PUBLIC_API_BASE_URL
        # is unset.
        assert ("https://laigo.onrender.com/jobs/job-nopdf/download"
                in post["json_body"]["html"])
        assert _email_json(job_dir)["link_only_fallback"] is True
        print("OK: zip without pdf member -> link-only send via RENDER_EXTERNAL_URL")
    finally:
        os.environ.pop("RENDER_EXTERNAL_URL", None)


def test_nothing_usable_fails():
    job_dir = _make_job("job-empty", with_zip=False, with_order=False)
    before = len(_POSTS)
    out = _send("job-empty", job_dir)
    assert out == "failed", out
    assert len(_POSTS) == before, "nothing to attach must not POST"
    rec = _email_json(job_dir)
    assert rec["status"] == "failed", rec
    assert "no attachable artifacts" in rec["detail"], rec
    print("OK: no zip + no order list -> failed, no POST")


def test_download_url_none_without_origin():
    os.environ.pop("PUBLIC_API_BASE_URL", None)
    os.environ.pop("RENDER_EXTERNAL_URL", None)
    assert emailer._download_url("j") is None
    job_dir = _make_job("job-nourl", with_pdf=False)
    out = _send("job-nourl", job_dir)
    assert out == "sent", out
    html = _POSTS[-1]["json_body"]["html"]
    assert "browser tab" in html, (
        "with no known origin the link-only body must point back to the browser"
    )
    print("OK: no origin configured -> link-only body says use the browser tab")


# ─────────────────────────────────────────────────────────────────────────────


def main() -> int:
    global _TMP
    saved_post = emailer._http_post
    saved_env = {
        k: os.environ.get(k)
        for k in ("EMAIL_ENABLED", "RESEND_API_KEY", "EMAIL_FROM",
                  "PUBLIC_API_BASE_URL", "RENDER_EXTERNAL_URL")
    }
    emailer._http_post = _fake_post
    os.environ["EMAIL_ENABLED"] = "true"
    os.environ["RESEND_API_KEY"] = "re_test_key_123"
    os.environ["EMAIL_FROM"] = "LAIGO Test <onboarding@resend.dev>"
    os.environ.pop("PUBLIC_API_BASE_URL", None)
    os.environ.pop("RENDER_EXTERNAL_URL", None)

    try:
        with tempfile.TemporaryDirectory() as td:
            _TMP = Path(td)
            test_happy_path()
            test_duplicate_suppressed()
            test_failure_then_retry()
            test_transport_exception_is_failed()
            test_oversize_falls_back_to_link()
            test_disabled_or_keyless_skips()
            test_missing_pdf_member_sends_link_only()
            test_nothing_usable_fails()
            test_download_url_none_without_origin()
    finally:
        emailer._http_post = saved_post
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    print()
    print("All emailer tests PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

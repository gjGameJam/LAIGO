-- 0002_saga_hold_disposition.up.sql
--
-- B55: add `sagas.hold_disposition` so the orphan-hold reconciler can tell
-- "operator wants this hold cancelled" apart from "operator must decide
-- whether to capture or cancel" when a saga is in MANUAL_REVIEW.
--
-- Prior to this column, the reconciler treated every MANUAL_REVIEW saga
-- with `Stripe.status == requires_capture` as an orphan and cancelled it
-- within ~1 hour. That defeated the operator's capture option for the
-- "capture exhausted retries / drift detected / stockout-retry cancel
-- failed" MANUAL_REVIEW write sites, where the runbook explicitly offers
-- a choice between capture and refund.
--
-- Values:
--   NULL              — no hold to dispose, OR conservative default (don't touch)
--   'cancel_safe'     — runbook says "cancel manually in Stripe dashboard";
--                       reconciler may auto-cancel as a safety net
--   'operator_decides' — runbook offers capture-OR-cancel; reconciler MUST
--                       NOT touch Stripe (operator may be mid-recovery)
--
-- NULL default keeps existing rows safe (conservative: reconciler won't
-- cancel them). saga.py + saga_resume.py write the explicit value at each
-- MANUAL_REVIEW write site; the reconciler reads it in fetch_for_reconcile.
--
-- B45 schema-evolution contract: column is NULLABLE with no DEFAULT, so
-- existing rows and unrelated INSERTs remain valid. The all-columns-explicit
-- helper in checkout_store_pg.py is updated in the SAME PR (see
-- _insert_sagas).
--
-- NO inner BEGIN/COMMIT — alembic wraps in a transaction.

ALTER TABLE sagas
    ADD COLUMN hold_disposition TEXT;

ALTER TABLE sagas
    ADD CONSTRAINT sagas_hold_disposition_valid
    CHECK (hold_disposition IS NULL OR hold_disposition IN ('cancel_safe', 'operator_decides'));

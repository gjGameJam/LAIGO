# Mosaic pipeline defects ledger

Source-of-truth catalog of **open** defects in the **mosaic (image → LEGO kit)
pipeline** — `scripts/Main.py`, `scripts/worker.py`, `scripts/picToMosiac.py`,
`scripts/MosiacToOrder.py`, `scripts/MosiacToInstruction.py`,
`scripts/VisualMaker.py`, `scripts/Util.py`, `scripts/logger.py`,
`scripts/preview_builder.py`.

The checkout/marketplace pipeline is **shelved** under the pay-what-you-want
model (see `CLAUDE.md`); its historical defect ledger lives in the shelved docs
`docs/PRE_RELEASE_PAYMENT_CHECKLIST.md §4` and `docs/CHECKOUT_AUDIT.md`. This
document does not cover those.

## How to use this document

- **Looking up a defect:** find it in the index table below by symptom or file,
  then jump to the full entry by ID. Each entry has location, root cause,
  reproduction, fix sketch, and verification steps.
- **Fixing something:** read the "Fix" section, check "Related" for adjacent
  defects worth bundling, then read "Verification" to know how to confirm.
- **When a fix ships:** **delete the entry and its index row** (git history
  preserves the detail). This ledger tracks only what is still open — closed
  defects are not retained here. See `§ How to add a defect`.

## Severity scale

| Severity | Definition |
|----------|------------|
| **P0** | Active customer impact: visible defect in shipped kits / customer-visible API responses. Affects every job in the relevant code path. |
| **P1** | Active operational impact: log noise, billing cost, wasted compute on every job. No customer-visible defect today but real cost. |
| **P2** | Latent: would be P0/P1 if a trigger fires, OR structurally blocks a planned change (e.g., CPU roadmap items). |
| **P3** | Maintainer landmine, config debt, documentation debt. No functional impact today, but easy to step on during future changes. |

## Status values

- **open** — no fix shipped.
- **deferred (`<reason>`)** — intentionally not fixed this pass (e.g.,
  regression risk).
- **wontfix-by-design** — acknowledged trade-off, not a defect to chase.

> **History:** The `fix/mosaic-defects-sweep` work (2026-06-16, commit `6a3b1fa`)
> closed 24 of the original 33 defects; a further 14 (D-012, D-015, D-034–D-039,
> D-042–D-043, D-045–D-046, D-053) were fixed in the 2026-06-29 performance &
> interface-redundancy sweep. Per the open-only convention, all closed entries have
> been **removed** from this ledger — retrieve their root-cause writeups from git
> history (`git log -p -- docs/MOSAIC_DEFECTS.md`). Only open/deferred/wontfix work
> remains below.

## Index (sorted by severity, then file)

| ID | Severity | Status | Title | File |
|----|----------|--------|-------|------|
| [D-040](#d-040) | **P2** | open | Timeout watchdog can't reclaim the pool worker (ghost slot) | Main.py |
| [D-041](#d-041) | **P2** | deferred (bundle with D-025) | Per-step PNG written to disk then re-decoded for the PDF | VisualMaker.py / MosiacToInstruction.py |
| [D-044](#d-044) | **P2** | wontfix-by-design (A/B: 38–47% studs change) | Redundant LAB↔RGB round-trips (the deferred CPU-fold lever) | picToMosiac.py |
| [D-017](#d-017) | **P3** | open | `pic_to_mosaic` returns `None`; caller masks with `or workspace` | picToMosiac.py / Main.py |
| [D-018](#d-018) | **P3** | open | `give_exception_message` is in-band log-and-reraise | picToMosiac.py |
| [D-024](#d-024) | **P3** | open | Inconsistent `step` return shape across instruction helpers | MosiacToInstruction.py + VisualMaker.py |
| [D-025](#d-025) | **P3** | open | `step` counter threaded through every function as a return value | MosiacToInstruction.py + VisualMaker.py |
| [D-026](#d-026) | **P3** | open | `_mark_submission_failed` does a redundant `rmtree(job_root)` | Main.py |
| [D-029](#d-029) | **P3** | open | `FRONTEND_ORIGIN` env var set but never read | Main.py |
| [D-047](#d-047) | **P3** | open | `colorQuant.py` executes at import (no `__main__` guard) | colorQuant.py |
| [D-048](#d-048) | **P3** | open | Scattered env reads; `RENDER` idiom diverges; `.env` loaded twice | Main.py / picToMosiac.py |
| [D-049](#d-049) | **P3** | open | Job `settings` dict crosses 3 boundaries with no schema | Main.py / worker.py |
| [D-050](#d-050) | **P3** | open | Page-geometry magic numbers duplicated (`612×792`) | VisualMaker.py / MosiacToInstruction.py |
| [D-051](#d-051) | **P3** | open | `pic_to_mosaic(block_width=...)` actually carries studs | picToMosiac.py / worker.py |
| [D-052](#d-052) | **P3** | open | Minor cluster (no-op / dead / duplication / taxonomy) | various |

---

# `scripts/picToMosiac.py`

## D-017

**Title:** `pic_to_mosaic` returns `None` on success; caller masks with `or workspace`
**Severity:** **P3** (latent — currently safe; landmine for future edits)
**Status:** open
**Location:** `scripts/picToMosiac.py` `pic_to_mosaic` (no return statement); `scripts/worker.py` `run_job` (caller fallback `... or workspace`)

### Symptom

None today. The pipeline works because every successful path inside
`pic_to_mosaic` writes outputs to `output_dir` (passed as parameter), and the
caller's fallback (`result_dir = pic_to_mosaic(...) or workspace`) treats the
returned `None` as "use the workspace I passed in."

### Root cause

```python
def pic_to_mosaic(img_path, block_width, mosiac_type, ...):
    try:
        ...
        # several branches; none of them return anything
    except Exception as e:
        give_exception_message(e)   # re-raises; see D-018
```

The function has no `return` statement on its success paths. Python returns
`None` implicitly. The caller:

```python
result_dir = pic_to_mosaic(
    Path(image_path), width, mosaic_type, background_pct, to_frame,
    output_dir=workspace, job_id=job_id, progress_callback=write_progress,
) or workspace
```

The `or workspace` papers over the missing return value. If anyone adds an
early-exit return (e.g., `if no_subject_detected: return None`), the pipeline
silently reports success on an empty workspace — the customer gets a "success"
download for a job that never produced anything.

### Impact

- No current customer impact.
- A single careless `return None` in a future edit could ship empty kits as
  success. The wrapper `manifest.json` would be written, the zip created
  (empty), and `/jobs/{id}` would report `complete`.

### Fix

Either:

1. Make `pic_to_mosaic` return `output_dir` on success. Then drop the
   `or workspace` fallback at the call site. Any future early-exit must
   either return the workspace path (deliberate) or raise (deliberate).

   ```python
   def pic_to_mosaic(...):
       try:
           if mosaic_type == MosaicType.THREE_D:
               ...
           else:
               ...
           return output_dir
       except Exception as e:
           give_exception_message(e)
   ```

2. Add a `return None` paired with a Pythonic `if result_dir is None: raise
   RuntimeError("pic_to_mosaic returned None — expected workspace path")` at
   the call site. Fail loud rather than silently substituting.

Option 1 is cleaner and removes the magic `or workspace`.

### Verification

- After fix: search for `pic_to_mosaic(` in the codebase; the only call site
  is in `worker.run_job`. Make sure that one captures the return value.

### Related

- [D-018](#d-018) — `give_exception_message` is the exception arm; both live
  in the same function's contract.

---

## D-018

**Title:** `give_exception_message` is an in-band log-and-reraise
**Severity:** **P3** (code smell; truncated tracebacks; fragile bare `raise`)
**Status:** open
**Location:** `scripts/picToMosiac.py` `give_exception_message`

### Symptom

Errors in `pic_to_mosaic` log a one-line summary with only the deepest stack
frame's filename, line, function, and code text — not the full traceback. The
operator must dig into `laigo.log` and correlate timestamps to reconstruct
the call chain.

### Root cause

```python
def give_exception_message(e):
    tb = traceback.extract_tb(e.__traceback__)[-1]   # ONLY the last frame
    log_error(f"[ERROR] {type(e).__name__}: {e}")
    log_error(f"File: {tb.filename}")
    log_error(f"Line: {tb.lineno}")
    log_error(f"Function: {tb.name}")
    log_error(f"Code: {tb.line}")
    raise   # bare raise — re-raises the currently active exception
```

Two problems:

1. Only the last frame is formatted. For a multi-layer exception (e.g.,
   `image_to_lego_mosaic` → `nearest_palette_index_lab` → numpy), we see
   the numpy frame but lose the pipeline call chain.
2. Bare `raise` works only inside `except` blocks; outside one it raises
   `RuntimeError("No active exception to re-raise")`. Today both call sites
   are inside `except`, but the contract is fragile and non-obvious.

### Impact

- Operator triage takes longer than it should — the log entry doesn't tell
  the operator which mosaic-pipeline frame called the failing numpy code.
- Future refactors that move `give_exception_message` out of an `except`
  block will fail at runtime, not at lint time.

### Fix

Replace with stdlib `logger.exception`:

```python
# Delete give_exception_message entirely. Replace both call sites:

except Exception as e:
    log.exception("pic_to_mosaic failed")
    raise
```

`log.exception` emits the full traceback (every frame), and the explicit
`raise e` (or just `raise`) makes the propagation obvious.

The `log_error` family in `Util.py` doesn't have an `.exception` method
because it's a thin wrapper around `logger.error`. Either:

- Add `def log_exception(msg): logger.exception(msg)` to `Util.py`.
- Or switch the call site to use `logger` directly (which already gives full
  tracebacks via `exc_info=True`).

### Verification

- Force a failure in `pic_to_mosaic` (e.g., feed a corrupt image after
  bypassing `Image.verify()`). The new log entry should show the full
  traceback from `pic_to_mosaic` down to the actual failing line, not just
  the deepest frame.

### Related

- [D-017](#d-017) — `pic_to_mosaic` return contract; both live in the same
  function.
- The broader logging fan-out architecture cleanup has shipped (commit
  `6a3b1fa`); this in-band re-raise is the residual.

---

# `scripts/MosiacToInstruction.py`

## D-024

**Title:** Inconsistent `step` return shape across instruction helpers
**Severity:** **P3** (API ergonomic defect; easy to misuse)
**Status:** open
**Location:** `scripts/VisualMaker.py` (`generate_baseplate_setup` returns `(step, img)`; `draw_grid_setup_instruction` / `draw_frame_instructions` / `draw_final_view` return `step` only); `scripts/MosiacToInstruction.py` (consumer)

### Symptom

Every caller of `generate_baseplate_setup` must remember to capture two
values; every caller of `draw_grid_setup_instruction`, `draw_frame_instructions`,
`draw_final_view` must capture one. Forgetting the asymmetry produces a
"too many values to unpack" or a silent wrong assignment that doesn't trip
until step numbering breaks.

### Root cause

`generate_baseplate_setup` returns the canvas it built so the caller can
keep drawing on it without re-reading from disk (a real perf win). The
other helpers don't need that contract.

```python
# In MosiacToInstruction.py:
step, img = GenerateBasePlateInstructions(blockH, blockHeight, blockW, blockWidth, step, output_dir)
draw = ImageDraw.Draw(img)
...
step = draw_grid_setup_instruction(step, output_dir)
if want_frame:
    step = draw_frame_instructions(bg_rgba.width, bg_rgba.height, step, output_dir)
    step = draw_final_view(step, composite, True, output_dir)
else:
    step = draw_final_view(step, composite, False, output_dir)
```

### Impact

- Cognitive load on callers.
- A future helper added with one return shape will inevitably mismatch the
  other.

### Fix

The right fix is the one for [D-025](#d-025) (stateful step counter).
Specifically: introduce a small `StepCounter` class that holds the int and
exposes `step.current` / `step.advance()`. Every helper then becomes:

```python
def draw_grid_setup_instruction(step: StepCounter, output_dir=None) -> None:
    ...
def generate_baseplate_setup(step: StepCounter, case, output_dir=None) -> Image.Image:
    ...
    return img_c    # only the canvas; step is mutated via the counter object
```

Returns become uniform: either `None` or the canvas. The step counter is
explicit and the contract is enforced.

### Verification

- After fix: `grep -n "step = \|step, " scripts/` should show the counter
  threaded as an explicit object, not as an int.

### Related

- [D-025](#d-025) — the underlying `step` antipattern (this is one of its
  symptoms).

---

## D-025

**Title:** `step` counter threaded through every function as a return value
**Severity:** **P3** (antipattern; cause of [D-024](#d-024) and easy to break in refactors)
**Status:** open
**Location:** `scripts/MosiacToInstruction.py` and `scripts/VisualMaker.py` (every instruction-drawing helper)

### Symptom

Every drawing helper accepts `step: int`, returns `step + N`, and callers
must capture-and-pass. Any refactor that adds, reorders, or conditionally
skips a step silently produces wrong step numbers. There is no compile-time
or runtime check that step numbering is monotonic — `_validate_sequence`
asserts the count is contiguous after the fact, which catches some bugs
but not all (e.g., off-by-one in a single helper's `N`).

### Root cause

Plain-int counter passed as both input and output. No mutable shared
reference. No object owning the increment policy.

### Impact

- Refactor hazard. Every code review for changes in this area must check
  every step return value against every call site.
- The asymmetry between `generate_baseplate_setup` (returns `(step, img)`)
  and the other helpers ([D-024](#d-024)) is a direct consequence.
- If a helper short-circuits early without incrementing (e.g., a transparent
  column in `draw_plate_column`), the numbering stays contiguous only
  because the *caller* gates the call. Move that check inside the helper and
  the contract breaks.

### Fix

Introduce a `StepCounter`:

```python
class StepCounter:
    __slots__ = ("_n",)
    def __init__(self, start: int = 1) -> None:
        self._n = start
    @property
    def current(self) -> int:
        return self._n
    def advance(self) -> int:
        n = self._n
        self._n += 1
        return n   # returns the value we just spent; useful for save_img_*
```

Update `save_img_and_increment_step`:

```python
def save_img_and_increment_step(img, step: StepCounter, output_dir=None, copy=True) -> None:
    save_target = img.copy() if copy else img
    ...
    saveName = get_file_name(step.advance(), output_dir)
    save_target.convert("RGB").save(saveName, format="PNG", compress_level=1)
```

Every helper takes `StepCounter` instead of `int` and returns whatever it
actually wants to return (e.g., the canvas, or `None`).

This is a wide-touch refactor, but the earlier `MosiacToInstruction.py`
cleanups (the dead-PNG-sort, missing-folder, and `assert`→`raise` fixes
formerly tracked as D-021/D-022/D-023) have already shipped in commit
`6a3b1fa`, so the surface area is already reduced and the StepCounter change
is now mostly mechanical.

### Verification

- After fix: confirm step numbers are still contiguous (existing
  `_validate_sequence` already does this — leave it in place as a regression
  guard).
- Diff a generated PDF before/after on a fixture; pages should be
  byte-identical.

### Related

- [D-024](#d-024) — inconsistent return shape (this fix unifies them).

---

# `scripts/Main.py`

## D-026

**Title:** `_mark_submission_failed` does a redundant `rmtree(job_root)`
**Severity:** **P3** (cosmetic; suggests a missing intent)
**Status:** open
**Location:** `scripts/Main.py` `_mark_submission_failed`

### Symptom

None — the path works.

### Root cause

```python
shutil.rmtree(job_root / "workspace", ignore_errors=True)   # narrow
shutil.rmtree(job_root, ignore_errors=True)                 # subsumes above
...
_write_error_manifest(job_root, ...)                        # recreates job_root via mkdir(parents=True)
```

The second `rmtree` makes the first redundant. The pair was likely written
to ensure "delete any stale `manifest_failed.json` from a previous attempt
before writing a new one" — but the manifest hasn't been written yet at
this point, so there's nothing stale to delete.

### Impact

- Two filesystem syscalls instead of one.
- Confusing for the next reader.

### Fix

Either delete `manifest_failed.json` specifically (matches the apparent
intent), or just delete the workspace and skip the full-tree rmtree:

```python
# Option A: keep only the targeted cleanup
shutil.rmtree(job_root / "workspace", ignore_errors=True)
# Option B: also clean up a stale manifest if one exists
(job_root / "manifest_failed.json").unlink(missing_ok=True)
```

`_write_error_manifest` already creates `job_root` via
`error_path.parent.mkdir(parents=True, exist_ok=True)`, so no `mkdir` is
needed here.

### Verification

- After fix: trigger a submission failure (e.g., inject a failure into
  `executor.submit`). Confirm `manifest_failed.json` is written and the
  workspace is gone.

### Related

- None.

---

## D-029

**Title:** `FRONTEND_ORIGIN` env var set but never read
**Severity:** **P3** (config debt)
**Status:** open
**Location:** `scripts/Main.py` (CORS middleware); `.env`

### Symptom

Operator sets `FRONTEND_ORIGIN=https://staging-frontend.example.com` to
configure CORS for a staging environment. Setting has no effect; CORS is
locked to the two hardcoded origins.

### Root cause

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://laigo-frontend.onrender.com",
        "http://localhost:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

`FRONTEND_ORIGIN` is in `.env` (documented in the config table as "unused")
and never referenced in code.

### Fix

Wire it:

```python
import os
_frontend_origin = os.getenv("FRONTEND_ORIGIN")
_origins = ["http://localhost:5173"]
if _frontend_origin:
    _origins.append(_frontend_origin)
else:
    _origins.append("https://laigo-frontend.onrender.com")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

Or accept a comma-separated list:

```python
_origins = [o.strip() for o in os.getenv(
    "FRONTEND_ORIGIN",
    "https://laigo-frontend.onrender.com,http://localhost:5173"
).split(",") if o.strip()]
```

The comma-separated form is more flexible (staging + prod) and matches the
pattern used in other LAIGO env vars (`BRICKLINK_ENABLED`).

### Verification

- Set `FRONTEND_ORIGIN=http://example.local` in `.env`, restart, hit
  `/health` with `Origin: http://example.local`; CORS header in response
  should allow it.

### Related

- None (the sibling `MAX_WORKERS` / `MAX_QUEUE_SIZE` env-wiring defect has
  shipped — commit `6a3b1fa`).

---

# 2026-06-29 architectural review — open items

A full-codebase interface/efficiency review (four parallel passes: orchestration,
core pipeline, rendering, cross-cutting seams) catalogued D-034–D-053; the fixed
ones have been pruned (see the History note above). The open/deferred/wontfix
remainder is below.

## D-040

**Title:** Timeout watchdog can't reclaim the pool worker (ghost slot + possible artifact/store mismatch)
**Severity:** **P2**
**Status:** open
**Location:** `Main.py:787-841` (`_check_timed_out_jobs`)

### Root cause

`ProcessPoolExecutor` has no per-task kill. On timeout the watchdog marks the job
failed and decrements `active_jobs` while the real worker keeps running: (a) a
second job is admitted to a 1-worker pool but can't start (ghost slot); (b) if the
zombie later *succeeds* it writes a valid `artifact.zip`/`preview.json` while the
store says `timed_out`, and `/download` / `/pay` (which key off `artifact.zip`
existence) would serve/charge for a "failed" job.

### Fix

On timeout, drop/rebuild the pool worker (`executor.shutdown(wait=False)` + recreate,
or track the worker PID and `os.kill`) and decrement `active_jobs` only once the
worker is actually gone; or explicitly document the limitation.

## D-041

**Title:** Per-step PNG written to disk then re-decoded for the PDF
**Severity:** **P2** (disk I/O + encode→decode round-trip per page)
**Status:** deferred — bundle with D-025. (2026-06-29) Analysis: holding pages in
memory regresses memory (a 40-block mosaic emits tens of thousands of pages); the
safe bounded fix is to stream each page onto the reportlab canvas as it's
generated, which requires the canvas to reach `save_img_and_increment_step` — i.e.
threading it through ~8 instruction functions, the same `step`/`output_dir`
threading the D-025 StepCounter is meant to replace. So it's done cleanly only as
part of D-025 (a context object carries the canvas + the sequence counter).
**Location:** `VisualMaker.py:61` (write) → `MosiacToInstruction.py:65` (`drawImage(str(path))`)

### Root cause

Every step is `.save()`d to a PNG, then `images_to_pdf` re-opens and re-decodes
each PNG to embed it: N encodes + N writes + N reads + N decodes + N re-encodes per
job. The generator and PDF assembler are fully decoupled through the filesystem.

### Fix

Feed in-memory `PIL.Image` pages to reportlab via `ImageReader(pil_image)` (no disk
round-trip), or stream onto a shared canvas. Best bundled with the StepCounter
rework (D-025). At minimum, dedupe the directory scans (D-052).

### Related

- D-025, CPU hotspot #5.

## D-044

**Title:** Redundant LAB↔RGB round-trips between `adjust_lightness_lab` and `image_to_lego_mosaic`
**Severity:** **P2** (CPU; the deferred LAB-fold lever)
**Status:** wontfix-by-design (2026-06-29 A/B). Folding the +5 L* shift into the post-resize LAB was implemented behind a `delta_L` param and A/B-tested on 5 real photos (stella1–4, labrador) at 2 stud widths: it changes **38–47% of studs**, not the hoped-for <0.5%. Floyd-Steinberg dithering is chaotically sensitive to small input perturbations, so moving the shift across the non-linear resize/LAB chain cascades into ~40% of studs flipping. The redundant round-trip is the price of keeping the lightness shift where it currently is; reverted. (Its **memory** cost is already bounded by the `MAX_PROCESSING_DIMENSION` input cap — see the CLAUDE.md config table.)
**Location:** `picToMosiac.py:276,281,283,332` (adjust calls) + `:114` (re-`rgb2lab`)

### Root cause

`adjust_lightness_lab` does a full RGB→LAB→RGB round-trip just to add +5 to L*, then
`image_to_lego_mosaic` does `rgb2lab` again at mosaic resolution. 3D path = 6
conversions (4 at the larger, capped resolution), 2D = 3.

### Fix

Add a `delta_L=0` param to `image_to_lego_mosaic`; after `lab = color.rgb2lab(rgb)`
do `lab[...,0] = np.clip(lab[...,0]+delta_L, 0, 100)`. Delete the `adjust_lightness_lab`
calls. (Re-test for the prior visual regression — the 2026-06-29 A/B showed it fails.)

### Related

- The `MAX_PROCESSING_DIMENSION` input cap already bounds the memory cost (CLAUDE.md config table).

## D-047

**Title:** `colorQuant.py` executes at import (no `__main__` guard)
**Severity:** **P3**
**Status:** open
**Location:** `scripts/colorQuant.py` (whole module)

`sys.argv` parsing, `sys.exit(1)`, image load, `KMeans.fit`, and `.show()` all run
at module top level — any accidental `import scripts.colorQuant` (test collection,
tooling) exits the interpreter or runs a full KMeans. It is the sole consumer of the
`sklearn` dependency and uses `print()` (incl. an emoji). Fix: wrap in
`def main(): ... / if __name__ == "__main__": main()`, or delete the demo.

## D-048

**Title:** Scattered env reads; `RENDER` gate idiom diverges; `.env` loaded twice
**Severity:** **P3**
**Status:** open
**Location:** `Main.py:93`, `picToMosiac.py:41`, `logger.py:18,22`, etc.

Every module does its own `os.getenv` with no central config. Two concrete clashes:
`Main.py` gates `.env` loading on `is_truthy(os.getenv("RENDER"))` while
`picToMosiac.py:41` uses `os.getenv("RENDER") is None`, so `RENDER=false` makes one
load `.env` and the other skip it (the `bool("False")` trap). `load_project_env()`
also runs twice in the main process (picToMosiac import + `Main.py:94`). Fix: a
single `config.py` bootstrap that reads + validates env once; use `is_truthy` in both
RENDER checks. Related: `STUDS_PER_BLOCK` is already centralized in `mosaic_types.py` — a model for the shared config module this needs.

## D-049

**Title:** Job `settings` dict crosses 3 boundaries with no schema
**Severity:** **P3**
**Status:** open
**Location:** `Main.py:1182-1187` (build), `worker.py:128-137` (re-parse + re-validate), `Main.py:602-609` (reconstruct)

The raw dict with magic string keys is built in `/generate`, re-validated key-by-key
in the worker subprocess (re-doing the width/type/pct checks `/generate` already
ran), and reconstructed with the same literal keys in a third place. Fix: a
`JobSettings` `TypedDict`/dataclass in a leaf module, built once at intake. Related: D-051.

## D-050

**Title:** Page-geometry magic numbers duplicated (`612×792`, `792` y-flip)
**Severity:** **P3**
**Status:** open
**Location:** `MosiacToInstruction.py:63,65`; `VisualMaker.py:70,77,1518,2292,2642` + `to_pillow`/`draw_stud` (`792 - y`)

US-Letter@72dpi is hardcoded across the PDF canvas, every `Image.new`, and the
y-flip helper; some canvases are `800×792`/`1100×792`. The 792 page height is an
implicit contract `to_pillow` depends on but isn't a shared constant. Fix:
`PAGE_W = 612`, `PAGE_H = 792` module constants referenced everywhere incl.
`to_pillow`. (Same named-constant pattern as the now-centralized `STUDS_PER_BLOCK` in `mosaic_types.py`.)

## D-051

**Title:** `pic_to_mosaic(block_width=...)` actually carries studs (misleading name at the seam)
**Severity:** **P3**
**Status:** open
**Location:** `worker.py:130` → `picToMosiac.py:256`, used as `studs_w` then re-divided `// STUDS_PER_BLOCK` at `:302/345`

`worker.run_job` passes `block_width = mosaic_block_width * studs_per_block` (studs)
into a param named `block_width`, which is then divided back out to recover the block
count — a multiply-here/divide-there round-trip that only works because two
independent env reads return the same 16. Fix: rename to `studs_width`; pass block
count + studs-per-block explicitly. Related: D-049 (settings schema); `STUDS_PER_BLOCK` is the centralized constant in `mosaic_types.py`.

## D-052

**Title:** Minor cluster (no-op / dead / duplication / taxonomy)
**Severity:** **P3**
**Status:** open
**Location:** various

A grab-bag to address opportunistically:
- `.zfill(4)` inside `int()` is a no-op (`MosiacToInstruction.py:33`).
- `right_color` computed but unused in `draw_corner_brick`/`draw_brick`/`draw_corner_plate` (`VisualMaker.py:1663,1720,1756`) — either a shading bug or dead work.
- Shading-triple (`*0.85` / `*0.70`) copy-pasted across ~12 build-art functions; a `_mini_shades` helper already exists for icons — extract `_shades()` and share.
- `preview_builder.py:125-128,156-159` raise `AssertionError` for runtime producer guards where sibling modules use `ValueError`/`RuntimeError` (D-023 taxonomy).
- `piece_specs.py` is an active dependency (`VisualMaker.py:6`) missing from the CLAUDE.md module-layout table + import graph.
- Pervasive "Mosiac" misspelling (`MosiacToOrder`, `mosiac_type`, ...) alongside correct "mosaic" (`mosaic_types`, `MosaicType`).

# How to add a defect

1. Pick the next ID: `D-NNN` (zero-padded, monotonically incrementing — do
   not reuse a retired ID).
2. Decide severity per the scale at the top.
3. Write the entry under the right section:
   - Per-file section for defects living in one file.
   - Cross-cutting section for architectural defects spanning multiple files.
4. Required subsections (use the existing entries as templates):
   - **Title** (one line)
   - **Severity** and **Status**
   - **Location** (file + symbol; line range if stable)
   - **Symptom** — what the operator or customer sees
   - **Root cause** — the technical mechanism, with a code excerpt
   - **Trigger / When this fires** — for latent defects only
   - **Impact** — blast radius
   - **Fix** — concrete code sketch
   - **Migration steps** — for wide-touch refactors only
   - **Verification** — how to confirm the fix works
   - **Related** — cross-reference IDs
5. Add a row to the index table at the top.
6. If the defect changes documentation in `CLAUDE.md`, update CLAUDE.md to
   point at the new entry (one-line description + `[D-NNN](docs/MOSAIC_DEFECTS.md#d-nnn)`).
7. Commit with a message that references the new ID:
   `docs: add D-NNN — <short title>`

**When a fix ships:** delete the entry here and its index row (plus the
matching row in `CLAUDE.md`'s "Known defects" table). Git history is the
archive — this ledger only tracks open work.

## Template

A copy-pasteable entry skeleton:

```markdown
## D-NNN

**Title:** ...
**Severity:** **P?** (one-line justification)
**Status:** open
**Location:** `scripts/...` symbol

### Symptom

...

### Root cause

```python
...
```

### Impact

...

### Fix

```python
...
```

### Verification

...

### Related

- [D-???](#d-???) — relationship.
```

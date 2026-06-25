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

> **History (2026-06-16, commit `6a3b1fa` "fixed known defects"):** the
> `fix/mosaic-defects-sweep` work closed 24 of the original 33 defects
> (D-001..D-011 except D-012, plus
> D-013/14/16/19/20/21/23/27/28/30/31/32/33) and subsumed D-022. Those entries
> were **removed** from this ledger in the 2026-06-19 documentation cleanup —
> retrieve their full text from git history (`git show 6a3b1fa:docs/MOSAIC_DEFECTS.md`)
> if you ever need the root-cause writeups. Eight defects remain open/deferred,
> listed below.

## Index (sorted by severity, then file)

| ID | Severity | Status | Title | File |
|----|----------|--------|-------|------|
| [D-012](#d-012) | **P2** | deferred (visual-regression risk) | `adjust_lightness_lab` runs at full input resolution | picToMosiac.py |
| [D-015](#d-015) | **P3** | open | `make_difference_transparent` doc string and name describe the inverse of the code | picToMosiac.py |
| [D-017](#d-017) | **P3** | open | `pic_to_mosaic` returns `None`; caller masks with `or workspace` | picToMosiac.py / Main.py |
| [D-018](#d-018) | **P3** | open | `give_exception_message` is in-band log-and-reraise | picToMosiac.py |
| [D-024](#d-024) | **P3** | open | Inconsistent `step` return shape across instruction helpers | MosiacToInstruction.py + VisualMaker.py |
| [D-025](#d-025) | **P3** | open | `step` counter threaded through every function as a return value | MosiacToInstruction.py + VisualMaker.py |
| [D-026](#d-026) | **P3** | open | `_mark_submission_failed` does a redundant `rmtree(job_root)` | Main.py |
| [D-029](#d-029) | **P3** | open | `FRONTEND_ORIGIN` env var set but never read | Main.py |

---

# `scripts/picToMosiac.py`

## D-012

**Title:** `adjust_lightness_lab` runs at full input resolution
**Severity:** **P2** (compute waste on every job; 5–8% of single-job CPU per CLAUDE.md hotspot #3)
**Status:** deferred (visual-regression risk)
**Location:** `scripts/picToMosiac.py:138–168`

### Symptom

Every job spends 5–8% of its CPU budget on a full-resolution RGB↔LAB round-
trip before the LANCZOS downscale. The full-res sRGB image is up to ~16 MP;
the actual mosaic is 16×40 = 640 studs wide at most.

### Root cause

```python
def adjust_lightness_lab(img_pil, delta_L):
    if delta_L == 0:
        return img_pil
    arr = np.asarray(img_pil)
    ...
    rgb = rgb.astype(np.float32) / 255.0
    lab = color.rgb2lab(rgb)        # full-res RGB→LAB
    np.add(lab[..., 0], delta_L, out=lab[..., 0])
    np.clip(lab[..., 0], 0, 100, out=lab[..., 0])
    rgb_out = color.lab2rgb(lab)    # full-res LAB→RGB
    ...
```

Called from `pic_to_mosaic` *before* `image_to_lego_mosaic` (which then does
its own RGB→LAB on the post-resize tensor). The result is two full-res
roundtrips for one `delta_L=5` shift.

### Impact

- ~5–8% of total CPU on every job (per `CLAUDE.md § CPU performance` hotspot
  #3). On Render's single vCPU, this is one of the biggest accidental tax
  items.

### Fix

Fold the L* shift into `image_to_lego_mosaic`'s post-LANCZOS LAB:

```python
def image_to_lego_mosaic(img, studs_w, alpha_mask=None, delta_L=0):
    ...
    img_small = img.resize((studs_w, studs_h), Image.LANCZOS)
    img_small = img_small.filter(ImageFilter.UnsharpMask(...))
    rgb = np.asarray(img_small) / 255.0
    lab = color.rgb2lab(rgb)
    if delta_L != 0:
        np.add(lab[..., 0], delta_L, out=lab[..., 0])
        np.clip(lab[..., 0], 0, 100, out=lab[..., 0])
    ...
```

Then delete the `adjust_lightness_lab` calls in `pic_to_mosaic` for the 2D and
3D paths and pass `delta_L=5` to `image_to_lego_mosaic` instead.

### Why this hasn't shipped yet

A previous attempt (per `CLAUDE.md`) was reverted on visual-regression concerns
— the shifted-after-LANCZOS output looked subtly different. Plausible cause:
the LANCZOS resampler operates on sRGB-encoded values (a non-linear space);
shifting L* on a downscaled image vs the original may produce different
boundary smoothing on bright/dark gradients.

Before re-shipping:
1. Fixture-based A/B: pick 3–5 representative photos (high-key portrait,
   low-key portrait, vibrant landscape). Run with and without the fold.
2. Visual diff: open both PDFs side-by-side. The output should be visually
   indistinguishable.
3. If they differ, the fix is salvageable by upsampling: do the L* shift in
   the full-res LAB, then resize, then continue. But that retains the full-
   res roundtrip (no win), so the simpler approach is to accept the
   slight tonal difference and update fixtures.

### Verification

- Fixture-based perceptual diff (above).
- Time `pic_to_mosaic` on a fixed input before and after the fix. Expect
  ~5–8% reduction in wall-clock time.

### Related

- CPU roadmap item #2 in `CLAUDE.md § CPU performance`.
- [D-015](#d-015) — `make_difference_transparent` documentation is wrong (same
  function neighborhood).

---

## D-015

**Title:** `make_difference_transparent` doc string and function name describe the inverse of the code
**Severity:** **P3** (future-maintainer landmine; code is functionally correct)
**Status:** open
**Location:** `scripts/picToMosiac.py:172–178`

### Symptom

None visible — the code is correct for its current use site. The risk is the
next developer.

### Root cause

```python
#makes the pixels that match on the original to be transparent
def make_difference_transparent(orig, new):
    orig = np.array(orig.convert("RGBA"))
    fg = np.array(new.convert("RGBA"))
    diff = np.any(fg[...,:3] != orig[...,:3], axis=-1)
    fg[diff, 3] = 0   # modify fg in place
    return Image.fromarray(fg)
```

The mask flags pixels where `fg` **differs from** `orig`, and sets those to
alpha=0. That is, "pixels that DIFFER from the original become transparent."
The doc string says the opposite ("pixels that MATCH the original"). The
function name (`make_difference_transparent`) is the only thing that lines up
with the code — and even then only with a generous reading.

### Why the code happens to be correct in the pipeline

`pic_to_mosaic` calls this with `(img, fg_pil)` where `fg_pil` came from
`remove_background`. In `remove_background`, the foreground array is built as
`np.where(fg_mask[...,None], img, 255)` — so pixels INSIDE the silhouette
match `img`, and pixels OUTSIDE are set to white. After `make_difference_transparent`:

- Inside the silhouette: `fg == orig`, so `diff` is False, alpha stays opaque.
- Outside the silhouette: `fg == (255,255,255)`, `orig` is the original scene
  (varied colors), so `diff` is True, alpha→0.

Net effect: background becomes transparent, foreground stays opaque. That's
what the pipeline wants, and the function name "make_difference_transparent"
is consistent with that intent (pixels that *differ* from the original — i.e.,
the white-fill background — become transparent).

### Impact

- A future developer reading the doc string ("pixels that match") will assume
  the function does the inverse and may either:
  - reuse it in a context where they think it does the opposite, producing
    a broken silhouette;
  - "fix" the comment to match the code but write the wrong description
    (the actual semantics are subtle and depend on the white-fill convention
    in the producer).

### Fix

Option A (preferred — minimal code change):

```python
def make_background_transparent(orig, pil_with_white_background):
    """Set alpha=0 for pixels that DIFFER from `orig`, i.e., the white-fill
    pixels introduced by `remove_background`. Pixels that match `orig`
    (i.e., the preserved silhouette) keep their original alpha.
    """
    orig_arr = np.array(orig.convert("RGBA"))
    fg_arr = np.array(pil_with_white_background.convert("RGBA"))
    diff = np.any(fg_arr[...,:3] != orig_arr[...,:3], axis=-1)
    fg_arr[diff, 3] = 0
    return Image.fromarray(fg_arr)
```

Update the call site:

```python
fg_alpha_pil = make_background_transparent(img, fg_pil)
```

Option B (avoid the rename if it's churn): keep the name, replace the doc
string with the corrected version above.

Either way, delete the misleading one-line comment.

### Verification

- `grep` for `make_difference_transparent` after the rename — exactly one
  call site in `picToMosiac.py`. No tests reference it (no test file
  exists for picToMosiac as of the audit).

### Related

- Same neighborhood as the now-fixed 3D foreground/background separation
  colorspace + background-budget bugs (shipped in commit `6a3b1fa`).

---

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

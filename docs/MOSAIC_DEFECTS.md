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
| [D-012](#d-012) | **P2** | memory MITIGATED (input cap); CPU-fold deferred | `adjust_lightness_lab` runs at full input resolution | picToMosiac.py |
| [D-015](#d-015) | **P3** | FIXED 2026-06-29 (function deleted, D-037) | `make_difference_transparent` doc string and name describe the inverse of the code | picToMosiac.py |
| [D-017](#d-017) | **P3** | open | `pic_to_mosaic` returns `None`; caller masks with `or workspace` | picToMosiac.py / Main.py |
| [D-018](#d-018) | **P3** | open | `give_exception_message` is in-band log-and-reraise | picToMosiac.py |
| [D-024](#d-024) | **P3** | open | Inconsistent `step` return shape across instruction helpers | MosiacToInstruction.py + VisualMaker.py |
| [D-025](#d-025) | **P3** | open | `step` counter threaded through every function as a return value | MosiacToInstruction.py + VisualMaker.py |
| [D-026](#d-026) | **P3** | open | `_mark_submission_failed` does a redundant `rmtree(job_root)` | Main.py |
| [D-029](#d-029) | **P3** | open | `FRONTEND_ORIGIN` env var set but never read | Main.py |
| [D-034](#d-034) | **P2** | FIXED 2026-06-29 | Main.py/worker logs never reach `laigo.log` (split logger trees) | logger.py / Main.py |
| [D-035](#d-035) | **P2** | FIXED 2026-06-29 | `STUD_WIDTH_OF_BLOCK` advertised configurable but `16` hardcoded in 4 modules | MosiacToInstruction.py / MosiacToOrder.py / VisualMaker.py |
| [D-036](#d-036) | **P2** | FIXED 2026-06-29 | `GenerateOrderList` counts from RGB instead of the index arrays already computed | MosiacToOrder.py / picToMosiac.py |
| [D-037](#d-037) | **P2** | FIXED 2026-06-29 | 3D `make_difference_transparent` leaks white background into FG; discards `fg_mask` | picToMosiac.py |
| [D-038](#d-038) | **P2** | FIXED 2026-06-29 | `/generate` blocks the event loop on full-image decode + `fsync` | Main.py |
| [D-039](#d-039) | **P2** | FIXED 2026-06-29 | `get_font()` re-parses the TTF on every call (no cache) | VisualMaker.py |
| [D-040](#d-040) | **P2** | open | Timeout watchdog can't reclaim the pool worker (ghost slot) | Main.py |
| [D-041](#d-041) | **P2** | deferred (bundle with D-025) | Per-step PNG written to disk then re-decoded for the PDF | VisualMaker.py / MosiacToInstruction.py |
| [D-042](#d-042) | **P2** | FIXED 2026-06-29 | Background pixels run through `np.unique` twice | picToMosiac.py |
| [D-043](#d-043) | **P2** | FIXED 2026-06-29 | `count_colors` sorts every pixel ×2 only to log two lines | MosiacToInstruction.py |
| [D-044](#d-044) | **P2** | wontfix-by-design (A/B: 38–47% studs change) | Redundant LAB↔RGB round-trips (D-012 CPU-fold lever) | picToMosiac.py |
| [D-045](#d-045) | **P3** | FIXED 2026-06-29 | No-op resizes + discarded background `out_img` | picToMosiac.py |
| [D-046](#d-046) | **P3** | FIXED 2026-06-29 | Dead `import gc` in Main.py | Main.py |
| [D-047](#d-047) | **P3** | open | `colorQuant.py` executes at import (no `__main__` guard) | colorQuant.py |
| [D-048](#d-048) | **P3** | open | Scattered env reads; `RENDER` idiom diverges; `.env` loaded twice | Main.py / picToMosiac.py |
| [D-049](#d-049) | **P3** | open | Job `settings` dict crosses 3 boundaries with no schema | Main.py / worker.py |
| [D-050](#d-050) | **P3** | open | Page-geometry magic numbers duplicated (`612×792`) | VisualMaker.py / MosiacToInstruction.py |
| [D-051](#d-051) | **P3** | open | `pic_to_mosaic(block_width=...)` actually carries studs | picToMosiac.py / worker.py |
| [D-052](#d-052) | **P3** | open | Minor cluster (no-op / dead / duplication / taxonomy) | various |
| [D-053](#d-053) | **P3** | FIXED 2026-06-29 | `simplify_background_lego` ran one `deltaE_ciede2000` call per unique index | picToMosiac.py |

---

# `scripts/picToMosiac.py`

## D-012

**Title:** `adjust_lightness_lab` runs at full input resolution
**Severity:** **P2** (compute waste on every job; 5–8% of single-job CPU per CLAUDE.md hotspot #3)
**Status:** memory aspect MITIGATED (2026-06-29, input-resolution cap); CPU-fold still deferred (visual-regression risk)
**Location:** `scripts/picToMosiac.py` — `adjust_lightness_lab`

### Mitigation shipped (2026-06-29) — memory

The **OOM aspect** of this defect was fixed by capping input resolution before
the heavy ops, not by the fold-into-resize CPU optimization below. `pic_to_mosaic`
now calls `cap_processing_resolution(img)` immediately after `open_image`, which
`Image.thumbnail`s the input down to `MAX_PROCESSING_DIMENSION` (default 2048 px
long edge, shrink-only). The full-res RGB↔LAB round-trip therefore allocates at
most ~2048×1536 float64 arrays (~75 MB each) instead of ~500 MB+ on a 20 MP
upload — the spike that OOM-killed the worker on 2026-06-28 (a single 4.4 MB
JPEG job). No perceptible quality change: the mosaic is ≤640 studs, so 2048
oversamples it >3×, and MediaPipe segmentation works at a fixed 256×256
internally. This protects **both** the 2D `adjust_lightness_lab` path and the 3D
`remove_background` + `adjust_lightness_lab` path. The CPU round-trip itself
still runs (now on the capped image); the fold-into-resize optimization below
remains the open CPU lever.

### Symptom

Every job spends 5–8% of its CPU budget on a full-resolution RGB↔LAB round-
trip before the LANCZOS downscale. The full-res sRGB image is up to ~16 MP;
the actual mosaic is 16×40 = 640 studs wide at most. (Memory aspect: those
full-res float64 LAB/RGB arrays could exceed 1 GB on large uploads — see the
mitigation above.)

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
**Status:** FIXED 2026-06-29 (pending prune to git history) — the function was deleted entirely as part of D-037 (the 3D path now builds `fg_a` from `fg_mask`), so the inverted name/doc are gone.
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

# 2026-06-29 architectural review (D-034 – D-052)

A full-codebase interface/efficiency review (four parallel passes: orchestration,
core pipeline, rendering, cross-cutting seams). Findings below were each verified
against source. P2s carry full detail; P3s are compact. Many are **interface-level
redundancy** — data the pipeline already computed, discarded and recomputed at the
next boundary.

## D-034

**Title:** Main.py / worker logs never reach `laigo.log` (split logger trees)
**Severity:** **P2** (operator forensics broken)
**Status:** FIXED 2026-06-29 (pending prune to git history) — `logger.py` attaches the shared file handler to the `"laigo"` logger
**Location:** `scripts/logger.py:25`, `scripts/Main.py:77-83`

### Symptom

Grepping `laigo.log` for an exception's `request_id` returns nothing — the
traceback is only on stdout/Render's stream.

### Root cause

`logger.py` owns the only `RotatingFileHandler`, attached to logger `"laigoLOG"`
(`propagate=False`). `Main.py` logs via `logging.getLogger("laigo")` configured
by `basicConfig(stream=sys.stdout)` — a *different* tree with no file handler. So
request/lifecycle/exception logs go to stdout only, while the global handler's
comment says "Operator greps laigo.log for request_id to find the full traceback."

### Fix

In `logger.py` (parent process only — `not _IS_WORKER`), attach the **same**
`file_handler` instance to `logging.getLogger("laigo")` (NOT a new handler — a
second handle to the file would race rotation, D-009). Keep `"laigo"` propagating
to root for stdout. Update the misleading comment.

### Related

- D-009 (single file-handle owner), D-002 (propagation to root).

## D-035

**Title:** `STUD_WIDTH_OF_BLOCK` advertised configurable but `16` hardcoded in 4 modules
**Severity:** **P2** (latent footgun — changing the knob fails every job)
**Status:** FIXED 2026-06-29 (pending prune to git history) — `STUDS_PER_BLOCK` is now a fixed constant in `mosaic_types.py`, imported everywhere; the `STUD_WIDTH_OF_BLOCK` env var was removed
**Location:** `MosiacToInstruction.py:91,112`, `MosiacToOrder.py:96,122`, ~14 sites in `VisualMaker.py`, vs `picToMosiac.py:34` / `Main.py:113`

### Root cause

Only `Main`/`picToMosiac` honor the env var; downstream hardcodes `16`, including
the guard `if W % 16 or H % 16: raise ValueError(...)`. A non-16 value makes
`picToMosiac` emit non-16-divisible mosaics → the guard raises → **all jobs fail**;
`GetBaseplatesForSize` also computes wrong block counts.

### Fix

Export `STUDS_PER_BLOCK` from one module (`mosaic_types.py` or `Util.py`) and
import everywhere `16` appears; or, if it's truly fixed, drop the env var and make
it a fixed named constant so the "configurable" illusion goes away.

### Related

- D-050 (page/layout magic numbers), D-051 (studs-vs-blocks naming).

## D-036

**Title:** `GenerateOrderList` counts from RGB instead of the index arrays already computed
**Severity:** **P2** (interface redundancy + self-inflicted failure path)
**Status:** FIXED 2026-06-29 (pending prune to git history) — `GenerateOrderList(fg_idx, fg_visible_mask, bg_idx, want_frame, output_dir)` now counts via `np.bincount` over the palette indices (`PALETTE_ELEMENT_IDS` maps index→element_id); the RGB `np.unique(axis=0)` + dict lookup and the D-008 KeyError are gone (replaced by an out-of-range-index guard). Proven content-identical to the old RGB counting across 32 randomized cases; the only change is order_list.json array order (palette-index vs RGB-sorted), which no consumer depends on. `test_order_list.py` rewritten for the index contract.
**Location:** `MosiacToOrder.py:39-76` consuming RGBA from `picToMosiac.py:287-324`

### Root cause

`pic_to_mosaic` holds exact palette **indices** (`fg_idx`, `bg_idx_simplified`) and
passes them straight to `preview_builder`, but converts them back to RGBA for the
order list, which `np.unique(bg_arr, axis=0)`-sorts ~400k rows and looks up a dict
keyed by RGB tuple. That RGB round-trip is the *only* thing that can produce the
off-palette `KeyError` the D-008 `RuntimeError` guards against.

### Fix

Change `GenerateOrderList` to accept `fg_idx`/`bg_idx` (+ alpha mask) and count via
`np.bincount`, mapping index→element_id through the palette list order. Removes the
sort, the RGB→tuple→dict map, and the D-008 KeyError surface.

### Related

- D-008 (guard becomes structurally unnecessary), D-045 (lazy `out_img`).

## D-037

**Title:** 3D: `make_difference_transparent` leaks white background into FG; discards `fg_mask`
**Severity:** **P2** (correctness; white/studio backgrounds — the common portrait case)
**Status:** FIXED 2026-06-29 (pending prune to git history) — 3D path builds `fg_a = fg_mask.astype(uint8)*255`; `make_difference_transparent` deleted. Verified: a white bg pixel that leaked under the old diff (alpha 255) is now background (alpha 0); fg + normal bg unchanged; 3D pipeline runs end-to-end.
**Location:** `picToMosiac.py:208-213` (defn), `:273` (`fg_mask` captured & unused), `:276` (call)

### Root cause

`remove_background` computes and returns the ground-truth `fg_mask`, but the caller
discards it and recovers foreground alpha by diffing the white-filled `fg_pil`
against the original:

```python
diff = np.any(fg[...,:3] != orig[...,:3], axis=-1);  fg[diff, 3] = 0
```

A background pixel that was **already white** in the original → `diff == False` →
stays opaque → treated as foreground and counted as a foreground brick.

### Fix

Delete `make_difference_transparent`; build `fg_a` from the returned `fg_mask`
(resize the boolean mask to mosaic resolution). Faster (no full-res RGBA diff) and
exact.

### Related

- D-015 (same function's inverted name/doc — supersede it when fixing this).

## D-038

**Title:** `/generate` blocks the event loop on full-image decode + `fsync`
**Severity:** **P2** (latency under concurrency)
**Status:** FIXED 2026-06-29 (pending prune to git history) — decode moved to `_validate_image` run via `await asyncio.to_thread(...)`; per-upload `os.fsync` removed. Verified: valid images accepted, invalid rejected (400 path intact).
**Location:** `Main.py:1158` (`os.fsync`), `:1174-1176` (`img.load()` / `convert("RGB")`)

### Root cause

The `async` handler synchronously decodes the entire upload (up to 250 MB) and
fsyncs inline. On single-worker uvicorn this freezes `/health`, every
`/jobs/{id}` status poll, and other uploads for the whole decode/flush.

### Fix

`await asyncio.to_thread(_validate_image, input_file)`; drop the per-upload
`os.fsync` (transient file, re-read in-process immediately).

## D-039

**Title:** `get_font()` re-parses the bundled TTF on every call (no cache)
**Severity:** **P2** (CPU on the per-step hot path)
**Status:** FIXED 2026-06-29 (pending prune to git history) — `@functools.lru_cache(maxsize=None)` on `get_font`. Verified: cached font renders pixel-identically to a fresh `truetype` across all 8 used sizes; pipeline fingerprint unchanged.
**Location:** `VisualMaker.py:39-49`; per-step caller `save_img_and_increment_step` `:55`

### Root cause

`ImageFont.truetype(str(_BUNDLED_FONT), size)` re-reads + re-parses the TTF every
call; a large mosaic emits 1000+ steps → 1000+ FreeType parses purely to stamp
step numbers.

### Fix

`@functools.lru_cache(maxsize=None)` on `get_font` (keys are the handful of sizes
used). One line, no behavior change.

### Related

- CPU hotspot #5 (instruction loop).

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

## D-042

**Title:** Background pixels run through `np.unique` twice
**Severity:** **P2**
**Status:** FIXED 2026-06-29 (pending prune to git history) — the 3D call site computes `np.unique(bg_idx[fg_mask_np==0], return_counts=True)` once and passes it into both `background_color_budget(..., unique_indices=)` and `simplify_background_lego(..., unique_counts=)` (both keep a `None` default = compute internally, so the standalone/test path is unchanged). Verified byte-identical + `test_background_budget` passes.
**Location:** `picToMosiac.py:80-94` (`background_color_budget`) + `:51-77` (`simplify_background_lego`), both called at `:291-292`

### Root cause

Both gather the same `bg_idx[fg_mask_np == 0]` (~400k pixels) and run `np.unique` —
once for the distinct-color *count*, once for the remap — over the largest array in
the 3D path, for one number.

### Fix

Compute `unique, counts = np.unique(...)` once, derive `k = max(1, int(pct/100 *
len(unique)))` from it, and pass `k` into `simplify_background_lego` — one pass.

### Related

- D-036.

## D-043

**Title:** `count_colors` sorts every pixel (`np.unique axis=0`) ×2 only to log two lines
**Severity:** **P2** (one-time per job, fully avoidable)
**Status:** FIXED 2026-06-29 (pending prune to git history) — packs RGB into one uint32 and runs a 1-D `np.unique` (avoids the lexicographic axis=0 sort). Verified: identical counts across 12 randomized trials + edge cases; pipeline fingerprint unchanged.
**Location:** `MosiacToInstruction.py:16-21,115,119`

### Root cause

`count_colors` runs an O(N·log N) `np.unique(rgb, axis=0)` over the whole bg and the
whole fg purely to feed `log_info("BG/FG unique RGB colors: ...")`. (The adjacent
D-004/D-005 comment removed the *other* expensive set-dump for exactly this reason;
this `np.unique` survived.)

### Fix

Gate behind `log_debug`, or compute the count via a packed 1-D view, or drop.

### Related

- D-004/D-005 (sibling set-dump removal).

## D-044

**Title:** Redundant LAB↔RGB round-trips between `adjust_lightness_lab` and `image_to_lego_mosaic`
**Severity:** **P2** (CPU; the open lever from D-012)
**Status:** wontfix-by-design (2026-06-29 A/B). Folding the +5 L* shift into the post-resize LAB was implemented behind a `delta_L` param and A/B-tested on 5 real photos (stella1–4, labrador) at 2 stud widths: it changes **38–47% of studs**, not the hoped-for <0.5%. Floyd-Steinberg dithering is chaotically sensitive to small input perturbations, so moving the shift across the non-linear resize/LAB chain cascades into ~40% of studs flipping. The redundant round-trip is the price of keeping the lightness shift where it currently is; reverted. (Its **memory** cost is already bounded by the D-012 input cap.)
**Location:** `picToMosiac.py:276,281,283,332` (adjust calls) + `:114` (re-`rgb2lab`)

### Root cause

`adjust_lightness_lab` does a full RGB→LAB→RGB round-trip just to add +5 to L*, then
`image_to_lego_mosaic` does `rgb2lab` again at mosaic resolution. 3D path = 6
conversions (4 at the larger, capped resolution), 2D = 3.

### Fix

Add a `delta_L=0` param to `image_to_lego_mosaic`; after `lab = color.rgb2lab(rgb)`
do `lab[...,0] = np.clip(lab[...,0]+delta_L, 0, 100)`. Delete the `adjust_lightness_lab`
calls. (This is D-012's CPU half; re-test for the prior visual regression.)

### Related

- D-012 (memory half mitigated via the input cap).

## D-045

**Title:** No-op resizes + discarded background `out_img` in the mosaic builder
**Severity:** **P3** (small data; pure waste + obscures intent)
**Status:** FIXED 2026-06-29 (pending prune to git history) — dropped the 3 identity NEAREST resizes; `image_to_lego_mosaic` gained `build_image=False` so the 3D bg path skips the throwaway PIL image. Verified byte-identical (2D + 3D fingerprints unchanged).
**Location:** `picToMosiac.py:139` (resize-to-current-size), `:316-318` (same-size resizes), `:288`→`:294` (bg `out_img` built then immediately rebuilt)

### Fix

Drop the identity resizes (`Image.fromarray(out_rgb)` is already the target size);
let `image_to_lego_mosaic` return the index array as primary and build the PIL image
lazily so the background path skips the throwaway image.

### Related

- D-036.

## D-046

**Title:** Dead `import gc` in Main.py
**Severity:** **P3**
**Status:** FIXED 2026-06-29 (pending prune to git history) — `import gc` removed from `Main.py`; `Main` imports cleanly (`gc` is used only in `worker.py`).
**Location:** `Main.py:72`

Leftover from the D-010 `run_job` move; `gc` is used only in `worker.py`. Remove the import.

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
**Location:** `Main.py:93`, `picToMosiac.py:17`, `logger.py:18,22`, etc.

Every module does its own `os.getenv` with no central config. Two concrete clashes:
`Main.py` gates `.env` loading on `is_truthy(os.getenv("RENDER"))` while
`picToMosiac.py:17` uses `os.getenv("RENDER") is None`, so `RENDER=false` makes one
load `.env` and the other skip it (the `bool("False")` trap). `load_project_env()`
also runs twice in the main process (picToMosiac import + `Main.py:94`). Fix: a
single `config.py` bootstrap that reads + validates env once; use `is_truthy` in both
RENDER checks. Related: D-028, D-035.

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
`to_pillow`. Related: D-035.

## D-051

**Title:** `pic_to_mosaic(block_width=...)` actually carries studs (misleading name at the seam)
**Severity:** **P3**
**Status:** open
**Location:** `worker.py:130` → `picToMosiac.py:256`, used as `studs_w` then re-divided `// STUDS_PER_BLOCK` at `:302/345`

`worker.run_job` passes `block_width = mosaic_block_width * studs_per_block` (studs)
into a param named `block_width`, which is then divided back out to recover the block
count — a multiply-here/divide-there round-trip that only works because two
independent env reads return the same 16. Fix: rename to `studs_width`; pass block
count + studs-per-block explicitly. Related: D-035, D-049.

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

## D-053

**Title:** `simplify_background_lego` ran one `deltaE_ciede2000` call per unique background index
**Severity:** **P3** (3D only; bounded ≤43 iterations, but each call allocates skimage's temporaries)
**Status:** FIXED 2026-06-29 (pending prune to git history) — replaced the per-index Python loop with one batched `deltaE_ciede2000(top_lab[:,None,:], unique_lab[None,:,:])` + `argmin(axis=0)`. Same pairs and same first-min tie-break → identical remap. Verified: matches the old loop across 50 random configs; 3D pipeline fingerprint unchanged.
**Location:** `picToMosiac.py:75-81` (`simplify_background_lego`)

### Related

- D-042 (same function's `np.unique` sharing), D-044 (the deferred LAB-fold lever).

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

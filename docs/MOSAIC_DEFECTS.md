# Mosaic pipeline defects ledger

Source-of-truth catalog of defects in the **mosaic (image → LEGO kit) pipeline**
— `scripts/Main.py`, `scripts/picToMosiac.py`, `scripts/MosiacToOrder.py`,
`scripts/MosiacToInstruction.py`, `scripts/VisualMaker.py`, `scripts/Util.py`,
`scripts/logger.py`, `scripts/preview_builder.py`.

For **checkout-pipeline** defects (BrickOwl / LEGO / Stripe / Saga / DB), see
`docs/PRE_RELEASE_PAYMENT_CHECKLIST.md §4` and `docs/CHECKOUT_AUDIT.md`. This
document does NOT cover those.

## How to use this document

- **Looking up a defect:** find it in the index table below by symptom or file,
  then jump to the full entry by ID. Each entry has location, root cause,
  reproduction, fix sketch, and verification steps.
- **Fixing something:** read the "Fix" section, check "Related" for adjacent
  defects worth bundling, then read "Verification" to know how to confirm.
- **Adding a defect:** use the template in `§ How to add a defect` at the bottom.
  Assign the next free ID. Keep severity, status, and the index table in sync.

## Severity scale

| Severity | Definition |
|----------|------------|
| **P0** | Active customer impact: visible defect in shipped kits / customer-visible API responses. Affects every job in the relevant code path. |
| **P1** | Active operational impact: log noise, billing cost, wasted compute on every job. No customer-visible defect today but real cost. |
| **P2** | Latent: would be P0/P1 if a trigger fires, OR structurally blocks a planned change (e.g., CPU roadmap items). |
| **P3** | Maintainer landmine, config debt, documentation debt. No functional impact today, but easy to step on during future changes. |

## Status values

- **open** — no fix shipped
- **mitigated** — partial workaround in place, root cause still present
- **wontfix-by-design** — acknowledged trade-off, not a defect to chase
- **fixed-in: <commit>** — closed; entry retained as a regression check

## Index (sorted by severity, then file)

| ID | Severity | Status | Title | File |
|----|----------|--------|-------|------|
| [D-001](#d-001) | **P0** | open | `background_color_percent` slider is non-functional in 3D | picToMosiac.py |
| [D-002](#d-002) | **P1** | open | Duplicate stdout from `laigoLOG` propagating to root | logger.py + Main.py |
| [D-003](#d-003) | **P1** | open | `_grid_to_python_ints` uses Python loop on 640×640 grid | preview_builder.py |
| [D-004](#d-004) | **P1** | open | `log_info(fg_colors)` / `log_info(bg_colors)` dump full sets at INFO | MosiacToInstruction.py, Util.py |
| [D-005](#d-005) | **P1** | open | Triple recomputation of fg/bg unique colors | MosiacToInstruction.py |
| [D-006](#d-006) | **P1** | open | `Image.verify()` is a header sniff, not a decode | Main.py |
| [D-007](#d-007) | **P2** | open | `cv2.cvtColor(img, COLOR_BGR2RGB)` feeds wrong colorspace to MediaPipe | picToMosiac.py |
| [D-008](#d-008) | **P2** | open | `GenerateOrderList` silently drops pieces on `KeyError` | MosiacToOrder.py |
| [D-009](#d-009) | **P2** | open | `RotatingFileHandler` opened by every worker subprocess | logger.py |
| [D-010](#d-010) | **P2** | open | Worker subprocess re-imports the FastAPI + checkout tree | Main.py |
| [D-011](#d-011) | **P2** | open | MediaPipe `SelfieSegmentation` never closed | picToMosiac.py |
| [D-012](#d-012) | **P2** | open | `adjust_lightness_lab` runs at full input resolution | picToMosiac.py |
| [D-013](#d-013) | **P2** | open | CLI / API duality in shared utility modules | cross-cutting |
| [D-014](#d-014) | **P2** | open | Logging architecture: per-process file handler + duplicate propagation | cross-cutting |
| [D-015](#d-015) | **P3** | open | `make_difference_transparent` doc string and name describe the inverse of the code | picToMosiac.py |
| [D-016](#d-016) | **P3** | open | `mask > 0.51` magic threshold | picToMosiac.py |
| [D-017](#d-017) | **P3** | open | `pic_to_mosaic` returns `None`; caller masks with `or workspace` | picToMosiac.py / Main.py |
| [D-018](#d-018) | **P3** | open | `give_exception_message` is in-band log-and-reraise | picToMosiac.py |
| [D-019](#d-019) | **P3** | open | Bare `from Util import …` in `MosiacToOrder.py:9` | MosiacToOrder.py |
| [D-020](#d-020) | **P3** | open | Bare `from logger import logger` in `Util.py:8` | Util.py |
| [D-021](#d-021) | **P3** | open | Two PNG sort strategies, one dead | MosiacToInstruction.py |
| [D-022](#d-022) | **P3** | open | `empty_instructions_folder` raises when folder is missing | MosiacToInstruction.py |
| [D-023](#d-023) | **P3** | open | `assert` used for runtime invariants | MosiacToInstruction.py |
| [D-024](#d-024) | **P3** | open | Inconsistent `step` return shape across instruction helpers | MosiacToInstruction.py + VisualMaker.py |
| [D-025](#d-025) | **P3** | open | `step` counter threaded through every function as a return value | MosiacToInstruction.py + VisualMaker.py |
| [D-026](#d-026) | **P3** | open | `_mark_submission_failed` does a redundant `rmtree(job_root)` | Main.py |
| [D-027](#d-027) | **P3** | open | `mp.set_start_method("spawn", force=True)` at module import | Main.py |
| [D-028](#d-028) | **P3** | open | `MAX_WORKERS` and `MAX_QUEUE_SIZE` env vars documented but ignored | Main.py |
| [D-029](#d-029) | **P3** | open | `FRONTEND_ORIGIN` env var set but never read | Main.py |
| [D-030](#d-030) | **P3** | open | `max_tasks_per_child=1` requires Python 3.12+, unenforced | Main.py / requirements.txt |
| [D-031](#d-031) | **P3** | open | `DEBUG = bool(os.getenv("DEBUG"))` is the D1 typo bug, latent | Util.py |
| [D-032](#d-032) | **P3** | open | `preview_builder` accepts string literals instead of `MosaicType` enum | preview_builder.py |
| [D-033](#d-033) | **P3** | open | Preview palette index 0 reserved but not enforced | preview_builder.py |

---

# `scripts/picToMosiac.py`

## D-001

**Title:** `background_color_percent` slider is non-functional in 3D mosaics
**Severity:** **P0** (visible defect in every 3D mosaic shipped)
**Status:** open
**Location:** `scripts/picToMosiac.py:267–270`

### Symptom

The `background_color_percent` slider on the customer UI has effectively no
effect in 3D mode. Customers who select 100% (preserve all background colors)
and 1% (collapse to one color) both receive 3D mosaics whose backgrounds use a
single LEGO color — typically white. The 2D path is unaffected.

### Root cause

```python
fg_mask_resized = fg_a.resize(bg_idx.shape[::-1], Image.NEAREST)
fg_mask_np = np.array(fg_mask_resized)
color_quant = max(1, int((background_color_percent/100) * len(np.unique(bg_idx[fg_mask_np==255]))))
bg_idx_simplified = simplify_background_lego(bg_idx, PALETTE_LAB, k=color_quant, alpha_mask=(255-fg_mask_np))
```

`fg_mask_np == 255` selects pixels where the **foreground** is opaque (the
silhouette). `bg_idx[fg_mask_np==255]` therefore indexes the background palette
map *at foreground locations*. But `remove_background` (`picToMosiac.py:131`)
deliberately fills the background array with white (255,255,255) at foreground
positions:

```python
background = np.where(~fg_mask[...,None], img, 255)
```

After `image_to_lego_mosaic`, those white pixels map to the LEGO-white palette
index (one specific integer). So `np.unique(bg_idx[fg_mask_np==255])` is almost
always `array([<white_idx>])` — length 1 — regardless of the source image. Then:

```python
color_quant = max(1, int((bg_pct/100) * 1))
            = max(1, int(bg_pct/100))
            = 1     for bg_pct ∈ [0, 99]
            = 1     for bg_pct = 100
```

`simplify_background_lego(..., k=1, ...)` collapses the *real* background
(selected via `alpha_mask=(255-fg_mask_np)`, which is correct) down to its
single most-frequent color. The simplification itself works as designed; it's
the **target** that's wrong.

### Reproduction

1. `POST /generate` with any photo, `mosaic_type=3d`, `background_color_percent=50`.
2. Repeat with `background_color_percent=1` and `background_color_percent=100`.
3. Compare the three resulting mosaics. All three backgrounds will be solid (or
   near-solid) single-color.

### Impact

- Every 3D customer who chose a non-default slider value got a kit that ignores
  their choice. Marketing copy promises a slider that doesn't work.
- Order list (`order_list.json`) reflects the collapsed palette, so the kit
  actually shipped is correct *for what was produced* — but it's the wrong
  product.
- 2D mosaics are unaffected (no foreground mask).

### Fix

Change the mask to select **background** pixels, not foreground:

```python
color_quant = max(1, int((background_color_percent/100) * len(np.unique(bg_idx[fg_mask_np==0]))))
```

`fg_mask_np == 0` is the visible background region; `np.unique` then returns
all real background LEGO colors and the percentage scales over them.

### Verification

- Run the reproduction above with the fix. The three mosaics should have
  visibly different background color counts.
- Add a regression test that mocks `bg_idx` and `fg_mask_np` with known shapes,
  asserts `color_quant` scales linearly with `background_color_percent`.
- For a more end-to-end check, compute `len(np.unique(bg_idx_simplified))`
  before and after the fix on a fixture image; the count should track the
  slider.

### Related

- [D-007](#d-007) — `cv2.cvtColor` channel swap to MediaPipe (same function;
  partially affects mask quality, which compounds with this defect's symptom
  if mask is loose).
- [D-016](#d-016) — `mask > 0.51` magic threshold (also in the same function).
- The 2D code path does not call `simplify_background_lego` — only the 3D
  branch is affected.

---

## D-007

**Title:** `cv2.cvtColor(img, COLOR_BGR2RGB)` feeds the wrong colorspace to MediaPipe
**Severity:** **P2** (degraded segmentation precision; not visible in output colors)
**Status:** open
**Location:** `scripts/picToMosiac.py:124–135`

### Symptom

3D mosaic foreground/background partition is slightly less accurate than it
should be. Manifests as halo bleed around the subject silhouette, or occasional
mis-segmented bright-color regions (a red shirt clipped, blue sky included in
foreground). The effect is subtle and hard to attribute without controlled
A/B testing.

### Root cause

```python
img = np.array(pil_img)                                # RGB (PIL native)
img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)         # WRONG conversion direction
results = mp_selfie.process(img_rgb)
```

`pil_img` is RGB (the project consistently calls `.convert("RGB")` before
passing). `cv2.cvtColor(arr, cv2.COLOR_BGR2RGB)` *interprets* the input as BGR
and produces (its idea of) RGB by swapping channels 0 and 2 — so the output is
actually a **BGR-coded array** misleadingly named `img_rgb`. MediaPipe receives
a BGR-coded tensor while its model expects RGB. The colorspace mismatch
degrades segmentation precision; the mask is still mostly usable because selfie
segmentation is largely shape-driven.

### Why output colors are NOT affected

The downstream consumers — `background` and `foreground` — use the
**original** `img` (RGB), not `img_rgb`:

```python
background = np.where(~fg_mask[...,None], img, 255)   # RGB-correct
foreground = np.where(fg_mask[...,None], img, 255)    # RGB-correct
```

So `bg_pil`, `fg_pil` are RGB-correct. The mosaic's palette mapping operates
on correct colors. Only the **mask** carries the degradation.

> **Note:** Earlier project documentation (`CLAUDE.md § Image quality / detail
> preservation`) claimed background pixel colors were R↔B swapped. That claim
> was incorrect — corrected by the introduction of this entry.

### Impact

- Subtle quality degradation on every 3D mosaic.
- More noticeable for subjects with strong red/blue cues that MediaPipe's
  RGB-trained model would lean on (red clothing, blue backgrounds).
- Worst case: silhouette artifacts around the customer's body — typically
  fixable in their kit assembly by ignoring stray bricks, but ugly in the
  preview render.

### Fix

```python
img = np.array(pil_img)                  # RGB
results = mp_selfie.process(img)         # MediaPipe wants RGB; PIL already gave us RGB
```

The `cv2.cvtColor` call is wholly unnecessary. PIL's `np.array` is RGB; that's
what MediaPipe expects. Delete `img_rgb` entirely.

Do NOT replace with `cv2.COLOR_RGB2BGR` — that would intentionally swap into
BGR, which is also wrong for MediaPipe (MediaPipe wants RGB).

### Verification

- Visual A/B on a held-out fixture: same image through the old code vs new
  code, diff the masks. Expect tighter silhouette edges and fewer false-
  positive background-as-foreground pixels.
- If formal benchmarking is justified, set up a small fixture set (5–10
  photos) and measure mask IoU against hand-labeled ground truth before and
  after.

### Related

- [D-001](#d-001) — `background_color_percent` slider non-functional. Mask
  quality affects how much background is visible after `simplify_background_lego`,
  so this defect compounds with D-001's symptom.
- [D-011](#d-011) — MediaPipe instance never closed (same function).

---

## D-011

**Title:** MediaPipe `SelfieSegmentation` instance is created per call and never closed
**Severity:** **P2** (latent; benign today, blocking for CPU roadmap items #5/#6)
**Status:** open
**Location:** `scripts/picToMosiac.py:122–135`

### Symptom

None today. The MediaPipe object holds native (C++) graph resources that are
not released when the Python wrapper goes out of scope. With
`max_tasks_per_child=1`, the worker subprocess exits after each job and the OS
reclaims the resources. **The leak only fires when `max_tasks_per_child` rises
above 1.**

### Root cause

```python
def remove_background(pil_img):
    mp_selfie = mp.solutions.selfie_segmentation.SelfieSegmentation(model_selection=1)
    ...
    # No close() and no `with` block — Python reference goes out of scope,
    # but the underlying graph stays alive until process exit.
```

MediaPipe's solution objects implement `__enter__`/`__exit__` (context-
manager protocol). Plain garbage collection does not reliably release the
native resources because the binding uses module-level caches and graph
runners that survive Python-side GC.

### When this fires

- When CPU performance roadmap item #5 lands (`max_tasks_per_child: 3–5`):
  the worker subprocess runs N jobs before exiting. Each job creates a new
  MediaPipe graph; only the last is reachable from Python, but the prior
  graphs remain allocated natively. RSS grows by ~50–100 MB per job until the
  subprocess exits.
- Also when CPU roadmap item #6 lands (`MAX_WORKERS=2`): the leak per worker
  multiplies by the worker count.

### Impact

- With max_tasks=3 and MAX_WORKERS=2: 6× MediaPipe graphs alive simultaneously
  at peak — roughly +300–600 MB RSS above today's baseline. Could push Render
  Standard tier (2 GB) over the limit.
- Could also degrade MediaPipe inference performance after several graphs
  have been instantiated (cache fragmentation).

### Fix

Use the context-manager form:

```python
def remove_background(pil_img):
    img = np.array(pil_img)
    with mp.solutions.selfie_segmentation.SelfieSegmentation(model_selection=1) as mp_selfie:
        results = mp_selfie.process(img)
        if results.segmentation_mask is None:
            raise RuntimeError("Selfie segmentation failed")
        mask = results.segmentation_mask
    fg_mask = mask > 0.5     # also see D-016
    background = np.where(~fg_mask[...,None], img, 255)
    foreground = np.where(fg_mask[...,None], img, 255)
    ...
```

Optionally hoist the construction to module level and reuse across calls (lower
allocation overhead, smaller memory footprint, only one graph per process at a
time). This is the right call if/when `max_tasks_per_child` rises, but adds
complexity (worker-subprocess startup must initialize it; process-pool restart
discipline must hold). Start with the context-manager fix; revisit pooling if
profiling shows construction is hot.

### Verification

- Set `max_tasks_per_child=5` in a test build. Run 10 jobs. Sample RSS via
  `/proc/self/status` after each job. Expect RSS to plateau, not grow linearly.

### Related

- [D-007](#d-007) — channel swap (same function).
- CPU roadmap items #5 and #6 in `CLAUDE.md § CPU performance` are blocked on
  this and [D-009](#d-009).

---

## D-012

**Title:** `adjust_lightness_lab` runs at full input resolution
**Severity:** **P2** (compute waste on every job; 5–8% of single-job CPU per CLAUDE.md hotspot #3)
**Status:** open
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
- Compounds with [D-010](#d-010) (worker re-imports) for cumulative cold-start
  + per-job overhead.

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
  call site in `picToMosiac.py:254`. No tests reference it (no test file
  exists for picToMosiac as of the audit).

### Related

- [D-001](#d-001) and [D-007](#d-007) — same file; this is the third bug
  living in the 3D-mode foreground/background separation block.

---

## D-016

**Title:** `mask > 0.51` is an undocumented magic threshold
**Severity:** **P3** (documentation debt; possibly a typo)
**Status:** open
**Location:** `scripts/picToMosiac.py:130`

### Symptom

None. The number works.

### Root cause

```python
fg_mask = mask > 0.51
```

MediaPipe SelfieSegmentation's documented default segmentation threshold is
0.5. The +0.01 is unjustified in code, comments, or commit history. The most
likely explanations:

1. Typo for `0.5` introduced and never noticed.
2. Empirical tweak after a single subject looked better; never documented.
3. Defensive bias toward "less foreground" to reduce halo.

### Impact

- A reader can't tell which of the above it is, and so can't tell whether
  changing it (e.g., to 0.5 to fix a halo problem) is safe.
- If it IS a typo, the segmentation is mildly biased against borderline
  foreground pixels (subject silhouette is ~1 pixel narrower than intended).

### Fix

Decide which case it is. If empirical: add a comment.

```python
# 0.51 (not the MediaPipe default 0.5) — empirically reduces halo around
# subjects with low-contrast edges. Re-evaluate alongside D-007 (channel
# swap) since correcting the colorspace input may shift the optimal threshold.
fg_mask = mask > 0.51
```

If a typo: change to `0.5` and note the change.

Best to bundle this with [D-007](#d-007) — fixing the channel swap may shift
whatever empirical sweet-spot motivated `0.51`.

### Verification

- Fixture A/B as in D-007. Pick a threshold per fixture, then choose the one
  that performs best across the suite and pin it as a named module constant.

### Related

- [D-007](#d-007) — channel swap (same code block).

---

## D-017

**Title:** `pic_to_mosaic` returns `None` on success; caller masks with `or workspace`
**Severity:** **P3** (latent — currently safe; landmine for future edits)
**Status:** open
**Location:** `scripts/picToMosiac.py:238–346` (no return statement); `scripts/Main.py:710` (caller fallback)

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
  is in `Main.py:run_job`. Make sure that one captures the return value.

### Related

- [D-018](#d-018) — `give_exception_message` is the exception arm; both live
  in the same function's contract.

---

## D-018

**Title:** `give_exception_message` is an in-band log-and-reraise
**Severity:** **P3** (code smell; truncated tracebacks; fragile bare `raise`)
**Status:** open
**Location:** `scripts/picToMosiac.py:218–226`

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
- [D-014](#d-014) — broader logging fan-out architecture issues.

---

# `scripts/MosiacToOrder.py`

## D-008

**Title:** `GenerateOrderList` silently drops pieces on palette `KeyError`
**Severity:** **P2** (latent — would be P0 if triggered; ships an incomplete kit)
**Status:** open
**Location:** `scripts/MosiacToOrder.py:53–57, 69–73`

### Symptom

None today. If the trigger fires, the symptom is invisible to the system but
catastrophic to the customer: a LEGO kit shipped without the bricks needed
to complete it. The only signal is a `log_error` line in `laigo.log`.

### Root cause

```python
for rgb_row, count in zip(unique_rgb, counts):
    color_tuple = tuple(rgb_row)
    try:
        piece_id = PALETTE_DICT[color_tuple]
        order[piece_id] += int(count)
    except KeyError:
        log_error(f"Background color {color_tuple} not found in LEGO palette")
        # ← silently drops `count` pieces from the order
```

The same pattern repeats for the foreground layer below.

### When this fires

Today, every pixel in `bg_rgba` and `fg_out_rgba` comes from
`LEGO_PALETTE_RGB[idx]` (in `picToMosiac.image_to_lego_mosaic` and
`simplify_background_lego`), so palette membership is by construction. The
trigger condition requires that some pipeline transformation introduce a
color **not in** `LEGO_PALETTE_RGB_DICT` between mosaic generation and order
list compilation.

Realistic triggers:

1. A future `alpha_composite` or color-blend operation introduced into the
   3D foreground/background merge.
2. PIL's RGBA→RGB conversion paths picking up gamma correction or premult.
3. A palette entry removed without removing pixels that mapped to it.
4. A frame-color decision that introduces a perimeter color not in
   `PALETTE_DICT`.

### Impact

- If triggered: customer kit is missing the dropped pieces. They cannot
  complete the mosaic. The chargeback-dispute window is 60 days
  (`docs/PRE_RELEASE_PAYMENT_CHECKLIST.md`), so refund liability is real.
- The only operator signal is a `log_error` line — easy to miss in noisy
  logs (compounded by [D-002](#d-002), [D-004](#d-004)).

### Fix

The current behavior — log and skip — is the worst of three options. Pick
one:

1. **Fail loud (preferred for now)**: raise an `AssertionError` or
   `RuntimeError`. The job fails, `manifest_failed.json` records it, the
   customer's job hits the failure path (no charge happens — the checkout
   pipeline reads `order_list.json` *after* job completion). Operator
   investigates immediately.

   ```python
   except KeyError:
       raise RuntimeError(
           f"Mosaic pixel color {color_tuple} (layer={layer_name}) is not in "
           "LEGO_PALETTE_RGB_DICT. This is a pipeline invariant violation — "
           "every mosaic pixel should map to a palette entry. Refusing to "
           "ship a kit missing bricks."
       )
   ```

2. **Defensive remap**: snap the unknown color to the nearest palette entry
   via `nearest_palette_index_lab` (already imported in `picToMosiac`).
   Requires moving palette-nearest logic to a shared module — fine, but
   silently papers over a real pipeline bug.

3. Keep log-and-drop (current). DO NOT KEEP THIS.

Option 1 is recommended. If the failure rate proves to be more than ~0 in
practice, switch to Option 2 with a paired alert.

### Verification

- After fix: deliberately inject an off-palette pixel (e.g., monkey-patch
  `LEGO_PALETTE_RGB_DICT` in a test). Run `GenerateOrderList`. With Option 1,
  job should raise; `manifest_failed.json` should record it. With Option 2,
  job should succeed and the count should attribute to the nearest palette
  entry.

### Related

- [D-019](#d-019) — bare imports in same file.
- [D-001](#d-001) — palette pipeline (k miscalc could in theory affect what
  goes into bg_rgba, though not directly trigger this).

---

## D-019

**Title:** Bare `from Util import …` in `MosiacToOrder.py:9`
**Severity:** **P3** (landmine; only works because of sys.path side effect)
**Status:** open
**Location:** `scripts/MosiacToOrder.py:9–16`

### Symptom

`MosiacToOrder` works when imported through `picToMosiac` (which appends
`scripts/` to `sys.path`). Imported standalone (test, REPL, or any future
entry point that hasn't touched picToMosiac first), it raises
`ModuleNotFoundError: No module named 'Util'`.

### Root cause

```python
from Util import (
    GetPaletteDict,
    GetOutputPathDir,
    SaveDictAsJsonsOptimized,
    log_info,
    log_debug,
    log_error
)
```

This is a bare import. It resolves only because `picToMosiac.py:12` does
`sys.path.append(str(Path(__file__).resolve().parent))` — a module-load side
effect — before importing `MosiacToOrder` and `MosiacToInstruction` via
relative imports. The order of imports in the package is load-bearing.

### Impact

- Adding a test that imports `MosiacToOrder` directly fails with
  `ModuleNotFoundError`.
- A future contributor who reorganizes imports may break the dependency
  chain non-obviously.
- The same bug exists in `Util.py:8` for `from logger import logger`
  ([D-020](#d-020)).

### Fix

Replace with relative imports:

```python
from .Util import (
    GetPaletteDict,
    GetOutputPathDir,
    SaveDictAsJsonsOptimized,
    log_info,
    log_debug,
    log_error
)
```

Delete the unused `log_debug` import while you're there (one of several dead
imports in this file — see also unused `os`, `math.ceil`).

After fixing both D-019 and D-020, remove the `sys.path.append` from
`picToMosiac.py:12` and from `preview_builder.py:33`. Run the test suite to
confirm no other bare imports exist.

### Verification

- After fix: `python -c "from scripts import MosiacToOrder"` should succeed
  from a fresh interpreter.
- After fix: `grep -rn "^from Util import\|^from logger import" scripts/`
  should return zero hits.

### Related

- [D-020](#d-020) — sibling bare import in `Util.py`.

---

# `scripts/MosiacToInstruction.py`

## D-004

**Title:** `log_info(fg_colors)` / `log_info(bg_colors)` dump full sets at INFO level
**Severity:** **P1** (ongoing log noise + disk cost; compounds with [D-005](#d-005))
**Status:** open
**Location:** `scripts/MosiacToInstruction.py:103, 108`; `scripts/MosiacToOrder.py:90` (`log_info(f"Sum...")` is fine; the offending line is the order_dict log if extended)

### Symptom

Every job emits two log lines containing the full Python `repr` of a set of
RGB tuples for every unique foreground and background color. For typical 3D
mosaics this is 20–80 tuples per set, repeated for fg and bg, at INFO level
— so every job that reaches `GenerateInstructions` writes ~200 unique RGB
tuples to `laigo.log` and stdout.

### Root cause

```python
if not fg_rgba is None:
    fg_colors = set(map(tuple, fg[:, :, :3].reshape(-1, 3)))
    log_debug(f"FG unique RGB colors ({len(fg_colors)}):")
    log_info(fg_colors)        # ← full set as INFO

bg_colors = set(map(tuple, bg[:, :, :3].reshape(-1, 3)))
log_debug(f"\nBG unique RGB colors ({len(bg_colors)}):")
log_info(bg_colors)            # ← full set as INFO
```

Notice the inversion: the *summary* (count) is at `log_debug` (suppressed in
prod), the *full dump* is at `log_info` (visible in prod).

### Impact

- `laigo.log` fills with color-tuple repr. The `RotatingFileHandler` rotates
  more frequently than it should ([D-009](#d-009)).
- Render's hosted log ingestion bills on volume.
- Operator triage is harder: real errors are buried under color dumps.
- Compounds with [D-005](#d-005): the data is computed three different ways
  for these debug-only log lines.

### Fix

Delete the `log_info(fg_colors)` and `log_info(bg_colors)` lines. Keep the
count summaries; promote them to `log_info` (or drop them entirely).

```python
if fg_rgba is not None:
    fg_colors_count = count_colors(fg_rgba)
    log_info(f"FG unique RGB colors: {fg_colors_count}")
bg_colors_count = count_colors(bg_rgba)
log_info(f"BG unique RGB colors: {bg_colors_count}")
```

Note this also closes [D-005](#d-005) — the `set(map(tuple, ...))`
constructions become unused and the redundant `count_colors` calls collapse
to one each.

### Verification

- `grep -n "log_info" scripts/MosiacToInstruction.py` after the fix.
- Run a job; tail `laigo.log`. Should see one count summary per layer, no
  set reprs.

### Related

- [D-005](#d-005) — triple recomputation in the same block.
- [D-014](#d-014) — overall logging architecture.

---

## D-005

**Title:** Triple recomputation of fg/bg unique colors for log-only purposes
**Severity:** **P1** (perf waste on every job; trivially deletable)
**Status:** open
**Location:** `scripts/MosiacToInstruction.py:101–121`

### Symptom

Every job computes the unique RGB set of fg and bg three independent ways
on the way into `GenerateInstructions`. For a 640×640 mosaic that's
3 × O(N log N) ≈ 1.2M operations per layer × 2 layers ≈ ~5M, twice — all
for log lines that should be `log_debug`.

### Root cause

```python
# (1) Materialize full set as Python tuples
if not fg_rgba is None:
    fg_colors = set(map(tuple, fg[:, :, :3].reshape(-1, 3)))
    log_debug(f"FG unique RGB colors ({len(fg_colors)}):")
    log_info(fg_colors)

bg_colors = set(map(tuple, bg[:, :, :3].reshape(-1, 3)))
log_debug(f"\nBG unique RGB colors ({len(bg_colors)}):")
log_info(bg_colors)

# (2) np.unique again via count_colors
bg_color_count = count_colors(bg_rgba)
log_info(f"BG unique RGB colors: {bg_color_count}")

if not fg_rgba is None:
    fg_color_count = count_colors(fg_rgba)
    log_info(f"FG unique RGB colors: {fg_color_count}")
```

Nothing downstream uses `fg_colors`, `bg_colors`, `fg_color_count`, or
`bg_color_count`. They are pure log-side computations.

### Impact

- Per-job wasted CPU on a hot path. Probably 100–300 ms on a 640×640 input.
- Allocates a Python set holding every unique color as a 3-tuple. Allocator
  pressure inside the worker process.

### Fix

Delete the entire block (lines 101–121). If color counts are wanted for
operator logs, keep ONLY the `count_colors(...)` versions at `log_info` —
they're the cheapest of the three.

### Verification

- After fix: run a representative job, time `GenerateInstructions` start-to-
  end. Expect 100–300 ms improvement.
- `grep -n "fg_colors\|bg_colors" scripts/MosiacToInstruction.py` should
  return no hits (variable should be unused).

### Related

- [D-004](#d-004) — same call site; the noise problem.

---

## D-021

**Title:** Two PNG sort strategies, one dead (dead one is a future-maintainer landmine)
**Severity:** **P3** (currently inert; obvious-looking pattern that is wrong)
**Status:** open
**Location:** `scripts/MosiacToInstruction.py:30–42` vs `:179–182`

### Symptom

None today. The PDF is correctly assembled.

### Root cause

Two sorts exist:

```python
# (A) Correct — used by images_to_pdf, sorts by regex-extracted step number:
def _extract_step_num(path: Path) -> int:
    match = re.search(r'(\d+)', path.stem)
    if not match:
        raise ValueError(f"Invalid filename (no step number): {path.name}")
    return int(match.group(1).zfill(4))

def _get_ordered_pngs(input_dir: Path) -> List[Path]:
    files = [f for f in input_dir.iterdir() if f.suffix.lower() == ".png"]
    if not files:
        raise ValueError(f"No PNG files found in: {input_dir}")
    files.sort(key=_extract_step_num)
    return files
```

```python
# (B) Dead — used only for the post-PDF deletion loop:
png_files = sorted(
    instructions_dir.glob("*.png"),
    key=lambda p: int(p.stem.split("_")[-1]) if "_" in p.stem else 0
)
```

Filenames are `1.png, 2.png, …, N.png` — no underscores. (B) collapses every
key to 0, so Python's stable sort preserves `glob()`'s filesystem order
(undefined). The list is then used only to delete PNGs after PDF assembly,
where order doesn't matter. So (B) is functionally dead.

### Impact

- A future reader, expecting (B) to be load-bearing, may reuse the lambda
  pattern in a context where ordering *does* matter (e.g., post-PDF QA, or
  if filenames start to include underscores). Silent wrong-order then.

### Fix

Delete (B). Replace with:

```python
png_files = list(instructions_dir.glob("*.png"))
if not png_files:
    raise RuntimeError("No instruction PNGs found — aborting PDF generation.")
images_to_pdf(str(instructions_dir), str(pdf_path))
...
for png_file in png_files:
    png_file.unlink()
```

Order is irrelevant for the deletion loop.

### Verification

- After fix: run a normal job. PDF should be assembled in step order
  (already was — uses (A) via `images_to_pdf`). PNGs should be deleted
  after.

### Related

- [D-022](#d-022) — sister cleanup function (`empty_instructions_folder`)
  in the same file.

---

## D-022

**Title:** `empty_instructions_folder` raises when the folder is missing
**Severity:** **P3** (only fires on legacy CLI path, which isn't hooked into prod)
**Status:** open
**Location:** `scripts/MosiacToInstruction.py:12–15`

### Symptom

Running the CLI path (`pic_to_mosaic` invoked without `output_dir`, which
triggers `empty_instructions_folder`) on a clean checkout crashes with
`FileNotFoundError`.

### Root cause

```python
def empty_instructions_folder():
    folder = Path(f"{GetOutputPathDir()}/Instructions")
    shutil.rmtree(folder)   # raises FileNotFoundError if folder doesn't exist
    folder.mkdir(parents=True, exist_ok=True)
```

Compare to the sister function `empty_order_list_folder` in
`MosiacToOrder.py:24–28`, which guards correctly:

```python
def empty_order_list_folder():
    folder = Path(get_order_lists_file_path())
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True, exist_ok=True)
```

### Impact

- The API path is unaffected (it passes `output_dir`, so this function never
  fires).
- The CLI path is broken on a fresh checkout — but the CLI path is also
  hardcoded to a specific image (`stella1.jpg`) and is not used in
  production, so this is essentially a developer-friction defect.

### Fix

Add the same guard:

```python
def empty_instructions_folder():
    folder = Path(f"{GetOutputPathDir()}/Instructions")
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True, exist_ok=True)
```

Or use `shutil.rmtree(folder, ignore_errors=True)` followed by `mkdir`.

The deeper fix — see [D-013](#d-013) — is to remove the CLI duality
entirely; this function becomes unreachable then.

### Verification

- Delete `outputs/Instructions/`. Run `python -m scripts.picToMosiac …`
  (the CLI path). It should now succeed instead of crashing.

### Related

- [D-013](#d-013) — CLI / API duality (the structural root cause).
- [D-021](#d-021) — same file, related dead-cleanup pattern.

---

## D-023

**Title:** `assert` used for runtime invariants (vanishes under `python -O`)
**Severity:** **P3** (latent; assertions don't fire in optimized mode)
**Status:** open
**Location:** `scripts/MosiacToInstruction.py:82, 83, 112, 113, 114`

### Symptom

If anyone deploys with `python -O` (which strips assertions), the invariant
checks for `bg_rgba` being a `PIL.Image.Image` in RGBA mode, and for the
`bg` numpy array having the expected shape, are silently disabled. Bugs
that would otherwise raise at this entry point would now propagate
unchecked into the column-drawing loop.

### Root cause

```python
assert isinstance(bg_rgba, Image.Image)
assert bg_rgba.mode == "RGBA"
...
H, W, C = bg.shape
log_debug(f"height: {H}, width: {W}")
assert C == 4
assert W == bg_w and H == bg_h
assert W % 16 == 0 and H % 16 == 0
```

`assert` is a debug aid, not a runtime check.

### Impact

- LAIGO doesn't run with `-O` today (the project hasn't documented it as a
  deployment knob). So the assertions fire as intended.
- If a future Render config adds `-O` for any reason (e.g., to reduce
  bytecode size, to speed startup), these checks vanish silently.

### Fix

Convert to explicit raises:

```python
if not isinstance(bg_rgba, Image.Image):
    raise TypeError(f"bg_rgba must be PIL.Image.Image, got {type(bg_rgba)}")
if bg_rgba.mode != "RGBA":
    raise ValueError(f"bg_rgba must be RGBA, got {bg_rgba.mode}")
...
H, W, C = bg.shape
if C != 4:
    raise ValueError(f"bg must have 4 channels, got {C}")
if W != bg_w or H != bg_h:
    raise ValueError(f"shape mismatch: numpy ({W},{H}) vs PIL ({bg_w},{bg_h})")
if W % 16 or H % 16:
    raise ValueError(f"dims not divisible by 16: ({W},{H})")
```

If the checks aren't load-bearing — i.e., callers always satisfy them — delete
them. Don't keep `assert`-as-invariant.

### Verification

- `grep -n "^\s*assert " scripts/` should return zero or only test-file hits.

### Related

- [D-024](#d-024) — adjacent file shape: step counter contract.

---

## D-024

**Title:** Inconsistent `step` return shape across instruction helpers
**Severity:** **P3** (API ergonomic defect; easy to misuse)
**Status:** open
**Location:** `scripts/VisualMaker.py:905` (`generate_baseplate_setup` returns `(step, img)`); `scripts/VisualMaker.py:1243, 1856, 1866` (return `step` only); `scripts/MosiacToInstruction.py:131` (consumer)

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
# In MosiacToInstruction.py:131:
step, img = GenerateBasePlateInstructions(blockH, blockHeight, blockW, blockWidth, step, output_dir)
draw = ImageDraw.Draw(img)
...
# In MosiacToInstruction.py:166–173:
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

This is a wide-touch refactor. Sequence it after the [D-021](#d-021),
[D-022](#d-022), [D-023](#d-023) cleanups — all of those reduce the surface
area of `MosiacToInstruction.py` and make the StepCounter change more
mechanical.

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

## D-006

**Title:** `Image.verify()` is a header sniff, not a decode
**Severity:** **P1** (wasted compute on every malformed upload)
**Status:** open
**Location:** `scripts/Main.py:1303–1309`

### Symptom

A truncated JPEG or partially-corrupt TIFF whose headers parse cleanly
passes intake validation, gets queued, dispatches to a worker subprocess,
and fails inside `image_to_lego_mosaic` 5–30 s later. The worker writes
`manifest_failed.json`, the customer sees `failed`, and the wall-clock
overhead is a worker spawn (~3–5 s cold-start, [D-010](#d-010)) plus the
partial decode.

### Root cause

```python
try:
    with Image.open(input_file) as img:
        img.verify()
except Exception as e:
    log.warning(f"Job {job_id} rejected — invalid image: {type(e).__name__}: {e}")
    input_file.unlink(missing_ok=True)
    raise HTTPException(status_code=400, detail="Invalid image file")
```

PIL's `verify()` parses headers and detects gross corruption but does not
decode the pixel stream. A JPEG with valid SOI/EOI markers and corrupt
scanlines passes verify and fails at decode. TIFFs with valid IFDs and
corrupt strips behave similarly.

### Impact

- For every malformed upload: a worker spawn + ~3–5 s cold start (per
  [D-010](#d-010)) + partial decode wasted. The watchdog
  (`JOB_TIMEOUT_SECONDS=1800`) eventually kills truly stuck jobs but most
  fail before then.
- Render's logs ingest a full failure manifest for each — not free.
- Customer experience: a `failed` status after a 5–30 s delay instead of
  an immediate 400.

### Fix

Force a full decode at intake:

```python
try:
    with Image.open(input_file) as img:
        img.load()                         # forces full decode of pixel data
        img.convert("RGB")                 # exercise the conversion path
except Exception as e:
    log.warning(f"Job {job_id} rejected — invalid image: {type(e).__name__}: {e}")
    input_file.unlink(missing_ok=True)
    raise HTTPException(status_code=400, detail="Invalid image file")
```

`load()` is the right tool for "fully decode this image and tell me if it
can be decoded." For 250 MB uploads this is real work — but bounded
(seconds, not minutes) and far cheaper than dispatching a doomed job.

### Verification

- Construct a deliberately-truncated JPEG (open a valid one, write back
  only the first 90% of the bytes). Upload to `/generate`. Should now get
  a 400 immediately instead of a `failed` status after a worker spawn.

### Related

- [D-010](#d-010) — worker cold-start cost (per-failure overhead).
- [D-014](#d-014) — overall failure-path observability.

---

## D-010

**Title:** Worker subprocess re-imports the FastAPI + checkout tree on every job
**Severity:** **P2** (3–5 s cold-start per job; CPU roadmap hotspot #6)
**Status:** open
**Location:** `scripts/Main.py` (entire module is loaded by every worker spawn because `run_job` lives in it)

### Symptom

Every job spawn pays a 3–5 s import overhead before the actual work begins.
Profiling the worker subprocess startup shows time spent in:

- `fastapi` and its dependency tree (`pydantic`, `starlette`, `anyio`, …)
- `checkout.router`, `checkout.debug_router`, `checkout.gate_router`
- `checkout.cache`, `checkout.gate`, `jobs_store_dispatch`
- `asyncpg` (transitively from `db.py` even though no pool is opened in
  the worker)
- `stripe` SDK (lazy-imported in `StripeProvider`, but the registry module
  is imported)

None of this is used by `run_job`. The worker needs only `picToMosiac` and
stdlib helpers.

### Root cause

`run_job` is defined inside `Main.py` (lines 625–786). The
`ProcessPoolExecutor` (spawn mode) re-imports the defining module in the
worker subprocess to resolve the function reference. That triggers all of
`Main.py`'s module-level imports and side effects:

- Lines 25–33: imports from `picToMosiac`, `Util`, `checkout.router`,
  `checkout.debug_router`, `checkout.gate_router`, `checkout.cache`,
  `checkout.gate`, `jobs_store_dispatch`.
- Lines 38–43: `logging.basicConfig(...)` runs in every worker (no harm but
  no help either).
- Lines 54–55: `load_project_env()` runs in every worker.
- Lines 82–96: directory `mkdir` and write-probe runs in every worker.
- Lines 98–107: 9 INFO log lines emitted at module load. Worker stdout
  fills with these on every job.

### Impact

- Single-job CPU is paying ~3–5 s of import overhead before mosaic work
  begins (per `CLAUDE.md § CPU performance` hotspot #6).
- Customer-visible: minimum job latency is bounded below by this overhead.
- Log noise: 9 INFO lines per job spawn, plus the load_project_env log,
  every job.
- Compounds with [D-002](#d-002) (duplicate console handler) and
  [D-009](#d-009) (per-process file rotation).
- Blocks the easy win of raising `max_tasks_per_child` because each
  reused worker still pays the FastAPI import on cold start.

### Fix

Move `run_job`, `_write_error_manifest`, and the few stdlib helpers it
needs into a new `scripts/worker.py`:

```python
# scripts/worker.py
"""Worker-side mosaic job runner. NEVER import FastAPI, checkout, or DB
from this module — every import here is paid at every job spawn."""
import gc, json, logging, shutil, sys, time, traceback
from pathlib import Path
from .picToMosiac import pic_to_mosaic, MosaicType

def run_job(job_id, image_path, settings, output_root, studs_per_block, progress_path):
    ...   # body unchanged

def _write_error_manifest(job_root, job_id, settings, error, tb):
    ...   # body unchanged
```

Then in `Main.py`:

```python
from .worker import run_job, _write_error_manifest
```

The worker subprocess re-imports `scripts.worker`, which imports
`scripts.picToMosiac` (which it needs anyway) and nothing else from the
project. FastAPI / checkout / asyncpg never load in the worker.

### Migration steps

1. Create `scripts/worker.py` with the two functions and their imports
   only. Run unit tests; nothing should change.
2. Update `Main.py` to import from `.worker` instead of defining the
   functions inline. Run tests.
3. Time a job before and after with a one-off `time uvicorn …` + `curl`.
   Worker cold-start should drop from ~3–5 s to ~1 s.
4. Tune `max_tasks_per_child` only after the worker.py move ships — the
   move makes the cost of cold-start small enough that you can afford
   to amortize over fewer jobs.

### Verification

- Add a `print(time.time())` at the top of `run_job` and at the bottom of
  the lifespan startup; record both. After the fix, the time from worker
  spawn to `run_job` entry should be < 1 s on Render Starter.
- `grep -n "from .checkout\|import asyncpg\|import fastapi" scripts/worker.py`
  should return nothing.

### Related

- [D-009](#d-009) — process-safe logging (prerequisite for raising
  `max_tasks_per_child` after this fix).
- [D-011](#d-011) — MediaPipe context-manager fix (same prerequisite for
  the same reason).
- CPU roadmap items #5 and #6 in `CLAUDE.md § CPU performance`.

---

## D-026

**Title:** `_mark_submission_failed` does a redundant `rmtree(job_root)`
**Severity:** **P3** (cosmetic; suggests a missing intent)
**Status:** open
**Location:** `scripts/Main.py:837–838`

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

## D-027

**Title:** `mp.set_start_method("spawn", force=True)` at module import
**Severity:** **P3** (testability hazard; works in production)
**Status:** open
**Location:** `scripts/Main.py:3–4`

### Symptom

Any test that imports `Main` resets the multiprocessing start method
process-wide. Tests that earlier set a different method (or that share a
process with code expecting `fork` on Linux/macOS) break unexpectedly.

### Root cause

```python
import multiprocessing as mp
mp.set_start_method("spawn", force=True)
```

The call runs at module load time, with `force=True`. The Python docs are
explicit that `set_start_method` should be called only once per process,
ideally inside an `if __name__ == "__main__":` guard. The `force=True`
overrides the once-per-process rule, which is convenient for the entry
point but hostile to anything that imports `Main`.

### Impact

- On Windows, "spawn" is already the default; the call is essentially a
  no-op. Production on Render (Linux) is the case where it matters.
- Test suites that import `Main` and also use multiprocessing (e.g., for
  fixtures) can hit surprising behavior.

### Fix

Move under the `if __name__ == "__main__":` guard if `Main.py` is ever
directly executed (it isn't — `uvicorn scripts.Main:app` imports the
module). For library imports, do the safe call:

```python
import multiprocessing as mp
try:
    mp.set_start_method("spawn")
except RuntimeError:
    # Already set (likely by a parent process or a test fixture).
    if mp.get_start_method() != "spawn":
        # Document why we MUST have spawn here — process-pool re-imports
        # don't work safely with fork on Linux for our pickled callables.
        raise RuntimeError(
            f"Main expects multiprocessing start method 'spawn', got "
            f"{mp.get_start_method()!r}. Cannot continue."
        )
```

This is loud and explicit. Tests that pre-set spawn will pass through.
Tests that pre-set fork will fail with a clear message.

### Verification

- Run the test suite (`.venv\Scripts\python.exe -m scripts.test_*` per
  `CLAUDE.md § Running locally`). All currently-passing tests should
  remain passing.

### Related

- None directly. Tangentially [D-010](#d-010): once `run_job` moves to
  `scripts/worker.py`, this guard can live there instead of in `Main`.

---

## D-028

**Title:** `MAX_WORKERS` and `MAX_QUEUE_SIZE` env vars documented but ignored
**Severity:** **P3** (config debt; operator surprise)
**Status:** open
**Location:** `scripts/Main.py:68–69`; `.env`; `CLAUDE.md § Configuration` table

### Symptom

An operator who sets `MAX_QUEUE_SIZE=50` in `.env` sees no effect. No
warning, no log line, no error. The hardcoded `MAX_QUEUE_SIZE = 20` is used.

### Root cause

```python
# Keep single-worker behavior for now; queueing controls waiting jobs.
MAX_WORKERS = 1
MAX_QUEUE_SIZE = 20
```

Both are local constants. The `.env` file lists `MAX_WORKERS=2` (per
`CLAUDE.md § Configuration`), but it's never read. The `CLAUDE.md`
configuration table documents both as "ignored; hardcoded."

### Impact

- Operator confusion when scaling experiments don't take effect.
- Misleading documentation (the table) and misleading `.env` entries
  (the file).

### Fix

Either:

1. Wire them:

   ```python
   MAX_WORKERS = int(os.getenv("MAX_WORKERS", "1"))
   MAX_QUEUE_SIZE = int(os.getenv("MAX_QUEUE_SIZE", "20"))
   ```

   Then remove the "ignored; hardcoded" notes in the config table.

2. Delete them from `.env` and the config table. Document the intent (kept
   constant because CPU roadmap #6 needs to ship before raising
   `MAX_WORKERS`).

Option 1 is correct: the existing CPU roadmap explicitly contemplates
raising both, and wiring the env var makes the change a single .env edit
instead of a code change. But it must land alongside [D-009](#d-009)
(process-safe logging), which is the prerequisite for any raise.

### Verification

- After fix: set `MAX_WORKERS=2` in a test build, restart, hit `/queue`,
  confirm `max_workers` field reports 2.

### Related

- [D-009](#d-009) — logging prerequisite.
- [D-010](#d-010) — worker-import prerequisite.
- [D-011](#d-011) — MediaPipe leak prerequisite.

---

## D-029

**Title:** `FRONTEND_ORIGIN` env var set but never read
**Severity:** **P3** (config debt; sibling to D-028)
**Status:** open
**Location:** `scripts/Main.py:454–463`; `.env`

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

- [D-028](#d-028) — same class of defect.

---

## D-030

**Title:** `max_tasks_per_child=1` requires Python 3.12+, unenforced
**Severity:** **P3** (deployment-environment landmine; latent on current Render)
**Status:** open
**Location:** `scripts/Main.py:299–302`; `requirements.txt` (no version pin)

### Symptom

Server fails to start with `TypeError: ProcessPoolExecutor.__init__()
got an unexpected keyword argument 'max_tasks_per_child'` if run on Python
3.11 or earlier.

### Root cause

```python
app.state.executor = ProcessPoolExecutor(
    max_workers=MAX_WORKERS,
    max_tasks_per_child=1
)
```

`max_tasks_per_child` was added to `ProcessPoolExecutor` in Python 3.12.
Earlier interpreters raise `TypeError` at construction.

`requirements.txt` does not specify a Python version constraint.
`pyproject.toml` does not exist. Render's runtime is determined by their
defaults plus `runtime.txt` (if present) — and the current deployment is
on 3.12, so the project works.

### Impact

- A contributor who installs the project locally on Python 3.11 (still
  common) sees a startup crash. No actionable error message points them
  to the version requirement.
- A future Render runtime downgrade (rare but possible) breaks production.

### Fix

Two layers:

1. Add a `runtime.txt` or `python-version` file pinning 3.12+:

   ```
   python-3.12
   ```

2. Add a sentinel check at the top of `Main.py`:

   ```python
   import sys
   if sys.version_info < (3, 12):
       raise RuntimeError(
           f"LAIGO requires Python 3.12+ (uses ProcessPoolExecutor."
           f"max_tasks_per_child). Got {sys.version_info.major}."
           f"{sys.version_info.minor}."
       )
   ```

   So contributors who try to start the server on the wrong Python see an
   actionable error.

### Verification

- After fix: try to start the server on Python 3.11 (if available locally).
  Expect the explicit error from the sentinel, not the TypeError from
  `ProcessPoolExecutor`.

### Related

- [D-010](#d-010), [D-011](#d-011) — same code block tunables.

---

# `scripts/Util.py`

## D-020

**Title:** Bare `from logger import logger` in `Util.py:8`
**Severity:** **P3** (landmine; sibling to D-019)
**Status:** open
**Location:** `scripts/Util.py:8`

### Symptom

`Util` works when imported through `picToMosiac` (which appends
`scripts/` to `sys.path`). Imported standalone, raises
`ModuleNotFoundError: No module named 'logger'`.

### Root cause

```python
from logger import logger
```

Bare import. Same mechanism as [D-019](#d-019). Resolves only because
`picToMosiac.py:12` appends `scripts/` to `sys.path` before any other
project module imports `Util`.

### Fix

```python
from .logger import logger
```

After fixing D-019 and this, remove the `sys.path.append` from
`picToMosiac.py:12` AND from `preview_builder.py:33`. (The latter exists
as a documented workaround for the same import problem — see
preview_builder.py:28–33 comment.)

### Verification

- `grep -rn "^from logger import\|^from Util import" scripts/` should
  return zero hits.
- `python -c "from scripts import Util"` should succeed from a fresh
  interpreter.

### Related

- [D-019](#d-019) — sibling defect.

---

## D-031

**Title:** `DEBUG = bool(os.getenv("DEBUG"))` is the D1 typo bug, latent
**Severity:** **P3** (latent — variable is dead today)
**Status:** open
**Location:** `scripts/Util.py:11`

### Symptom

None. `Util.DEBUG` has no readers in the codebase.

### Root cause

```python
DEBUG = bool(os.getenv("DEBUG"))
```

`bool("False") is True` because non-empty strings are truthy. This is the
exact defect class as D1 in `docs/CHECKOUT_AUDIT.md §10`, which was fixed
in `Main.py` by introducing `is_truthy` (in `scripts/checkout/_env.py`).
The `Util.DEBUG` constant has no readers — `logger.py` sets the logger
level to DEBUG unconditionally, and no call site references `Util.DEBUG`
— so the bug has no behavioral effect today.

### Impact

- Today: zero.
- If anyone wires it back in (e.g., to gate verbose logging behind a flag),
  they inherit the trap.

### Fix

Either delete the line entirely (preferred — it's dead code), or replace
with the canonical helper:

```python
from .checkout._env import is_truthy
DEBUG = is_truthy(os.getenv("DEBUG"))
```

Deleting is cleaner. The logger.py setup already prints DEBUG-level logs
unconditionally; that's the actual debug control today.

### Verification

- After delete: `grep -rn "Util.DEBUG\b\|from .Util import.*DEBUG" scripts/`
  should return zero hits.

### Related

- D1 in `docs/CHECKOUT_AUDIT.md §10` (canonical fix pattern).
- [D-008](#d-008) — `give_exception_message` is in the same file
  neighborhood.

---

# `scripts/logger.py`

## D-009

**Title:** `RotatingFileHandler` is opened by every worker subprocess
**Severity:** **P2** (latent today; blocking for CPU roadmap items #5/#6)
**Status:** open
**Location:** `scripts/logger.py:22–27`

### Symptom

Today: minimal — `MAX_WORKERS=1` and `max_tasks_per_child=1` keep the race
window narrow. The parent process and at most one worker hold an open
handle to `laigo.log` at any time, and the worker handle is short-lived.

If either lever rises: log lines from concurrent worker processes
interleave at the byte level (the handler is not write-locked across
processes), rotation triggered by one process leaves other processes
writing to the renamed inode, and on Windows the rename itself can fail
with `WinError 32` (sharing violation).

### Root cause

```python
if not logger.handlers:
    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=MAX_BYTES,
        backupCount=1,
        encoding="utf-8"
    )
    ...
    logger.addHandler(file_handler)
```

Every worker subprocess imports `logger.py` (via `Util.py`, via `picToMosiac.py`,
during the `Main.py` re-import — [D-010](#d-010)) and the `if not
logger.handlers` guard fires *per process*. Each process therefore owns
its own file handler against the same path.

### Impact

- Today: minimal.
- After CPU roadmap #5 (`max_tasks_per_child=3-5`): same worker writes
  more lines per spawn, but only one worker at a time still. Worst case:
  one rotation race per worker lifecycle.
- After CPU roadmap #6 (`MAX_WORKERS=2`): two concurrent workers write to
  the same file. Rotation by one corrupts the other's output.
- Compounds with [D-002](#d-002) (duplicate console handler).

### Fix

Workers stream to stdout only; parent owns the file. Pseudo:

```python
# scripts/logger.py — only configure file handler in the PARENT process.
import multiprocessing
IS_WORKER = multiprocessing.parent_process() is not None

if not logger.handlers:
    if not IS_WORKER:
        # Parent only: rotating file handler
        file_handler = RotatingFileHandler(LOG_FILE, maxBytes=MAX_BYTES, backupCount=1, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    # Both parent and worker: stdout handler (uvicorn captures worker stdout)
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)
```

Uvicorn (and Render) capture worker stdout into the parent's stream;
that's the right channel for cross-process log aggregation. The file
handler stays single-writer.

For full robustness as `MAX_WORKERS` grows, the proper fix is a
`QueueHandler` in workers + a `QueueListener` in the parent. That's more
code, and the stdout-only fix is sufficient for `MAX_WORKERS=2`.

### Migration steps

1. Implement the worker/parent split above.
2. Add a test: start the server, run two jobs concurrently, assert
   `laigo.log` ends with a complete final line (no torn writes).
3. Land alongside [D-010](#d-010) (worker-module split) — both are
   prerequisites for raising the concurrency knobs in [D-028](#d-028).

### Verification

- Start the server with `MAX_WORKERS=2` (after fixing [D-028](#d-028)).
  Run a load test: 10 jobs in flight. Tail `laigo.log` for torn lines
  (lines that don't end in a newline, or that mix tokens from different
  log records). Expect zero.

### Related

- [D-002](#d-002) — duplicate console output (manifestation of
  [D-014](#d-014)).
- [D-014](#d-014) — overall logging architecture.
- [D-010](#d-010) — worker module split (same prerequisite group).

---

## D-002

**Title:** Duplicate stdout from `laigoLOG` propagating to root
**Severity:** **P1** (ongoing log volume cost on Render)
**Status:** open
**Location:** `scripts/logger.py:36–38`; `scripts/Main.py:38–43`

### Symptom

Every `log_info(...)` from `Util.py` (and transitively from every pipeline
module that uses the `Util` helpers) prints to stdout twice. The file
handler is fine; only the console is doubled. On Render, log ingestion
bills by volume.

### Root cause

Two competing handler chains:

1. `logger.py` creates a logger named `"laigoLOG"` and attaches both a
   `RotatingFileHandler` and a `StreamHandler(stdout)`. `propagate` is
   left at its default of `True`.

2. `Main.py:38–43` calls `logging.basicConfig(stream=sys.stdout)`. This
   attaches a `StreamHandler` to the **root** logger.

When `Util.log_info("foo")` runs:

- The `"laigoLOG"` logger writes "foo" to its file handler — good.
- The `"laigoLOG"` logger writes "foo" to its stream handler — stdout #1.
- Because `propagate=True`, the message bubbles up to root.
- Root's stream handler writes "foo" to stdout — stdout #2.

### Impact

- Render log ingestion doubled for every pipeline log line.
- Console reading during local development is noisier.
- Operator triage harder — every important line shows up twice.

### Fix

Pick ONE of:

1. (Recommended) Set `logger.propagate = False` in `logger.py`. The
   `"laigoLOG"` logger then handles its own messages and doesn't bubble
   up. Root is reserved for messages logged via the root logger (e.g.,
   `Main.py`'s own `logging.getLogger("laigo")`).

2. Delete the `StreamHandler` from `logger.py`. Let propagation deliver
   stdout via root's handler. This works but couples `logger.py` to
   `Main.py`'s logging setup, which is fragile.

Option 1 is cleaner.

```python
# scripts/logger.py
logger = logging.getLogger("laigoLOG")
logger.setLevel(logging.DEBUG)
logger.propagate = False   # ← add this
if not logger.handlers:
    ...
```

### Verification

- After fix: run a job, count lines containing a known unique substring
  in stdout. Each should appear exactly once.
- `laigo.log` should still receive lines as before.

### Related

- [D-014](#d-014) — overall logging architecture.
- [D-009](#d-009) — file handler concurrency.

---

# `scripts/preview_builder.py`

## D-003

**Title:** `_grid_to_python_ints` uses Python list-comprehension instead of `.tolist()`
**Severity:** **P1** (perf waste on every job; 200–500 ms on largest mosaics)
**Status:** open
**Location:** `scripts/preview_builder.py:64–66, 113, 137`

### Symptom

Building `preview.json` for a 640×640 mosaic takes 200–500 ms longer than
it should. Two list-comprehensions over the 2D numpy grid (background and,
in 3D mode, foreground) account for the difference.

### Root cause

```python
def _grid_to_python_ints(remapped: np.ndarray) -> list[list[int]]:
    """numpy 2D array -> list-of-lists of pure Python ints (json.dumps-safe)."""
    return [[int(v) for v in row] for row in remapped]
```

For a 640×640 grid, this is 409,600 Python `int()` calls inside a
double-nested comprehension. The result type is identical to what
`.tolist()` produces — except `.tolist()` is a single C-level numpy
operation, 10–50× faster.

### Impact

- Per-job overhead on a non-negligible step.
- Doubles for 3D mode (background + foreground grids).
- The function is called from `pic_to_mosaic` after the LEGO mosaic is
  built but before the order list / instruction generation; it sits in
  the critical path.

### Fix

```python
def _grid_to_python_ints(remapped: np.ndarray) -> list[list[int]]:
    """numpy 2D array -> list-of-lists of pure Python ints (json.dumps-safe).
    
    Uses ndarray.tolist() — a single C-level conversion — rather than a
    Python double-comprehension. 10–50× faster on 640×640 grids.
    """
    return remapped.tolist()
```

`np.int32.tolist()` returns Python `int`, so `json.dumps` accepts it
without numpy-type errors. Confirmed in the numpy docs.

### Verification

- Before/after time: `time python -c "..."` on the function with a
  640×640 random grid. Expect 200–500 ms saving.
- `json.dumps(payload)` should still succeed (no numpy types leaked
  through).

### Related

- [D-007](#d-007), [D-012](#d-012) — other per-job perf wins. Bundle.

---

## D-032

**Title:** `preview_builder` accepts string literals instead of `MosaicType` enum
**Severity:** **P3** (latent; type-safety hole)
**Status:** open
**Location:** `scripts/preview_builder.py:69–98`

### Symptom

If anyone adds a new `MosaicType` (e.g., `MosaicType.ISO = "iso"`),
`build_preview_payload` silently rejects it with `ValueError` instead of
accepting it as a known enum value.

### Root cause

```python
def build_preview_payload(*, mosaic_type: str, ...) -> dict:
    if mosaic_type not in ("2d", "3d"):
        raise ValueError(f"mosaic_type must be '2d' or '3d', got {mosaic_type!r}")
```

The parameter is annotated `str` and validated against hardcoded literals.
The caller in `picToMosiac.py:279, 322` passes the literal strings
directly:

```python
preview_payload = build_preview_payload(
    job_id=job_id or "",
    mosaic_type="3d",
    ...
```

### Impact

- A future `MosaicType` addition silently fails preview generation
  for that mode. The customer's mosaic is built; preview.json is not.
  `/jobs/{id}/preview` returns 404.

### Fix

Either accept the enum directly:

```python
from .picToMosiac import MosaicType   # circular-import risk — see below

def build_preview_payload(*, mosaic_type: MosaicType, ...) -> dict:
    ...
```

Or validate dynamically:

```python
def build_preview_payload(*, mosaic_type: str, ...) -> dict:
    _valid = {m.value for m in MosaicType}
    if mosaic_type not in _valid:
        raise ValueError(f"mosaic_type must be one of {sorted(_valid)}, got {mosaic_type!r}")
```

The enum-typed option is cleaner but introduces a circular import risk
(`preview_builder` would import from `picToMosiac`, which already imports
from `preview_builder`). The dynamic-validation option avoids the cycle.

A third option: move `MosaicType` to a new `scripts/types.py` (or
`scripts/_mosaic_enums.py`) that has no other dependencies, then import
from there in both `picToMosiac` and `preview_builder`. This is the right
long-term shape.

### Verification

- After fix: add a temporary `MosaicType.TEST = "test"` and pass it to
  `build_preview_payload`. Should not raise.

### Related

- [D-013](#d-013) — the CLI/API duality; preview_builder lives only on
  the API side.

---

## D-033

**Title:** Preview palette index 0 reserved for frame but not enforced
**Severity:** **P3** (forward-looking safeguard; no current bug)
**Status:** open
**Location:** `scripts/preview_builder.py:44, 52–61, 115–137`

### Symptom

None today. The producer (`_build_local_palette`) correctly assigns
palette index 0 to the frame slot and indices ≥1 to mosaic colors. The
grids never contain index 0 by construction.

### Root cause

```python
_GLOBAL_RGB_BY_INDEX: list[tuple[int, int, int]] = list(LEGO_PALETTE_RGB_DICT.keys())

def _build_local_palette(used_global_indices: set[int]) -> tuple[list[dict], dict[int, int]]:
    """Return (palette_list, global_to_local). Index 0 is always the frame slot."""
    palette: list[dict] = [{"hex": FRAME_HEX, "element_id": None}]
    global_to_local: dict[int, int] = {}
    for gidx in sorted(used_global_indices):
        rgb = _GLOBAL_RGB_BY_INDEX[gidx]
        element_id = LEGO_PALETTE_RGB_DICT[rgb]
        palette.append({"hex": _hex_for_rgb(rgb), "element_id": int(element_id)})
        global_to_local[gidx] = len(palette) - 1
    return palette, global_to_local
```

The convention is correct; nothing enforces it. A future change to
`_build_local_palette` (e.g., to skip the frame allocation when
`to_frame=False`) could leak index 0 into the grids. The frontend
contract (see `docs/PREVIEW_API.md`) assumes index 0 is never used in
grids, so if anyone later builds an in-viewer order list via
`palette[grid[y][x]].element_id`, a `None` element_id leaks into an
order pipeline.

### Impact

- None today.
- A `None` element_id in a future order pipeline = an unorderable line
  item = a customer kit short of bricks ([D-008](#d-008)-class
  consequence).

### Fix

Add a debug assertion in `build_preview_payload`:

```python
# After computing bg_local and (for 3D) fg_local, before returning:
assert int(bg_local.min()) >= 1, (
    "preview producer leaked palette index 0 (frame slot) into background_grid"
)
if is_3d:
    # fg_local_masked uses FOREGROUND_EMPTY_SENTINEL (-1) for empty pixels;
    # check only the non-sentinel cells.
    fg_real = fg_local_masked[fg_local_masked >= 0]
    if fg_real.size > 0:
        assert int(fg_real.min()) >= 1, (
            "preview producer leaked palette index 0 (frame slot) into foreground_grid"
        )
```

`assert` is appropriate here because this is a producer-invariant check,
not a runtime-input check. (Note: not subject to [D-023](#d-023) because
this is a fail-fast producer guard, not a contract enforcement against
external callers.)

Document the contract in `docs/PREVIEW_API.md` once the assertion is in
place.

### Verification

- After fix: pass a `bg_idx` containing the global palette index that
  happens to be at index 0 in `_GLOBAL_RGB_BY_INDEX` (LEGO red, `(180, 0, 0)`).
  The producer should still emit it at a local index ≥ 1 (since
  `_build_local_palette` assigns indices in `sorted(...)` order starting
  from `len(palette)=1`).

### Related

- [D-008](#d-008) — order-list invariant violation (same blast radius
  if this leaks).

---

# Cross-cutting

## D-013

**Title:** CLI / API duality in shared utility modules
**Severity:** **P2** (architectural; enables [D-022](#d-022) and adds maintenance tax)
**Status:** open
**Location:** `scripts/MosiacToOrder.py:36-37`, `scripts/MosiacToInstruction.py:85-86`, `scripts/picToMosiac.py:349-358` (`__main__`), `scripts/colorQuant.py`

### Symptom

`MosiacToOrder.py`, `MosiacToInstruction.py`, and `picToMosiac.py` each
carry two control flows:

1. **API path:** `output_dir` is passed in; outputs go to
   `outputs/{job_id}/workspace/...`.
2. **CLI path:** `output_dir is None`; outputs go to
   `outputs/Instructions/...` via `GetOutputPathDir()` (the legacy
   "scratch" directory in the project root).

The CLI path runs against hardcoded image paths in each `__main__`,
isn't hooked into production, and has bit-rot ([D-022](#d-022) crashes
on a fresh checkout).

### Root cause

```python
# MosiacToInstruction.py:85
if output_dir is None: #only clear local folder if output_dir not provided
    empty_instructions_folder()
```

Every API-shared utility module has its own `if output_dir is None: …`
branch with subtly different cleanup behavior. The CLI path was the
original code path; the API was bolted on. Both have been maintained in
parallel ever since.

### Impact

- Every change to shared utility code requires reasoning about both
  paths.
- The CLI path has bugs ([D-022](#d-022)) that aren't caught because
  no one runs it.
- New contributors see two ways to do the same thing and don't know
  which is canonical.

### Fix

Delete the CLI path. Specifically:

1. Remove `if output_dir is None:` branches from
   `GenerateInstructions` and `GenerateOrderList`. Make `output_dir` a
   required (non-None) parameter.
2. Remove `empty_instructions_folder` and `empty_order_list_folder`
   (they only existed for the CLI path).
3. Remove the `__main__` block at the bottom of `picToMosiac.py`.
4. If a CLI is still wanted for ad-hoc local testing, write a single
   `scripts/cli.py` that calls into the API code path with a
   temp `output_dir`. Documented as "dev tooling, not production."
5. `colorQuant.py` is already standalone (noted in `CLAUDE.md` as "not
   used by the pipeline"). Leave it as-is or delete it — separate decision.

### Migration steps

1. Audit `output_dir=None` callers. With the API as the only entry
   point ([D-010](#d-010) makes `run_job` the canonical caller), there
   shouldn't be any None callers.
2. Make the parameter required: `output_dir: Path` (not `Optional[Path]`).
3. Run the existing test suite. Anything that relied on the CLI path
   will break — fix or delete.

### Verification

- `grep -rn "output_dir is None\|output_dir=None" scripts/` should return
  zero hits after the fix (except in `colorQuant.py` if you chose to leave
  it).
- `grep -rn "GetOutputPathDir" scripts/` should return only the
  function definition; no call sites.

### Related

- [D-022](#d-022) — direct symptom.
- [D-021](#d-021) — adjacent dead code in the same file.

---

## D-014

**Title:** Logging architecture: per-process file handler + duplicate propagation
**Severity:** **P2** (meta-defect; root cause of D-002 and D-009)
**Status:** open
**Location:** `scripts/logger.py` + `scripts/Main.py:38–43` + every module that uses `Util.log_info`

### Symptom

See [D-002](#d-002) (duplicate stdout) and [D-009](#d-009) (per-process
file rotation race). Both are visible symptoms; this entry is the
architectural diagnosis they share.

### Root cause

Three problems compound:

1. `RotatingFileHandler` is opened by every process that imports
   `logger.py` ([D-009](#d-009)). Cross-process rotation races.
2. The `"laigoLOG"` named logger propagates to root, which has its own
   handler from `Main.py`'s `basicConfig` ([D-002](#d-002)). Duplicate
   stdout.
3. `Util.log_info`/`log_debug`/`log_error` are thin wrappers around the
   `"laigoLOG"` logger. Every pipeline log line traverses both handler
   chains (good for the file, bad for stdout).

There is no single architectural decision in the codebase about which
logger owns which sink. The setup grew organically; each piece is
locally reasonable; the composition is wrong.

### Impact

- Doubled Render log ingestion ([D-002](#d-002)).
- Latent race on the file handler ([D-009](#d-009)).
- Blocks the easy wins of raising `MAX_WORKERS` and `max_tasks_per_child`.

### Fix

The right architecture:

1. **Parent process owns the file.** The `RotatingFileHandler` is
   attached to root in `Main.py`, not in `logger.py`. Workers do not
   write to the file.
2. **Workers use stdout only.** Render captures worker stdout via
   uvicorn, so all worker logs reach Render's log ingestion through
   the parent.
3. **One logger hierarchy.** Either fold `"laigoLOG"` into the `"laigo"`
   tree (rename to `"laigo.pipeline"` or similar) so root's handlers
   serve both, or set `propagate=False` on `"laigoLOG"` and give it
   exactly the handlers it needs.

Concrete sequence:

1. Fix [D-002](#d-002) first (`logger.propagate = False`). Removes the
   double-stdout. Doesn't change file behavior.
2. Fix [D-009](#d-009) next (parent-only file handler). Closes the
   rotation race.
3. Optional follow-up: rename `"laigoLOG"` to `"laigo.pipeline"` so the
   single root-level configuration covers all loggers.

### Verification

- After [D-002](#d-002) fix: every pipeline log line shows up exactly
  once in stdout, exactly once in `laigo.log`.
- After [D-009](#d-009) fix: torn-line test under concurrent jobs
  (see D-009 Verification).

### Related

- [D-002](#d-002), [D-009](#d-009) — direct symptoms.
- [D-028](#d-028) — concurrency knobs that need this fix as a
  prerequisite.

---

# How to add a defect

1. Pick the next ID: `D-NNN` (zero-padded, monotonically incrementing).
2. Decide severity per the scale at the top.
3. Write the entry under the right section:
   - Per-file section for defects living in one file.
   - Cross-cutting section for architectural defects spanning multiple files.
4. Required subsections (use the existing entries as templates):
   - **Title** (one line)
   - **Severity** and **Status**
   - **Location** (file:line range)
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

## Templates

A copy-pasteable entry skeleton:

```markdown
## D-NNN

**Title:** ...
**Severity:** **P?** (one-line justification)
**Status:** open
**Location:** `scripts/...:lines`

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

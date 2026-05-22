# 3D Preview API — Developer & Frontend Reference

## 1. Overview

After a mosaic job completes, the backend persists a small JSON file describing the finished mosaic in machine-readable form. The frontend fetches it via `GET /jobs/{job_id}/preview` and renders a 3D scene (Three.js or equivalent) showing baseplates, studs, the optional perimeter frame, and — for 3D mosaics — raised foreground studs.

**Why an endpoint, not a baked-in artifact:** keeps the backend presentation-agnostic. The frontend can iterate on rendering without redeploying the API.

**Lifecycle:** the file is written at job-success time, lives at `outputs/{job_id}/preview.json`, and is rmtree'd along with the rest of the job dir when `cleanup_loop` evicts the job at TTL (`JOB_TTL_SECONDS`).

---

## 2. HTTP contract

```
GET {API_BASE}/jobs/{job_id}/preview
```

- **When to call:** after `GET /jobs/{job_id}` reports `status: "complete"`.
- **Success:** `200 application/json` with the payload in §3.
- **Cacheability:** payload is immutable for the life of the job; cache aggressively client-side.

### Error responses

| Status | `detail.code`            | Cause                                                                              |
| ------ | ------------------------ | ---------------------------------------------------------------------------------- |
| 404    | `PREVIEW_NOT_AVAILABLE`  | Job missing / in-progress / failed / TTL-evicted. Treat all four cases identically. |
| 500    | `PREVIEW_CORRUPTED`      | File present but unreadable. Should not happen in normal operation.                 |

Both error bodies follow the project's modern error contract:
```json
{ "detail": { "error": "<human message>", "code": "<machine code>" } }
```
Match on `detail.code`, never on the human-readable `error` string.

---

## 3. Payload schema

```jsonc
{
  "schema_version": 1,
  "job_id": "9f43a60f-...",
  "mosaic_type": "2d",             // "2d" | "3d"
  "width_studs": 64,               // total mosaic width in studs
  "height_studs": 32,              // total mosaic height in studs
  "block_width": 4,                // # of 16x16 baseplate blocks horizontally
  "block_height": 2,               // ...vertically
  "has_frame": true,
  "foreground_lift_plates": 0,     // 0 for 2D; 1 for 3D
  "frame": {                        // ALWAYS present; render only when has_frame
    "thickness_studs": 1,
    "height_plates": 6,            // 2 bricks * 3 plates
    "palette_index": 0
  },
  "palette": [
    {"hex": "#1B2A34", "element_id": null},   // INDEX 0: frame slot. See §6.
    {"hex": "#B40000", "element_id": 302421}
    // ... only colors actually used (compacted)
  ],
  "background_grid": [[ /* uint8 */ ]],   // height_studs × width_studs
  "foreground_grid": [[ /* int16 */ ]]    // 3D ONLY; -1 sentinel for empty
}
```

### Invariants

- `width_studs === block_width * 16` and `height_studs === block_height * 16` — both grid dimensions are always multiples of 16.
- `background_grid.length === height_studs` and `background_grid[0].length === width_studs`.
- `foreground_grid` (when present) has the **same shape** as `background_grid`.
- `mosaic_type === "2d"` ⇒ `foreground_grid` key omitted, `foreground_lift_plates === 0`.
- `mosaic_type === "3d"` ⇒ `foreground_grid` present, `foreground_lift_plates === 1`.
- `frame` object always present (check `has_frame` before rendering).

---

## 4. Coordinate system

- Rows are top-down, columns are left-to-right: `grid[row][col]` is the cell at image-pixel position `(col, row)`.
- Backend ships **stud-grid positions only** — the frontend chooses world units (typically 1 stud = 8mm to match real LEGO pitch).
- Baseplate sits at `y = 0`; studs and the frame rise upward.

### LEGO physical reference (use whatever scale; the ratios matter)

| Element     | Height (mm) | Notes                          |
| ----------- | ----------- | ------------------------------ |
| 1 plate     | 3.2         |                                |
| 1 brick     | 9.6         | 3 plates                       |
| Stud cap    | 1.8         | Diameter 4.8mm                 |
| Stud pitch  | 8           | X/Z spacing between studs      |

`frame.height_plates: 6` ⇒ `6 × 3.2mm = 19.2mm` = 2 bricks tall.

---

## 5. Grid interpretation

### `background_grid`

A `height_studs × width_studs` 2D array. Every cell is a non-negative palette index.

```js
const localIdx = payload.background_grid[row][col];
const { hex, element_id } = payload.palette[localIdx];
// Render a 1×1 stud at (col, row) with color `hex`.
```

For 2D mosaics this *is* the whole mosaic. For 3D mosaics, this is the layer flush against the baseplate; the foreground sits above it.

### `foreground_grid` (3D mosaics only)

Same shape as `background_grid`. Per-cell semantics:

- `value >= 0` → render a stud here in `palette[value]`, raised by `foreground_lift_plates × PLATE_HEIGHT` above the baseplate.
- `value === -1` → no foreground here. Only the background cell at this position is rendered.

The background grid stays complete underneath the foreground — so when the camera rotates and looks at the mosaic from a low angle, the user sees background color peeking out from under the raised foreground (matches a real 3D LEGO mosaic build).

---

## 6. Palette semantics

Two rules drive the palette's design:

1. **Index 0 is reserved for the perimeter frame.** Always present at `palette[0]`, always `{"hex": "#1B2A34", "element_id": null}`. Present even when `has_frame === false` — keeps grid indices stable across builds, so the frontend never needs conditional index shifting.

2. **`element_id: null` means "not orderable".** The frame is hardcoded structural black; it doesn't appear in the customer's brick order (the order list ships frame pieces separately as structural parts — corner blocks, axle bricks, etc., not as colored studs). Every other palette entry has a real LEGO `element_id` matching the order list JSON.

### LEGO black ≠ frame black

The mosaic itself may legitimately use LEGO black (element_id `302426`). When it does, the palette contains **two entries with hex `#1B2A34`** — one at index 0 (frame slot, `element_id: null`) and another at a higher index (orderable LEGO black, `element_id: 302426`). They have the same hex but distinct semantics:

```jsonc
"palette": [
  {"hex": "#1B2A34", "element_id": null},    // index 0 — frame, hardcoded
  ...
  {"hex": "#1B2A34", "element_id": 302426}   // index N — orderable LEGO Black
]
```

The grids reference whichever index the mosaic uses. The frontend renders them with the same color but counts them separately if it shows a "colors used" sidebar.

### Compaction

The palette only contains colors actually used in the mosaic, plus the frame slot. Typical: 15–35 entries. Maximum: 44 (43 LEGO colors + frame slot).

---

## 7. Frame rendering

Render the frame as a **perimeter wall** around the mosaic when `has_frame === true`:

- **Footprint:** 1-stud-thick (`thickness_studs`) border on all 4 sides. Outer footprint is `(width_studs + 2 × thickness_studs) × (height_studs + 2 × thickness_studs)` studs.
- **Height:** `height_plates × PLATE_HEIGHT` above the baseplate (= 19.2mm with default 6 plates). Rises above the mosaic surface → shadow-box effect.
- **Color:** `palette[frame.palette_index].hex` (= `palette[0].hex`). Reference via the index, don't hardcode.

**Frame cells do NOT appear in the grids.** The grids describe the mosaic interior only; the frame is metadata to be drawn from dimensions.

---

## 8. Schema versioning

`schema_version: 1` is the launch baseline.

| Change                                       | Version bump? | Frontend behavior on unknown version       |
| -------------------------------------------- | ------------- | ------------------------------------------ |
| New optional field (additive)                | No            | Tolerate unknown keys; render normally     |
| Field rename / removal / semantic change     | Yes (→ 2)     | Refuse-with-message, do not best-effort    |
| Grid sentinel change (e.g., `-1` → `null`)   | Yes (→ 2)     | Refuse-with-message                        |

Frontends should treat `payload.schema_version > KNOWN_VERSION` as a hard error.

---

## 9. Backend module map

| File                              | Role                                                                                                                  |
| --------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| `scripts/preview_builder.py`      | Pure `build_preview_payload(...)` + `write_preview_atomic(payload, path)`. No I/O outside the writer.                  |
| `scripts/picToMosiac.py`          | Calls `build_preview_payload(...)` after index arrays are computed in both 2D and 3D branches; writes to `workspace/preview.json`. Failure is non-fatal (logs and continues). |
| `scripts/Main.py:run_job`         | Copies `workspace/preview.json` → `outputs/{job_id}/preview.json` before workspace deletion. Mirrors the order_list handoff. |
| `scripts/Main.py:get_job_preview` | The HTTP route. Reads bytes into memory (NOT `FileResponse`) to avoid Windows rmtree races with `cleanup_loop`.        |
| `scripts/test_preview.py`         | 13 standalone tests — pure-function unit + endpoint handler. Run: `.venv\Scripts\python.exe -m scripts.test_preview`.   |

### Worker write atomicity

`write_preview_atomic` writes to `{path}.tmp` then `os.replace()` to the final path. Readers never observe a half-written file.

### Why read-into-memory in the handler

`Response(content=bytes, ...)` closes the file descriptor immediately. `FileResponse` keeps it open across the await boundary, which on Windows blocks `cleanup_loop.shutil.rmtree`. Grids are bounded by ~1.5 MB at max mosaic size — the memcopy is cheap.

---

## 10. What is NOT in the payload (and where it lives)

| Concern              | Lives in       | Notes                                                                                |
| -------------------- | -------------- | ------------------------------------------------------------------------------------ |
| Baseplate color      | Frontend       | Render whatever color you want. Black matches the underside of real LEGO baseplates. |
| Baseplate connectors | Frontend       | Positions deterministic from `block_width × block_height` — compute, don't fetch.    |
| Lighting / camera    | Frontend       | Pure presentation.                                                                   |
| Stud geometry detail | Frontend       | Backend says "color at z-offset" — frontend decides cylinder vs. shader vs. mesh.    |
| LEGO color names     | Not exposed    | Look up via `element_id` if you need a display name. Hex + element_id is canonical.  |

---

## 11. Failure modes

| Symptom                                                       | Likely cause                                                | Fix                                                                  |
| ------------------------------------------------------------- | ----------------------------------------------------------- | -------------------------------------------------------------------- |
| 404 immediately after `/jobs/{id}` reports `complete`         | Worker logged a non-fatal preview failure (see logs)        | Re-run the job. Worker logs `preview.json build/write failed (non-fatal): ...` |
| 404 after a while (job was completing earlier)                | TTL eviction (`JOB_TTL_SECONDS`)                            | Re-run the job; tune TTL if jobs are needed longer.                  |
| 500 PREVIEW_CORRUPTED                                         | File present but unreadable (permissions, disk fault)       | Inspect `outputs/{job_id}/preview.json` manually.                    |
| 404 only for old jobs that completed before this feature shipped | Backfill not implemented — by design                       | Re-run the job to regenerate.                                        |

Preview write failure does **not** fail the job — the customer still gets their build kit (PDF + order list). Only the 3D preview becomes unavailable.

---

## 12. Verification

```powershell
# Unit + endpoint tests
.venv\Scripts\python.exe -m scripts.test_preview

# Manual smoke test
uvicorn scripts.Main:app --reload
# Submit a small job via /docs, wait for complete, then:
curl http://127.0.0.1:8000/jobs/{job_id}/preview
```

Expect `200 application/json` with the schema in §3. CORS allows `http://localhost:5173` (Vite default) and the deployed frontend URL — see `Main.py` middleware.

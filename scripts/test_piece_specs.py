"""Tests for the per-step "pieces used" legend (piece_specs + VisualMaker).

Covers:
  * scripts/piece_specs.SPEC_BY_ELEMENT — must describe every element id that
    MosiacToOrder.GetBaseplatesForSize / GetFrameForSize emit, with sane dims.
  * scripts/VisualMaker._baseplate_step_b_parts — per-case connector attribution
    for the baseplate "step after" (5x 2x2 plates + needed connectors).
  * scripts/VisualMaker.draw_step_parts_legend — renders only in the top strip,
    never spilling into the isometric build mid-band or the bottom step number.

Run from project root:
    .venv\\Scripts\\python.exe -m scripts.test_piece_specs
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

from scripts import piece_specs as ps
from scripts.MosiacToOrder import GetBaseplatesForSize, GetFrameForSize
from scripts.VisualMaker import _baseplate_step_b_parts, draw_step_parts_legend, draw_mini_piece


def test_spec_table_covers_all_order_elements():
    # Every structural + frame element a build can order must have a legend spec.
    elements = set()
    for w, h in ((32, 32), (48, 32), (16, 16)):
        elements |= set(GetBaseplatesForSize(w, h))
        elements |= set(GetFrameForSize(w, h))
    missing = elements - set(ps.SPEC_BY_ELEMENT)
    assert not missing, f"piece_specs missing element ids: {sorted(missing)}"
    print("OK: test_spec_table_covers_all_order_elements")


def test_known_piece_dimensions():
    # Spot-check dims/shape against the frame draw_* ground truth + the palette.
    assert ps.SPEC_BY_ELEMENT[6302092].shape == ps.SHAPE_BASEPLATE
    assert (ps.SPEC_BY_ELEMENT[6302092].width, ps.SPEC_BY_ELEMENT[6302092].length) == (16, 16)
    assert (ps.SPEC_BY_ELEMENT[4211094].width, ps.SPEC_BY_ELEMENT[4211094].length) == (2, 2)
    assert (ps.SPEC_BY_ELEMENT[383226].width, ps.SPEC_BY_ELEMENT[383226].length) == (10, 2)
    assert (ps.SPEC_BY_ELEMENT[300826].width, ps.SPEC_BY_ELEMENT[300826].length) == (8, 1)
    axle = ps.SPEC_BY_ELEMENT[6178922]
    assert (axle.width, axle.length) == (2, 1) and axle.axle is True
    assert ps.SPEC_BY_ELEMENT[235726].shape == ps.SHAPE_CORNER
    assert ps.SPEC_BY_ELEMENT[6526672].shape == ps.SHAPE_CONNECTOR
    print("OK: test_known_piece_dimensions")


def test_corner_pieces_distinct():
    # Corner PLATE = large flat L (4x4 minus 2x2 = 12 studs), plate height 1.
    plate = ps.SPEC_BY_ELEMENT[6483102]
    assert (plate.width, plate.length, plate.height) == (4, 4, 1), "corner plate dims"
    assert plate.shape == ps.SHAPE_CORNER
    # Corner BRICK = small thick L (2x2 minus 1x1 = 3 studs), block height 3.
    brick = ps.SPEC_BY_ELEMENT[235726]
    assert (brick.width, brick.length, brick.height) == (2, 2, 3), "corner brick dims"
    assert brick.shape == ps.SHAPE_CORNER
    print("OK: test_corner_pieces_distinct")


def test_mosaic_plate_spec():
    spec = ps.mosaic_plate_spec((0.1, 0.2, 0.3, 1.0))
    assert spec.shape == ps.SHAPE_PLATE
    assert (spec.width, spec.length) == (1, 1)
    assert spec.color == (0.1, 0.2, 0.3)  # alpha dropped
    print("OK: test_mosaic_plate_spec")


def _ids(parts):
    return [spec.name for spec, _qty in parts]


def test_step_b_parts_per_case():
    # Every case always includes the 5x 2x2 plates.
    for case in range(4):
        parts = _baseplate_step_b_parts(case)
        twos = [(s, q) for s, q in parts if s is ps.SPEC_BY_ELEMENT[4211094]]
        assert twos == [(ps.SPEC_BY_ELEMENT[4211094], 5)], f"case {case}: 2x2 plates wrong"

    # case 0 (interior): green + red connectors and both bridge plates.
    c0 = _baseplate_step_b_parts(0)
    assert "green conn" in _ids(c0) and "red conn" in _ids(c0)
    # case 1 (last column -> bottom neighbor only): red, no green.
    c1 = _ids(_baseplate_step_b_parts(1))
    assert "red conn" in c1 and "green conn" not in c1
    # case 2 (last row -> right neighbor only): green, no red.
    c2 = _ids(_baseplate_step_b_parts(2))
    assert "green conn" in c2 and "red conn" not in c2
    # case 3 (corner -> no neighbors): just the 2x2 plates.
    assert _ids(_baseplate_step_b_parts(3)) == ["2x2 plate"]
    print("OK: test_step_b_parts_per_case")


def test_step_b_connector_quantities():
    # Green/red connectors come in 2s; bridge plates singly — matches the
    # per-block counts in GetBaseplatesForSize.
    parts = dict((s, q) for s, q in _baseplate_step_b_parts(0))
    assert parts[ps.SPEC_BY_ELEMENT[6526672]] == 2   # green connectors
    assert parts[ps.SPEC_BY_ELEMENT[6347789]] == 2   # red connectors
    assert parts[ps.SPEC_BY_ELEMENT[4621548]] == 1   # green bridge plate
    assert parts[ps.SPEC_BY_ELEMENT[379521]] == 1    # red bridge plate
    print("OK: test_step_b_connector_quantities")


def _nonwhite_bbox(img):
    arr = np.asarray(img.convert("RGB"))
    mask = np.any(arr != 255, axis=2)
    ys, xs = np.where(mask)
    return (xs.min(), ys.min(), xs.max(), ys.max()), int(mask.sum())


def test_legend_stays_in_top_strip():
    # The legend must render in the top strip only — clear of the isometric build
    # (mid-band, ~y>=250) and the bottom-center step number (~y>=700).
    img = Image.new("RGBA", (612, 792), (255, 255, 255, 255))
    draw = ImageDraw.Draw(img)
    parts = [
        (ps.SPEC_BY_ELEMENT[6302092], 1),    # 16x16 baseplate
        (ps.SPEC_BY_ELEMENT[4211094], 5),    # 2x2 plate
        (ps.SPEC_BY_ELEMENT[6526672], 2),    # green connector
        (ps.SPEC_BY_ELEMENT[383226], 8),     # 10x2 plate
        (ps.SPEC_BY_ELEMENT[235726], 4),     # corner brick
    ]
    draw_step_parts_legend(draw, parts)
    (x0, y0, x1, y1), n = _nonwhite_bbox(img)
    assert n > 0, "legend drew nothing"
    assert y1 < 160, f"legend spilled below the top strip (y_max={y1})"
    assert x1 < 480, f"legend ran past the minimap zone (x_max={x1})"
    print(f"OK: test_legend_stays_in_top_strip (bbox=({x0},{y0},{x1},{y1}), px={n})")


def _render_icon_bbox(element_id):
    # Render just the icon (no label text) so the bbox measures the piece itself.
    img = Image.new("RGBA", (400, 400), (255, 255, 255, 255))
    draw_mini_piece(ImageDraw.Draw(img), 200, 200, 11.0, ps.SPEC_BY_ELEMENT[element_id])
    return _nonwhite_bbox(img)


def test_corner_icons_render_distinctly():
    # The corner plate (4x4-minus-2x2, 12 studs) must render a clearly larger icon
    # than the corner brick (2x2-minus-1x1, 3 studs) — no longer the same icon.
    (px0, _py0, px1, _py1), plate_px = _render_icon_bbox(6483102)
    (bx0, _by0, bx1, _by1), brick_px = _render_icon_bbox(235726)
    plate_w = px1 - px0
    brick_w = bx1 - bx0
    assert plate_w > brick_w, f"corner plate icon ({plate_w}) not wider than brick ({brick_w})"
    # The bigger L with 12 studs paints more pixels than the small 3-stud L.
    assert plate_px > brick_px, f"corner plate px ({plate_px}) not > brick px ({brick_px})"
    print(f"OK: test_corner_icons_render_distinctly (plate_w={plate_w}, brick_w={brick_w})")


def test_legend_empty_is_noop():
    img = Image.new("RGBA", (612, 792), (255, 255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw_step_parts_legend(draw, [])
    arr = np.asarray(img.convert("RGB"))
    assert np.all(arr == 255), "empty legend should draw nothing"
    print("OK: test_legend_empty_is_noop")


if __name__ == "__main__":
    test_spec_table_covers_all_order_elements()
    test_known_piece_dimensions()
    test_corner_pieces_distinct()
    test_mosaic_plate_spec()
    test_step_b_parts_per_case()
    test_step_b_connector_quantities()
    test_legend_stays_in_top_strip()
    test_corner_icons_render_distinctly()
    test_legend_empty_is_noop()
    print("\nAll piece-specs / legend tests passed.")

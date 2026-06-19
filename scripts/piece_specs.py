"""
piece_specs.py — dependency-free physical-piece reference table.

Each instruction step's "pieces used" legend (rendered by VisualMaker's
``draw_step_parts_legend`` / ``draw_mini_piece``) needs to know the shape, stud
dimensions, color and human label of every physical LEGO piece a build uses.
This module is the single source of truth, keyed by the LEGO element IDs that
``MosiacToOrder.GetBaseplatesForSize`` / ``GetFrameForSize`` emit.

Kept a leaf (stdlib only, like ``mosaic_types.py``) so ``VisualMaker`` can import
it without a cycle. Dimensions sourced from: the 1x1-plate palette in ``Util``,
the frame ``draw_*`` calls in ``VisualMaker`` (ground truth for the rectangular
frame pieces), and the ``GetFrameForSize`` / ``GetBaseplatesForSize`` variable
names. Colors mirror how each piece is drawn elsewhere so the legend icon
matches the build art.
"""
from dataclasses import dataclass

# Shape vocabulary understood by VisualMaker.draw_mini_piece:
#   "plate"     — flat rectangular WxL piece (1 plate tall)
#   "brick"     — raised rectangular WxL piece (~3 plates tall)
#   "baseplate" — large flat WxL plate; drawn as a box (studs suggested, not all 256)
#   "corner"    — L-shaped corner piece (2x2 minus 1x1)
#   "connector" — specialty LEGO Art pin; drawn with the baseplate-underside pin shape
SHAPE_PLATE = "plate"
SHAPE_BRICK = "brick"
SHAPE_BASEPLATE = "baseplate"
SHAPE_CORNER = "corner"
SHAPE_CONNECTOR = "connector"

# Normalized (0..1) RGB colors matching how the pieces are drawn elsewhere.
_GREY_BASEPLATE = (0.2, 0.2, 0.2)     # draw_baseplate_* default
_GREY_PLATE = (0.44, 0.44, 0.44)      # 2x2 reinforcement plates on the underside
_GREEN = (0.647, 0.792, 0.09)         # LEGO Art green connector / bridge plate
_RED = (0.8, 0.1, 0.1)                # LEGO Art red connector / bridge plate
_FRAME_BLACK = (0.3, 0.3, 0.3)        # frame pieces are drawn in this dark grey


@dataclass(frozen=True)
class PieceSpec:
    name: str                # human label, e.g. "8x1 brick", "green connector"
    width: int               # studs along the X axis
    length: int              # studs along the Y axis
    height: int              # plate layers (1 = plate, 3 = brick) — controls icon depth
    shape: str               # one of the SHAPE_* constants
    color: tuple             # normalized (r, g, b)
    axle: bool = False       # draw an axle-hole "+" marker on the front face
    show_dims: bool = True   # label as "WxH"; False -> use ``name`` (connectors/corners)


def mosaic_plate_spec(color):
    """Spec for a single 1x1 mosaic-pixel plate of the given normalized color."""
    return PieceSpec("1x1 plate", 1, 1, 1, SHAPE_PLATE, tuple(color[:3]))


# Element-ID -> PieceSpec. Keys are exactly the IDs returned by
# MosiacToOrder.GetBaseplatesForSize and GetFrameForSize (see test_piece_specs).
SPEC_BY_ELEMENT = {
    # --- baseplate foundation (GetBaseplatesForSize) ---
    6302092: PieceSpec("16x16 baseplate", 16, 16, 1, SHAPE_BASEPLATE, _GREY_BASEPLATE),
    4211094: PieceSpec("2x2 plate", 2, 2, 1, SHAPE_PLATE, _GREY_PLATE),
    6526672: PieceSpec("green conn", 1, 1, 1, SHAPE_CONNECTOR, _GREEN, show_dims=False),
    6347789: PieceSpec("red conn", 1, 1, 1, SHAPE_CONNECTOR, _RED, show_dims=False),
    4621548: PieceSpec("green bridge plate", 2, 4, 1, SHAPE_PLATE, _GREEN),
    379521:  PieceSpec("red bridge plate", 4, 2, 1, SHAPE_PLATE, _RED),
    # Nail/hanging hooks — deferred (omitted from legends for now); listed for coverage.
    6302094: PieceSpec("nail hook", 1, 1, 1, SHAPE_CONNECTOR, _GREY_PLATE, show_dims=False),
    6279875: PieceSpec("hook connector", 1, 1, 1, SHAPE_CONNECTOR, _GREY_PLATE, show_dims=False),

    # --- frame (GetFrameForSize) ---
    # Corner brick: small thick L (2x2 minus front 1x1 = 3 studs), block height.
    235726:  PieceSpec("corner brick", 2, 2, 3, SHAPE_CORNER, _FRAME_BLACK, show_dims=False),
    # Corner plate: large flat L (4x4 minus front 2x2 = 12 studs), plate height.
    6483102: PieceSpec("corner plate", 4, 4, 1, SHAPE_CORNER, _FRAME_BLACK, show_dims=False),
    300526:  PieceSpec("1x1 brick", 1, 1, 3, SHAPE_BRICK, _FRAME_BLACK),
    6178922: PieceSpec("2x1 axle brick", 2, 1, 3, SHAPE_BRICK, _FRAME_BLACK, axle=True),
    4109810: PieceSpec("axle peg", 1, 1, 1, SHAPE_CONNECTOR, _FRAME_BLACK, show_dims=False),
    300826:  PieceSpec("8x1 brick", 8, 1, 3, SHAPE_BRICK, _FRAME_BLACK),
    301026:  PieceSpec("4x1 brick", 4, 1, 3, SHAPE_BRICK, _FRAME_BLACK),
    383226:  PieceSpec("10x2 plate", 10, 2, 1, SHAPE_PLATE, _FRAME_BLACK),
    379526:  PieceSpec("6x2 plate", 6, 2, 1, SHAPE_PLATE, _FRAME_BLACK),
    246526:  PieceSpec("16x1 brick", 16, 1, 3, SHAPE_BRICK, _FRAME_BLACK),
    6439175: PieceSpec("thin corner plate", 2, 2, 1, SHAPE_CORNER, _FRAME_BLACK, show_dims=False),
    243126:  PieceSpec("1x4 flat plate", 1, 4, 1, SHAPE_PLATE, _FRAME_BLACK),
}


def get_spec(element_id):
    """Look up the PieceSpec for a LEGO element id (raises KeyError if unknown)."""
    return SPEC_BY_ELEMENT[element_id]

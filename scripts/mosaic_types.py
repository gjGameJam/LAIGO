"""Shared mosaic enums — a dependency-free leaf module.

D-032: MosaicType lived in picToMosiac.py, but preview_builder.py needs it to
validate its `mosaic_type` argument. Importing picToMosiac from preview_builder
would be circular (picToMosiac imports preview_builder), so the enum is hoisted
here where both can import it without a cycle. picToMosiac re-exports it for
backward compatibility (`from .picToMosiac import MosaicType` still works).
"""
from enum import Enum


class MosaicType(str, Enum):
    TWO_D = "2d"
    THREE_D = "3d"


# D-035: studs along one edge of a baseplate "block". This is a STRUCTURAL
# constant, NOT a runtime knob. The baseplate art and per-column plate geometry
# in VisualMaker / MosiacToInstruction are hand-built for a 16x16 block — fixed
# plate y-positions, the `range(STUDS_PER_BLOCK)` column extraction with the
# `15 - y` flip, the baseplate hole layout — so changing this value would require
# reworking that drawing code, not just the divisions. It lives here (the shared
# dependency-free leaf) so every module imports one source of truth instead of
# the old per-module literal `16` / the divergent `STUD_WIDTH_OF_BLOCK` env var
# that picToMosiac honored but the downstream `W % 16` guard did not — a mismatch
# that failed every job if the env var was ever changed.
STUDS_PER_BLOCK = 16

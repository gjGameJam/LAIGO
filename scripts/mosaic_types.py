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

"""Tests for expanded image-format support (HEIC/HEIF + AVIF) and EXIF orientation.

Covers the two upload decode sites and the pillow-heif plugin registration:
  - scripts.picToMosiac.open_image (worker subprocess) — must decode HEIC/AVIF
    and apply EXIF orientation before the RGB convert.
  - scripts.Main._validate_image (API parent) — must accept HEIC/AVIF up front
    (inherits the openers registered at import in picToMosiac).

Importing scripts.picToMosiac at the top triggers register_heif_opener(), which
lets this module SAVE/OPEN synthetic .heic fixtures. AVIF decode/encode is native
to Pillow (>=11.3) — no registration needed. Fixtures are synthesized in a temp
dir — no on-disk assets.

Note: test_validate_accepts_heic imports scripts.Main, which runs
mp.set_start_method("spawn") and builds the FastAPI app at import. Harmless when
run standalone, but it's the reason this must be run as its own module:

    .venv\\Scripts\\python.exe -m scripts.test_image_formats
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from PIL import Image

import scripts.picToMosiac as _p2m  # noqa: F401 — side effect: registers HEIF/AVIF openers
from scripts.picToMosiac import open_image


def _make(dirpath: str, name: str, fmt: str, size=(64, 48), color=(200, 50, 25)) -> Path:
    """Save a solid-color RGB image in the given format; return its path."""
    p = Path(dirpath) / name
    Image.new("RGB", size, color).save(p, format=fmt)
    return p


def _make_jpeg_oriented(dirpath: str, size=(64, 48), orientation: int = 6) -> Path:
    """Write a JPEG carrying an EXIF Orientation tag (0x0112).

    Orientation 6 = rotate 90° CW: a stored 64x48 landscape frame should DISPLAY
    as 48x64 portrait once exif_transpose is applied.
    """
    p = Path(dirpath) / "rot.jpg"
    exif = Image.Exif()
    exif[0x0112] = orientation
    Image.new("RGB", size, (10, 120, 200)).save(p, format="JPEG", exif=exif)
    return p


def test_open_image_heic_rgb_and_size():
    with tempfile.TemporaryDirectory() as d:
        img = open_image(_make(d, "sample.heic", "HEIF", size=(64, 48)))
        assert img.mode == "RGB", f"expected RGB, got {img.mode}"
        assert img.size == (64, 48), f"expected (64, 48), got {img.size}"
    print("OK: test_open_image_heic_rgb_and_size")


def test_open_image_avif_rgb_and_size():
    with tempfile.TemporaryDirectory() as d:
        img = open_image(_make(d, "sample.avif", "AVIF", size=(64, 48)))
        assert img.mode == "RGB", f"expected RGB, got {img.mode}"
        assert img.size == (64, 48), f"expected (64, 48), got {img.size}"
    print("OK: test_open_image_avif_rgb_and_size")


def test_open_image_applies_exif_orientation():
    with tempfile.TemporaryDirectory() as d:
        # stored 64x48 landscape + Orientation=6 -> displayed portrait 48x64
        img = open_image(_make_jpeg_oriented(d, size=(64, 48), orientation=6))
        assert img.size == (48, 64), f"orientation not applied: {img.size}"
    print("OK: test_open_image_applies_exif_orientation")


def test_validate_accepts_heic_and_avif():
    # Local import: pulls in the FastAPI app (and mp spawn setup) at import time.
    from scripts.Main import _validate_image

    with tempfile.TemporaryDirectory() as d:
        _validate_image(_make(d, "sample.heic", "HEIF"))  # must not raise
        _validate_image(_make(d, "sample.avif", "AVIF"))  # must not raise
    print("OK: test_validate_accepts_heic_and_avif")


def test_validate_rejects_garbage():
    from scripts.Main import _validate_image

    with tempfile.TemporaryDirectory() as d:
        bad = Path(d) / "not_an_image.heic"
        bad.write_bytes(b"this is not an image")
        try:
            _validate_image(bad)
        except Exception:
            print("OK: test_validate_rejects_garbage")
            return
        raise AssertionError("garbage file was accepted by _validate_image")


if __name__ == "__main__":
    test_open_image_heic_rgb_and_size()
    test_open_image_avif_rgb_and_size()
    test_open_image_applies_exif_orientation()
    test_validate_accepts_heic_and_avif()
    test_validate_rejects_garbage()
    print("\nAll image-format tests passed.")

"""Page rasterization for figure inspection.

pypdfium2 renders a page to a bitmap; this module turns that bitmap into PNG
bytes. It encodes PNG itself (zlib plus a 13-byte header) because the runtime
dependency set has numpy but no imaging library, and it refuses a render that
would exceed the caller's pixel budget instead of quietly lowering the
resolution: a figure inspected at the wrong scale is a wrong observation.
"""

from __future__ import annotations

import math
import struct
import zlib

import numpy as np
import pypdfium2 as pdfium

from boardmodeler.documents.pdf import PdfDocument

__all__ = ["PageRenderError", "page_size_points", "render_page_png"]

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_PNG_GRAYSCALE = 0
_PNG_RGB = 2
_PNG_RGBA = 6
_PNG_COLOR_TYPES = {1: _PNG_GRAYSCALE, 3: _PNG_RGB, 4: _PNG_RGBA}
_PNG_BIT_DEPTH = 8
_ZLIB_LEVEL = 6


class PageRenderError(RuntimeError):
    """A page could not be rasterized; the message carries the observed values."""


def page_size_points(pdf: PdfDocument, pdf_page: int) -> tuple[float, float]:
    """Size of ``pdf_page`` in PDF points, as displayed (page rotation applied)."""
    _require_page(pdf, pdf_page)
    try:
        with pdfium.PdfDocument(str(pdf.path)) as document:
            page = document[pdf_page]
            try:
                width, height = page.get_size()
            finally:
                page.close()
    except pdfium.PdfiumError as exc:
        raise PageRenderError(f"could not measure page {pdf_page} of {pdf.path}: {exc}") from exc
    return (float(width), float(height))


def render_page_png(
    pdf: PdfDocument, pdf_page: int, *, scale: float = 2.0, max_pixels: int = 40_000_000
) -> bytes:
    """Rasterize one page to PNG bytes at ``scale`` pixels per PDF point.

    Raises ``ValueError`` naming the observed page size and the pixel count when
    the render would exceed ``max_pixels``: the caller asked for a resolution, so
    handing back a silently downscaled image would misrepresent what was read.
    """
    if scale <= 0:
        raise ValueError(f"scale must be > 0, got {scale}")
    if max_pixels < 1:
        raise ValueError(f"max_pixels must be >= 1, got {max_pixels}")
    _require_page(pdf, pdf_page)
    try:
        with pdfium.PdfDocument(str(pdf.path)) as document:
            page = document[pdf_page]
            try:
                return _render_page_png(page, pdf_page, scale=scale, max_pixels=max_pixels)
            finally:
                page.close()
    except pdfium.PdfiumError as exc:
        raise PageRenderError(f"could not render page {pdf_page} of {pdf.path}: {exc}") from exc


# --------------------------------------------------------------------------- #
# internals


def _require_page(pdf: PdfDocument, pdf_page: int) -> None:
    if pdf_page < 0 or pdf_page >= pdf.page_count:
        raise IndexError(f"page {pdf_page} is outside {pdf.path}, which has {pdf.page_count} pages")


def _render_page_png(page: object, pdf_page: int, *, scale: float, max_pixels: int) -> bytes:
    width_points, height_points = (float(value) for value in page.get_size())
    pixel_width = math.ceil(width_points * scale)
    pixel_height = math.ceil(height_points * scale)
    if pixel_width * pixel_height > max_pixels:
        raise ValueError(
            f"page {pdf_page} is {width_points:.1f}x{height_points:.1f} pt; at scale {scale} that "
            f"is {pixel_width}x{pixel_height} = {pixel_width * pixel_height} pixels, over the "
            f"max_pixels={max_pixels} limit - lower 'scale' instead of expecting a downscale"
        )
    # rev_byteorder gives RGB rather than pdfium's native BGR, so the PNG
    # contains the colours the page actually shows.
    bitmap = page.render(scale=scale, rev_byteorder=True)
    try:
        return _encode_png(bitmap.to_numpy())
    finally:
        bitmap.close()


def _encode_png(pixels: np.ndarray) -> bytes:
    """Encode an 8-bit grayscale/RGB/RGBA array as a non-interlaced PNG."""
    array = np.asarray(pixels)
    if array.dtype != np.uint8:
        raise PageRenderError(f"render produced {array.dtype} pixels, expected uint8")
    if array.ndim == 2:
        height, width = array.shape
        channels = 1
        array = array.reshape(height, width, 1)
    elif array.ndim == 3:
        height, width, channels = array.shape
    else:
        raise PageRenderError(f"render produced an unsupported pixel shape {array.shape}")
    color_type = _PNG_COLOR_TYPES.get(channels)
    if color_type is None:
        raise PageRenderError(f"render produced {channels} channels, which has no PNG colour type")
    # Each scanline is prefixed by a filter byte; 0 means "no filter".
    scanlines = np.zeros((height, 1 + width * channels), dtype=np.uint8)
    scanlines[:, 1:] = np.ascontiguousarray(array).reshape(height, width * channels)
    header = struct.pack(">IIBBBBB", width, height, _PNG_BIT_DEPTH, color_type, 0, 0, 0)
    return (
        _PNG_SIGNATURE
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(scanlines.tobytes(), _ZLIB_LEVEL))
        + _png_chunk(b"IEND", b"")
    )


def _png_chunk(tag: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + tag
        + payload
        + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
    )

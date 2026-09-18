"""LTspice schematic / symbol text parsing (D9).

``.asc`` parsing feeds layout cross-checks and pin-position resolution; it is
never the authoritative netlist (that is what :mod:`boardmodeler.schematic.netlist`
is for, applied to the ``.net`` LTspice emits).

The rotation transforms are the ones measured on LTspice 26.0.0.3 (D-006): a
symbol pin sits at ``anchor + rotate(local_pin, rotation)`` with ``R0`` identity,
``R90: (x, y) -> (-y, x)``, ``R180: (-x, -y)``, ``R270: (y, -x)``, and the mirror
prefixes (``M0``/``M90``/``M180``/``M270``) applying ``(-x, y)`` on top of the
matching rotation.  Only the four rotations are used for generated schematics;
mirrors are parsed but rejected by :func:`boardmodeler.schematic.ascgen.generate_asc`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "ROTATIONS",
    "AscFormatError",
    "AscSchematic",
    "AscSymbol",
    "AscText",
    "AscWindow",
    "AsyPin",
    "asy_attributes",
    "asy_pins",
    "is_rotation",
    "parse_asc",
    "pin_offsets",
    "pin_position",
    "read_asc",
    "read_text",
    "rotate_point",
]

#: Rotations written into a schematic.
ROTATIONS: tuple[str, ...] = ("R0", "R90", "R180", "R270")
#: Mirror prefixes; parsed but never generated.
MIRRORS: tuple[str, ...] = ("M0", "M90", "M180", "M270")
_ALL_ROTATIONS = (*ROTATIONS, *MIRRORS)

_ATOMS: dict[str, tuple[int, int, int, int]] = {
    # (m00, m01, m10, m11): x' = m00*x + m01*y, y' = m10*x + m11*y
    "R0": (1, 0, 0, 1),
    "R90": (0, -1, 1, 0),
    "R180": (-1, 0, 0, -1),
    "R270": (0, 1, -1, 0),
}

_PIN_RE = re.compile(r"^\s*PIN\s+(-?\d+)\s+(-?\d+)\s+(\S+)\s+(\d+)\s*$")
_PINATTR_RE = re.compile(r"^\s*PINATTR\s+(\S+)\s+(.*?)\s*$")
_SYMATTR_RE = re.compile(r"^\s*SYMATTR\s+(\S+)\s*(.*?)\s*$")


class AscFormatError(ValueError):
    """A recognized ``.asc``/``.asy`` line that cannot be parsed."""


def is_rotation(value: str) -> bool:
    """True for ``R0``/``R90``/``R180``/``R270`` and the mirror prefixes."""
    return value.upper() in _ALL_ROTATIONS


def rotate_point(local: tuple[int, int], rotation: str) -> tuple[int, int]:
    """Sheet-space offset of a symbol-local point for ``rotation``."""
    key = rotation.upper()
    if key not in _ALL_ROTATIONS:
        raise AscFormatError(
            f"unknown rotation {rotation!r}; expected one of {', '.join(_ALL_ROTATIONS)}"
        )
    mirrored = key.startswith("M")
    atom = "R" + key[1:] if mirrored else key
    m00, m01, m10, m11 = _ATOMS[atom]
    x = m00 * local[0] + m01 * local[1]
    y = m10 * local[0] + m11 * local[1]
    return (-x, y) if mirrored else (x, y)


def pin_position(anchor: tuple[int, int], local: tuple[int, int], rotation: str) -> tuple[int, int]:
    """Absolute sheet position of a pin: ``anchor + rotate(local, rotation)``."""
    dx, dy = rotate_point(local, rotation)
    return (anchor[0] + dx, anchor[1] + dy)


@dataclass(frozen=True)
class AsyPin:
    """One ``PIN`` / ``PINATTR`` pair from an ``.asy`` file."""

    name: str
    x: int
    y: int
    orientation: str = "NONE"
    size: int = 0
    order: int = 0

    @property
    def offset(self) -> tuple[int, int]:
        return (self.x, self.y)


def asy_pins(asy_text: str) -> dict[str, AsyPin]:
    """Every ``PIN`` in an ``.asy``, keyed by its ``PINATTR PinName``.

    A pin is only complete once its ``PinName``/``SpiceOrder`` pair is read, so
    the attributes are grouped per ``PIN`` line.  Name-less pins are keyed by
    position (``"1"``...), so a symbol never loses a pin to parsing.
    """
    pins: dict[str, AsyPin] = {}
    coords: tuple[int, int, str, int] | None = None
    name: str | None = None
    order: int | None = None
    unnamed = 0

    def flush() -> None:
        nonlocal coords, name, order, unnamed
        if coords is None:
            return
        x, y, orientation, size = coords
        if name:
            key = name
        else:
            unnamed += 1
            key = str(unnamed)
        pins.setdefault(
            key,
            AsyPin(name=key, x=x, y=y, orientation=orientation, size=size, order=order or 0),
        )
        coords, name, order = None, None, None

    for raw in asy_text.splitlines():
        pin_match = _PIN_RE.match(raw)
        if pin_match:
            flush()
            coords = (
                int(pin_match.group(1)),
                int(pin_match.group(2)),
                pin_match.group(3).upper(),
                int(pin_match.group(4)),
            )
            continue
        attr = _PINATTR_RE.match(raw)
        if attr is None or coords is None:
            continue
        key = attr.group(1).lower()
        value = attr.group(2).strip()
        if key == "pinname":
            name = value
        elif key == "spiceorder":
            try:
                order = int(value)
            except ValueError as error:
                raise AscFormatError(f"PINATTR SpiceOrder {value!r} is not an integer") from error
    flush()
    return pins


def pin_offsets(asy_text: str) -> dict[str, tuple[int, int]]:
    """Pin name -> symbol-local ``(x, y)`` for an ``.asy``."""
    return {name: pin.offset for name, pin in asy_pins(asy_text).items()}


def asy_attributes(asy_text: str) -> dict[str, str]:
    """``SYMATTR`` attributes of an ``.asy`` (``Prefix``, ``SpiceModel``, ...)."""
    attributes: dict[str, str] = {}
    for raw in asy_text.splitlines():
        match = _SYMATTR_RE.match(raw)
        if match:
            attributes[match.group(1)] = match.group(2)
    return attributes


@dataclass(frozen=True)
class AscSymbol:
    """One ``SYMBOL`` line with the ``SYMATTR`` lines that follow it."""

    name: str
    anchor: tuple[int, int]
    rotation: str
    attributes: dict[str, str] = field(default_factory=dict)
    index: int = 0

    @property
    def refdes(self) -> str:
        return self.attributes.get("InstName", "")


@dataclass(frozen=True)
class AscText:
    """One ``TEXT`` line (``!`` = SPICE directive, ``;`` = comment)."""

    x: int
    y: int
    text: str
    anchor: str = "Left"
    size: int = 2

    @property
    def is_directive(self) -> bool:
        return self.text.startswith("!")

    @property
    def directive(self) -> str:
        return self.text[1:].strip() if self.is_directive else ""


@dataclass(frozen=True)
class AscWindow:
    """One ``WINDOW`` line."""

    index: int
    x: int
    y: int
    anchor: str = "Left"
    size: int = 2


@dataclass
class AscSchematic:
    """A parsed ``.asc`` file."""

    version: int = 4
    sheet_index: int = 1
    sheet: tuple[int, int] = (880, 680)
    symbols: list[AscSymbol] = field(default_factory=list)
    wires: list[tuple[int, int, int, int]] = field(default_factory=list)
    flags: list[tuple[int, int, str]] = field(default_factory=list)
    texts: list[AscText] = field(default_factory=list)
    windows: list[AscWindow] = field(default_factory=list)
    #: Lines this parser does not model (kept so nothing is silently dropped).
    unparsed: list[str] = field(default_factory=list)
    source_path: Path | None = None

    @property
    def symbol_positions(self) -> dict[str, tuple[int, int, str]]:
        """refdes -> ``(x, y, rotation)`` for every symbol with an InstName."""
        positions: dict[str, tuple[int, int, str]] = {}
        for symbol in self.symbols:
            if symbol.refdes:
                positions[symbol.refdes] = (symbol.anchor[0], symbol.anchor[1], symbol.rotation)
        return positions

    def refdes(self) -> list[str]:
        """Instance names in file order."""
        return [symbol.refdes for symbol in self.symbols if symbol.refdes]

    def symbol_of(self, refdes: str) -> AscSymbol | None:
        for symbol in self.symbols:
            if symbol.refdes == refdes:
                return symbol
        return None

    def directives(self) -> list[str]:
        return [text.directive for text in self.texts if text.is_directive]

    def nets(self) -> list[str]:
        """Flag names in file order, ground (``0``) included."""
        return [name for _x, _y, name in self.flags]

    def pin_positions(
        self, refdes: str, offsets: Mapping[str, tuple[int, int]]
    ) -> dict[str, tuple[int, int]]:
        """Absolute pin positions of ``refdes`` given local ``offsets``."""
        symbol = self.symbol_of(refdes)
        if symbol is None:
            return {}
        return {
            name: pin_position(symbol.anchor, local, symbol.rotation)
            for name, local in offsets.items()
        }

    def symbol_pin_positions(self, symbol_dir: str | Path) -> dict[str, dict[str, tuple[int, int]]]:
        """refdes -> pin name -> absolute position, reading each ``.asy``."""
        directory = Path(symbol_dir)
        out: dict[str, dict[str, tuple[int, int]]] = {}
        for symbol in self.symbols:
            if not symbol.refdes:
                continue
            asy = directory / f"{symbol.name}.asy"
            if not asy.is_file():
                continue
            out[symbol.refdes] = self.pin_positions(symbol.refdes, pin_offsets(read_text(asy)))
        return out


# --------------------------------------------------------------------------- #
# parsing


def parse_asc(text: str) -> AscSchematic:
    """Parse ``.asc`` text (``SYMBOL``/``SYMATTR``/``WIRE``/``FLAG``/``TEXT``/``WINDOW``)."""
    schematic = AscSchematic()
    current: AscSymbol | None = None
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        tokens = line.split()
        keyword = tokens[0].lower()
        try:
            if keyword == "version":
                schematic.version = int(tokens[1] if len(tokens) > 1 else 0)
            elif keyword == "sheet":
                schematic.sheet_index = int(tokens[1])
                schematic.sheet = (int(tokens[2]), int(tokens[3]))
            elif keyword == "wire":
                schematic.wires.append(
                    (int(tokens[1]), int(tokens[2]), int(tokens[3]), int(tokens[4]))
                )
            elif keyword == "flag":
                schematic.flags.append(
                    (int(tokens[1]), int(tokens[2]), tokens[3] if len(tokens) > 3 else "")
                )
            elif keyword == "symbol":
                current = AscSymbol(
                    name=tokens[1],
                    anchor=(int(tokens[2]), int(tokens[3])),
                    rotation=tokens[4].upper() if len(tokens) > 4 else "R0",
                    index=len(schematic.symbols),
                )
                schematic.symbols.append(current)
            elif keyword == "symattr":
                if current is None:
                    raise AscFormatError(f"SYMATTR before any SYMBOL (line {number})")
                key = tokens[1]
                current.attributes[key] = line.split(None, 2)[2] if len(tokens) > 2 else ""
            elif keyword == "text":
                schematic.texts.append(
                    AscText(
                        x=int(tokens[1]),
                        y=int(tokens[2]),
                        anchor=tokens[3] if len(tokens) > 3 else "Left",
                        size=int(tokens[4]) if len(tokens) > 4 else 2,
                        text=line.split(None, 5)[5] if len(tokens) > 5 else "",
                    )
                )
            elif keyword == "window":
                schematic.windows.append(
                    AscWindow(
                        index=int(tokens[1]),
                        x=int(tokens[2]),
                        y=int(tokens[3]),
                        anchor=tokens[4] if len(tokens) > 4 else "Left",
                        size=int(tokens[5]) if len(tokens) > 5 else 2,
                    )
                )
            else:
                schematic.unparsed.append(line)
        except (IndexError, ValueError) as error:
            if isinstance(error, AscFormatError):
                raise
            raise AscFormatError(f"malformed {keyword.upper()} line {number}: {line!r}") from error
    return schematic


def read_asc(path: str | Path) -> AscSchematic:
    """Read and parse an ``.asc`` file."""
    target = Path(path)
    schematic = parse_asc(read_text(target))
    schematic.source_path = target
    return schematic


def read_text(path: str | Path) -> str:
    """Decode a text file the way LTspice writes them (UTF-8, UTF-16, latin-1)."""
    raw = Path(path).read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace")
    if len(raw) >= 2 and raw[1] == 0:
        return raw.decode("utf-16-le", errors="replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1", errors="replace")

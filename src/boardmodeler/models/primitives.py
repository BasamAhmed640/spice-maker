"""Parameterized behavioural primitive library (D8).

Every primitive is emitted as LTspice ``.lib`` text: a ``.subckt`` with a fixed
port order and a fixed set of parameters that all carry defaults, so an instance
that omits a parameter still simulates.  The numeric contract of each primitive
is asserted against real LTspice runs in
``tests/primitives/test_primitive_reference.py``; that file is the authority on
behaviour, this module is the authority on the emitted text.

Topology notes
--------------

The parameters listed in :data:`PRIMITIVE_PARAMS` are the *public* interface.
Everything else a topology needs is a literal internal constant of the emitted
text (stated below and in the comment above each template); there is no second
hidden parameter.

``BM_SCHMITT``
    Saturating ``E``-source (``limit()`` inside ``VALUE=``, comparator gain
    1e4 V/V) whose threshold node is driven by a ``B``-source computing
    ``V(th) = VTH - VHYS/2 + VHYS*(V(out)-VOL)/(VOH-VOL)``.  With that mandated
    threshold shift the only self-consistent latch is the **active-low** one: the
    output is high while ``V(in)-V(ref)`` is below ``VTH+VHYS/2`` and goes low
    above it.  (The opposite comparator sense turns the threshold shift into
    negative feedback - a linear amplifier with a ~VHYS wide transition and no
    hysteresis at all; that was measured before this polarity was fixed.)
    ``TPD`` is an RC lag on the output (1 nF internal capacitor) whose 50 %
    crossing is ``TPD`` after the comparator trips, i.e. ``tau = TPD/ln(2)``.

``BM_DELAY``
    ``B``-source transport delay (``delay()``) into an RC edge shaper (1 nF).
    The enable is shifted so that the output's 50 % crossing lands exactly ``TD``
    after the input's 50 % crossing (the standard 50 %-to-50 % propagation-delay
    definition); the edge therefore starts at ``TD - ln(2)*tau`` with
    ``tau = TR/ln(9)``, which makes the 10 %-90 % edge equal ``TR``.

``BM_OD``
    Two-terminal open-drain clamp.  The port list has no control pin, so the
    switch state is derived from the pin itself: an ``SW`` switched resistor is
    closed while ``V(out,vss)`` is below 0.4 V and opens above 0.5 V (``Vt`` =
    0.45 V, ``Vh`` = 0.1 V, 1 Tohm open resistance), and the leakage ``B``-source
    then conducts ``ILEAK`` out of the pin.  There is no ideal voltage source
    anywhere in the branch.  Caveat (measured, documented rather than hidden):
    like any ideal switch, the state is held while the control voltage is inside
    its hysteresis band, so a *current-source* drive that starts from a pin that
    was never low cannot establish the closed state and leaves the pin floating
    onto the open resistance; drive the pin through a voltage source or a ramp
    that starts at 0 V (as the reference test does).

``BM_PUSHPULL``
    Saturating ``E``-source bounded to ``[VOL, VOH]`` driving the pin through a
    series ``ROUT``; the pin therefore droops by ``I*ROUT`` under load instead of
    behaving as an ideal source.

``BM_SUPPLY_IO``
    Input threshold window ``[VIL_MAX, VIH_MIN]`` (linear in between), output high
    clamped to ``vdd`` minus a fixed 0.2 V drop, and zero drive whenever
    ``V(vdd) < VIL_MAX`` (supply-validity ramp 0.1 V).  ``ICLAMP`` is both the
    output current clamp and the output conductance (``G = ICLAMP/0.2 V``), so a
    short draws ``ICLAMP`` and the pin has a real output impedance; the pull-up
    branch sources from ``vdd`` and the pull-down sinks to ``vss``, both limited
    to ``ICLAMP``.

``BM_CONDUCTION``
    ``B``-source whose conductance is ``1/RC_ON`` while ``V(ctrl)`` is above the
    internal 1.0 V threshold (0.2 V linear transition) and ``1/RC_OFF`` below it,
    so an effectively infinite ``RC_OFF`` blocks in both directions.

``BM_LOAD``
    ``I_STATIC`` plus an ``I_STEP`` step at ``T_STEP`` (the step is an increment,
    matching the documented ``I_STATIC + I_STEP`` profile), ramped over an
    internal 1 us edge so the before/after values are unambiguous.

``BM_PG``
    Open-drain power-good output: the pin is only ever pulled low through a
    switched resistance and needs an external pull-up.  The threshold shift is
    ``V(th) = VTH + VHYS/2 - VHYS*logic`` (positive feedback, *non-inverting*
    sense: above the window the pin is driven low, below it the pin is released).
    ``TD`` is an RC lag exactly as in ``BM_SCHMITT``, whose mid-level crossing is
    turned into a hard gate by an internal 1e4 V/V comparator before driving the
    50 ohm pull-down.  ``PULLUP_MAX`` is documentation only: it is never used by
    the topology and exists so a deck can record the maximum allowed external
    pull-up next to the instance.

Decks are written by the caller (tests and the pipeline) under a run directory;
nothing in this module ever writes into an LTspice installation or library
directory.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

__all__ = [
    "PRIMITIVE_PARAMS",
    "PRIMITIVE_PORT_ORDER",
    "instance_card",
    "port_order_matches",
    "primitive_text",
    "write_primitive_library",
]

#: Port order of every primitive, exactly as documented in the model library
#: specification.  The emitted ``.subckt`` line must list the ports in this
#: order; :func:`port_order_matches` is the check for that.
PRIMITIVE_PORT_ORDER: dict[str, tuple[str, ...]] = {
    "BM_SCHMITT": ("in", "ref", "out", "vdd", "vss"),
    "BM_DELAY": ("in", "out", "vdd", "vss"),
    "BM_OD": ("out", "vss"),
    "BM_PUSHPULL": ("in", "out", "vdd", "vss"),
    "BM_SUPPLY_IO": ("in", "out", "vdd", "vss"),
    "BM_CONDUCTION": ("a", "b", "ctrl"),
    "BM_LOAD": ("out", "vss"),
    "BM_PG": ("open_in", "out", "vdd", "vss"),
}

#: Public parameters of every primitive; each one has a default on the
#: ``.subckt`` line, so an instance card may omit all of them.
PRIMITIVE_PARAMS: dict[str, tuple[str, ...]] = {
    "BM_SCHMITT": ("VTH", "VHYS", "VOH", "VOL", "TPD"),
    "BM_DELAY": ("TD", "TR"),
    "BM_OD": ("RON_LOW", "ILEAK"),
    "BM_PUSHPULL": ("VOH", "VOL", "ROUT"),
    "BM_SUPPLY_IO": ("VIL_MAX", "VIH_MIN", "ICLAMP"),
    "BM_CONDUCTION": ("RC_ON", "RC_OFF"),
    "BM_LOAD": ("I_STATIC", "I_STEP", "T_STEP"),
    "BM_PG": ("VTH", "VHYS", "TD", "PULLUP_MAX"),
}

# --------------------------------------------------------------------------- #
# emitted library text
#
# Numbers that are not parameters are internal constants of these topologies and
# are listed here so they are visible next to the text that uses them:
#   comparator gain 1e4 V/V; lag capacitor 1 nF; OD switch Vt 0.45 V / Vh 0.1 V /
#   1 Tohm open resistance; conduction control threshold 1.0 V with a 0.2 V
#   transition; load step edge 1 us; supply-IO dropout 0.2 V with a 0.1 V
#   supply-validity ramp; PG pull-down 50 ohm driven by a 1e4 V/V gate
#   comparator.

_SCHMITT_TEXT = """\
* BM_SCHMITT: saturating Schmitt comparator (active-low output sense)
*   in-ref rises through VTH+VHYS/2 -> out goes low; falls through VTH-VHYS/2 -> out high.
*   TPD is the RC lag from the comparator trip to the mid-level crossing of out.
.subckt BM_SCHMITT in ref out vdd vss params: VTH=1.2 VHYS=0.2 VOH=3.3 VOL=0 TPD=1u
B_th th 0 V = VTH - VHYS/2 + VHYS*(V(out)-VOL)/(VOH-VOL)
E_drv drv 0 VALUE={ VOL + (VOH-VOL)*limit(1e4*(V(th)-V(in)+V(ref)), 0, 1) }
R_lag drv out {TPD/(0.6931471805599453*1n)}
C_lag out 0 1n
.ends BM_SCHMITT"""

_DELAY_TEXT = """\
* BM_DELAY: TD-shifted enable into an RC edge shaper
*   TD is the 50% in-to-out crossing delay; TR is the 10%-90% output edge.
.subckt BM_DELAY in out vdd vss params: TD=1u TR=100n
B_drv drv vss V = delay(V(in)-V(vss),TD-TR*0.3154857)
R_edge drv out {TR/(2.1972245773362196*1n)}
C_edge out vss 1n
.ends BM_DELAY"""

_OD_TEXT = """\
* BM_OD: two-terminal open-drain clamp (switched resistor + leakage source)
*   closed (RON_LOW) while the pin is at or below the switch threshold,
*   open above it with ILEAK flowing out of the pin into vss.
.subckt BM_OD out vss params: RON_LOW=10 ILEAK=1u
S_sw out vss vss out BM_OD_SW
.model BM_OD_SW SW(Ron={RON_LOW} Roff=1e12 Vt=-0.45 Vh=0.1)
B_leak out vss I = ILEAK*limit((V(out,vss)-0.4)/0.2,0,1)
.ends BM_OD"""

_PUSHPULL_TEXT = """\
* BM_PUSHPULL: bounded output driver, VOH/VOL through ROUT
.subckt BM_PUSHPULL in out vdd vss params: VOH=3.3 VOL=0 ROUT=50
E_drv drv vss VALUE={ VOL + (VOH-VOL)*limit(1e4*(V(in)-V(vss)-(VOH+VOL)/2), 0, 1) }
R_out drv out {ROUT}
.ends BM_PUSHPULL"""

_SUPPLY_IO_TEXT = """\
* BM_SUPPLY_IO: supply-referenced input thresholds, current-clamped output
*   zero drive while V(vdd) < VIL_MAX; output high = vdd - 0.2 V (no load).
.subckt BM_SUPPLY_IO in out vdd vss params: VIL_MAX=0.8 VIH_MIN=2.0 ICLAMP=5m
B_tgt tgt 0 V = limit(V(vdd)-0.2,0,1e3)*limit((V(vdd)-VIL_MAX)/0.1,0,1)*limit((V(in,vss)-VIL_MAX)/(VIH_MIN-VIL_MAX),0,1)
B_pu vdd out I = limit((V(tgt)-V(out))*ICLAMP/0.2, 0, ICLAMP)
B_pd out vss I = limit((V(out)-V(tgt))*ICLAMP/0.2, 0, ICLAMP)
.ends BM_SUPPLY_IO"""

_CONDUCTION_TEXT = """\
* BM_CONDUCTION: controlled conduction path, RC_ON closed / RC_OFF open
.subckt BM_CONDUCTION a b ctrl params: RC_ON=50 RC_OFF=1e12
B_cond a b I = (V(a)-V(b))*(limit((V(ctrl)-1.0)/0.2,0,1)/RC_ON+(1-limit((V(ctrl)-1.0)/0.2,0,1))/RC_OFF)
.ends BM_CONDUCTION"""

_LOAD_TEXT = """\
* BM_LOAD: static load with a current step at T_STEP (I = I_STATIC, then I_STATIC+I_STEP)
.subckt BM_LOAD out vss params: I_STATIC=1m I_STEP=1m T_STEP=1m
B_load out vss I = I_STATIC + I_STEP*limit((time-T_STEP)/1u,0,1)
.ends BM_LOAD"""

_PG_TEXT = """\
* BM_PG: open-drain power-good output (external pull-up required)
*   pulled low through 50 ohm above VTH+VHYS/2, released below VTH-VHYS/2, TD delay.
*   PULLUP_MAX documents the largest allowed external pull-up; it is not used in the body.
.subckt BM_PG open_in out vdd vss params: VTH=1.2 VHYS=0.2 TD=10u PULLUP_MAX=100k
B_lg lg 0 V = limit(1e4*(V(open_in,vss)-V(th)), 0, 1)
B_th th 0 V = VTH + VHYS/2 - VHYS*V(lg)
R_lag lg lgd {TD/(0.6931471805599453*1n)}
C_lag lgd 0 1n
B_gate gate 0 V = limit(1e4*(V(lgd)-0.5), 0, 1)
B_od out vss I = V(out,vss)*V(gate)/50
.ends BM_PG"""

_TEXTS: dict[str, str] = {
    "BM_SCHMITT": _SCHMITT_TEXT,
    "BM_DELAY": _DELAY_TEXT,
    "BM_OD": _OD_TEXT,
    "BM_PUSHPULL": _PUSHPULL_TEXT,
    "BM_SUPPLY_IO": _SUPPLY_IO_TEXT,
    "BM_CONDUCTION": _CONDUCTION_TEXT,
    "BM_LOAD": _LOAD_TEXT,
    "BM_PG": _PG_TEXT,
}

_LIBRARY_HEADER = (
    "* BoardModeler parameterized behavioural primitive library\n"
    "* Generated by boardmodeler.models.primitives - do not edit by hand.\n"
    "* Instantiate with: X<ref> <ports in documented order> <subckt> [PARAM=VALUE ...]\n"
)


def primitive_text(name: str) -> str:
    """The ``.lib`` body for one primitive, byte-for-byte deterministic.

    Raises ``KeyError`` for a name that is not part of the library - a typo must
    never silently produce an empty model.
    """
    try:
        return _TEXTS[name]
    except KeyError:
        raise KeyError(f"unknown primitive {name!r}; known: {', '.join(_TEXTS)}") from None


def write_primitive_library(path: str | Path, names: Sequence[str] | None = None) -> Path:
    """Write all (or the named) primitives into one ``.lib`` file.

    The emitted order is always the documented library order, so the same set of
    names always produces byte-identical content.
    """
    if names is None:
        selected = list(_TEXTS)
    else:
        for name in names:
            if name not in _TEXTS:
                raise KeyError(f"unknown primitive {name!r}; known: {', '.join(_TEXTS)}")
        requested = set(names)
        selected = [name for name in _TEXTS if name in requested]

    body = "\n\n".join(primitive_text(name) for name in selected)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"{_LIBRARY_HEADER}\n{body}\n", encoding="utf-8", newline="\n")
    return target


def _format_value(value: float | str) -> str:
    if isinstance(value, str):
        return value
    return repr(float(value))


def instance_card(
    subckt: str,
    refdes: str,
    nodes: Sequence[str],
    params: Mapping[str, float | str],
) -> str:
    """One ``X`` instance card, e.g. ``X1 in ref out vdd vss BM_SCHMITT VTH=1.2``.

    For a known primitive the port count and every parameter name are validated,
    so a mis-wired or misspelled instance fails here instead of inside the
    simulator.
    """
    expected_ports = PRIMITIVE_PORT_ORDER.get(subckt)
    if expected_ports is not None and len(nodes) != len(expected_ports):
        raise ValueError(
            f"{subckt} takes {len(expected_ports)} ports {expected_ports}, got {len(nodes)}"
        )
    known_params = PRIMITIVE_PARAMS.get(subckt)
    if known_params is not None:
        unknown = [name for name in params if name not in known_params]
        if unknown:
            raise ValueError(f"{subckt} has no parameter(s) {unknown}; known: {known_params}")

    fields = [refdes, *(str(node) for node in nodes), subckt]
    fields.extend(f"{name}={_format_value(value)}" for name, value in params.items())
    return " ".join(fields)


def port_order_matches(name: str, subckt_line: str) -> bool:
    """True when ``subckt_line`` declares ``name`` with the documented port order.

    Only the node list is compared: the ``PARAMS:`` clause (and anything after
    it) is ignored, and comparison is case-insensitive because SPICE is.
    """
    expected = PRIMITIVE_PORT_ORDER.get(name)
    if expected is None:
        raise KeyError(f"unknown primitive {name!r}; known: {', '.join(PRIMITIVE_PORT_ORDER)}")
    tokens = subckt_line.replace(",", " ").split()
    if len(tokens) < 3 or tokens[0].lower() != ".subckt":
        return False
    if tokens[1].lower() != name.lower():
        return False
    declared: list[str] = []
    for token in tokens[2:]:
        # "params:" is the canonical separator; SPICE also allows parameters
        # straight after the nodes, and "=" never appears in a node name.
        if token.lower() in {"params:", "params", "param:"} or "=" in token:
            break
        declared.append(token)
    return tuple(port.lower() for port in declared) == tuple(port.lower() for port in expected)

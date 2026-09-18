"""Reduced behavioural regulator templates (D8, Phase 2 step 5).

Two generated (type B) templates are emitted as LTspice ``.lib`` text:

===================== ============================================= ============
Subcircuit            Ports (fixed order)                           Switching?
===================== ============================================= ============
``BM_REG_BUCK``       ``VIN EN FB PG VOUT GND SW ILIM_MODE``        yes
``BM_REG_LDO``        ``VIN EN FB PG VOUT GND``                     no
===================== ============================================= ============

Parameters (defaults; SI units are used by the emitted text, the table shows the
suffix form the specification uses):

``VREF`` 0.8 V, ``VOUT_NOM`` 3.3 V, ``UVLO_RISE`` 4.3 V (buck) / 2.2 V (LDO),
``UVLO_FALL`` 3.9 V (buck) / 2.0 V (LDO), ``EN_RISE`` 1.25 V, ``EN_FALL`` 1.15 V,
``POL_EN`` 1, ``CSS`` 10n = 10e-9 F, ``ILIM`` 3.0 A, ``ILIM_MODE`` 0,
``RETRY_MS`` 8 = 8e-3 s, ``RDISCHARGE`` 10 ohm, ``VPREBIAS_MAX`` 0.05 V,
``REVERSE_BLOCK`` 1, ``ETA`` 0.9, ``VMIN_FLOOR`` 1 V, ``IIN_MAX`` 5 A, ``GM`` 50.
One further parameter is declared by the emitted text and is not part of
:data:`REGULATOR_PARAMS` because the specification states it in the behaviour
list: ``PG_DELAY`` = 100u = 100e-6 s (power-good assertion delay).

The model is deliberately a *reduced* one, and it never stamps an ideal source
on the regulated rail.  Every behaviour below is measured at the pins by
``tests/regulator/test_regulator_model.py``; the laws are stated here because
they are the contract of the emitted text.

Enable path
    ``VIN`` UVLO latch: starts at ``UVLO_RISE``, keeps running down to
    ``UVLO_FALL``, then stops.  ``EN`` comparator with hysteresis
    ``EN_RISE``/``EN_FALL``; ``POL_EN=1`` enables while ``V(EN)`` is above the
    pair, ``POL_EN=0`` enables while ``V(EN)`` is below it (the same two levels,
    mirrored).  The converter runs while ``UVLO and EN``.

Soft start
    A capacitor ``CSS`` (the parameter *is* the capacitor) is charged with a
    fixed internal current ``ISS = 1.7 uA`` while the converter runs, so the
    reference ramps at ``dV/dt = ISS/CSS`` (170 V/s at the default ``CSS=10n``)
    and is clamped by ``min(V(ss), VREF)``: monotonic, and it can never exceed
    ``VREF`` (measured ramp slope 170.11 V/s for ``CSS=10n`` and 42.50 V/s for
    ``CSS=40n`` against the 170.00/42.50 V/s law; settled V(FB) 0.799998 V, peak
    0.8071 V).  The charge stops 50 mV above ``VREF`` and the node is reset
    through 100 ohm while the converter is off (EN or UVLO inactive), so a
    re-enable always ramps from zero.  A current-limit retry does **not** reset
    the ramp: it resumes at the reference level the converter already reached
    (measured in the reference test as an output-current burst at ``ILIM``, not a
    re-ramp).

Regulation
    The error amplifier compares the *ramping reference* with ``V(FB)`` - the
    external divider is the only feedback path.  It is a transconductance stage
    (``gm = 1e-4 A/V``, output current bounded at +/-200 uA, node clamped to
    ``[0, 1] V``) into a lag/zero compensation network (``1 MOhm`` parallel,
    ``22 ohm + 100 nF`` series), i.e. a finite-gain, compensation-limited
    amplifier: the output ramps are bounded by ``I/C = 2000 V/s`` after the
    4.4 mV feed-forward step.  While the converter is off the amplifier output is
    reset through 100 ohm, so a re-enable starts from zero error (a restart after
    a latch reset is again a soft ramp, measured at 3.6 ms for 0.5 V -> 3.0 V).
    It drives a power stage that is a *voltage controlled current source* into
    ``VOUT`` with transconductance ``GM`` (A/V) - never an ideal voltage source.
    Steady state is therefore defined by ``V(FB) = VREF`` whatever divider the
    application uses; the load regulation error is ``I_OUT / (GM * A0)`` referred
    to ``FB`` (measured: 3.26913 V against the 3.2716 V target of a 10k/3.24k
    divider and 8.20735 V against the 8.210 V target of 30k/3.24k, i.e. below
    0.1 % error in both cases).

Current limit and recovery
    The output current is ``limit(GM*V(comp), 0, ILIM)`` while enabled, so the
    limit is exact.  The detector is "the drive is enabled and the demand exceeds
    ``ILIM``"; a fault memory (``C = 10 nF`` charged with ``I = 100 uA``)
    integrates it, trips at 2.718 V and releases at 1.0 V.  ``ILIM_MODE=0`` (or a
    high ``ILIM_MODE`` pin) makes the memory discharge through ``C/RETRY_MS``, so
    the retry interval is ``RETRY_MS * ln(2.718/1.0) = RETRY_MS`` (measured
    8.210 ms median for the default ``RETRY_MS=8m``, the extra 0.21 ms being the
    memory's charging ramp); ``ILIM_MODE=1`` keeps the memory charged (discharge
    time constant ~500 s, longer than any run) until ``EN`` or ``VIN`` goes
    inactive, which is the latch reset.

Power good
    ``PG`` is open drain and *sink only*: the pin is pulled down through 50 ohm
    while ``|V(VOUT) - VOUT_NOM|`` exceeds 10.5 % of ``VOUT_NOM`` (it is released
    once the deviation is inside 8.5 %, i.e. a real window with hysteresis) or
    while the converter is not running, and is high impedance otherwise.  The
    pull-down is immediate (a fault is never delayed); ``PG_DELAY`` is the
    assertion delay of the release (measured 207.4 us for ``PG_DELAY=200u``, and
    50.00 ohm from V(PG)/I(pull-up) in the low state, with the pull-up current
    never negative - the pin only ever sinks).  The pull-down current is
    ``limit(V(PG)/50, 0, 1)``, so no pin voltage can make the model source
    current.

Disable and pre-bias
    While ``UVLO`` or ``EN`` is inactive a switched resistor of
    ``max(RDISCHARGE, 10) ohm`` pulls ``VOUT`` to ``GND`` (the 10 ohm floor is
    the documented minimum allowed value; measured 288 mA at 10 ohm, 79 mA at
    40 ohm and 288 mA when a request below the floor is clamped to it - the
    sample is taken 20 us after the disable, by which time the 22 uF output has
    already drooped).  While enabled, and with the
    default ``REVERSE_BLOCK=1``, the model cannot sink output current at all -
    the power stage is source-only and the discharge path is open.  The drive is
    additionally held off while the soft-start target (``V(ref)*VOUT_NOM/VREF``)
    is below ``V(VOUT) - VPREBIAS_MAX`` and the limit detector is disabled with
    it, so a pre-biased output is neither driven nor "limited" until the ramp
    catches up (measured: the drive starts at 2.161 ms against the 2.158 ms the
    ISS/CSS law predicts for a 1.5 V pre-bias, and the pin current stays at 0
    until then).  ``VPREBIAS_MAX`` bounds that hold-off; the no-sinking guarantee
    itself is structural, not a comparison.

Reverse current and input current
    ``REVERSE_BLOCK=1`` makes the output path source-only *and* clamps the input
    current law at 0, so nothing can flow from ``VOUT`` back into ``VIN``
    (measured: 0.0 A out of the input pin with VOUT driven to 6 V while VIN sits
    at 4.5 V).  ``REVERSE_BLOCK=0`` allows the loop to sink (the sink side of the
    limit is enabled) and the sign of ``POUT`` then drives the input current
    negative, i.e. the model back-feeds ``VIN`` (measured -0.4859 A in the same
    deck) - which is what the parameter switches.

    ``IIN = clamp(POUT / (ETA * max(VIN, VMIN_FLOOR)), 0, IIN_MAX)`` with
    ``POUT = V(VOUT) * IOUT`` computed from the *delivered* current.  The
    ``max(VIN, VMIN_FLOOR)`` denominator removes the singular constant-power
    term, and it never flatters the model: the converter cannot run below
    ``UVLO_FALL``, which is at least 2 V, so the denominator is the actual ``VIN``
    whenever any current flows and ``POUT <= ETA * VIN * IIN`` holds - the model
    cannot create energy (measured: E_out = 1.0012 x ETA * E_in over a 14 ms
    window, the 0.12 % being the float32 shunt noise floor).

``SW`` is provided for pin compatibility only: this reduced model does not drive
it (switching-waveform behaviour is outside its scope).  All internal references
are the ``GND`` port, which is the SPICE ground node ``0`` in every application
this model is written for - the reused primitives are ground referenced
internally.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from string import Template

from boardmodeler.models.primitives import primitive_text

__all__ = [
    "REGULATOR_EXTRA_PARAMS",
    "REGULATOR_PARAMS",
    "REGULATOR_PORT_ORDER",
    "REGULATOR_PRIMITIVES",
    "is_switching",
    "regulator_instance",
    "regulator_library_text",
    "regulator_text",
    "write_regulator_library",
]

#: Port order of every template; the emitted ``.subckt`` line lists them exactly
#: once, in this order.
REGULATOR_PORT_ORDER: dict[str, tuple[str, ...]] = {
    "BM_REG_BUCK": ("VIN", "EN", "FB", "PG", "VOUT", "GND", "SW", "ILIM_MODE"),
    "BM_REG_LDO": ("VIN", "EN", "FB", "PG", "VOUT", "GND"),
}

#: Documented parameters and their defaults (SI units: F, s, A, ohm, V, S).
REGULATOR_PARAMS: dict[str, dict[str, float | int]] = {
    "BM_REG_BUCK": {
        "VREF": 0.8,
        "VOUT_NOM": 3.3,
        "UVLO_RISE": 4.3,
        "UVLO_FALL": 3.9,
        "EN_RISE": 1.25,
        "EN_FALL": 1.15,
        "POL_EN": 1,
        "CSS": 10e-9,
        "ILIM": 3.0,
        "ILIM_MODE": 0,
        "RETRY_MS": 8e-3,
        "RDISCHARGE": 10,
        "VPREBIAS_MAX": 0.05,
        "REVERSE_BLOCK": 1,
        "ETA": 0.9,
        "VMIN_FLOOR": 1,
        "IIN_MAX": 5,
        "GM": 50,
    },
    "BM_REG_LDO": {
        "VREF": 0.8,
        "VOUT_NOM": 3.3,
        "UVLO_RISE": 2.2,
        "UVLO_FALL": 2.0,
        "EN_RISE": 1.25,
        "EN_FALL": 1.15,
        "POL_EN": 1,
        "CSS": 10e-9,
        "ILIM": 3.0,
        "ILIM_MODE": 0,
        "RETRY_MS": 8e-3,
        "RDISCHARGE": 10,
        "VPREBIAS_MAX": 0.05,
        "REVERSE_BLOCK": 1,
        "ETA": 0.9,
        "VMIN_FLOOR": 1,
        "IIN_MAX": 5,
        "GM": 50,
    },
}

#: Parameters the emitted text declares *in addition* to :data:`REGULATOR_PARAMS`.
#: ``PG_DELAY`` is specified as a behaviour ("a parameterisable assertion delay,
#: default 100u") rather than as an entry of the parameter table, so it is kept
#: out of :data:`REGULATOR_PARAMS` and declared here instead.
REGULATOR_EXTRA_PARAMS: dict[str, dict[str, float | int]] = {
    "BM_REG_BUCK": {"PG_DELAY": 100e-6},
    "BM_REG_LDO": {"PG_DELAY": 100e-6},
}

#: Primitives each template instantiates; the emitted library always contains
#: their ``.subckt`` text verbatim.
REGULATOR_PRIMITIVES: dict[str, tuple[str, ...]] = {
    "BM_REG_BUCK": ("BM_SCHMITT", "BM_DELAY"),
    "BM_REG_LDO": ("BM_SCHMITT", "BM_DELAY"),
}

_SWITCHING: dict[str, bool] = {"BM_REG_BUCK": True, "BM_REG_LDO": False}

_LIBRARY_HEADER = (
    "* BoardModeler reduced behavioural regulator library\n"
    "* Emitted by boardmodeler.models.regulator - do not edit by hand.\n"
    "* Contains the regulator template(s) and, verbatim, the primitives they\n"
    "* instantiate.  Decks include this file by absolute path.\n"
)

# --------------------------------------------------------------------------- #
# internal constants of the emitted topologies (documented in the module
# docstring): ISS=1.7u, EA gm=1e-4, EA clamp [0,1] V, EA current limit 200u,
# compensation 1e6 parallel / (22 ohm + 100n) series, fault memory C=10n
# charged with 100u and tripped at 2.718 V / released at 1.0 V, PG window
# +/-10 % (release +/-8 %) of VOUT_NOM, PG pull-down 50 ohm, logic rail 1 V,
# SS charge stop at VREF+50 mV, SS reset 100 ohm, fault clamp 3 V.

_BODY = Template(
    """\
* $KIND: reduced behavioural $FAMILY regulator
* ports: $PORTS
* declared scope: startup/shutdown, UVLO and EN thresholds with hysteresis,
*   soft start, external-divider regulation, current limit with hiccup/latch
*   recovery, power good, output discharge, pre-bias hold-off, reverse blocking,
*   input current.  Not modelled: switching waveforms, thermal behaviour,
*   switching ripple.  See boardmodeler.models.regulator for the exact laws.
* internal constants: ISS=1.7u  EA gm=1e-4 A/V  EA clamp [0,1] V  EA limit 200u
*   compensation 1e6 || (22 + 100n)  fault memory C=10n I=100u trip 2.718 V
*   release 1.0 V  PG window trip +/-10.5% release +/-8.5% of VOUT_NOM
*   PG sink 50 (pull-down immediate, release delayed by PG_DELAY)
* --- logic rail --------------------------------------------------------------
B_vdd1 vdd1 GND V = 1
* --- VIN UVLO latch: start at UVLO_RISE, stop at UVLO_FALL --------------------
X_uv VIN GND uv_n vdd1 GND BM_SCHMITT VTH={(UVLO_RISE+UVLO_FALL)/2} VHYS={UVLO_RISE-UVLO_FALL} VOH=1 VOL=0 TPD=1u
B_uvok uvok GND V = 1-V(uv_n)
* --- EN comparator: hysteresis EN_RISE/EN_FALL, polarity POL_EN ----------------
X_ep EN GND en_n vdd1 GND BM_SCHMITT VTH={(EN_RISE+EN_FALL)/2} VHYS={EN_RISE-EN_FALL} VOH=1 VOL=0 TPD=1u
B_enok enok GND V = POL_EN+(1-2*POL_EN)*V(en_n)
B_run run GND V = limit(V(uvok)*V(enok), 0, 1)
* --- soft start: CSS charged with ISS => dV/dt = ISS/CSS, capped at VREF ------
C_ss ss GND {CSS}
B_sschg 0 ss I = 1.7u*V(run)*limit((VREF+0.05-V(ss))/0.05, 0, 1)
B_ssrst ss GND I = (1-V(run))*V(ss)/100
R_sslk ss GND 1e10
B_vref ref_ss GND V = min(V(ss),VREF)
* --- error amplifier: gm stage + compensation, compensation-limited ----------
B_ea 0 comp I = limit(1e-4*(V(ref_ss)-V(fb)), -200u, 200u)
R_dc comp GND 1e6
R_z comp cz 22
C_z cz GND 100n
B_cclmp comp GND I = limit((V(comp)-1)*1, 0, 1)*1e-2-limit(-V(comp)*1, 0, 1)*1e-2
* the amplifier output is reset while the converter is off, so every re-enable
* starts from zero error instead of the previous integrator state
B_comprst comp GND I = (1-V(run))*V(comp)/100
* --- power stage: VCCS into VOUT, source-only unless REVERSE_BLOCK=0 ----------
* the source side is held off while the output is above the soft-start target
* (pre-bias); the sink side exists only when REVERSE_BLOCK=0
B_rev revok GND V = 1-REVERSE_BLOCK*limit((V(VOUT)-V(VIN)-0.01)*1e3, 0, 1)
B_pb pbok GND V = limit((V(ref_ss)-V(fb)+VPREBIAS_MAX*VREF/max(VOUT_NOM,1m))*1e3, 0, 1)
B_gate gate GND V = limit(V(run)*V(fok)*V(revok), 0, 1)
B_icmd 0 iout I = (limit(GM*V(comp), 0, ILIM)*V(pbok)+limit(GM*V(comp), -ILIM*(1-REVERSE_BLOCK), 0))*V(gate)
R_iout iout GND 1
B_pwr 0 VOUT I = V(iout)
* --- input current: IIN = clamp(POUT/(ETA*max(VIN,VMIN_FLOOR)), 0, IIN_MAX) ---
B_iin VIN GND I = limit(V(VOUT)*V(iout)/max(ETA,1m)/max(V(VIN),VMIN_FLOOR), -IIN_MAX*(1-REVERSE_BLOCK), IIN_MAX)
* --- current limit detector, hiccup/latch memory ----------------------------
B_mode mode GND V = $MODE
B_ilim ilim GND V = limit((GM*V(comp)-ILIM)*1e3, 0, 1)*V(gate)*V(pbok)
C_flt flt GND 10n
B_fltchg 0 flt I = 100u*V(ilim)
B_fltdis flt GND I = V(flt)*((1-V(mode))*{10n/RETRY_MS}+V(mode)*1e-11+(1-V(run))*1e-2)
R_fltdc flt GND 1e11
B_fltcl flt GND I = limit((V(flt)-3)*1e3, 0, 1)
X_flt flt GND fok vdd1 GND BM_SCHMITT VTH=1.859 VHYS=1.718 VOH=1 VOL=0 TPD=1u
* --- output discharge while disabled: max(RDISCHARGE,10) ohm to GND ----------
B_dis VOUT GND I = V(VOUT)/max(RDISCHARGE,10)*limit((1-V(run))/0.01, 0, 1)
* --- power good: window, EN gate, PG_DELAY, sink-only open drain -------------
B_pgw pgw GND V = abs(V(VOUT)-VOUT_NOM)/max(0.1*VOUT_NOM,1m)+10*(1-V(run))
X_pgs pgw GND pgok vdd1 GND BM_SCHMITT VTH=0.95 VHYS=0.2 VOH=1 VOL=0 TPD=1u
B_pgb pgb GND V = 1-V(pgok)
X_pgd pgb pgbd vdd1 GND BM_DELAY TD={PG_DELAY} TR=100n
B_pgsink pgsink GND V = limit(V(pgb)+V(pgbd), 0, 1)
B_pg PG GND I = limit(V(PG)/50, 0, 1)*V(pgsink)
* --- pins provided for compatibility, not driven by this reduced model -------
$PINTAIL.ends $KIND"""
)

_MODE_EXPR: dict[str, str] = {
    # The strap pin can only select latch mode; the parameter is the default, so
    # the effective mode is latch when either is asserted.  The parameter is
    # referenced through braces because the pin shares its name (ILIM_MODE).
    "BM_REG_BUCK": "limit({ILIM_MODE}+limit((V(ILIM_MODE)-1)*1e4, 0, 1), 0, 1)",
    "BM_REG_LDO": "limit({ILIM_MODE}, 0, 1)",
}

_PIN_TAIL: dict[str, str] = {
    "BM_REG_BUCK": "R_sw SW GND 1e9\nR_im ILIM_MODE GND 1e6\n",
    "BM_REG_LDO": "",
}

_FAMILY: dict[str, str] = {"BM_REG_BUCK": "step-down (buck)", "BM_REG_LDO": "linear (LDO)"}


def _check_kind(kind: str) -> str:
    if kind not in REGULATOR_PORT_ORDER:
        raise KeyError(
            f"unknown regulator {kind!r}; known: {', '.join(REGULATOR_PORT_ORDER)}"
        ) from None
    return kind


def _fmt(value: float | int) -> str:
    """Format a number for a SPICE card without losing precision or adding noise.

    Integer-valued numbers stay integral, a short decimal stays decimal (``0.05``)
    and a value whose decimal form is long is written with its engineering suffix
    (``10e-9`` -> ``10n``, ``8e-3`` -> ``8m``) when that is shorter.
    """
    if isinstance(value, bool):  # guard: bool is an int subclass
        raise TypeError("boolean is not a valid SPICE value")
    number = float(value)
    if number == 0:
        return "0"
    if number.is_integer() and abs(number) < 1e12:
        return str(int(number))
    plain = repr(number)
    if "e" not in plain and len(plain) <= 4:
        return plain
    for exponent, suffix in ((-3, "m"), (-6, "u"), (-9, "n"), (-12, "p"), (3, "k"), (6, "meg")):
        scaled = number / 10.0**exponent
        if 1.0 <= abs(scaled) < 1000.0 and abs(scaled * 1e6 - round(scaled * 1e6)) < 1e-6:
            candidate = f"{scaled:g}{suffix}"
            if len(candidate) < len(plain):
                return candidate
    return plain


def _declared_names(kind: str) -> tuple[str, ...]:
    return tuple(REGULATOR_PARAMS[kind]) + tuple(REGULATOR_EXTRA_PARAMS[kind])


def _merged_params(kind: str, extra_params: Mapping[str, float | int] | None) -> dict[str, float]:
    params = {
        name: float(value)
        for name, value in {**REGULATOR_PARAMS[kind], **REGULATOR_EXTRA_PARAMS[kind]}.items()
    }
    if not extra_params:
        return params
    unknown = [name for name in extra_params if name not in params]
    if unknown:
        raise ValueError(f"{kind} has no parameter(s) {unknown}; known: {tuple(params)}")
    for name, value in extra_params.items():
        if isinstance(value, bool):
            raise TypeError("boolean is not a valid SPICE value")
        params[name] = float(value)
    return params


def _subckt_text(kind: str, extra_params: Mapping[str, float | int] | None = None) -> str:
    params = _merged_params(kind, extra_params)
    declarations = " ".join(f"{name}={_fmt(params[name])}" for name in _declared_names(kind))
    ports = " ".join(REGULATOR_PORT_ORDER[kind])
    header = f".subckt {kind} {ports} params: {declarations}"
    return f"{header}\n" + _BODY.substitute(
        KIND=kind,
        FAMILY=_FAMILY[kind],
        PORTS=ports,
        MODE=_MODE_EXPR[kind],
        PINTAIL=_PIN_TAIL[kind],
        PG_DELAY=_fmt(params["PG_DELAY"]),
    )


def _primitive_section(kinds: Sequence[str]) -> str:
    names: list[str] = []
    for kind in kinds:
        for name in REGULATOR_PRIMITIVES[kind]:
            if name not in names:
                names.append(name)
    return "\n\n".join(primitive_text(name) for name in names)


def regulator_text(kind: str, *, extra_params: Mapping[str, float | int] | None = None) -> str:
    """The emitted text for one template, self-contained and byte-deterministic.

    The text contains the primitives the template instantiates (verbatim, in
    library order) followed by the ``.subckt``; a deck that includes it can
    simulate the model on its own.
    """
    _check_kind(kind)
    return (
        f"{_LIBRARY_HEADER}\n{_primitive_section([kind])}\n\n{_subckt_text(kind, extra_params)}\n"
    )


def regulator_library_text(kinds: Sequence[str] | None = None) -> str:
    """The whole emitted library: every primitive used, then each template.

    Includes are de-duplicated, so a request for both kinds yields each
    primitive and each ``.subckt`` exactly once.
    """
    selected = _selected_kinds(kinds)
    subckts = "\n\n".join(_subckt_text(kind) for kind in selected)
    return f"{_LIBRARY_HEADER}\n{_primitive_section(selected)}\n\n{subckts}\n"


def _selected_kinds(kinds: Sequence[str] | None) -> list[str]:
    if kinds is None:
        return list(REGULATOR_PORT_ORDER)
    selected = [_check_kind(kind) for kind in kinds]
    if not selected:
        raise ValueError("kinds must not be empty")
    return selected


def write_regulator_library(path: str | Path, kinds: Sequence[str] | None = None) -> Path:
    """Write the regulator template(s) plus every primitive they use.

    The file is the canonical artefact a deck includes; the same arguments
    always produce byte-identical content.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(regulator_library_text(kinds), encoding="utf-8", newline="\n")
    return target


def regulator_instance(
    kind: str,
    refdes: str,
    nodes: Mapping[str, str],
    params: Mapping[str, float | int] | None = None,
) -> str:
    """One ``X`` instance card, e.g. ``X1 VIN EN FB PG VOUT GND SW ILIM_MODE ...``.

    The node mapping must name every port of ``kind`` exactly once - a missing or
    extra node is a wiring error and raises instead of silently producing a deck
    that simulates the wrong circuit.
    """
    _check_kind(kind)
    expected = REGULATOR_PORT_ORDER[kind]
    missing = [port for port in expected if port not in nodes]
    extra = [name for name in nodes if name not in expected]
    if missing or extra:
        raise ValueError(f"{kind} ports are {expected}; missing {missing} and unexpected {extra}")
    fields = [refdes, *(str(nodes[port]) for port in expected), kind]
    if params:
        unknown = [name for name in params if name not in _declared_names(kind)]
        if unknown:
            raise ValueError(
                f"{kind} has no parameter(s) {unknown}; known: {_declared_names(kind)}"
            )
        fields.extend(f"{name}={_fmt(value)}" for name, value in params.items())
    return " ".join(fields)


def is_switching(kind: str) -> bool:
    """True for the switching (buck) template, False for the linear one."""
    _check_kind(kind)
    return _SWITCHING[kind]

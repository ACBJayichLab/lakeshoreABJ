"""Lake Shore 33x cryogenic temperature controllers (335, 336).

One driver, a capability table per model.  The command set is common across the
family -- ``KRDG?``, ``SETP``, ``RANGE``, ``HTR?``, ``PID`` -- and what differs
is how many inputs, loops and heaters a box has, which is data, not code.

The transport underneath is whatever the config chose: pyvisa for a box on
GPIB, :class:`~lschart.transport.LakeshoreTransport` for one on a COM port or
Ethernet.  Nothing in here knows which.

Writing
-------

These boxes run their own PID loop.  This software's job is to hand it a
setpoint and to say what it is allowed to do about it, which makes three
commands the whole control surface::

    SETP <loop>,<kelvin>     where to go
    RANGE <output>,<n>       how much power it may use -- 0 turns it OFF
    PID <loop>,<P>,<I>,<D>   the loop's own gains

**A setpoint does nothing while the range is OFF.**  Raising the range is the
act that applies power to a heater, so it is never done implicitly: no method
here turns a heater on as a side effect of setting a temperature, and
``allow_writes`` gates every one of them.  That mirrors the rule the software
loop obeys on the LTSPM3 cryostat -- nothing raises a heater except an operator
asking it to, in so many words.

``max_setpoint_k`` is a second, blunter guard: a typo that asks a cryostat for
3000 K should be refused by the software rather than politely forwarded.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from ..model import Reading
from ..transport import Transport, TransportError
from .base import Instrument, InstrumentError, parse_float, parse_float_list

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ModelCaps:
    """What a given box physically has.

    ``heater_outputs`` are the ones with a power range and an ``HTR?`` reading;
    ``analog_outputs`` are voltage-only and have neither.  On a 336 outputs 3
    and 4 are analog; on a 335 there are none.
    """

    inputs: tuple[str, ...]
    loops: tuple[int, ...]
    heater_outputs: tuple[int, ...]
    analog_outputs: tuple[int, ...] = ()


CAPS: dict[str, ModelCaps] = {
    "335": ModelCaps(inputs=("A", "B"), loops=(1, 2), heater_outputs=(1, 2)),
    "336": ModelCaps(
        inputs=("A", "B", "C", "D"), loops=(1, 2, 3, 4),
        heater_outputs=(1, 2), analog_outputs=(3, 4),
    ),
}

#: ``RANGE`` values for a current-mode heater.  A 335's output 2 in *voltage*
#: mode takes 0/1 instead, which is why :meth:`LS33x.set_heater_range` accepts
#: a plain int and only range-checks it against this when the box says the
#: output is in current mode.
HEATER_RANGE_NAMES = {0: "off", 1: "low", 2: "medium", 3: "high"}


def _close_enough(got, expected, tol: float) -> bool:
    """Compare a readback with what was commanded, elementwise for tuples."""
    if isinstance(expected, tuple):
        return (
            isinstance(got, tuple) and len(got) == len(expected)
            and all(_close_enough(g, e, tol) for g, e in zip(got, expected))
        )
    if isinstance(expected, bool):
        return bool(got) == expected
    return abs(float(got) - float(expected)) <= tol


class LS33x(Instrument):
    """A 33x-family controller.  Read-only until ``allow_writes`` is set."""

    def __init__(
        self,
        transport: Transport,
        *,
        model: str = "336",
        name: str | None = None,
        channels: dict[str, str] | None = None,
        read_status: bool = True,
        read_setpoints: bool = True,
        read_heaters: bool = True,
        read_analog_outputs: bool = False,
        allow_writes: bool = False,
        max_setpoint_k: float = 350.0,
        verify_writes: bool = True,
    ) -> None:
        model = str(model)
        if model not in CAPS:
            raise ValueError(f"unsupported model {model!r}; known: {sorted(CAPS)}")
        super().__init__(transport, name=name or f"ls{model}")
        self.model = model
        self.caps = CAPS[model]
        # {input letter: display name}.  Defaults to the instrument's own labels.
        self.channels = dict(channels) if channels else {}
        self.read_status = read_status
        self.read_setpoints = read_setpoints
        self.read_heaters = read_heaters
        self.read_analog_outputs = read_analog_outputs
        self.allow_writes = allow_writes
        self.max_setpoint_k = max_setpoint_k
        self.verify_writes = verify_writes

    # -- identity ---------------------------------------------------------

    def verify_model(self) -> str:
        """Check the box is the model the config claims.

        A 335 config pointed at a 336 reads four inputs as two and writes
        setpoints to loops that mean something different.  Cheap to check once
        at startup, expensive to discover later.
        """
        idn = self.idn()
        parts = [p.strip() for p in idn.split(",")]
        reported = parts[1].upper().replace("MODEL", "") if len(parts) > 1 else ""
        if reported and reported != self.model:
            raise TransportError(
                f"{self.name}: config says model {self.model} but the instrument "
                f"reports {reported!r} (*IDN? = {idn!r})"
            )
        return idn

    def discover_channel_names(self) -> dict[str, str]:
        """Pull the operator-assigned labels ("RAD SHIELD", "Sample", ...).

        Returns ``{}`` if *nothing* came back, so the caller can decline to
        cache it.  Discovery happens on the first frame, which is exactly when
        a link may still be coming up -- and caching "Input A" from a failed
        query would mislabel every column for the rest of the run, in a CSV
        that might not be looked at for a month.
        """
        names: dict[str, str] = {}
        answered = False
        for letter in self.caps.inputs:
            try:
                label = self.transport.query(f"INNAME? {letter}").strip()
                answered = True
            except TransportError as exc:
                log.warning("%s: INNAME? %s failed: %s", self.name, letter, exc)
                label = ""
            names[letter] = label or f"Input {letter}"
        return names if answered else {}

    # -- reading ----------------------------------------------------------

    def read_frame(self) -> tuple[dict[str, Reading], dict[str, float]]:
        if not self.channels:
            # Empty means discovery failed outright; try again next frame
            # rather than committing to placeholder labels.
            self.channels = self.discover_channel_names()

        kelvin = parse_float_list(self.transport.query("KRDG? 0"))
        readings: dict[str, Reading] = {}
        for idx, letter in enumerate(self.caps.inputs):
            if letter not in self.channels or idx >= len(kelvin):
                continue
            label = self.channels[letter]
            k = kelvin[idx]
            status = self._status(letter) if self.read_status else self._status_ok()
            readings[label] = Reading(
                channel=label,
                kelvin=k,
                status=status,
                validity=self._classify(k, status),
            )

        aux: dict[str, float] = {}
        if self.read_setpoints:
            for loop in self.caps.loops:
                self._try_aux(aux, f"{self.name}.setpoint{loop}", f"SETP? {loop}")
        if self.read_heaters:
            for out in self.caps.heater_outputs:
                self._try_aux(aux, f"{self.name}.heater{out}", f"HTR? {out}")
                self._try_aux(aux, f"{self.name}.range{out}", f"RANGE? {out}")
        if self.read_analog_outputs:
            for out in self.caps.analog_outputs:
                self._try_aux(aux, f"{self.name}.aout{out}", f"AOUT? {out}")
        return readings, aux

    def _try_aux(self, aux: dict[str, float], key: str, query: str) -> None:
        """Auxiliaries are nice-to-have; one that errors must not kill the frame."""
        try:
            aux[key] = parse_float(self.transport.query(query))
        except (TransportError, ValueError) as exc:
            log.debug("%s: %s failed: %s", self.name, query, exc)

    def aux_keys(self) -> list[str]:
        """The aux columns this instrument will produce, in a stable order.

        The recorder needs the header before the first frame arrives, so this
        has to be derivable from configuration alone.
        """
        keys: list[str] = []
        if self.read_setpoints:
            keys += [f"{self.name}.setpoint{i}" for i in self.caps.loops]
        if self.read_heaters:
            for out in self.caps.heater_outputs:
                keys += [f"{self.name}.heater{out}", f"{self.name}.range{out}"]
        if self.read_analog_outputs:
            keys += [f"{self.name}.aout{i}" for i in self.caps.analog_outputs]
        return keys

    def transactions_per_frame(self) -> int:
        """Bus transactions one :meth:`read_frame` costs, for the poll budget."""
        n = 1                                            # KRDG? 0
        if self.read_status:
            n += len(self.channels or self.caps.inputs)  # RDGST? per input
        if self.read_setpoints:
            n += len(self.caps.loops)
        if self.read_heaters:
            n += 2 * len(self.caps.heater_outputs)       # HTR? and RANGE?
        if self.read_analog_outputs:
            n += len(self.caps.analog_outputs)
        return n

    # -- writing (guarded) -------------------------------------------------

    def _require_writes(self, what: str) -> None:
        if not self.allow_writes:
            raise PermissionError(
                f"{self.name} is configured read-only; set allow_writes: true "
                f"on this instrument to {what}"
            )

    def _write_verified(self, cmd: str, read_back, expected, *, what: str,
                        tol: float = 1e-3, attempts: int = 5,
                        pause_s: float = 0.1):
        """Send ``cmd``, then confirm the instrument actually took it.

        These boxes apply a command asynchronously.  Measured on a 336 over
        USB: query immediately after a write and you get the PREVIOUS value;
        query 50 ms after and readbacks lag by exactly one write.  Both look
        like success while reporting fiction, which for a setpoint means
        printing a confident confirmation of a value the instrument is not
        holding.

        The transport's ``write_settle_s`` makes that unlikely; this makes it
        *detectable*.  A threshold tuned on one box over one link is not
        something to stake a cryostat on, so the write is confirmed by reading
        it back, and a mismatch raises rather than being reported as success.
        """
        self.transport.write(cmd)
        if not self.verify_writes:
            return None
        got = None
        for attempt in range(attempts):
            try:
                got = read_back()
            except (TransportError, ValueError) as exc:
                log.debug("%s: readback of %s failed (attempt %d): %s",
                          self.name, what, attempt + 1, exc)
                got = None
            if got is not None and _close_enough(got, expected, tol):
                return got
            time.sleep(pause_s)
        raise InstrumentError(
            f"{self.name}: {what} did not take. Commanded {expected!r} with "
            f"{cmd!r}; the instrument still reports {got!r} after "
            f"{attempts} readbacks. The write was NOT applied -- do not assume "
            f"the instrument is in the state you asked for."
        )

    def _check_loop(self, loop: int) -> None:
        if loop not in self.caps.loops:
            raise ValueError(
                f"{self.name} (model {self.model}) has no loop {loop}; "
                f"valid loops are {list(self.caps.loops)}"
            )

    def set_setpoint(self, loop: int, kelvin: float) -> None:
        """Tell the instrument's own PID loop where to go.

        Does *not* turn a heater on: if the output's range is off this changes
        the display and nothing else.  That is deliberate -- see the module
        docstring.
        """
        self._require_writes("change loop setpoints")
        self._check_loop(loop)
        if not 0.0 <= kelvin <= self.max_setpoint_k:
            raise ValueError(
                f"setpoint {kelvin} K is outside [0, {self.max_setpoint_k}] "
                f"for {self.name}; raise max_setpoint_k if this is intended"
            )
        self._write_verified(
            f"SETP {loop},{kelvin:.4f}",
            lambda: self.setpoint(loop), kelvin,
            what=f"setpoint on loop {loop}",
        )
        log.warning("%s: SETP %d,%.4f (verified)", self.name, loop, kelvin)

    def setpoint(self, loop: int) -> float:
        self._check_loop(loop)
        return parse_float(self.transport.query(f"SETP? {loop}"))

    def set_heater_range(self, output: int, value: int) -> None:
        """Set how much power the loop may use.  ``0`` turns the heater OFF.

        This is the command that actually applies power, so it is always an
        explicit act.  Raising a range is logged at WARNING with the old value
        alongside the new one, because "who turned the heater on" is the first
        question asked after a surprise.
        """
        self._require_writes("change heater ranges")
        if output not in self.caps.heater_outputs:
            raise ValueError(
                f"{self.name} (model {self.model}) has no heater output {output}; "
                f"valid outputs are {list(self.caps.heater_outputs)}"
            )
        if not 0 <= int(value) <= 3:
            raise ValueError(f"heater range must be 0..3, got {value!r}")
        try:
            previous = self.heater_range(output)
        except (TransportError, ValueError):
            previous = None
        self._write_verified(
            f"RANGE {output},{int(value)}",
            lambda: self.heater_range(output), int(value),
            what=f"heater range on output {output}",
        )
        log.warning(
            "%s: RANGE %d,%d (%s -> %s, verified)", self.name, output, int(value),
            HEATER_RANGE_NAMES.get(previous, previous),
            HEATER_RANGE_NAMES.get(int(value), value),
        )

    def heater_range(self, output: int) -> int:
        return int(parse_float(self.transport.query(f"RANGE? {output}")))

    def heater_output(self, output: int) -> float:
        """Present heater output in percent of the selected range."""
        return parse_float(self.transport.query(f"HTR? {output}"))

    def all_heaters_off(self) -> None:
        """Panic button.  Ranges to zero on every heater output."""
        self._require_writes("turn heaters off")
        for out in self.caps.heater_outputs:
            self._write_verified(
                f"RANGE {out},0", lambda o=out: self.heater_range(o), 0,
                what=f"heater range on output {out}",
            )
        log.warning("%s: all heaters OFF (verified)", self.name)

    def pid(self, loop: int) -> tuple[float, float, float]:
        self._check_loop(loop)
        p, i, d = parse_float_list(self.transport.query(f"PID? {loop}"))
        return p, i, d

    def set_pid(self, loop: int, p: float, i: float, d: float) -> None:
        """The instrument's own gains -- nothing to do with the software loop."""
        self._require_writes("change loop PID gains")
        self._check_loop(loop)
        self._write_verified(
            f"PID {loop},{p:.1f},{i:.1f},{d:.1f}",
            lambda: self.pid(loop), (p, i, d),
            what=f"PID gains on loop {loop}", tol=0.05,
        )
        log.warning("%s: PID %d,%.1f,%.1f,%.1f (verified)", self.name, loop, p, i, d)

    def set_ramp(self, loop: int, rate_k_per_min: float, *, enable: bool = True) -> None:
        """Use the *instrument's* setpoint ramp.

        The 33x boxes ramp a setpoint in firmware, which is better than doing it
        in software over a bus that can drop: the ramp continues if this program
        stops.  ``rate_k_per_min`` of 0 with ``enable`` means "as fast as
        possible" to the instrument, so it is rejected here as almost certainly
        a mistake.
        """
        self._require_writes("change the setpoint ramp")
        self._check_loop(loop)
        if enable and rate_k_per_min <= 0:
            raise ValueError(
                "a ramp rate of 0 K/min means 'infinitely fast' to the "
                "instrument; pass enable=False to turn ramping off instead"
            )
        self._write_verified(
            f"RAMP {loop},{1 if enable else 0},{rate_k_per_min:.3f}",
            lambda: self.ramp(loop), (bool(enable), rate_k_per_min),
            what=f"setpoint ramp on loop {loop}",
        )
        log.warning("%s: RAMP %d,%d,%.3f (verified)", self.name, loop,
                    1 if enable else 0, rate_k_per_min)

    def ramp(self, loop: int) -> tuple[bool, float]:
        self._check_loop(loop)
        on, rate = parse_float_list(self.transport.query(f"RAMP? {loop}"))
        return bool(on), rate

    def is_ramping(self, loop: int) -> bool:
        """True while the instrument is still traversing to a new setpoint."""
        self._check_loop(loop)
        return bool(int(parse_float(self.transport.query(f"RAMPST? {loop}"))))


class LS336(LS33x):
    """The 4-input, 4-loop box.  Read-only by default."""

    model_number = "336"

    def __init__(self, transport: Transport, **kw) -> None:
        kw.setdefault("name", "ls336")
        super().__init__(transport, model="336", **kw)


class LS335(LS33x):
    """The 2-input, 2-loop box.  Read-only by default."""

    model_number = "335"

    def __init__(self, transport: Transport, **kw) -> None:
        kw.setdefault("name", "ls335")
        super().__init__(transport, model="335", **kw)

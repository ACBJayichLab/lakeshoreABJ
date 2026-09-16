"""The phase 3 bench harness -- the fitted plant, at a chosen temperature.

**Not `conftest.py`, and the name matters.**  `tests/` and `tests_ltspm3/` are
both directories without `__init__.py`, so pytest puts each on `sys.path` and
the module name `conftest` means whichever of the two was imported first.  A
test doing `from conftest import ...` therefore passes under `pytest -q` and
fails under `pytest tests_ltspm3 tests`, which is the same
depends-on-how-you-ran-it fragility CLAUDE.md's "must not depend on the working
directory" rule exists for.  A uniquely named module has no such ambiguity.

`conftest.py` imports the fixtures from here; the tests import the class and
the temperature list from here.
"""
from __future__ import annotations

from pathlib import Path

from loop_harness import Harness

#
# A SECOND harness, on the FITTED plant.  `Harness` above runs
# `sim_response`'s two-pole model, whose one tau of 620 s came from a single
# step at 137 K -- right there and wrong everywhere else -- and every control
# test written before phase 1 is calibrated to it.  Those tests stay on it:
# re-pointing them would silently re-grade a hundred assertions against a
# different cryostat.
#
# What phase 3 has to prove is different.  It is that the loop works at 10 K
# and at 180 K, where tau runs from under a second to 611 s and the gain spans
# forty-fold, and only the fitted table knows that.  So: same wiring, same
# virtual clock, a different plant -- and the LIMITS come from
# `config-ltspm3-armed.yaml` rather than from a literal in a test, because the
# numbers the bench proves have to be the numbers the cryostat would run.

BENCH_CONFIG = Path(__file__).resolve().parents[1] / "config-ltspm3-armed.yaml"

#: The DESIGN ENVELOPE the bench grades against, which is not the same thing
#: as the commissioning stage the cryostat is armed at today.  See the long
#: note in `FittedHarness.__init__` for why these two are pinned here rather
#: than read from `BENCH_CONFIG` like every other limit.
#:
#: `authority_pct` 1.0 is what phase 3 proved the loop over: the 10-180 K
#: sweeps need the full window and rail against 4a's 0.1 by construction.
#: Feedforward on is likewise the end state -- 4c switches it on last, after
#: a gauge somebody believes, and `plans/pid-4-commissioning.md` records why.
BENCH_AUTHORITY_PCT = 1.0
BENCH_FEEDFORWARD = True

#: The six the plan grades at.  They are not evenly spaced in kelvin because
#: nothing about this cryostat is: 10 and 30 K are where the delay floor binds
#: and the watt residual has no opinion, 60 K is where the plant's own tau
#: overtakes the filter, and 100-180 K is the only part anybody has run.
BENCH_TEMPERATURES = (10.0, 30.0, 60.0, 100.0, 140.0, 180.0)


def bench_app_config():
    """The armed config, loaded whole.

    Loaded through `lschart.config.load`, not read as YAML, so the bench proves
    the file the cryostat would be given -- unknown keys, validators and all.
    """
    import ltspm3.config  # noqa: F401  -- registers `control:` and `monitor:`
    from lschart.config import load

    return load(str(BENCH_CONFIG))


def bench_control_config():
    """Just the `control:` section of it."""
    return bench_app_config().section("control")


class FittedHarness(Harness):
    """`Harness` on `FittedResponse`, at a chosen temperature.

    Two differences beyond the plant, and both are deliberate:

    * the cadence is the config's **2 s**, not `Harness.DT`'s 4.  The loop's
      dead time is derived from the cadence, so a bench that runs at twice the
      real period is proving a loop with twice the delay floor.
    * the authority band is re-centred on the scenario's own operating point.
      **That is the bench doing what the cryostat cannot**, and it is exactly
      what step 4 removes: `HeaterSupervisor.band` is two config constants and
      `_apply_band_to_pid()` runs once, in `__init__`, so a band centred at
      63.96 % cannot reach 24.22 % where 10 K lives.  Until step 4 lands, every
      scenario here is a loop that was handed the right window in advance.
    """

    def __init__(self, *, kelvin: float, sup_cfg=None, pid_cfg=None,
                 guard_cfg=None, filter_kwargs=None, cadence_s=None,
                 ff_cfg=None, delivered_frac=1.0, sink_offset_k=0.0, **kw):
        import dataclasses

        from ltspm3.model.fitted_response import FittedResponse

        app = bench_app_config()
        cfg = app.section("control")
        # THE CADENCE IS THE CONFIG'S, not a number in this file.  The loop's
        # dead time is derived from it, so a bench running at a different
        # period is grading a loop with a different delay floor.
        self.DT = float(app.acquisition.interval_s)
        self.bench_k = float(kelvin)
        # Start the plant AT the output that holds this temperature, so the
        # cryostat is in equilibrium on the first cycle.  One several kelvin
        # from it is a genuine anomaly and would mask every test here.
        #
        # **THE TWO KNOBS ARE WHAT MAKES THE CRYOSTAT WRONG** rather than the
        # controller -- `delivered_frac` is a heater that does not deliver what
        # `P(u)` claims and `sink_offset_k` is a coldplate off its locus, and
        # both are things the loop is supposed to notice.  The controller's own
        # model is perturbed by a different test, and the difference between
        # the two is the whole argument of §3.7's last row: mistuned must not
        # fault, broken must.
        plant = FittedResponse(start_k=self.bench_k,
                               delivered_frac=delivered_frac,
                               sink_offset_k=sink_offset_k)
        self.bench_pct = plant.percent_for(self.bench_k)
        plant.pct = self.bench_pct

        # THE SIMULATED COLDPLATE MUST BE THE MODEL'S OWN LOCUS, not the
        # generic simulator's scenery.  `SimulatedCryostat.DEFAULT_AUX_BASE`
        # puts input 2 at 8.06 K -- a plausible number, and a PRE-RECALIBRATION
        # one -- while the fit's settled locus runs 4.9 K at 10 K to 6.8 K at
        # 180 K.  That 1.2-1.5 K gap is not cosmetic: `Lambda(T_s) -
        # Lambda(T_c)` is what the watt residual is made of, and a sink 1.5 K
        # too warm is **25 to 47 mW of false missing power**, which is two to
        # four times the fault level.  Measured, and it faulted the bench at
        # 30 K and 180 K before this line existed.
        #
        # First order in the coupling, which is all the generic simulator
        # offers: the base at this temperature and the locus's local slope.
        from lschart.instruments.sim import SimulatedCryostat as _SC

        from ltspm3.model import fitted_response as _M
        from ltspm3.model.sim_response import LTSPM3_AUX_COUPLING

        h = 0.5
        slope = (_M.coldplate_k(self.bench_k + h)
                 - _M.coldplate_k(max(self.bench_k - h, _M.T_MIN_K))) / (2 * h)
        self._sink_locus_k = _M.coldplate_k(self.bench_k)
        kw.setdefault("aux_base", {**_SC.DEFAULT_AUX_BASE,
                                   "218.2": self._sink_locus_k + sink_offset_k})
        kw.setdefault("aux_coupling", {**LTSPM3_AUX_COUPLING, "218.2": slope})

        # **THE STAGE SWITCHES ARE PINNED HERE, NOT READ FROM THE FILE.**
        #
        # Everything else this harness takes from `config-ltspm3-armed.yaml`
        # is a property of the CRYOSTAT -- `hard_max_pct`, the rates, the
        # guard's thresholds -- and reading them from the file is what makes
        # the bench grade the numbers the cryostat runs.  These two are not.
        # They are where COMMISSIONING has got to, and 4a runs at
        # `authority_pct: 0.1` with feedforward off while 4c ends at 1.0 with
        # it on.  Both are the same cryostat.
        #
        # Reading them from the file made the file mean two things at once, so
        # the operational values could not be committed: narrowing to 4a's
        # numbers failed 26 scenarios here, because the 10-180 K sweeps rail
        # against +/-0.1 % by construction.  That left the working
        # configuration sitting uncommitted in somebody's tree, one
        # `git checkout` away from restoring the feedforward that cost 350 mK
        # on 2026-09-16 (HANDOFF, item B).
        #
        # So: the bench grades the ENVELOPE, and the file says what is
        # actually armed today.  A test that wants a different envelope passes
        # its own `sup_cfg`/`ff_cfg`, exactly as before.
        sup = sup_cfg or dataclasses.replace(
            cfg.supervisor,
            operating_point_pct=self.bench_pct,
            authority_pct=BENCH_AUTHORITY_PCT,
        )
        pid = pid_cfg or dataclasses.replace(cfg.pid, setpoint=self.bench_k)

        super().__init__(sup_cfg=sup, pid_cfg=pid,
                         guard_cfg=guard_cfg or cfg.guard,
                         # The FITTED curve, from the armed config -- the pair
                         # that goes with this plant.  Inheriting the legacy
                         # harness's CD10 default put 6.8 K of model error
                         # between the bench's cryostat and the bench's
                         # controller, which is a mismatch no test here asked
                         # for.
                         ff_cfg=ff_cfg or dataclasses.replace(
                             cfg.feedforward, enabled=BENCH_FEEDFORWARD),
                         filter_kwargs=filter_kwargs or dict(cfg.filter),
                         cadence_s=self.DT if cadence_s is None else cadence_s,
                         start_k=self.bench_k, model=plant, **kw)

        # `Harness` sets the plant's output from the simulator's DAC, which
        # starts at zero.  Put the heater where this scenario says instead --
        # on both sides, so the loop's belief and the instrument agree before
        # the first cycle rather than after the first write.
        self.sim.analog_pct = self.bench_pct
        plant.pct = self.bench_pct
        self.sup.output_pct = self.bench_pct
        self.sup.manual_pct = self.bench_pct
        self.equilibrium_k = self.bench_k

    @property
    def plant(self):
        return self.cryostat.response

    def deliver(self, frac: float) -> None:
        """The heater starts delivering ``frac`` of the power ``P(u)`` claims.

        A genuine fault: the watts stop adding up, the residual steps by
        ``-(1-frac) P(u)`` immediately, and the loop rails trying to make it up
        because restoring the power needs ``1/sqrt(frac)`` of the output and
        the band is one percent wide.
        """
        self.plant.delivered_frac = float(frac)

    def sink_offset(self, offset_k: float) -> None:
        """Put the coldplate ``offset_k`` above its fitted locus.

        **The reading follows the plant**, which is the half the old
        rising-coldplate row was missing: `_aux_base` alone moves what the
        thermometer says and leaves the sample where it was, so the residual
        saw a sink it could correct for and the loop saw no disturbance at all.
        Moving both is a cooler that is genuinely losing ground -- scenario 1
        of plans/pid-3-review.md, which warns however far it goes and never
        faults, because less heat needed is the safe direction.

        `_aux_base` is the simulator's own dictionary and there is no public
        setter; the bench has always reached in here.
        """
        self.plant.sink_offset_k = float(offset_k)
        self.cryostat._aux_base["218.2"] = self._sink_locus_k + float(offset_k)

    def minutes(self, n, dt=None):
        """`n` minutes of closed loop, at the bench cadence."""
        dt = dt or self.DT
        return self.step(int(round(n * 60.0 / dt)), dt=dt)

    def outputs(self):
        return [s.output_pct for s in self.history if s.output_pct is not None]



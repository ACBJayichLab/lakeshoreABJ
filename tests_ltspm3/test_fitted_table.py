"""The shipped table says what is known wrong with it.

Finding 2 of archive/AUDIT-2026-09-09.md: the ladder measured the fitted steady state
low by up to 4.5 K, five documents recorded that, and none of them was a file
that ships the table.  ``analysis/export_response.py`` now emits the caveat into
the generated header, and this pins the two together -- because the failure mode
is not somebody deleting the warning, it is a refit regenerating the file and
dropping it silently while the warning is still true.

Both files are read as TEXT.  ``analysis/`` imports neither package and is
imported by neither, and ``export_response`` needs scipy, which this suite does
not have.

Nothing here skips.  A skipped test fails the build in this repo, and the empty
case -- a refit having reconciled the two -- is a state this must keep asserting
in, not stop running in: that is exactly when a stale warning would be left
behind.
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "analysis" / "export_response.py"
TABLE = ROOT / "ltspm3" / "model" / "_fitted_table.py"

HEAD = 'SUPERSEDED_NOTE = """'
#: How the generator says it has no caveat at all.  A cleared note is a state
#: this test has to keep asserting in -- that is exactly when a stale warning
#: would be left behind in the table -- so the empty form is recognised rather
#: than read as "the mechanism was deleted".
CLEARED = 'SUPERSEDED_NOTE = ""'

#: Phrases that only ever appear in a caveat.  Used to catch a warning left in
#: the table after the generator's was cleared; update alongside the note.
SENTINELS = ("KNOWN LOW", "not yet refitted")


def _note() -> str:
    """The generator's caveat, or "" when it declares none."""
    src = GENERATOR.read_text(encoding="utf-8")
    if HEAD not in src and CLEARED in src:
        return ""
    if HEAD not in src:
        pytest.fail(f"{GENERATOR.name} no longer defines SUPERSEDED_NOTE; if the "
                    "caveat mechanism was removed, remove this test with it")
    i = src.index(HEAD) + len(HEAD)
    i += src[i] == "\\"
    i += src[i] == "\n"
    return src[i:src.index('"""', i)]


def _header() -> str:
    """The table's module docstring -- the first triple-quoted string in it."""
    return TABLE.read_text(encoding="utf-8").split('"""')[1]


def test_the_generated_table_and_the_generator_agree_about_the_caveat():
    note, header = _note().strip(), _header()
    if not note:
        # A refit reconciled them.  The table must not still be warning about a
        # discrepancy that no longer exists.
        for sentinel in SENTINELS:
            assert sentinel not in header, (
                f"analysis/export_response.py declares no caveat but the table's "
                f"header still says {sentinel!r} -- one of the two was updated")
        return
    for line in (ln for ln in note.split("\n") if ln.strip()):
        assert line in header, (
            f"ltspm3/model/_fitted_table.py's header is missing a line of "
            f"SUPERSEDED_NOTE -- a refit probably regenerated it and dropped "
            f"the caveat:\n  {line!r}")


def test_the_caveat_is_inside_the_docstring_where_it_will_be_read():
    """A warning below the closing quotes is a comment, and nobody reads it."""
    note = _note().strip()
    if not note:
        return
    first = note.split("\n")[0]
    text = TABLE.read_text(encoding="utf-8")
    assert text.index(first) < text.index("TABLE = ("), (
        "the caveat is outside the module docstring")


# -- the band --------------------------------------------------------------
#
# PID phase 1 section 1.2: the table carries its own error band, so that
# control/ and monitor.py read ONE source and cannot disagree about what
# typical means.  A bare float in a generated file is a number nobody can
# check, so each is asserted to be PRESENT and in a range that is physically
# meaningful rather than merely non-empty -- the failure this guards against is
# a refit exporting a plausible-looking band with a term collapsed to zero,
# which reads as "the cryostat is very well behaved" and is how a monitor comes
# to have no opinion about anything.

import datetime as _dt  # noqa: E402

from ltspm3.model import _fitted_table as T  # noqa: E402
from ltspm3.model import fitted_response as M  # noqa: E402

#: ``(name, low, high, unit)``.  The bounds are wide -- they are not a second
#: opinion about the measurement, they are the range outside which the number
#: cannot be what its name says.
BAND = (
    ("SIGMA_MODEL_K", 0.01, 0.5, "K"),
    ("SIGMA_TINF_K", 1e-3, 0.1, "K"),
    ("DIURNAL_K", 1e-3, 0.1, "K"),
    ("TC_RMS_K", 1e-3, 0.2, "K"),
    ("TAU_BATH_S", 30.0, 1000.0, "s"),
    ("SIGMA_C_FRAC", 2e-3, 0.15, "-"),
    ("DRIFT_W_PER_DAY", 0.0, 2e-3, "W/day"),
    #: The heater is rated 1.68 W, so a reference power above it is a typo.
    ("DRIFT_REF_W", 0.1, 1.68, "W"),
    ("DELTA_P_FRAC", 0.0, 0.05, "-"),
)


@pytest.mark.parametrize("name,low,high,unit", BAND)
def test_every_band_constant_is_present_and_in_range(name, low, high, unit):
    value = getattr(M, name)
    assert isinstance(value, float), f"{name} is {type(value).__name__}"
    assert low <= value <= high, f"{name} = {value} {unit}, outside {low}-{high}"


def test_the_drift_spread_brackets_the_drift():
    """Three independent output bands, and the median has to be between them."""
    lo, hi = T.DRIFT_SPREAD_W_PER_DAY
    assert lo < M.DRIFT_W_PER_DAY < hi


def test_the_gauge_epoch_and_its_date_are_the_same_moment():
    """Two spellings of one instant, and a consumer may read either."""
    stamp = _dt.datetime.utcfromtimestamp(M.DRIFT_T0_UNIX).strftime("%Y-%m-%d")
    assert stamp == M.DRIFT_T0


def test_the_table_says_which_fit_it_is():
    """PID_PLAN.md's 'pastes rot' trap: a schedule row carries this key."""
    key = T.FIT_KEY
    assert len(key) == 32 and all(c in "0123456789abcdef" for c in key), key


def test_the_gauge_is_a_calibration_and_not_a_refit():
    """A delivered-power gauge of more than a few percent is not a gauge.

    REFIT_PLAN.md section 7.2 measures the events at a few tenths of a percent
    each; if this ever comes back at 10 % it is the fit's level that moved, and
    dividing the curve by it would be hiding a refit inside a calibration.
    """
    assert abs(T.GAUGE_FRAC) < 0.05
    assert T.GAUGE_N >= 10

"""The shipped table says what is known wrong with it.

Finding 2 of AUDIT-2026-09-09.md: the ladder measured the fitted steady state
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
TABLE = ROOT / "ltspm3" / "_fitted_table.py"

HEAD = 'SUPERSEDED_NOTE = """'

#: Phrases that only ever appear in a caveat.  Used to catch a warning left in
#: the table after the generator's was cleared; update alongside the note.
SENTINELS = ("KNOWN LOW", "not yet refitted")


def _note() -> str:
    """The generator's caveat, or "" when it declares none."""
    src = GENERATOR.read_text(encoding="utf-8")
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
            f"ltspm3/_fitted_table.py's header is missing a line of "
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

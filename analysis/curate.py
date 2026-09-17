"""Propose the cooldown-10 manifest, and diff a proposal against the committed one.

The fits used to *discover* their dataset: five overlapping tables scanned for
constant-heater dwells by a rule keyed on ``steps.U_TOL_PCT``, so changing that
constant changed which 234 dwells existed with nothing in review to show it.
This is the other way round.  The dataset is
``reference/cooldown-10/segments.csv``, a committed file, and a change to the
finder or its constants shows up here as a **diff** somebody has to look at.

What is authored and what is derived
------------------------------------

The manifest is not hand-written, and it is not machine-written either.  Three
things in it are a human's and everything else follows from them:

``mask`` rows
    a window nothing may be fitted from, with a note saying why -- a power
    transient, an instrument swap, an hour somebody was leaning on the table.
    **Masks are an INPUT to this proposal**, not an output of it: a dwell that
    would cross a mask is cut at its edge, and a dwell inside one is dropped.
    That is what lets ``--propose`` report *no diff* on a clean tree while
    still respecting a human's judgement, and it means there is exactly one
    mechanism for "do not use this" rather than a per-row override flag.
``trace`` rows
    a long trajectory for the ODE.  Hand-chosen, because which stretch of a
    cooldown is one experiment is not a thing a dwell finder can know.  Passed
    through untouched.
the ``note`` column
    words, carried forward by ``id``.  If a boundary moves the id moves with
    it and the note is reported orphaned rather than silently reattached to a
    window it was not written about.

Everything else -- which dwells exist, their kind, their ``use`` and their
``quality`` -- is computed from the archive by :mod:`steps`' finder and grader.

Usage::

    python analysis/curate.py --propose        # diff against the committed file
    python analysis/curate.py --propose -v     # ...with the grading numbers
    python analysis/curate.py --write          # accept the proposal
"""
from __future__ import annotations

import csv
import os
import sys


import segments as S
import steps

#: Where a dwell stops being a step somebody drove and starts being a hold.
#:
#: 8 hours, and both halves of the reason are about the plant rather than the
#: clock.  It is **54 time constants** at 114 K, where tau is longest in this
#: cooldown at 530 s, so a dwell this long cannot be long *because of* the
#: relaxation -- the extra time is information about drift instead.  And it is
#: a third of the diurnal period, the shortest window in which the building's
#: 24 h cycle shows as curvature rather than as a straight line, which is what
#: Phase A's harmonic needs to be identifiable at all.
#:
#: A ``hold`` is not treated better than a ``jump`` by any fit.  ``use`` decides
#: that, and it comes from the grader either way.  The distinction is about
#: what a window can be *asked*: a jump is asked for a time constant and a
#: steady state, a hold is asked for drift, a diurnal amplitude and how the
#: bath channels reach the sample.
HOLD_MIN_S = 8 * 3600.0

#: The manifest's columns, in order.  ``REFIT_PLAN.md`` §5.2 fixes these.
FIELDS = ("id", "kind", "file", "t_start", "t_end", "u_pct", "T_lo", "T_hi",
          "use", "quality", "note")

#: Short, stable tag per archive table, so an id says at a glance which third
#: of the cooldown it is in.
TAGS = {
    "cd10_20260715_prepython.csv": "pp",
    "cd10_20260824_recorder.csv": "rec",
    "cd10_20260904_recorder.csv": "pc",
}

#: Why the grader said no.  These are the three rejections ``steps.grade``
#: makes, named, so a verdict change shows up in the manifest as a word rather
#: than as a blank cell.
#:
#: ``UNSETTLED`` and ``UNRESOLVED`` are different failures and were one word
#: until REFIT_PLAN.md section 6.2 option 4 separated them.  Unsettled is the
#: dwell's own pole saying it was still moving when it ended.  Unresolved is the
#: pole saying **nothing** -- fitted to noise, or pinned at the top of the
#: search where it has degenerated to a straight line -- on a dwell that had not
#: run enough of the PLANT's time constants to be believed without it.  The
#: first is a measurement of a dwell that was cut short; the second is the
#: absence of one, and a reviewer wants to be able to tell them apart in the
#: manifest rather than by re-running the grader.
UNSETTLED = "unsettled"
UNRESOLVED = "unresolved"
OVER_EXTRAPOLATED = "over-extrapolated"


def _tag(file: str) -> str:
    return TAGS[file]


def _id(file: str, epoch: float) -> str:
    """``rec-20260902-124200`` -- the table and the second it starts.

    Deliberately does not contain the kind or the grade: those change when a
    constant moves, and an id that changes with them takes its note with it.
    """
    import datetime as dt
    return f"{_tag(file)}-{dt.datetime.fromtimestamp(epoch):%Y%m%d-%H%M%S}"


def propose(masks_by_file: dict) -> list:
    """One row per constant-heater dwell in the archive, graded and named.

    The scan and the fit are :func:`steps.archive_dwells` -- deliberately not a
    second loop here.  What this adds is the naming and the two judgements the
    manifest records: which kind of window it is, and what a fit may do with it.
    """
    out = []
    for r in steps.archive_dwells(masks_by_file):
        if r["grade"]:
            use = quality = r["grade"]
        elif abs(r["settle_K"]) > steps.MAX_SETTLE_K:
            use, quality = "excluded", OVER_EXTRAPOLATED
        elif (steps.pole_unbelievable(r)
              and not steps.long_enough(r, r.get("tau_plant_s"))):
            use, quality = "excluded", UNRESOLVED
        else:
            use, quality = "excluded", UNSETTLED
        out.append({
            "id": _id(r["file"], S._stamp(r["t_start"])),
            "kind": "hold" if r["span_s"] >= HOLD_MIN_S else "jump",
            "file": r["file"],
            "t_start": r["t_start"],
            "t_end": r["t_end"],
            "u_pct": round(r["u_pct"], 4),
            "T_lo": round(r["T_lo"], 4),
            "T_hi": round(r["T_hi"], 4),
            "use": use,
            "quality": quality,
            "note": "",
            "_fit": r,
        })
    return out


def read_manifest(path: str) -> list:
    """The committed manifest as plain dicts, or [] if there is not one yet."""
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def authored(rows: list) -> tuple:
    """Split a manifest into (masks, traces, notes-by-id) -- the human's half."""
    masks, traces, notes = [], [], {}
    for r in rows:
        if (r.get("note") or "").strip():
            notes[r["id"]] = r["note"].strip()
        if r["kind"] == "mask":
            masks.append(S.Window(
                id=r["id"], kind=r["kind"], file=r["file"],
                t_start=r["t_start"], t_end=r["t_end"],
                u_pct=float(r["u_pct"]), T_lo=float(r["T_lo"]),
                T_hi=float(r["T_hi"]), use=r["use"],
                quality=r["quality"], note=r.get("note", "")))
        elif r["kind"] == "trace":
            traces.append(r)
    return masks, traces, notes


def assemble(path: str) -> tuple:
    """(rows, orphans) -- the full proposed manifest, in file and time order."""
    committed = read_manifest(path)
    masks, traces, notes = authored(committed)
    by_file: dict = {}
    for m in masks:
        by_file.setdefault(m.file, []).append(m)

    rows = propose(by_file)
    # Masks and traces are the human's and are carried through verbatim, so
    # that re-proposing is a no-op on them.
    for m in masks:
        rows.append({k: getattr(m, k) for k in FIELDS} | {"_fit": None})
    for tr in traces:
        rows.append({k: tr[k] for k in FIELDS} | {"_fit": None})

    ids = {r["id"] for r in rows}
    for r in rows:
        if r["id"] in notes:
            r["note"] = notes[r["id"]]
    orphans = {i: n for i, n in notes.items() if i not in ids}
    order = {f: i for i, f in enumerate(S.TABLES)}
    rows.sort(key=lambda r: (order[r["file"]], r["t_start"], r["kind"]))
    return rows, orphans


def diff(proposed: list, committed: list) -> list:
    """Every disagreement, one line each.  Empty means the tree is clean."""
    P = {r["id"]: r for r in proposed}
    C = {r["id"]: r for r in committed}
    out = []
    for i in sorted(set(C) - set(P)):
        out.append(f"- {i:<18} {C[i]['kind']:<6} {C[i]['t_start']}  GONE from the proposal")
    for i in sorted(set(P) - set(C)):
        r = P[i]
        out.append(f"+ {i:<18} {r['kind']:<6} {r['t_start']}  NEW: "
                   f"use={r['use']} quality={r['quality']}")
    for i in sorted(set(P) & set(C)):
        p, c = P[i], C[i]
        for f in FIELDS:
            if f in ("id", "note"):
                continue
            a, b = str(c[f]).strip(), str(p[f]).strip()
            if f in ("u_pct", "T_lo", "T_hi"):
                if abs(float(a) - float(b)) < 5e-5:
                    continue
            elif a == b:
                continue
            out.append(f"~ {i:<18} {f}: {a!r} -> {b!r}")
    return out


def write(path: str, rows: list) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(FIELDS))
        w.writeheader()
        for r in rows:
            w.writerow({k: r[k] for k in FIELDS})


def summarise(rows: list) -> None:
    from collections import Counter
    kinds, uses, quals = (Counter(r["kind"] for r in rows),
                          Counter(r["use"] for r in rows),
                          Counter(r["quality"] for r in rows))
    print(f"\n{len(rows)} windows")
    for name, c in (("kind", kinds), ("use", uses), ("quality", quals)):
        print(f"  {name:<9}" + "  ".join(f"{k}={v}" for k, v in sorted(c.items())))
    # The band the fit actually uses.  Reported because it is the number the
    # refit is being judged on -- 109 anchors in 4-200 K -- and because a
    # grader change that moves it should be impossible to miss.
    fit = [r for r in rows if r["use"] in ("tau", "steady") and r.get("_fit")]
    inband = [r for r in fit if 4.0 <= r["_fit"]["T_inf"] <= 200.0]
    if fit:
        print(f"  anchors   {len(fit)} usable, {len(inband)} inside 4-200 K, "
              f"{sum(1 for r in fit if r['use'] == 'tau')} with a believable tau")


#: A plant-clock verdict decided by less than this factor is quoted as needing
#: a look.
#:
#: A factor of two either way, and it is chosen to catch a specific row rather
#: than for roundness.  ``pp-20260808-155602`` -- 840 s at 99.42 K -- is decided
#: at 0.63, which is archive/AUDIT-2026-09-10-REJOINDER.md's "a margin of 1.6 in tau,
#: not the three orders of magnitude the other six enjoy", and it is the one
#: verdict that document asks a reviewer to look at by name.  A bar at 1.5 puts
#: it a hundredth outside the list it exists to be on.  Two also happens to be
#: about the largest error the interpolant could plausibly carry: where it is
#: checkable it agrees with the shipped table to 2.2 %.
MARGIN_ATTENTION = 2.0


def plant_report(rows: list) -> None:
    """Every window the plant clock was asked about, and by how much it decided.

    :func:`steps.long_enough` only speaks when the fitted pole cannot -- see
    ``steps.pole_unbelievable`` -- so this is the whole of what
    REFIT_PLAN.md section 6.2 option 4 changed, on one page.  It is printed
    rather than stored because the manifest's columns are fixed by section 5.2
    and the verdict is already in ``use``; what wants recording per row is the
    MARGIN, and for the rows where that is thin the place for it is the
    ``note`` column, written by a human.
    """
    judged = [r for r in rows
              if r.get("_fit") and steps.pole_unbelievable(r["_fit"])]
    if not judged:
        return
    fits = [r["_fit"] for r in rows if r.get("_fit")]
    clock = steps.plant_clock([f for f in fits if f["grade"] == "tau"])
    band = f"{clock.band[0]:.1f}-{clock.band[1]:.1f} K, {clock.n} anchors" if clock \
        else "NO PLANT CLOCK -- falling back to the MIN_SPAN_S proxy"
    print(f"\nthe plant clock: tau(T) over {band}")
    print(f"{'id':<22}{'T_inf':>9}{'span s':>9}{'tau fit':>10}{'tau plant':>10}"
          f"{'reach':>9}{'margin':>8}  {'verdict':<9}why the pole is mute")
    for r in sorted(judged, key=lambda r: r["_fit"]["plant_margin"]):
        f = r["_fit"]
        why = ("pinned at the search ceiling" if steps.pole_ceiling(f)
               else f"nothing moved ({f['amp_sigma']:.1f} sigma)")
        print(f"{r['id']:<22}{f['T_inf']:>9.3f}{f['span_s']:>9.0f}"
              f"{f['tau_s']:>10.1f}{f['tau_plant_s']:>10.2f}"
              f"{f['reach_plant']:>9.2f}{f['plant_margin']:>8.2f}"
              f"  {r['use']:<9}{why}")
    thin = [r for r in judged
            if 1.0 / MARGIN_ATTENTION <= r["_fit"]["plant_margin"] <= MARGIN_ATTENTION]
    print(f"\n  {sum(1 for r in judged if r['use'] != 'excluded')} of {len(judged)} "
          f"kept.  margin is the factor tau(T) would have to be wrong by to flip "
          f"the verdict.")
    if thin:
        print(f"  {len(thin)} decided by under {MARGIN_ATTENTION}x -- these are the "
              f"rows to read rather than wave through:")
        for r in thin:
            print(f"    {r['id']:<22}{r['_fit']['T_inf']:>9.3f} K  "
                  f"margin {r['_fit']['plant_margin']:.2f}  -> {r['use']}"
                  + (f"   note: {r['note']}" if r["note"] else "   NO NOTE"))


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        description="propose the cooldown-10 manifest, or diff a proposal "
                    "against the committed one")
    ap.add_argument("-m", "--manifest", default=None,
                    help="default: reference/cooldown-10/segments.csv")
    ap.add_argument("--propose", action="store_true",
                    help="print the diff against the committed manifest (default)")
    ap.add_argument("--write", action="store_true",
                    help="accept the proposal and write the manifest")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="print every proposed window with its grading numbers")
    ap.add_argument("--plant", action="store_true",
                    help="print the plant clock and every verdict it decided")
    a = ap.parse_args(argv)

    path = a.manifest
    if path is None:
        # Not segments.manifest_path(): that resolves an EXISTING file, and
        # --write has to be able to create the first one.
        from _data import ARCHIVE_DIR, REPO_ROOT
        path = os.path.join(REPO_ROOT, ARCHIVE_DIR, S.MANIFEST)

    rows, orphans = assemble(path)
    committed = read_manifest(path)

    if a.verbose:
        print(f"{'id':<22}{'kind':<6}{'use':<9}{'span h':>8}{'u%':>9}"
              f"{'T_inf':>9}{'tau s':>10}{'reach':>8}{'rem K':>8}{'K/h':>9}  quality")
        for r in rows:
            f = r.get("_fit")
            if f is None:
                print(f"{r['id']:<22}{r['kind']:<6}{r['use']:<9}"
                      f"{'':>8}{r['u_pct']:>9}{'':>9}{'':>10}{'':>8}{'':>8}{'':>9}  "
                      f"{r['quality']}")
                continue
            print(f"{r['id']:<22}{r['kind']:<6}{r['use']:<9}"
                  f"{f['span_s'] / 3600:>8.2f}{r['u_pct']:>9.4f}"
                  f"{f['T_inf']:>9.3f}{f['tau_s']:>10.1f}{f['reach']:>8.2f}"
                  f"{f['remainder_K']:>8.3f}{f['end_rate_k_per_h']:>9.3f}  "
                  f"{r['quality']}")

    if a.plant:
        plant_report(rows)

    summarise(rows)

    for i, n in sorted(orphans.items()):
        print(f"\n  ORPHANED NOTE  {i}: {n!r}\n"
              f"  No window has that id any more -- a boundary moved.  Reattach "
              f"it or delete it;\n  it is not being carried forward.")

    if a.write:
        write(path, rows)
        print(f"\nwrote {os.path.relpath(path)}  ({len(rows)} windows)")
        S.check(path)
        print("validated: every window inside its file, inside one segment, "
              "no two of a kind overlapping")
        return 0

    d = diff(rows, committed)
    if not committed:
        print(f"\n{os.path.relpath(path)} does not exist yet -- "
              f"--write would create it with the {len(rows)} windows above")
        return 1
    if d:
        print(f"\n{len(d)} disagreement(s) with {os.path.relpath(path)}:\n")
        for line in d:
            print("  " + line)
        print("\nThe manifest is what the fits read.  Either the finder changed "
              "and this is the\nreview, or the manifest is stale -- decide "
              "which, then --write.")
        return 1
    print(f"\nno diff against {os.path.relpath(path)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

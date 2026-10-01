"""Extend the shipped table past the fit's range by CONFIRMED EXTRAPOLATION.

The fit (`export_response.py`) ends where the 43 h sweep ended, at 195 K.
Jeff wants the loop at 200-250 K, and the way he asked for it is the right
one: do not refit, step up by hand, hold each rung until it settles, and
check that the table's own last slope, carried straight on, predicted what
the cryostat did.  Where it did -- and on 2026-09-30 it did to 0.2 K at
225 K, 0.7 K at 237 K and 1.8 K at 249 K, the miss growing linearly -- the
extension is bookkeeping, and this is the bookkeeping.

The anchors are a COMMITTED FILE, `reference/cooldown-10/extension_anchors.csv`,
one row per settled hold above the fit's top: the output, the settled
temperature with its bar, the time constant where a clean step resolved one,
and the coldplate.  Measured by hand from the recorder's CSV (an exponential
approach, whole window against last half, the two agreeing before a number is
written down); the file carries the window and the fit so the next person can
redo it.  Curate, do not discover: a new rung is a new row somebody reviewed.

What is extrapolated, and how little:

* ``q(T)``, the power that holds T, continues from the fit's edge with the
  fit's own slope plus ONE quadratic term fitted to the anchors --
  ``q = q_e + s_e d + b d^2``, ``d = T - T_edge``.  Continuous in value and in
  slope at the edge, so the gain schedule does not kink there.  ``b`` is the
  whole of what the anchors add to the level, and `--check` prints what each
  anchor would have been predicted at with ``b = 0``, which is the
  confirmation.
* ``slope`` is that curve's derivative.
* ``cap`` is ``tau x slope``, with ``tau`` the MEASURED time constant above
  the edge -- it is what the controller actually needs, it was measured
  directly, and the table's own C/Lambda' at the edge (605 s) agrees with it
  to a percent, so the join is continuous.
* ``tc`` is a straight line through the edge and the anchors.

The grid keeps the table's own spacing.  `T_MAX_K` moves to the highest
anchor; above THAT the table still clamps, and the supervisor's
`authority_beyond_table_pct_per_k` still applies there.

Idempotent and re-runnable: the fit's own top is recorded as
``EXTENSION_FROM_K`` and every row above it is replaced, so a re-export of the
fit followed by this script gives the same file whichever order the two ran
in -- `export_response.py` calls `apply()` itself for exactly that reason.

Usage::

    python analysis/extend_table.py --check      # the confirmation table, write nothing
    python analysis/extend_table.py              # rewrite ltspm3/model/_fitted_table.py

Stdlib only, like the rest of `ltspm3/model/`, and it imports neither package:
the generated table is loaded as a DATA FILE by path.
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ANCHORS = ROOT / "reference" / "cooldown-10" / "extension_anchors.csv"
TABLE = ROOT / "ltspm3" / "model" / "_fitted_table.py"

#: Markers that delimit what this script owns inside the generated file, so a
#: second run replaces its own work rather than stacking on it.
DOC_BEGIN = "EXTENDED PAST THE FIT BY CONFIRMED EXTRAPOLATION"
DOC_END = "(end of the extension note)"
BLOCK_BEGIN = "# -- extension: see analysis/extend_table.py --"
BLOCK_END = "# -- end extension --"


def _load_table(path: Path):
    """The generated module, loaded from its file -- data, not a package import."""
    spec = importlib.util.spec_from_file_location("_fitted_table_data", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def read_anchors(path: Path = ANCHORS) -> list[dict]:
    out = []
    with open(path, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            out.append({
                "date": row["date"],
                "u_pct": float(row["u_pct"]),
                "t_inf_k": float(row["t_inf_k"]),
                "t_inf_err_k": float(row["t_inf_err_k"]),
                "tau_s": float(row["tau_s"]) if row["tau_s"] else None,
                "tau_err_s": float(row["tau_err_s"]) if row["tau_err_s"] else None,
                "tc_k": float(row["tc_k"]),
                "note": row["note"],
            })
    return sorted(out, key=lambda a: a["t_inf_k"])


def power_w(pct: float, m) -> float:
    return (m.GAIN * m.V_FS * max(pct, 0.0) / 100.0) ** 2 / m.R_OHM


def fit_extension(m, anchors: list[dict]) -> dict:
    """The one quadratic term, the measured tau, the coldplate line."""
    from_k = float(getattr(m, "EXTENSION_FROM_K", m.TABLE[-1][0]))
    base = [r for r in m.TABLE if r[0] <= from_k + 1e-9]
    t_e, q_e, s_e, c_e, tc_e = base[-1]
    above = [a for a in anchors if a["t_inf_k"] > t_e]
    if not above:
        raise SystemExit(f"no anchor above the fit's top ({t_e:g} K); nothing to extend")
    d = [a["t_inf_k"] - t_e for a in above]
    r = [power_w(a["u_pct"], m) - q_e - s_e * di for a, di in zip(above, d)]
    # Weighted by the level bar: a 0.2 K anchor should not pull as hard as a
    # 0.05 K one.  Least squares on b alone, closed form.
    w = [1.0 / max(a["t_inf_err_k"], 1e-3) ** 2 for a in above]
    b = (sum(wi * ri * di * di for wi, ri, di in zip(w, r, d))
         / sum(wi * di ** 4 for wi, di in zip(w, d)))
    taus = [(a["tau_s"], a["tau_err_s"] or 30.0) for a in above if a["tau_s"]]
    if taus:
        wt = [1.0 / e ** 2 for _, e in taus]
        tau = sum(wi * t for wi, (t, _) in zip(wt, taus)) / sum(wt)
    else:
        tau = c_e / s_e
    # Coldplate: a line through the edge and the anchors.
    xs = [t_e] + [a["t_inf_k"] for a in above]
    ys = [tc_e] + [a["tc_k"] for a in above]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    tc_slope = (sum((x - mx) * (y - my) for x, y in zip(xs, ys))
                / sum((x - mx) ** 2 for x in xs))
    return {
        "from_k": t_e, "base": base, "q_e": q_e, "s_e": s_e, "tc_e": tc_e,
        "b": b, "tau_s": tau, "tau_edge_s": c_e / s_e,
        "tc_slope": tc_slope, "top_k": above[-1]["t_inf_k"], "anchors": above,
    }


def q_of(t: float, f: dict) -> float:
    d = t - f["from_k"]
    return f["q_e"] + f["s_e"] * d + f["b"] * d * d


def slope_of(t: float, f: dict) -> float:
    return f["s_e"] + 2.0 * f["b"] * (t - f["from_k"])


def tc_of(t: float, f: dict) -> float:
    return f["tc_e"] + f["tc_slope"] * (t - f["from_k"])


def t_for_q(q: float, f: dict) -> float:
    """Invert the quadratic on its rising branch."""
    a, bq, c = f["b"], f["s_e"], f["q_e"] - q
    if abs(a) < 1e-15:
        return f["from_k"] - c / bq
    disc = bq * bq - 4 * a * c
    return f["from_k"] + (-bq + math.sqrt(max(disc, 0.0))) / (2 * a)


def rows(f: dict) -> list[tuple]:
    base = f["base"]
    ratio = base[-1][0] / base[-2][0]
    out = []
    t = f["from_k"]
    while True:
        t *= ratio
        if t >= f["top_k"] * (1 - 1e-9):
            t = f["top_k"]
        q, s = q_of(t, f), slope_of(t, f)
        out.append((t, q, s, f["tau_s"] * s, tc_of(t, f)))
        if t >= f["top_k"]:
            return out


def check(f: dict, m) -> None:
    print(f"fit's top {f['from_k']:g} K: q {f['q_e']*1e3:.1f} mW, slope "
          f"{f['s_e']*1e3:.3f} mW/K, tau C/Lambda' {f['tau_edge_s']:.0f} s")
    print(f"extension: b {f['b']*1e6:+.4g} uW/K^2, tau {f['tau_s']:.0f} s "
          f"(measured), top {f['top_k']:g} K, coldplate {f['tc_slope']*1e3:+.2f} mK/K")
    print()
    print(f"{'anchor':>8} {'output':>8} {'measured':>9} {'straight line':>14} "
          f"{'miss':>6} {'extended':>9} {'resid':>6} {'tau meas':>9}")
    for a in f["anchors"]:
        q = power_w(a["u_pct"], m)
        straight = f["from_k"] + (q - f["q_e"]) / f["s_e"]
        ext = t_for_q(q, f)
        tau = f"{a['tau_s']:.0f} s" if a["tau_s"] else "--"
        print(f"{a['t_inf_k']:8.2f} {a['u_pct']:7.3f}% {a['t_inf_k']:9.2f} "
              f"{straight:14.2f} {straight - a['t_inf_k']:+6.2f} {ext:9.2f} "
              f"{ext - a['t_inf_k']:+6.2f} {tau:>9}")
    print()
    print("the 'miss' column is the confirmation: what the fit's last slope, "
          "carried straight on with nothing fitted, predicted for each rung.")


def apply(path: Path = TABLE, anchors_path: Path = ANCHORS) -> None:
    """Rewrite the generated table with the extension rows and constants."""
    m = _load_table(path)
    f = fit_extension(m, read_anchors(anchors_path))
    ext = rows(f)
    src = path.read_text(encoding="utf-8")

    # 1. the docstring note, replaced if present
    if DOC_BEGIN in src:
        i = src.index(DOC_BEGIN)
        j = src.index(DOC_END, i) + len(DOC_END) + 1
        src = src[:i] + src[j:]
    note = "\n".join([
        f"{DOC_BEGIN} -- analysis/extend_table.py,",
        f"{f['anchors'][-1]['date']}.  The fit ends at {f['from_k']:g} K; the rows above "
        f"it continue",
        "its last slope with one quadratic term fitted to the settled rungs in",
        "reference/cooldown-10/extension_anchors.csv, and `cap` there is the",
        "MEASURED tau times the slope.  Nothing above the fit's top is a fit.",
        "`python analysis/extend_table.py --check` prints the confirmation.",
        f"{DOC_END}",
        "",
    ])
    head_end = src.index('"""', src.index('"""') + 3)
    src = src[:head_end] + note + src[head_end:]

    # 2. T_MAX_K
    lines = src.split("\n")
    for k, line in enumerate(lines):
        if line.startswith("T_MAX_K = "):
            lines[k] = f"T_MAX_K = {f['top_k']:.6g}"
            break
    src = "\n".join(lines)

    # 3. the constants block, replaced if present, before the TABLE
    if BLOCK_BEGIN in src:
        i = src.index(BLOCK_BEGIN)
        j = src.index(BLOCK_END, i) + len(BLOCK_END) + 1
        src = src[:i] + src[j:]
    anchors_repr = "".join(
        f"\n    ({a['t_inf_k']:g}, {a['u_pct']:g}, {a['t_inf_err_k']:g}, "
        f"{a['tau_s'] if a['tau_s'] is not None else 'None'}),"
        for a in f["anchors"]) + "\n"
    block = "\n".join([
        BLOCK_BEGIN,
        "#: Where the FIT ends.  Every row above it is the extension, and the",
        "#: band widening past the table starts at T_MAX_K, not here.",
        f"EXTENSION_FROM_K = {f['from_k']:.6g}",
        "#: The one fitted term: q = q_edge + slope_edge*d + EXTENSION_B*d^2.",
        f"EXTENSION_B = {f['b']:.6g}",
        "#: The measured time constant above the fit, and what `cap` is made of.",
        f"EXTENSION_TAU_S = {f['tau_s']:.6g}",
        "#: (T_inf K, output %, level bar K, tau s or None) -- the settled rungs.",
        f"EXTENSION_ANCHORS = ({anchors_repr})",
        BLOCK_END,
        "",
    ])
    marker = "#: ``(T, lam, slope, cap, tc)``"
    src = src.replace(marker, block + marker, 1)

    # 4. the rows: everything above the fit's top replaced
    i = src.index("TABLE = (")
    j = src.index("\n)", i)
    kept = [line for line in src[i:j].split("\n")[1:]
            if line.strip() and float(line.strip()[1:].split(",")[0]) <= f["from_k"] + 1e-9]
    new = [f"    ({T:.6g}, {q:.8g}, {s:.8g}, {cap:.8g}, {tc:.6g})," for T, q, s, cap, tc in ext]
    src = src[:i] + "TABLE = (\n" + "\n".join(kept + new) + src[j:]
    path.write_text(src, encoding="utf-8", newline="\n")
    print(f"wrote {path}: {len(kept)} fitted rows + {len(new)} extension rows, "
          f"top {f['top_k']:g} K")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="print the confirmation table and write nothing")
    ap.add_argument("--table", default=str(TABLE))
    args = ap.parse_args()
    m = _load_table(Path(args.table))
    f = fit_extension(m, read_anchors())
    check(f, m)
    if args.check:
        return 0
    apply(Path(args.table))
    return 0


if __name__ == "__main__":
    sys.exit(main())

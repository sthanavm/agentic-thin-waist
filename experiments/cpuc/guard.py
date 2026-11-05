"""The browser background-traffic guard. A HARD gate, not a warning.

Why this is a gate and not a note: on 31 archived captures from the pramana
dataset, 22 would fail this test. Chrome's own background fetches -- the
component updater on the `rr*---sn-*.gvt1.com` GGC edges, plus www.google.com,
gstatic and update.googleapis.com -- held more than 10% of the shaped capacity
for 3 or more seconds INSIDE the app's own measured window, peaking at
9.91 Mb/s against a 10 Mb/s cap on one run and ~99% of the cap on another. A
run like that measures Chrome competing with itself, not the network condition
we configured, and its QoE label is not evidence about IQB's thresholds.

So there are two checks, and both are fatal:

  1. BEFORE the run: the suppression flags are actually on the launched
     browser. Intending to pass them is not the same as having passed them --
     a typo in a flag name is silently ignored by Chrome.
  2. AFTER the run: no non-app bucket took more than the budget inside the
     app's measured window, judged per bin on the same time base as the app's
     own series.

Check 2 reuses `pramana_helpers.link_interference` rather than reimplementing
it, because a second copy of the same judgement would drift from the first and
then two datasets would disagree about which runs were valid.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# Exactly the flags build_driver passes for this purpose, by name. Asserted
# against the browser's real command line, so a renamed or dropped flag fails
# the run instead of quietly letting the updater back onto the link.
REQUIRED_SUPPRESSION_FLAGS = (
    "--disable-component-update",
    "--disable-background-networking",
    "--disable-domain-reliability",
    "--no-first-run",
    "--no-default-browser-check",
)
# Substrings that must appear inside --disable-features and
# --host-resolver-rules respectively.
REQUIRED_DISABLED_FEATURES = (
    "OptimizationGuideModelDownloading",
    "OptimizationHints",
    "OptimizationTargetPrediction",
)
REQUIRED_RESOLVER_MAPPINGS = (
    "optimizationguide-pa.googleapis.com",
    "edgedl.me.gvt1.com",
    "update.googleapis.com",
)

# Budget: a non-app bucket may not hold more than this fraction of the cap for
# more than MIN_BINS bins inside the app's measured window.
INTERFERENCE_BUDGET_FRACTION = 0.10
INTERFERENCE_MIN_BINS = 3


class SuppressionNotActive(RuntimeError):
    """Raised before a run when the browser is not actually suppressed."""


class BackgroundContention(RuntimeError):
    """Raised after a run that had non-app traffic inside the app's window."""


class GuardUnavailable(RuntimeError):
    """Raised when the interference judgement itself is not importable."""


def assert_suppression_flags(chrome_cmdline: str) -> None:
    """Fail unless every suppression flag is present on the real command line.

    `chrome_cmdline` should come from the launched process (for example
    /proc/<pid>/cmdline inside the collector container, or chrome://version's
    command line), never from the list we meant to pass.
    """
    if not chrome_cmdline or not chrome_cmdline.strip():
        raise SuppressionNotActive(
            "no browser command line captured; suppression cannot be asserted, "
            "so the run is not valid"
        )

    missing = [f for f in REQUIRED_SUPPRESSION_FLAGS if f not in chrome_cmdline]
    missing += [
        f"--disable-features={f}"
        for f in REQUIRED_DISABLED_FEATURES
        if f not in chrome_cmdline
    ]
    missing += [
        f"--host-resolver-rules MAP {h}"
        for h in REQUIRED_RESOLVER_MAPPINGS
        if h not in chrome_cmdline
    ]
    if missing:
        raise SuppressionNotActive(
            "browser background-traffic suppression is NOT active; missing: "
            + ", ".join(missing)
        )


def _load_link_interference():
    try:
        from . import deps  # noqa: PLC0415 - resolved at call time, not import

        deps.install()
    except Exception:  # noqa: BLE001 - fall through to the plain import below
        pass
    try:
        import pramana_helpers  # noqa: PLC0415 - optional at import time
    except Exception as exc:  # noqa: BLE001
        raise GuardUnavailable(
            "cannot import pramana_helpers to judge background contention: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    fn = getattr(pramana_helpers, "link_interference", None)
    if fn is None:
        raise GuardUnavailable(
            "pramana_helpers has no link_interference(). The CPUC harness "
            "requires the per-bin interference judgement from branch "
            "jenil/pramana/collector-driver-prefetch (commit e0a6b01); it is "
            "not on main yet. Land that first -- this guard must not be "
            "reimplemented here, because two copies of the same judgement "
            "would disagree about which runs are valid."
        )
    return fn


@dataclass
class GuardVerdict:
    ok: bool
    checked: bool
    offenders: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    app_window_s: Optional[list[float]] = None
    note: str = ""

    def as_row(self) -> dict:
        return {
            "guard_ok": self.ok,
            "guard_checked": self.checked,
            "guard_offenders": " | ".join(self.offenders),
            "guard_warnings": " | ".join(self.warnings),
            "guard_app_window_s": (
                f"{self.app_window_s[0]:g}-{self.app_window_s[1]:g}"
                if self.app_window_s and len(self.app_window_s) == 2
                else ""
            ),
            "guard_note": self.note,
        }


def assert_no_background_contention(
    attribution: Any,
    apps: list[str],
    cap_mbps: float,
    strict: bool = True,
) -> GuardVerdict:
    """Judge the capture and, by default, RAISE if a non-app bucket competed.

    `strict=False` exists only for inspecting an archived capture; a collection
    run must leave it True. The plan's requirement is that such a run is
    invalid, so it fails loudly here rather than being written to the CSV with
    a warning nobody reads.
    """
    link_interference = _load_link_interference()
    li = link_interference(
        attribution,
        apps,
        cap_mbps,
        budget_fraction=INTERFERENCE_BUDGET_FRACTION,
        min_bins=INTERFERENCE_MIN_BINS,
    )

    verdict = GuardVerdict(
        ok=bool(li.ok),
        checked=bool(li.checked),
        offenders=list(li.offenders or []),
        warnings=list(li.warnings or []),
        app_window_s=list(li.app_window_s) if li.app_window_s else None,
        note=getattr(li, "note", "") or "",
    )

    # `checked=False` means there was nothing to judge -- an empty capture, or
    # an app that never moved a byte. That is NOT a pass, and must not be
    # recorded as one.
    if not verdict.checked:
        verdict.ok = False
        verdict.note = (
            "interference guard had nothing to judge (no app traffic or empty "
            "capture); not a pass"
        ) + (f"; {verdict.note}" if verdict.note else "")
        if strict:
            raise BackgroundContention(verdict.note)
        return verdict

    if not verdict.ok and strict:
        raise BackgroundContention(
            "non-app traffic competed inside the app's measured window, so this "
            "run is invalid: " + "; ".join(verdict.offenders)
        )
    return verdict


def verdict_from_record(record: dict) -> GuardVerdict:
    """Strict verdict from the `link_interference` block run_direct already wrote.

    run_direct computes the per-bin judgement itself and stores it, so re-deriving
    it here would mean parsing the pcap a second time and risking two answers
    about the same capture. This reads that block and applies the CPUC policy to
    it, which differs from the platform default in one way that matters: the
    platform records a verdict and warns, and here it is fatal.
    """
    li = record.get("link_interference")
    if not isinstance(li, dict):
        return GuardVerdict(
            ok=False,
            checked=False,
            note=(
                "run record carries no link_interference block, so background "
                "contention was never judged; not a pass"
            ),
        )
    v = GuardVerdict(
        ok=bool(li.get("ok")),
        checked=bool(li.get("checked")),
        offenders=list(li.get("offenders") or []),
        warnings=list(li.get("warnings") or []),
        app_window_s=list(li.get("app_window_s") or []) or None,
        note=str(li.get("note") or ""),
    )
    if not v.checked:
        v.ok = False
        v.note = (
            "interference guard had nothing to judge (no app traffic or empty "
            "capture); not a pass"
        ) + (f"; {v.note}" if v.note else "")
    return v


def enforce_record_verdict(record: dict) -> GuardVerdict:
    """As above, but raises -- the hard requirement, applied at the call site."""
    v = verdict_from_record(record)
    if not v.ok:
        raise BackgroundContention(
            "background-traffic guard failed: "
            + ("; ".join(v.offenders) if v.offenders else v.note)
        )
    return v

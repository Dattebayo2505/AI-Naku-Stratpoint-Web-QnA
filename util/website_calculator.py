"""Website design cost calculator (PHP).

A standalone, stdlib-only reimplementation of the pricing calculator at
https://qadra.studio/pricing/web-design-pricing-philippines/#calc. The base
rates match the source calculator; three inputs are dropped from the form:

  * "Pages needing copywriting"      (Scope)
  * "Copywriting revision rounds"    (Revisions)
  * "Security"                       (Speed, security & analytics)

Copywriting is treated as out of scope entirely, so neither the per-page
copy rate nor the copy-revision rate exists here. Security hardening is
likewise not billed as a line item.

Run it:

    python website_calculator.py                 # interactive prompts
    python website_calculator.py --pages 12 --style adv --anim adv
    python website_calculator.py --pages 8 --json

Import it:

    from website_calculator import Selections, estimate
    est = estimate(Selections(pages=8, style="mid"))
    print(est.low, est.high)
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Hardcoded base values
#
# Every rate below is in Philippine pesos and matches the source calculator's
# published numbers. Change them here and nothing else needs to move.
# ---------------------------------------------------------------------------

# --- Scope -----------------------------------------------------------------
STYLE_BASE: dict[str, int] = {
    "basic": 60_000,
    "mid": 150_000,
    "adv": 350_000,
}
STYLE_LABEL: dict[str, str] = {
    "basic": "Basic",
    "mid": "Mid-level",
    "adv": "Advanced",
}

PER_PAGE_RATE = 6_000       # per page

ANIMATION: dict[str, int] = {
    "none": 0,
    "basic": 25_000,
    "adv": 90_000,
}
ANIMATION_LABEL: dict[str, str] = {
    "none": "",
    "basic": "Basic",
    "adv": "Advanced",
}

RESPONSIVE: dict[str, int] = {
    "fluid": 0,
    "custom": 40_000,
}

# --- Revisions -------------------------------------------------------------
DESIGN_REVISION_RATE = 8_000    # per round
MAX_REVISION_ROUNDS = 3

# --- Support & maintenance -------------------------------------------------
MAINTENANCE: list[int] = [
    0,                      # none
    30_000,                 # 6 mo / 30 hrs
    55_000,                 # 6 mo / 60 hrs
    100_000,                # 12 mo / 120 hrs
]
MAINTENANCE_LABEL: list[str] = [
    "None",
    "6 mo / 30 hrs",
    "6 mo / 60 hrs",
    "12 mo / 120 hrs",
]

TRAINING_RATE = 8_000

# --- Speed & analytics -----------------------------------------------------
SPEED: list[int] = [
    0,                      # basic (included)
    15_000,                 # mid-level
    35_000,                 # advanced
]
SPEED_LABEL: list[str] = ["Basic", "Mid-level", "Advanced"]

ANALYTICS_RATE = 6_000      # Google Analytics setup
SEARCH_CONSOLE_RATE = 4_000  # Search Console setup

# --- Presentation ----------------------------------------------------------
RANGE_LOW_FACTOR = 0.90
RANGE_HIGH_FACTOR = 1.15
RANGE_ROUNDING = 1_000      # round the displayed range to the nearest ₱1,000

PHP_PER_USD = 56
USD_ROUNDING = 50           # round the USD figure to the nearest $50

MIN_PAGES = 1
MAX_PAGES = 200


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Selections:
    """One set of calculator answers. Defaults mirror the form's defaults."""

    pages: int = 8
    style: str = "mid"                  # basic | mid | adv
    animation: str = "basic"            # none | basic | adv
    responsive: str = "fluid"           # fluid | custom
    design_revisions: int = 1           # 0..3
    maintenance: int = 0                # index into MAINTENANCE
    training: bool = False
    speed: int = 0                      # index into SPEED
    google_analytics: bool = False
    search_console: bool = False

    def __post_init__(self) -> None:
        if self.style not in STYLE_BASE:
            raise ValueError(f"style must be one of {sorted(STYLE_BASE)}")
        if self.animation not in ANIMATION:
            raise ValueError(f"animation must be one of {sorted(ANIMATION)}")
        if self.responsive not in RESPONSIVE:
            raise ValueError(f"responsive must be one of {sorted(RESPONSIVE)}")
        if not MIN_PAGES <= self.pages <= MAX_PAGES:
            raise ValueError(f"pages must be between {MIN_PAGES} and {MAX_PAGES}")
        if not 0 <= self.design_revisions <= MAX_REVISION_ROUNDS:
            raise ValueError(f"design_revisions must be 0..{MAX_REVISION_ROUNDS}")
        if not 0 <= self.maintenance < len(MAINTENANCE):
            raise ValueError(f"maintenance must be 0..{len(MAINTENANCE) - 1}")
        if not 0 <= self.speed < len(SPEED):
            raise ValueError(f"speed must be 0..{len(SPEED) - 1}")


@dataclass(frozen=True)
class Estimate:
    """An itemized quote plus the low/high range shown to the client."""

    items: list[tuple[str, int]] = field(default_factory=list)
    subtotal: int = 0
    low: int = 0
    high: int = 0

    def as_dict(self) -> dict:
        return {
            "items": [{"label": label, "amount": amount} for label, amount in self.items],
            "subtotal": self.subtotal,
            "low": self.low,
            "high": self.high,
            "usd_low": to_usd(self.low),
            "usd_high": to_usd(self.high),
        }


def _round_to(value: float, step: int) -> int:
    return int(round(value / step) * step)


def to_usd(php: int) -> int:
    """Convert pesos to a deliberately coarse USD figure."""
    return _round_to(php / PHP_PER_USD, USD_ROUNDING)


def peso(amount: int) -> str:
    return f"₱{amount:,}"


def estimate(sel: Selections) -> Estimate:
    """Price a set of selections into an itemized :class:`Estimate`."""
    items: list[tuple[str, int]] = []

    def add(label: str, amount: int) -> None:
        if amount > 0:
            items.append((label, amount))

    # Scope
    add(f"{STYLE_LABEL[sel.style]} design base", STYLE_BASE[sel.style])
    add(f"Pages (×{sel.pages})", sel.pages * PER_PAGE_RATE)
    add(f"{ANIMATION_LABEL[sel.animation]} animation".strip(), ANIMATION[sel.animation])
    if sel.responsive == "custom":
        add("Custom per-device design", RESPONSIVE["custom"])

    # Revisions
    add(
        f"Design revisions (×{sel.design_revisions})",
        sel.design_revisions * DESIGN_REVISION_RATE,
    )

    # Support & maintenance
    add(
        f"Support & maintenance ({MAINTENANCE_LABEL[sel.maintenance]})",
        MAINTENANCE[sel.maintenance],
    )
    add("Management training", TRAINING_RATE if sel.training else 0)

    # Speed & analytics
    add(f"{SPEED_LABEL[sel.speed]} speed optimization", SPEED[sel.speed])
    add("Google Analytics setup", ANALYTICS_RATE if sel.google_analytics else 0)
    add("Search Console setup", SEARCH_CONSOLE_RATE if sel.search_console else 0)

    subtotal = sum(amount for _, amount in items)
    return Estimate(
        items=items,
        subtotal=subtotal,
        low=_round_to(subtotal * RANGE_LOW_FACTOR, RANGE_ROUNDING),
        high=_round_to(subtotal * RANGE_HIGH_FACTOR, RANGE_ROUNDING),
    )


def format_report(est: Estimate, width: int = 52) -> str:
    """Render an estimate as a plain-text quote."""
    lines = ["", "Estimated project cost".upper(), "=" * width]
    for label, amount in est.items:
        lines.append(f"{label:<{width - 14}}{peso(amount):>14}")
    lines.append("-" * width)
    lines.append(f"{'Subtotal':<{width - 14}}{peso(est.subtotal):>14}")
    lines.append("=" * width)
    lines.append(f"  {peso(est.low)} – {peso(est.high)}")
    lines.append(f"  ≈ ${to_usd(est.low):,} – ${to_usd(est.high):,}")
    lines.append("")
    lines.append("A ballpark based on your selections. Final pricing")
    lines.append("depends on the specifics of your build.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Interactive prompts
# ---------------------------------------------------------------------------

_DEFAULTS = Selections()


def _ask_int(prompt: str, default: int, low: int, high: int) -> int:
    while True:
        raw = input(f"{prompt} [{default}]: ").strip()
        if not raw:
            return default
        try:
            value = int(raw)
        except ValueError:
            print(f"  Enter a whole number between {low} and {high}.")
            continue
        if low <= value <= high:
            return value
        print(f"  Enter a whole number between {low} and {high}.")


def _ask_choice(prompt: str, options: list[tuple[str, str]], default: str) -> str:
    """`options` is a list of (value, label) pairs; returns the chosen value."""
    print(prompt)
    for index, (value, label) in enumerate(options, start=1):
        marker = " (default)" if value == default else ""
        print(f"  {index}. {label}{marker}")
    while True:
        raw = input("  choice: ").strip()
        if not raw:
            return default
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1][0]
        print(f"  Enter 1–{len(options)}.")


def _ask_yes_no(prompt: str, default: bool) -> bool:
    hint = "Y/n" if default else "y/N"
    while True:
        raw = input(f"{prompt} [{hint}]: ").strip().lower()
        if not raw:
            return default
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        print("  Enter y or n.")


def prompt_selections() -> Selections:
    print("\n-- Scope --")
    pages = _ask_int("Number of web pages", _DEFAULTS.pages, MIN_PAGES, MAX_PAGES)
    style = _ask_choice(
        "Style of design",
        [(key, STYLE_LABEL[key]) for key in ("basic", "mid", "adv")],
        _DEFAULTS.style,
    )
    animation = _ask_choice(
        "Animation capability",
        [("none", "None"), ("basic", "Basic"), ("adv", "Advanced")],
        _DEFAULTS.animation,
    )
    responsive = _ask_choice(
        "Responsive design",
        [("fluid", "Fluid system"), ("custom", "Custom per device")],
        _DEFAULTS.responsive,
    )

    print("\n-- Revisions --")
    design_revisions = _ask_int(
        "Design revision rounds", _DEFAULTS.design_revisions, 0, MAX_REVISION_ROUNDS
    )

    print("\n-- Support & maintenance --")
    maintenance = int(
        _ask_choice(
            "Support / maintenance",
            [(str(i), label) for i, label in enumerate(MAINTENANCE_LABEL)],
            str(_DEFAULTS.maintenance),
        )
    )
    training = _ask_yes_no("Website management training?", _DEFAULTS.training)

    print("\n-- Speed & analytics --")
    speed = int(
        _ask_choice(
            "Speed optimization",
            [(str(i), label) for i, label in enumerate(SPEED_LABEL)],
            str(_DEFAULTS.speed),
        )
    )
    google_analytics = _ask_yes_no("Google Analytics setup?", _DEFAULTS.google_analytics)
    search_console = _ask_yes_no("Search Console setup?", _DEFAULTS.search_console)

    return Selections(
        pages=pages,
        style=style,
        animation=animation,
        responsive=responsive,
        design_revisions=design_revisions,
        maintenance=maintenance,
        training=training,
        speed=speed,
        google_analytics=google_analytics,
        search_console=search_console,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="website_calculator",
        description="Estimate website design cost in PHP. Run with no flags for prompts.",
    )
    parser.add_argument("--pages", type=int, default=_DEFAULTS.pages,
                        help=f"number of web pages ({MIN_PAGES}-{MAX_PAGES})")
    parser.add_argument("--style", choices=sorted(STYLE_BASE), default=_DEFAULTS.style,
                        help="style of design")
    parser.add_argument("--anim", dest="animation", choices=sorted(ANIMATION),
                        default=_DEFAULTS.animation, help="animation capability")
    parser.add_argument("--responsive", choices=sorted(RESPONSIVE),
                        default=_DEFAULTS.responsive, help="responsive design approach")
    parser.add_argument("--design-revisions", type=int, default=_DEFAULTS.design_revisions,
                        choices=range(MAX_REVISION_ROUNDS + 1), metavar="0-3",
                        help="design revision rounds")
    parser.add_argument("--maintenance", type=int, default=_DEFAULTS.maintenance,
                        choices=range(len(MAINTENANCE)), metavar="0-3",
                        help="0=none, 1=6mo/30hrs, 2=6mo/60hrs, 3=12mo/120hrs")
    parser.add_argument("--training", action="store_true",
                        help="include website management training")
    parser.add_argument("--speed", type=int, default=_DEFAULTS.speed,
                        choices=range(len(SPEED)), metavar="0-2",
                        help="0=basic, 1=mid-level, 2=advanced")
    parser.add_argument("--ga", dest="google_analytics", action="store_true",
                        help="include Google Analytics setup")
    parser.add_argument("--gsc", dest="search_console", action="store_true",
                        help="include Search Console setup")
    parser.add_argument("--json", action="store_true",
                        help="emit the estimate as JSON instead of a text report")
    return parser


def _force_utf8_stdout() -> None:
    """Windows consoles default to cp1252, which cannot encode ₱ or –."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):  # already detached or non-reconfigurable
                pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdout()
    argv = sys.argv[1:] if argv is None else argv
    parser = build_parser()

    if not argv:
        print("Website design cost calculator — press Enter to accept a default.")
        selections = prompt_selections()
        as_json = False
    else:
        args = parser.parse_args(argv)
        as_json = args.json
        selections = Selections(
            pages=args.pages,
            style=args.style,
            animation=args.animation,
            responsive=args.responsive,
            design_revisions=args.design_revisions,
            maintenance=args.maintenance,
            training=args.training,
            speed=args.speed,
            google_analytics=args.google_analytics,
            search_console=args.search_console,
        )

    try:
        est = estimate(selections)
    except ValueError as exc:
        parser.error(str(exc))
        return 2

    if as_json:
        print(json.dumps(est.as_dict(), indent=2))
    else:
        print(format_report(est))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.")
        raise SystemExit(130) from None

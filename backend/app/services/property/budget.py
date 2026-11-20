"""Guest budget semantics for recommend_properties -- pure, no I/O.

A guest's budget is an amount PLUS a basis ("per night" vs "for the whole
stay"), never a bare number. Confirmed live 2026-09-25: a guest said "₹7000
per night", recommend_properties only had a single ambiguous `budget` float,
and nothing in the pipeline could tell a nightly ceiling from a total one.
Everything numeric about the budget (nightly ceiling, stay-total ceiling,
whether a property fits) is decided here, deterministically -- the LLM only
relays the guest's amount and the basis words they used.

Eligibility is judged on the property's APPLICABLE stay price
(pricing_engine.evaluate_stay_prices: cached live rates, base_price, and the
host's automatic length-of-stay offer), never on the stored base_price alone
-- this module only compares numbers it's handed.
"""

import re
from dataclasses import dataclass
from typing import Literal

BudgetBasis = Literal["per_night", "total_stay", "unspecified"]
ClarificationNeeded = Literal["budget_basis", "stay_length", "currency"]

SUPPORTED_CURRENCY = "INR"

# Existing product behavior (filter_builder's `base_price <= budget * 1.15`,
# predating explicit budget semantics): properties up to 15% over the
# guest's ceiling are still surfaced -- but now only as clearly-labelled
# near-budget alternatives, never mixed in as if they were within budget.
NEAR_BUDGET_STRETCH = 1.15

# Upsell ("I have another option, a bit steeper"): the most a single
# better-but-pricier property may exceed the guest's budget by to still be
# pitched. Beyond this it stops being a stretch the guest might say yes to.
UPSELL_MAX_OVER_BUDGET = 1.5

# "Around 4k" / "about 4k" / "roughly 4k" is a band, not a ceiling: the guest
# is not asking for "4,000 or less" (a ₹2,500 place is not what they mean) and
# not for "anything up to 5,000" either. +/-15% matches NEAR_BUDGET_STRETCH so
# there is one tolerance in the product, not two.
AROUND_TOLERANCE = 0.15

# A property just under a stated range's lower bound ("10-15k", it costs 9.2k)
# is the mirror of NEAR_BUDGET_STRETCH: never a match, but a fair labelled
# alternative when nothing is inside the range.
NEAR_RANGE_FLOOR_STRETCH = 0.85

_BASIS_ALIASES: dict[str, BudgetBasis] = {
    "per night": "per_night",
    "pernight": "per_night",
    "nightly": "per_night",
    "night": "per_night",
    "total stay": "total_stay",
    "total": "total_stay",
    "whole stay": "total_stay",
    "entire stay": "total_stay",
    "overall": "total_stay",
}


def coerce_budget_basis(value: str | None) -> BudgetBasis:
    """The tool arg is a plain string (pipecat renders a Literal type hint
    as an untyped schema, so the LLM gets no enum to obey) -- map whatever
    it sent onto the three real values, never raising mid-call. Anything
    unrecognized is "unspecified", which is the safe state: it can trigger a
    clarification question, never a silent guess."""
    if not value:
        return "unspecified"
    return _BASIS_ALIASES.get(value.strip().lower().replace("-", " ").replace("_", " "), "unspecified")


def canonical_budget_band(
    amount: float | None,
    min_amount: float | None = None,
    approximate: bool = False,
) -> tuple[float | None, float | None, bool]:
    """The guest's stated figures -> (max, min, approximate), cleaned up once
    so every caller (tool wrapper, slots, filter) agrees on what was meant.

    - max (`amount`) is the single figure for "under 10k" / "around 4k" and the
      UPPER figure of a range ("10-15k" -> 15000); `min` is only ever set for a
      real range, and is None otherwise.
    - A range given the wrong way round ("15 to 10k") is swapped; a "range"
      whose two ends are equal is just a single budget.
    - "around" only makes sense for one figure: an explicit range already says
      exactly what the guest will accept, so approximate is dropped for it.
    - A min with no max is not a budget at all (nothing to compare it to), and
      is dropped rather than guessed at."""
    if amount is None or amount <= 0:
        return None, None, False
    amount = float(amount)
    low = float(min_amount) if min_amount is not None and min_amount > 0 else None
    if low is not None and low > amount:
        amount, low = low, amount
    if low is not None and low == amount:
        low = None
    return amount, low, bool(approximate) and low is None


def _band(amount: float, min_amount: float | None, approximate: bool) -> tuple[float, float | None]:
    """(ceiling, floor) in the guest's own basis."""
    if approximate:
        return round(amount * (1 + AROUND_TOLERANCE), 2), round(amount * (1 - AROUND_TOLERANCE), 2)
    return amount, min_amount


@dataclass(frozen=True)
class BudgetConstraint:
    # `amount` is the figure as the guest said it: the ceiling for "under X",
    # the centre for "around X", the UPPER end for a range. The real bounds
    # the filter applies are ceiling / floor below (in the guest's own basis).
    amount: float
    basis: BudgetBasis
    nights: int | None
    currency: str = SUPPORTED_CURRENCY
    # None whenever the constraint can't be applied yet (see clarification).
    # Both are the CEILING (including the "around" tolerance), expressed per
    # night and for the whole stay.
    max_nightly_rate: float | None = None
    max_total_rate: float | None = None
    clarification: ClarificationNeeded | None = None
    # True when the guest never said per night vs total, the one allowed
    # clarification was already asked, and per night was assumed (the
    # common reading) rather than asking again.
    basis_assumed: bool = False
    # Lower end of an explicit range ("10-15k"), in the guest's own basis;
    # None for every non-range budget -- the existing single-ceiling behaviour.
    min_amount: float | None = None
    # "Around X": a +/-AROUND_TOLERANCE band centred on `amount`.
    approximate: bool = False
    min_nightly_rate: float | None = None

    @property
    def is_applicable(self) -> bool:
        return self.clarification is None and self.max_nightly_rate is not None

    @property
    def is_range(self) -> bool:
        return self.min_amount is not None

    @property
    def ceiling(self) -> float:
        """Highest acceptable figure in the guest's own basis."""
        return _band(self.amount, self.min_amount, self.approximate)[0]

    @property
    def floor(self) -> float | None:
        """Lowest acceptable figure in the guest's own basis (None = no lower bound)."""
        return _band(self.amount, self.min_amount, self.approximate)[1]

    def _scaled(self, per_night: float, total: float | None) -> tuple[float, float | None, float]:
        """(value, floor, ceiling) on one scale: the stay total when the guest
        gave a total budget (compared directly, never via a divided-down
        nightly figure, so ₹14,000/3 nights can't lose a paisa to rounding),
        otherwise the effective nightly rate."""
        if self.basis == "total_stay" and total is not None:
            return total, self.floor, self.ceiling
        return per_night, self.min_nightly_rate, self.max_nightly_rate

    def position(self, per_night: float, total: float | None = None) -> Literal["within", "above", "below", "unknown"]:
        """Where this price sits against the guest's budget, on the applicable
        price. A property with no usable price (<= 0) is "unknown" -- its fit
        is unknown, so it never counts as within."""
        if not self.is_applicable or per_night <= 0:
            return "unknown"
        value, low, high = self._scaled(per_night, total)
        if value > high:
            return "above"
        if low is not None and value < low:
            return "below"
        return "within"

    def fits(self, per_night: float, total: float | None = None) -> bool:
        """Within budget on the applicable price: at or under the ceiling and,
        for a range / "around", at or over the floor."""
        return self.position(per_night, total) == "within"

    def over_but_within(self, factor: float, per_night: float, total: float | None = None) -> bool:
        """Over the ceiling, but by no more than `factor` x the ceiling (in the
        guest's own basis). A price under a range's floor is never "over"."""
        if self.position(per_night, total) != "above":
            return False
        value, _, high = self._scaled(per_night, total)
        return value <= high * factor

    def within_stretch(self, per_night: float, total: float | None = None) -> bool:
        """Over budget but within NEAR_BUDGET_STRETCH of it -- eligible
        only as a clearly-labelled near-budget alternative."""
        return self.over_but_within(NEAR_BUDGET_STRETCH, per_night, total)

    def just_below_range(self, per_night: float, total: float | None = None) -> bool:
        """Under a range's / "around" band's floor, but not by much -- eligible
        only as a clearly-labelled alternative, never as a match. Always False
        for a plain "under X" budget (no floor: cheaper is simply a fit)."""
        if self.position(per_night, total) != "below":
            return False
        value, low, _ = self._scaled(per_night, total)
        return low is not None and value >= low * NEAR_RANGE_FLOOR_STRETCH

    def over_budget_by(self, per_night: float, total: float | None = None) -> float:
        """How far over the ceiling, in the guest's own basis (per night, or
        for the whole stay) -- for labelling a near-budget alternative honestly."""
        if per_night <= 0 or not self.is_applicable:
            return 0.0
        value, _, high = self._scaled(per_night, total)
        return max(0.0, value - high)

    def under_budget_by(self, per_night: float, total: float | None = None) -> float:
        """How far under the floor, in the guest's own basis."""
        if per_night <= 0 or not self.is_applicable:
            return 0.0
        value, low, _ = self._scaled(per_night, total)
        return max(0.0, low - value) if low is not None else 0.0


def normalize_budget(
    amount: float | None,
    basis: BudgetBasis | str | None,
    nights: int | None,
    currency: str | None = SUPPORTED_CURRENCY,
    assume_per_night: bool = False,
    min_amount: float | None = None,
    approximate: bool = False,
) -> BudgetConstraint | None:
    """amount + basis + stay length -> the canonical ceilings. None means no
    budget was given at all (no filter). assume_per_night: the basis
    question was already asked once this call and still isn't answered --
    take the common per-night reading instead of asking again. Never infers total_stay
    just because nights is known, and never infers per_night just because the
    number "looks nightly" -- an unspecified basis is only resolvable when
    the two readings coincide (a 1-night stay).

    min_amount: the LOWER end when the guest gave a range ("10 to 15k" ->
    amount=15000, min_amount=10000), in the same basis as `amount`.
    approximate: "around/about X" -- a +/-AROUND_TOLERANCE band around
    `amount`. Both leave a plain single budget exactly as it was."""
    amount, min_amount, approximate = canonical_budget_band(amount, min_amount, approximate)
    if amount is None:
        return None
    resolved_basis = coerce_budget_basis(basis)
    resolved_nights = nights if nights is not None and nights > 0 else None
    resolved_currency = (currency or SUPPORTED_CURRENCY).strip().upper()

    def _constraint(basis_: BudgetBasis = resolved_basis, **kwargs) -> BudgetConstraint:
        return BudgetConstraint(
            amount=amount,
            basis=basis_,
            nights=resolved_nights,
            currency=resolved_currency,
            min_amount=min_amount,
            approximate=approximate,
            **kwargs,
        )

    if resolved_currency != SUPPORTED_CURRENCY:
        # Portfolio prices are INR only and there's no FX source here --
        # converting would be exactly the LLM-guessed arithmetic this module
        # exists to prevent.
        return _constraint(clarification="currency")

    # The guest's figures as a ceiling / floor, before they are split into
    # nightly and total.
    ceiling, floor = _band(amount, min_amount, approximate)

    if resolved_basis == "unspecified":
        if resolved_nights == 1:
            # Per night and total are the same number for a 1-night stay.
            return _constraint(max_nightly_rate=ceiling, max_total_rate=ceiling, min_nightly_rate=floor)
        if resolved_nights is None:
            # Ask the stay length first, not the basis: the flow needs it
            # anyway, and once known it may make the basis question moot
            # (1 night) -- never two budget questions where one will do.
            return _constraint(clarification="stay_length")
        if not assume_per_night:
            return _constraint(clarification="budget_basis")
        return _constraint(
            "per_night",
            max_nightly_rate=ceiling,
            max_total_rate=ceiling * resolved_nights,
            min_nightly_rate=floor,
            basis_assumed=True,
        )

    if resolved_basis == "per_night":
        return _constraint(
            max_nightly_rate=ceiling,
            max_total_rate=ceiling * resolved_nights if resolved_nights else None,
            min_nightly_rate=floor,
        )

    # total_stay
    if resolved_nights is None:
        return _constraint(clarification="stay_length")
    return _constraint(
        max_nightly_rate=ceiling / resolved_nights,
        max_total_rate=ceiling,
        min_nightly_rate=floor / resolved_nights if floor is not None else None,
    )


# --- Deterministic basis backstop from the guest's own words -------------
#
# The LLM is instructed to pass budget_basis, but prompt wording alone has
# repeatedly proven unreliable in this codebase, so the tool wrapper also
# reads the guest's recent transcript when the model leaves the basis unset.
# Only unambiguous phrasings count; an utterance matching BOTH readings (e.g.
# "7k a night, so 14k total") is treated as no signal at all.

# Money-shaped only (3+ digits, or a k/thousand multiplier) -- "we are total
# 4 people" is a guest count, not a budget.
_AMOUNT = r"(?:\d[\d,]{2,}(?:\.\d+)?|\d+(?:\.\d+)?\s*(?:k|thousand|hazaa?r)\b)\s*(?:rs\.?|rupees?|inr|/-)?\s*"

_PER_NIGHT_RE = re.compile(
    r"\bper[\s-]?night\b"
    r"|\bnightly\b"
    r"|\b(?:each|every)\s+night\b"
    rf"|{_AMOUNT}(?:a|an|/)\s*night\b"
    r"|\bper\s+raat\b|\bek\s+raat\s+ka\b"
    r"|प्रति\s*रात|पर\s*नाइट|एक\s*रात\s*का",
    re.IGNORECASE,
)

_TOTAL_STAY_RE = re.compile(
    r"\b(?:total|overall)\s+budget\b"
    r"|\bbudget\b[^.?!]{0,20}\b(?:total|overall|in\s+all)\b"
    rf"|\b(?:total|overall)\s+(?:of\s+|is\s+|around\s+|about\s+)?(?:rs\.?\s*|₹\s*|inr\s*)?{_AMOUNT}"
    rf"|{_AMOUNT}(?:in\s+)?total\b"
    r"|\b(?:whole|entire|full)\s+(?:stay|trip)\b"
    r"|\baltogether\b|\ball\s+inclusive\b"
    r"|कुल\s*बजट|टोटल",
    re.IGNORECASE,
)


_AMOUNT_RE = re.compile(_AMOUNT, re.IGNORECASE)


def mentions_amount(text: str | None) -> bool:
    return bool(text and _AMOUNT_RE.search(text))


def infer_budget_basis(text: str | None) -> BudgetBasis | None:
    """None = no unambiguous basis signal in this text (including when it
    signals both). Never returns "unspecified" -- the caller decides what an
    absent signal means."""
    if not text:
        return None
    per_night = bool(_PER_NIGHT_RE.search(text))
    total = bool(_TOTAL_STAY_RE.search(text))
    if per_night == total:
        return None
    return "per_night" if per_night else "total_stay"

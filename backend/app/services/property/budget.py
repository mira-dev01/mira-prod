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


@dataclass(frozen=True)
class BudgetConstraint:
    amount: float
    basis: BudgetBasis
    nights: int | None
    currency: str = SUPPORTED_CURRENCY
    # None whenever the constraint can't be applied yet (see clarification).
    max_nightly_rate: float | None = None
    max_total_rate: float | None = None
    clarification: ClarificationNeeded | None = None
    # True when the guest never said per night vs total, the one allowed
    # clarification was already asked, and per night was assumed (the
    # common reading) rather than asking again.
    basis_assumed: bool = False

    @property
    def is_applicable(self) -> bool:
        return self.clarification is None and self.max_nightly_rate is not None

    def fits(self, per_night: float, total: float | None = None) -> bool:
        """Strictly within budget, on the applicable price. total_stay
        compares the stay total directly (never a divided-down nightly
        ceiling, so ₹14,000/3 nights can't lose a paisa to rounding);
        per_night compares the effective nightly rate. A property with no
        usable price (<= 0) never fits -- its fit is unknown."""
        if not self.is_applicable or per_night <= 0:
            return False
        if self.basis == "total_stay" and total is not None:
            return total <= self.amount
        return per_night <= self.max_nightly_rate

    def over_but_within(self, factor: float, per_night: float, total: float | None = None) -> bool:
        """Over budget, but by no more than `factor` x the budget (in the
        guest's own basis)."""
        if not self.is_applicable or per_night <= 0 or self.fits(per_night, total):
            return False
        if self.basis == "total_stay" and total is not None:
            return total <= self.amount * factor
        return per_night <= self.max_nightly_rate * factor

    def within_stretch(self, per_night: float, total: float | None = None) -> bool:
        """Over budget but within NEAR_BUDGET_STRETCH of it -- eligible
        only as a clearly-labelled near-budget alternative."""
        return self.over_but_within(NEAR_BUDGET_STRETCH, per_night, total)

    def over_budget_by(self, per_night: float, total: float | None = None) -> float:
        """How far over, in the guest's own basis (per night, or for the
        whole stay) -- for labelling a near-budget alternative honestly."""
        if self.basis == "total_stay" and total is not None:
            return max(0.0, total - self.amount)
        return max(0.0, per_night - (self.max_nightly_rate or 0.0))


def normalize_budget(
    amount: float | None,
    basis: BudgetBasis | str | None,
    nights: int | None,
    currency: str | None = SUPPORTED_CURRENCY,
    assume_per_night: bool = False,
) -> BudgetConstraint | None:
    """amount + basis + stay length -> the canonical ceilings. None means no
    budget was given at all (no filter). assume_per_night: the basis
    question was already asked once this call and still isn't answered --
    take the common per-night reading instead of asking again. Never infers total_stay just
    because nights is known, and never infers per_night just because the
    number "looks nightly" -- an unspecified basis is only resolvable when
    the two readings coincide (a 1-night stay)."""
    if amount is None or amount <= 0:
        return None
    resolved_basis = coerce_budget_basis(basis)
    resolved_nights = nights if nights is not None and nights > 0 else None
    resolved_currency = (currency or SUPPORTED_CURRENCY).strip().upper()
    amount = float(amount)

    def _constraint(**kwargs) -> BudgetConstraint:
        return BudgetConstraint(
            amount=amount, basis=resolved_basis, nights=resolved_nights, currency=resolved_currency, **kwargs
        )

    if resolved_currency != SUPPORTED_CURRENCY:
        # Portfolio prices are INR only and there's no FX source here --
        # converting would be exactly the LLM-guessed arithmetic this module
        # exists to prevent.
        return _constraint(clarification="currency")

    if resolved_basis == "unspecified":
        if resolved_nights == 1:
            # Per night and total are the same number for a 1-night stay.
            return _constraint(max_nightly_rate=amount, max_total_rate=amount)
        if resolved_nights is None:
            # Ask the stay length first, not the basis: the flow needs it
            # anyway, and once known it may make the basis question moot
            # (1 night) -- never two budget questions where one will do.
            return _constraint(clarification="stay_length")
        if not assume_per_night:
            return _constraint(clarification="budget_basis")
        return BudgetConstraint(
            amount=amount,
            basis="per_night",
            nights=resolved_nights,
            currency=resolved_currency,
            max_nightly_rate=amount,
            max_total_rate=amount * resolved_nights,
            basis_assumed=True,
        )

    if resolved_basis == "per_night":
        return _constraint(
            max_nightly_rate=amount,
            max_total_rate=amount * resolved_nights if resolved_nights else None,
        )

    # total_stay
    if resolved_nights is None:
        return _constraint(clarification="stay_length")
    return _constraint(max_nightly_rate=amount / resolved_nights, max_total_rate=amount)


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

"""Choosing how much model a task is worth, before the task is attempted.

Until now a call's model came from its :class:`~core.config.Purpose` alone, so
every developer service — a three-file worker and a thirty-endpoint API — was
written by the same model at the same price. The architecture already says which
is which: it names the files each service must have, the endpoints it exposes,
the models it owns and the services it leans on. This turns that into a decision.

The policy in one line: *use the cheapest tier appropriate to the task's
estimated difficulty*. Estimated, deliberately. This is a deterministic reading
of what the architect wrote down, not a claim about which model is cheapest or
best for a given job — nothing here knows that, and pretending otherwise would be
the kind of number that looks authoritative and is not.

What this module is
-------------------

A pure function of integers and settings. It reads no environment, builds no
clients, makes no calls, touches no budget, holds no credential and keeps no
state between decisions. It answers "which tier, and why" and hands that to the
registry, which owns everything else — model construction, the account pool,
metering and retries.

What it deliberately does not use
---------------------------------

**Prior failures, as a difficulty term.** The tempting shortcut is "this service
has failed twice, so score it harder". That would make the *difficulty* of a task
depend on how a previous attempt went, and difficulty is a property of the work.
Failure is handled where it belongs instead: `RouteSignal.escalations` raises the
tier by a whole step, decided by `agents/attribution.py:escalation_reason` from
real verification evidence, and recorded separately so a score and an escalation
never blur into one number. The four counts below stay fixed across every attempt
at a service, which is what makes the difficulty half of the decision
reproducible.

**History.** No learning, no per-model quality estimates, no adaptation. Given
the same architecture, this returns the same plan today and next month, which is
what makes it possible to measure whether it was a good idea.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from core.config import Purpose, Settings

# What each declaration in the architecture contributes to a service's estimated
# difficulty. Constants rather than settings: these define what the word
# "difficulty" means here, which is a design decision to argue with in review
# rather than a dial to turn per deployment. The thresholds those scores are
# compared against *are* configurable, because where the lines fall depends on
# what a given team builds.
#
# Files weigh most because they are the closest thing the architecture has to a
# measure of how much code must be written. Endpoints and data models weigh less
# each but there are usually more of them, and a service dense in either writes
# far more than its file count suggests. A dependency on another service weighs
# as much as a file: it is the one input that measures coupling rather than
# volume, and getting an integration wrong is the expensive kind of wrong.
FILE_WEIGHT = 2
ENDPOINT_WEIGHT = 1
DATA_MODEL_WEIGHT = 1
DEPENDENCY_WEIGHT = 2


class ModelTier(StrEnum):
    """How much model a task is judged to be worth.

    Three, and only three. A finer scale would need evidence to justify the extra
    boundaries, and there is none yet.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# Weakest first. The order is the policy: "the strongest tier no stronger than
# the difficulty" and "step down when nothing fits" both read off this list.
TIER_ORDER: tuple[ModelTier, ...] = (ModelTier.LOW, ModelTier.MEDIUM, ModelTier.HIGH)

# Only the developer path is routed in this phase. It is the one place the
# architecture exposes per-task structure rich enough to score, and inventing a
# difficulty for a PRD from the length of a sentence would be a guess dressed as
# a measurement. Every other purpose keeps exactly the model it has today.
ROUTABLE_PURPOSES: frozenset[Purpose] = frozenset({Purpose.HEAVY})


@dataclass(frozen=True)
class RouteSignal:
    """What is deterministically known about a task before it is attempted.

    The difficulty signal: counts taken straight from the architecture contract,
    which is itself derived from the document a human approved. Every field
    defaults to zero, so a signal built from an architecture that says nothing
    scores nothing and lands in the cheapest tier — which is the honest answer
    for a task nobody described.
    """

    key_files: int = 0
    endpoints: int = 0
    data_models: int = 0
    dependencies: int = 0

    # How many times verification has already said this task needs a stronger
    # model, and why. Kept out of the score entirely: difficulty is a property of
    # the work and must not move because an attempt went badly. This raises the
    # tier by whole steps afterwards, which keeps the two decisions legible apart.
    escalations: int = 0
    escalation_reason: str = ""

    @property
    def score(self) -> int:
        """The weighted difficulty score. Integers throughout, so it is readable."""
        return (
            FILE_WEIGHT * max(0, self.key_files)
            + ENDPOINT_WEIGHT * max(0, self.endpoints)
            + DATA_MODEL_WEIGHT * max(0, self.data_models)
            + DEPENDENCY_WEIGHT * max(0, self.dependencies)
        )

    @property
    def is_empty(self) -> bool:
        """True when the architecture said nothing measurable about this task."""
        return self.score == 0

    def explain(self) -> str:
        """The arithmetic, so a tier is never a number nobody can check."""
        parts = [
            f"{self.key_files} file(s)×{FILE_WEIGHT}",
            f"{self.endpoints} endpoint(s)×{ENDPOINT_WEIGHT}",
            f"{self.data_models} model(s)×{DATA_MODEL_WEIGHT}",
            f"{self.dependencies} dependency(ies)×{DEPENDENCY_WEIGHT}",
        ]
        return f"{' + '.join(parts)} = {self.score}"


@dataclass(frozen=True)
class RoutePlan:
    """The decision, and enough of its reasoning to argue with.

    ``tier`` is ``None`` when routing did not apply — the flag is off, the purpose
    is not routable, or nothing was known about the task. The registry reads that
    as "resolve the model exactly as you always have", which is what keeps the
    default path untouched.

    ``difficulty`` is the tier the score implied and ``tier`` is the one actually
    used. They differ only when the implied tier had no model configured or no
    resource that could hold the call, and keeping both is what makes a stepped
    down route visible afterwards rather than looking like a misjudged score.
    """

    purpose: Purpose
    tier: ModelTier | None = None
    difficulty: ModelTier | None = None
    score: int = 0
    reason: str = ""
    escalations: int = 0
    escalation_reason: str = ""

    @property
    def routed(self) -> bool:
        return self.tier is not None

    @property
    def escalated(self) -> bool:
        """Did verification evidence raise this above what the score asked for?"""
        return self.escalations > 0 and self.tier is not None

    @property
    def stepped_down(self) -> bool:
        """Did the budget or the configuration force a weaker tier than the score?"""
        return (
            self.tier is not None
            and self.difficulty is not None
            and TIER_ORDER.index(self.tier) < TIER_ORDER.index(self.difficulty)
        )

    def describe(self) -> str:
        """A one-line, non-secret account of the decision, for the run log."""
        if not self.routed:
            return f"{self.purpose.value}: not routed ({self.reason})"
        line = f"{self.purpose.value}: {self.tier.value} tier, score {self.score}"
        if self.escalated:
            line += f" (escalated from {self.difficulty.value}: {self.escalation_reason})"
        elif self.stepped_down:
            line += f" (stepped down from {self.difficulty.value})"
        return f"{line} — {self.reason}"


def difficulty_for(signal: RouteSignal, settings: Settings) -> ModelTier:
    """Which tier a score falls in.

    Two thresholds, three bands, no interpolation. The middle band is deliberately
    the widest: an ordinary service should be ordinary, and a policy that called
    most work HIGH would spend more than the static one it replaced while
    claiming to be cost-aware.
    """
    score = signal.score
    if score < settings.routing_low_max_score:
        return ModelTier.LOW
    if score < settings.routing_high_min_score:
        return ModelTier.MEDIUM
    return ModelTier.HIGH


def next_tier(tier: ModelTier) -> ModelTier | None:
    """One step up the ladder, or ``None`` at the top.

    One step, never two: an escalation is a response to a single piece of
    evidence, and jumping past a tier would spend the top of the range before
    anything established that the middle of it was not enough.
    """
    index = TIER_ORDER.index(tier)
    return TIER_ORDER[index + 1] if index + 1 < len(TIER_ORDER) else None


def escalate(tier: ModelTier, steps: int) -> tuple[ModelTier, int]:
    """Raise a tier by up to ``steps``, and say how many actually applied.

    Applied rather than requested, because HIGH has nowhere to go: a service
    already at the top stays there and reports zero escalations, so nothing
    downstream reads it as having been upgraded when it was not.

    Only ever upward. A failure never lowers a tier — the one thing it certainly
    is not evidence for is that a weaker model would have done better.
    """
    applied = 0
    for _ in range(max(0, steps)):
        stronger = next_tier(tier)
        if stronger is None:
            break
        tier = stronger
        applied += 1
    return tier, applied


def plan_for(
    purpose: Purpose,
    signal: RouteSignal | None,
    settings: Settings,
    eligible: frozenset[ModelTier] | None = None,
) -> RoutePlan:
    """Decide which tier should serve this call.

    ``eligible`` is the set of tiers the registry found a configured model *and* a
    resource with room for. It is passed in rather than discovered here because
    reading a budget is not this module's business — the policy states a
    preference and the caller supplies the facts.

    The rule, in order:

    1. Not routing? Say so and change nothing.
    2. Score the signal; that is the difficulty.
    3. Take the strongest eligible tier *no stronger than* the difficulty. Never
       stronger — the objective is the cheapest appropriate model, so a cheap task
       is never promoted because an expensive tier happens to be free.
    4. If nothing at or below the difficulty is eligible, keep the difficulty tier
       and let the budget refuse the call exactly as it does today. Silently
       downgrading past the point of usefulness would hide the real problem.
    """
    if not settings.routing_enabled:
        return RoutePlan(purpose=purpose, reason="routing is disabled")

    if purpose not in ROUTABLE_PURPOSES:
        return RoutePlan(purpose=purpose, reason=f"{purpose.value} is not routed")

    if signal is None:
        return RoutePlan(purpose=purpose, reason="the caller described no task")

    if signal.is_empty:
        return RoutePlan(
            purpose=purpose, reason="the architecture describes nothing measurable"
        )

    difficulty = difficulty_for(signal, settings)
    score = signal.score

    # Escalation is applied to the difficulty tier, and then the result stands.
    # It is not offered to the eligibility check below, deliberately: stepping an
    # escalated route back down would put the work in front of the very model
    # whose output was just judged inadequate, and would turn a full window into
    # evidence about model quality. If the stronger tier has no room, the budget
    # waits for it exactly as it would for any other call.
    #
    # Clamped here as well as where escalations are counted. The caller that
    # produces the count already respects the configured maximum, but a policy
    # that would honour any number it was handed makes that maximum true only by
    # convention -- and a limit on spending should hold wherever the decision is
    # actually made, not only where it is currently made.
    requested = min(max(0, signal.escalations), max(0, settings.max_model_escalations))
    escalated, applied = escalate(difficulty, requested)
    if applied:
        return RoutePlan(
            purpose=purpose,
            tier=escalated,
            difficulty=difficulty,
            score=score,
            reason=signal.explain(),
            escalations=applied,
            escalation_reason=signal.escalation_reason,
        )

    if eligible is None:
        return RoutePlan(
            purpose=purpose,
            tier=difficulty,
            difficulty=difficulty,
            score=score,
            reason=signal.explain(),
        )

    affordable = [
        tier
        for tier in TIER_ORDER[: TIER_ORDER.index(difficulty) + 1]
        if tier in eligible
    ]
    if not affordable:
        return RoutePlan(
            purpose=purpose,
            tier=difficulty,
            difficulty=difficulty,
            score=score,
            reason=f"{signal.explain()}; no weaker tier is available either",
        )

    chosen = affordable[-1]
    reason = signal.explain()
    if chosen is not difficulty:
        reason += f"; {difficulty.value} had no model configured or no room"

    return RoutePlan(
        purpose=purpose,
        tier=chosen,
        difficulty=difficulty,
        score=score,
        reason=reason,
    )

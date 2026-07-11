"""Sandbox risk primitives + classifier (Phase 1 skeleton).

This module exposes:

* :class:`RiskLevel` — ``IntEnum`` of severity bands.
* :class:`RiskAssessment` — frozen dataclass describing a single hit.
* :class:`RiskPolicy` — ``Protocol`` for pluggable rule implementations.
* :class:`RiskClassifier` — aggregator that walks every policy and returns
  the union of their assessments (Phase 1 stub: ``assess`` is a placeholder).

The wiring of policies + ``ExecutionContext`` + ``sandbox_mode`` aliasing is
deferred to later phases (see ``openspec/changes/2026-07-11-sandbox-policy-refactor/``).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Protocol, Sequence


class RiskLevel(IntEnum):
    """Severity band. Higher = more dangerous."""

    NONE = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    SEVERE = 4


@dataclass(frozen=True)
class RiskAssessment:
    """One rule hit.

    Attributes:
        level: Severity band.
        policy_name: Short policy identifier (``"blocklist"`` / ``"metachar"`` / ...).
        rule_id: Specific rule inside the policy
            (e.g. ``"forbidden_cmd:rm"`` or ``"ps_pipe_in_shell"``).
        message: Human-readable reason, suitable for LLM / UI display.
        matched_chars: Tuple of offending characters (metachar scenario).
        suggestion: How to rewrite the command to pass.
    """

    level: RiskLevel
    policy_name: str
    rule_id: str
    message: str
    matched_chars: tuple[str, ...] = ()
    suggestion: str = ""

    def __post_init__(self) -> None:
        # Force tuple coercion for matched_chars. ``dataclasses`` does not
        # enforce container types in ``__init__``; we normalise here so that
        # ``RiskAssessment`` is always hashable and immutable.
        if not isinstance(self.matched_chars, tuple):
            try:
                coerced = tuple(self.matched_chars)
            except TypeError as exc:
                raise TypeError(
                    "matched_chars must be iterable of str"
                ) from exc
            object.__setattr__(self, "matched_chars", coerced)


class RiskPolicy(Protocol):
    """Minimal interface every policy must implement.

    ``name`` is exposed for diagnostics and for assembling the rule_id prefix
    in the classifier.
    """

    name: str

    def assess(self, command: str, ctx: Any) -> list[RiskAssessment]: ...


class RiskClassifier:
    """Aggregator over a list of :class:`RiskPolicy` instances.

    The classifier is responsible for the top-level sandbox_mode aliasing:

    * ``"sandbox"`` (default): every policy runs, assessments are returned as-is.
    * ``"off"``: every policy is short-circuited — the user has explicitly
      opted out of sandbox enforcement.
    * ``"manual"``: every policy runs, but every assessment is downgraded
      to ``MEDIUM`` and a "请向用户说明..." suggestion is added so the LLM
      routes the user to a manual decision.

    Individual policies are isolated: if one raises, only that policy is
    skipped, the rest of the chain still runs.
    """

    _MANUAL_SUGGESTION = "请向用户说明此操作的预期影响，由用户决定是否手动执行"

    def __init__(self, policies: Sequence[RiskPolicy] | None = None) -> None:
        self.policies: list[RiskPolicy] = list(policies) if policies else []

    @classmethod
    def default(cls) -> "RiskClassifier":
        """Build a classifier wired to the four standard policies.

        Imports are deferred to avoid a circular dependency with the
        ``policies/`` subpackage (the policies import from ``risk.py``).
        """
        from app.security.policies.blocklist import BlocklistPolicy
        from app.security.policies.git_write import GitWritePolicy
        from app.security.policies.metachar import MetacharPolicy
        from app.security.policies.path_policy import PathPolicy

        return cls(
            policies=[
                BlocklistPolicy(),
                GitWritePolicy(),
                MetacharPolicy(),
                PathPolicy(),
            ]
        )

    def assess(self, command: str, ctx: Any) -> list[RiskAssessment]:
        # off-mode short-circuit: user has explicitly opted out.
        if ctx is not None and getattr(ctx, "is_path_unrestricted", False):
            return []

        assessments: list[RiskAssessment] = []
        for policy in self.policies:
            try:
                assessments.extend(policy.assess(command, ctx))
            except Exception:  # noqa: BLE001
                # Isolated: a buggy policy must not take down the chain.
                # The policy itself is responsible for raising in catastrophic
                # cases (e.g. MetacharPolicy emits a scan_error MEDIUM).
                continue

        # manual-mode downgrade
        if ctx is not None and getattr(ctx, "sandbox_mode", None) == "manual":
            return [
                RiskAssessment(
                    level=RiskLevel.MEDIUM,
                    policy_name=a.policy_name,
                    rule_id=a.rule_id,
                    message=a.message,
                    matched_chars=a.matched_chars,
                    suggestion=self._MANUAL_SUGGESTION,
                )
                for a in assessments
            ]
        return assessments

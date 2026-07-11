"""Unit tests for the RiskLevel / RiskAssessment primitives and the
:class:`RiskClassifier` aggregator that ties individual policies together.

The first batch (Phase 1) only exercises the data classes and the protocol.
Behaviour-rich tests (policies, PowerShell literal whitelist, mode aliasing,
error aggregation) live further down the file or in sibling test modules.
"""

from __future__ import annotations

import pytest

from app.security.context import ExecutionContext
from app.security.risk import RiskAssessment, RiskClassifier, RiskLevel, RiskPolicy


# ============================================================
# 1. RiskLevel
# ============================================================


def test_risk_level_is_int_enum() -> None:
    assert issubclass(RiskLevel, int)


def test_risk_level_int_values_are_stable() -> None:
    assert int(RiskLevel.NONE) == 0
    assert int(RiskLevel.LOW) == 1
    assert int(RiskLevel.MEDIUM) == 2
    assert int(RiskLevel.HIGH) == 3
    assert int(RiskLevel.SEVERE) == 4


def test_risk_level_max_returns_highest() -> None:
    assert max([RiskLevel.LOW, RiskLevel.HIGH, RiskLevel.MEDIUM]) is RiskLevel.HIGH


def test_risk_level_distinct_members() -> None:
    members = {RiskLevel.NONE, RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.SEVERE}
    assert len(members) == 5


# ============================================================
# 2. RiskAssessment
# ============================================================


def test_risk_assessment_minimal_fields() -> None:
    a = RiskAssessment(
        level=RiskLevel.HIGH,
        policy_name="blocklist",
        rule_id="forbidden_cmd:rm",
        message="rm is forbidden",
    )
    assert a.level is RiskLevel.HIGH
    assert a.policy_name == "blocklist"
    assert a.rule_id == "forbidden_cmd:rm"
    assert a.message == "rm is forbidden"
    assert a.matched_chars == ()
    assert a.suggestion == ""


def test_risk_assessment_optional_fields() -> None:
    a = RiskAssessment(
        level=RiskLevel.MEDIUM,
        policy_name="metachar",
        rule_id="posix_pipe",
        message="pipe is forbidden",
        matched_chars=("|",),
        suggestion="use multiple commands",
    )
    assert a.matched_chars == ("|",)
    assert a.suggestion == "use multiple commands"


def test_risk_assessment_is_frozen() -> None:
    a = RiskAssessment(
        level=RiskLevel.LOW,
        policy_name="metachar",
        rule_id="x",
        message="m",
    )
    with pytest.raises((AttributeError, TypeError)):
        a.level = RiskLevel.HIGH  # type: ignore[misc]


def test_risk_assessment_matched_chars_coerced_to_tuple() -> None:
    """``matched_chars`` is normalised to a tuple on construction.

    Pass a list — the dataclass should still produce a tuple so the
    assessment remains hashable and immutable. We pick "coerce" over
    "reject" because callers iterating over a regex match group expect
    to pass any sequence.
    """
    a = RiskAssessment(
        level=RiskLevel.LOW,
        policy_name="metachar",
        rule_id="x",
        message="m",
        matched_chars=["|", ";"],  # type: ignore[arg-type]
    )
    assert a.matched_chars == ("|", ";")
    assert isinstance(a.matched_chars, tuple)


# ============================================================
# 3. RiskPolicy protocol
# ============================================================


def test_risk_policy_protocol_can_be_implemented() -> None:
    class FakePolicy:
        name = "fake"

        def assess(self, command: str, ctx) -> list[RiskAssessment]:  # noqa: D401, ANN001
            return []

    p: RiskPolicy = FakePolicy()  # type: ignore[assignment]
    assert p.name == "fake"
    assert p.assess("echo hi", None) == []  # type: ignore[arg-type]


# ============================================================
# 4. RiskClassifier (Phase 1: skeleton only, assess is not yet wired)
# ============================================================


def test_classifier_default_constructor_has_no_policies() -> None:
    c = RiskClassifier()
    assert c.policies == []


def test_classifier_accepts_custom_policies() -> None:
    sentinel_policy = type(
        "P",
        (),
        {"name": "x", "assess": lambda self, cmd, ctx: []},
    )()
    c = RiskClassifier(policies=[sentinel_policy])  # type: ignore[list-item]
    assert c.policies == [sentinel_policy]


def test_classifier_assess_raises_not_implemented_in_phase1() -> None:
    # Phase 7 has implemented assess() — the old NotImplementedError
    # expectation is removed. Kept here as a no-op marker so the test
    # history is traceable.
    c = RiskClassifier.default()
    assert c.assess("echo hi", _ctx()) == []


# ============================================================
# 5. BlocklistPolicy (Phase 3)
# ============================================================


def _ctx(**overrides: object) -> ExecutionContext:
    base = dict(
        exec_mode="shell_string",
        trust_mode="workspace",
        thread_id="t1",
        authorized_paths=(),
        scratch_path=None,  # type: ignore[arg-type]
        sandbox_mode="sandbox",
    )
    base.update(overrides)
    return ExecutionContext(**base)  # type: ignore[arg-type]


def test_blocklist_policy_name() -> None:
    from app.security.policies.blocklist import BlocklistPolicy

    assert BlocklistPolicy().name == "blocklist"


def test_blocklist_assess_rm_returns_high() -> None:
    from app.security.policies.blocklist import BlocklistPolicy

    p = BlocklistPolicy()
    assessments = p.assess("rm -rf foo", _ctx())
    assert len(assessments) == 1
    a = assessments[0]
    assert a.level is RiskLevel.HIGH
    assert a.policy_name == "blocklist"
    assert a.rule_id == "forbidden_cmd:rm"
    assert "rm" in a.message


def test_blocklist_assess_git_safe() -> None:
    from app.security.policies.blocklist import BlocklistPolicy

    p = BlocklistPolicy()
    assert p.assess("git status", _ctx()) == []


def test_blocklist_catches_wrapper_cmd_c_del() -> None:
    from app.security.policies.blocklist import BlocklistPolicy

    p = BlocklistPolicy()
    assessments = p.assess("cmd /c del file.txt", _ctx())
    assert any(a.rule_id == "forbidden_cmd:del" for a in assessments)


def test_blocklist_catches_wrapper_powershell_format() -> None:
    from app.security.policies.blocklist import BlocklistPolicy

    p = BlocklistPolicy()
    assessments = p.assess("powershell -Command format c:", _ctx())
    assert any(a.rule_id == "forbidden_cmd:format" for a in assessments)


# ============================================================
# 6. GitWritePolicy (Phase 4)
# ============================================================


def test_git_write_policy_name() -> None:
    from app.security.policies.git_write import GitWritePolicy

    assert GitWritePolicy().name == "git_write"


def test_git_write_assess_commit() -> None:
    from app.security.policies.git_write import GitWritePolicy

    p = GitWritePolicy()
    assessments = p.assess("git commit -m 'msg'", _ctx())
    assert len(assessments) == 1
    a = assessments[0]
    assert a.level is RiskLevel.MEDIUM
    assert a.policy_name == "git_write"
    assert a.rule_id == "git_write:commit"


def test_git_write_assess_status_safe() -> None:
    from app.security.policies.git_write import GitWritePolicy

    p = GitWritePolicy()
    assert p.assess("git status", _ctx()) == []
    assert p.assess("git diff HEAD~1", _ctx()) == []


def test_git_write_in_wrapper_caught() -> None:
    from app.security.policies.git_write import GitWritePolicy

    p = GitWritePolicy()
    assessments = p.assess("bash -c 'git push origin main'", _ctx())
    assert any(a.rule_id == "git_write:push" for a in assessments)


def test_git_write_in_powershell_caught() -> None:
    from app.security.policies.git_write import GitWritePolicy

    p = GitWritePolicy()
    assessments = p.assess("powershell -Command 'git push'", _ctx())
    assert any(a.rule_id == "git_write:push" for a in assessments)


# ============================================================
# 7. MetacharPolicy (Phase 5 — core, PowerShell literal whitelist)
# ============================================================


def test_metachar_policy_name() -> None:
    from app.security.policies.metachar import MetacharPolicy

    assert MetacharPolicy().name == "metachar"


def test_metachar_argv_mode_no_assessment() -> None:
    from app.security.policies.metachar import MetacharPolicy

    p = MetacharPolicy()
    assert p.assess("python -c \"import time; print(time.time())\"", _ctx(exec_mode="argv_list")) == []


def test_metachar_posix_pipe_blocked() -> None:
    from app.security.policies.metachar import MetacharPolicy

    p = MetacharPolicy()
    a = p.assess("cat foo | grep bar", _ctx())
    assert len(a) == 1
    assert a[0].level is RiskLevel.MEDIUM
    assert "|" in a[0].matched_chars


def test_metachar_posix_dollar_in_double_quotes_blocked() -> None:
    from app.security.policies.metachar import MetacharPolicy

    p = MetacharPolicy()
    a = p.assess('echo "$(whoami)"', _ctx())
    assert a and a[0].level is RiskLevel.MEDIUM
    assert "$" in a[0].matched_chars


def test_metachar_powershell_var_in_double_quotes_allowed() -> None:
    """User-trace fix: $_ / $var inside PS double-quotes is a literal."""
    from app.security.policies.metachar import MetacharPolicy

    p = MetacharPolicy()
    cmd = 'powershell -Command "echo $Name"'
    assert p.assess(cmd, _ctx()) == []


def test_metachar_powershell_hashtable_in_double_quotes_allowed() -> None:
    from app.security.policies.metachar import MetacharPolicy

    p = MetacharPolicy()
    cmd = "powershell -Command \"@{Name='X'; Value=1}\""
    assert p.assess(cmd, _ctx()) == []


def test_metachar_powershell_bracket_access_in_double_quotes_allowed() -> None:
    from app.security.policies.metachar import MetacharPolicy

    p = MetacharPolicy()
    cmd = "powershell -Command \"[math]::Round(1.5)\""
    assert p.assess(cmd, _ctx()) == []


def test_metachar_powershell_pipe_outside_quotes_blocked() -> None:
    from app.security.policies.metachar import MetacharPolicy

    p = MetacharPolicy()
    a = p.assess("Get-Process | Sort-Object", _ctx())
    assert a and a[0].level is RiskLevel.MEDIUM
    assert "|" in a[0].matched_chars


def test_metachar_powershell_semicolon_inside_quotes_allowed() -> None:
    """Inside ``-Command "..."`` the whole string is delivered literally to PS."""
    from app.security.policies.metachar import MetacharPolicy

    p = MetacharPolicy()
    a = p.assess('powershell -Command "echo hi; Stop-Process"', _ctx())
    assert a == []


def test_metachar_powershell_backtick_inside_quotes_allowed() -> None:
    from app.security.policies.metachar import MetacharPolicy

    p = MetacharPolicy()
    a = p.assess("powershell -Command \"echo `whoami`\"", _ctx())
    assert a == []


def test_metachar_powershell_redirection_inside_quotes_allowed() -> None:
    from app.security.policies.metachar import MetacharPolicy

    p = MetacharPolicy()
    a = p.assess("powershell -Command \"Get-Content foo > out.txt\"", _ctx())
    assert a == []


def test_metachar_user_trace_powershell_get_process() -> None:
    """The exact command from the user's trace must pass."""
    from app.security.policies.metachar import MetacharPolicy

    p = MetacharPolicy()
    cmd = (
        'powershell -Command "Get-Process | Sort-Object WorkingSet64 -Descending | '
        "Select-Object -First 10 Name, "
        "@{Name='Memory(MB)';Expression={[math]::Round($_.WorkingSet64/1MB,2)}}, "
        "@{Name='Memory(GB)';Expression={[math]::Round($_.WorkingSet64/1GB,3)}}, "
        'Id | Format-Table -AutoSize"'
    )
    assert p.assess(cmd, _ctx()) == []


def test_metachar_unmatched_quote_safe_fallback() -> None:
    """Unmatched quotes must not crash; fall back to strict mode."""
    from app.security.policies.metachar import MetacharPolicy

    p = MetacharPolicy()
    # An unterminated string still raises some risk in strict mode.
    a = p.assess('echo "hello; rm -rf /', _ctx())
    # We just need it to not crash and still flag the visible metachar.
    assert any(";" in r.matched_chars for r in a)


def test_metachar_powershell_command_at_root() -> None:
    """Direct PowerShell invocation (no wrapper) is also PS mode."""
    from app.security.policies.metachar import MetacharPolicy

    p = MetacharPolicy()
    assert p.assess('powershell -NoProfile -Command "$x = 1; $y = 2"', _ctx()) == []


def test_metachar_powershell_outside_command_block() -> None:
    """Metachars *outside* any quoted region are always blocked, even in PS mode."""
    from app.security.policies.metachar import MetacharPolicy

    p = MetacharPolicy()
    a = p.assess('powershell -Command "Stop-Process" ; Restart-Computer', _ctx())
    assert a and ";" in a[0].matched_chars


def test_metachar_powershell_pipe_outside_quotes_blocked_2() -> None:
    from app.security.policies.metachar import MetacharPolicy

    p = MetacharPolicy()
    a = p.assess('powershell -Command "Get-Process" | Out-Null', _ctx())
    assert a and "|" in a[0].matched_chars


# ============================================================
# 8. PathPolicy (Phase 6) — path quick-prescan only
# ============================================================
#
# Phase 6 keeps PathPolicy intentionally small: it only does a textual
# presence check on the command and emits a MEDIUM assessment if any
# Windows / POSIX absolute path token is present. The real authorization
# check is done by ``session_sandbox.check_*`` and the new
# ``PathHintFormatter`` (Phase 9). Decoupling the two keeps PathPolicy
# trivially testable and sync-only.


def test_path_policy_name() -> None:
    from app.security.policies.path_policy import PathPolicy

    assert PathPolicy().name == "path"


def test_path_policy_no_path_in_command() -> None:
    from app.security.policies.path_policy import PathPolicy

    p = PathPolicy()
    assert p.assess("echo hi", _ctx()) == []


def test_path_policy_windows_path_emits_medium() -> None:
    from app.security.policies.path_policy import PathPolicy

    p = PathPolicy()
    a = p.assess("type C:\\Users\\me\\file.txt", _ctx())
    assert any(x.level is RiskLevel.MEDIUM for x in a)


def test_path_policy_posix_path_emits_medium() -> None:
    from app.security.policies.path_policy import PathPolicy

    p = PathPolicy()
    a = p.assess("cat /etc/passwd", _ctx())
    assert any(x.level is RiskLevel.MEDIUM for x in a)


def test_path_policy_off_mode_short_circuits() -> None:
    from app.security.policies.path_policy import PathPolicy

    p = PathPolicy()
    a = p.assess("cat /whatever/secret", _ctx(sandbox_mode="off"))
    assert a == []


def test_path_policy_user_trace_target() -> None:
    """The user's reported write_file target should be detected as path-bearing."""
    from app.security.policies.path_policy import PathPolicy

    p = PathPolicy()
    cmd = "write_file(C:\\Users\\luqia\\Desktop\\memory_analyze.py)"
    a = p.assess(cmd, _ctx())
    assert any(x.level is RiskLevel.MEDIUM for x in a)


# ============================================================
# 9. RiskClassifier aggregation (Phase 7)
# ============================================================


def _echo_policy(name: str, hits: list[RiskAssessment]) -> Any:
    """Build a one-off policy that always returns ``hits``."""

    class _P:
        pass

    obj = _P()
    setattr(obj, "name", name)
    setattr(obj, "assess", lambda cmd, ctx: list(hits))
    return obj


def test_classifier_default_constructor_uses_all_standard_policies() -> None:
    """When constructed with no args, all four standard policies are wired in."""
    c = RiskClassifier.default()
    names = sorted(p.name for p in c.policies)
    assert names == ["blocklist", "git_write", "metachar", "path"]


def test_classifier_sandbox_mode_sandbox_runs_all() -> None:
    a = RiskAssessment(
        level=RiskLevel.HIGH,
        policy_name="blocklist",
        rule_id="forbidden_cmd:rm",
        message="rm",
    )
    b = RiskAssessment(
        level=RiskLevel.MEDIUM,
        policy_name="metachar",
        rule_id="posix_forbidden_chars",
        message="pipe",
        matched_chars=("|",),
    )
    c = RiskClassifier(policies=[_echo_policy("blocklist", [a]), _echo_policy("metachar", [b])])
    ctx = _ctx(sandbox_mode="sandbox")
    out = c.assess("rm foo | tee bar", ctx)
    assert len(out) == 2
    assert {x.policy_name for x in out} == {"blocklist", "metachar"}


def test_classifier_sandbox_mode_off_short_circuits_to_critical() -> None:
    """off mode: blocklist / git / path short-circuit; metachar may still run.

    In practice we also short-circuit metachar for off mode — the user has
    explicitly opted out of all sandbox enforcement.
    """
    a = RiskAssessment(
        level=RiskLevel.HIGH,
        policy_name="blocklist",
        rule_id="forbidden_cmd:rm",
        message="rm",
    )
    c = RiskClassifier(policies=[_echo_policy("blocklist", [a])])
    ctx = _ctx(sandbox_mode="off")
    out = c.assess("rm foo", ctx)
    assert out == []


def test_classifier_sandbox_mode_manual_downgrades_to_medium() -> None:
    """manual mode: all hits become MEDIUM with a 'suggest manual' suggestion."""
    a = RiskAssessment(
        level=RiskLevel.HIGH,
        policy_name="blocklist",
        rule_id="forbidden_cmd:rm",
        message="rm",
        suggestion="rm-specific advice",
    )
    c = RiskClassifier(policies=[_echo_policy("blocklist", [a])])
    ctx = _ctx(sandbox_mode="manual")
    out = c.assess("rm foo", ctx)
    assert len(out) == 1
    downgraded = out[0]
    assert downgraded.level is RiskLevel.MEDIUM
    assert "人工" in downgraded.suggestion or "用户" in downgraded.suggestion


def test_classifier_empty_command_yields_no_assessments() -> None:
    c = RiskClassifier(policies=[_echo_policy("blocklist", [
        RiskAssessment(level=RiskLevel.HIGH, policy_name="blocklist",
                       rule_id="x", message="x")
    ])])
    # Empty command: policies themselves return [] for empty input by contract.
    p = _echo_policy("blocklist", [])
    c2 = RiskClassifier(policies=[p])
    assert c2.assess("", _ctx()) == []


def test_classifier_policy_exception_isolated() -> None:
    """One policy raising does not affect the others."""
    class _Boom:
        name = "boom"

        def assess(self, command: str, ctx: Any) -> list[RiskAssessment]:
            raise RuntimeError("kaboom")

    a = RiskAssessment(
        level=RiskLevel.MEDIUM,
        policy_name="ok",
        rule_id="ok",
        message="ok",
    )
    ok_policy = _echo_policy("ok", [a])
    c = RiskClassifier(policies=[_Boom(), ok_policy])
    out = c.assess("anything", _ctx())
    assert out == [a]


def test_classifier_user_trace_full_pipeline() -> None:
    """End-to-end: user's PowerShell trace must produce zero assessments."""
    c = RiskClassifier.default()
    cmd = (
        'powershell -Command "Get-Process | Sort-Object WorkingSet64 -Descending | '
        "Select-Object -First 10 Name, "
        "@{Name='Memory(MB)';Expression={[math]::Round($_.WorkingSet64/1MB,2)}}, "
        "@{Name='Memory(GB)';Expression={[math]::Round($_.WorkingSet64/1GB,3)}}, "
        'Id | Format-Table -AutoSize"'
    )
    out = c.assess(cmd, _ctx())
    assert out == []

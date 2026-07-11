# Tasks: 沙箱风险分级重构

> 每个任务都按 TDD 推进：写测试 → 看红 → 最小实现 → 看绿 → refactor。
> 任务粒度 ≤ 1 个文件改动 / 1 个 commit。

## Phase 0：脚手架

- [ ] T0.1 创建 `backend/app/security/policies/__init__.py`（空文件）
- [ ] T0.2 确认 `pytest` 与 `pytest-asyncio` 在 backend 已配置（看 `pyproject.toml` / `tests/conftest.py`）
- [ ] T0.3 跑 `pytest tests/python/unit/test_security_command_filter.py -q` 确认基线绿

## Phase 1：RiskLevel + RiskAssessment 数据类

- [ ] **T1.1 [RED]** 写 `tests/python/unit/test_risk_classifier.py`：
  - `test_risk_level_int_values`
  - `test_risk_assessment_is_frozen`
  - `test_risk_assessment_default_fields`
- [ ] **T1.2 [GREEN]** 实现 `backend/app/security/risk.py`：
  - `class RiskLevel(IntEnum)`（NONE/LOW/MEDIUM/HIGH/SEVERE）
  - `@dataclass(frozen=True) class RiskAssessment`
  - `class RiskPolicy(Protocol)`（仅 name + assess 接口）
  - `class RiskClassifier`（仅 `__init__(policies=None)`，assess 抛 `NotImplementedError`）
- [ ] **T1.3 [REFACTOR]** 抽常量 `_RULE_ID_DELIMITER = ":"`，便于后续 parse

## Phase 2：ExecutionContext + Builder

- [ ] **T2.1 [RED]** 写 `tests/python/unit/test_execution_context.py`：
  - `test_context_is_frozen`
  - `test_is_argv_property`
  - `test_is_full_trust_property`
  - `test_is_path_unrestricted_property`
  - `test_build_cli_execute_context_uses_argv_mode`
  - `test_build_shell_backend_context_uses_shell_mode`
- [ ] **T2.2 [GREEN]** 实现 `backend/app/security/context.py`：
  - `@dataclass(frozen=True) class ExecutionContext`（含 3 个 property）
  - `build_cli_execute_context(thread_id, sandbox) -> ExecutionContext`
  - `build_shell_backend_context(thread_id, sandbox) -> ExecutionContext`
- [ ] **T2.3 [REFACTOR]** 加 `is_powershell_wrapper(command)` helper

## Phase 3：BlocklistPolicy（最简单，先做）

- [ ] **T3.1 [RED]** 在 `test_risk_classifier.py` 加：
  - `test_blocklist_policy_assess_rm`
  - `test_blocklist_policy_assess_git_safe`
  - `test_blocklist_policy_catches_wrapper_cmd_c_del`
  - `test_blocklist_policy_in_wrapper_powershell_format`
- [ ] **T3.2 [GREEN]** 实现 `backend/app/security/policies/blocklist.py`：
  - `class BlocklistPolicy: name="blocklist"`
  - `assess(command, ctx)` → 调用 `command_filter.is_command_blocked` × `cmd_name` × `inner_cmd_name`
  - 命中 → 1 条 HIGH 级 assessment（rule_id=`forbidden_cmd:{name}`）
- [ ] **T3.3 [REFACTOR]** 抽 `_assess_one_command_name(name, level=HIGH)` helper

## Phase 4：GitWritePolicy

- [ ] **T4.1 [RED]** 加测试：
  - `test_git_write_policy_assess_commit`
  - `test_git_write_policy_assess_status_safe`
  - `test_git_write_policy_in_wrapper`
- [ ] **T4.2 [GREEN]** 实现 `backend/app/security/policies/git_write.py`：
  - `class GitWritePolicy: name="git_write"`
  - 复用 `is_git_write_command`（含包装器递归）
  - 命中 → MEDIUM（rule_id=`git_write:{subcmd}`）

## Phase 5：MetacharPolicy（核心，PS 放行）

- [ ] **T5.1 [RED]** 加测试：
  - `test_metachar_argv_mode_no_assessment`
  - `test_metachar_posix_pipe_blocked`
  - `test_metachar_posix_dollar_in_double_quotes_blocked`
  - `test_metachar_powershell_var_in_double_quotes_allowed`（核心 PS case 1）
  - `test_metachar_powershell_hashtable_in_double_quotes_allowed`（核心 PS case 2）
  - `test_metachar_powershell_bracket_access_in_double_quotes_allowed`（核心 PS case 3）
  - `test_metachar_powershell_pipe_outside_quotes_blocked`
  - `test_metachar_powershell_semicolon_always_blocked`
  - `test_metachar_powershell_backtick_always_blocked`
  - `test_metachar_powershell_redirection_blocked`
  - `test_metachar_user_trace_powershell_get_process`（用户原始命令）
  - `test_metachar_unmatched_quote_safe_fallback`
- [ ] **T5.2 [GREEN]** 实现 `backend/app/security/policies/metachar.py`：
  - `class MetacharPolicy: name="metachar"`
  - `assess(command, ctx)` 完整算法（POSIX 严格 + PS 宽松）
  - PS 字面量白名单正则（`$_` / `$var` / `@{...}` / `[...]`）
  - 未闭合引号安全降级为严格模式
- [ ] **T5.3 [REFACTOR]** 抽 `_scan_metachars_posix()` / `_scan_metachars_powershell()` 函数
- [ ] **T5.4** 在 `command_filter.py` 旧 `has_forbidden_args` 内部委托新 MetacharPolicy（保留兼容）

## Phase 6：PathPolicy

- [ ] **T6.1 [RED]** 加测试：
  - `test_path_policy_extracts_windows_path`
  - `test_path_policy_extracts_posix_path`
  - `test_path_policy_authorized_path_no_assessment`
  - `test_path_policy_unauthorized_returns_medium`
  - `test_path_policy_off_mode_short_circuits`
- [ ] **T6.2 [GREEN]** 实现 `backend/app/security/policies/path_policy.py`：
  - `class PathPolicy: name="path"`
  - 用 `sandbox_escalation.extract_path_from_command` 提取
  - 调 `sandbox.is_path_authorized` 检查
  - 命中 → MEDIUM（rule_id=`path:{status}`）
  - `ctx.is_path_unrestricted` → 直接返回 `[]`

## Phase 7：RiskClassifier 聚合

- [ ] **T7.1 [RED]** 加测试：
  - `test_classifier_combines_all_policies`
  - `test_classifier_sandbox_mode_off_short_circuits`
  - `test_classifier_sandbox_mode_manual_downgrades_to_medium`
  - `test_classifier_sandbox_mode_normal_full_assessment`
  - `test_classifier_empty_assessments_returns_empty_list`
  - `test_classifier_policy_exception_isolated`
- [ ] **T7.2 [GREEN]** 实现 `backend/app/security/risk.py::RiskClassifier.assess`：
  - `off` 模式 → 只跑 Blocklist + GitWrite 走 critical-only 路径（其他 Policy 短路）
  - `manual` 模式 → 全部 assessment.level = MEDIUM + suggestion = "请向用户说明..."
  - `sandbox` 模式 → 正常聚合
  - 单 Policy 抛异常 → log + skip，不影响其他
- [ ] **T7.3 [REFACTOR]** 抽 `_run_policies_safely(policies, command, ctx)` helper

## Phase 8：RiskReporter.aggregate 错误聚合

- [ ] **T8.1 [RED]** 加测试：
  - `test_aggregate_empty_returns_empty_string`
  - `test_aggregate_single_assessment`
  - `test_aggregate_multiple_assessments_numbered`
  - `test_aggregate_truncates_long_command`
  - `test_aggregate_includes_suggestion_when_present`
  - `test_aggregate_includes_matched_chars_when_present`
- [ ] **T8.2 [GREEN]** 实现 `backend/app/security/reporter.py`：
  - `aggregate(assessments, command, ctx) -> str`
  - 格式："命令无法执行（N 项命中）：\n  [1] policy.rule_id（LEVEL）\n      原因：...\n      建议：...\n      命中字符：...\n..."

## Phase 9：PathHintFormatter 路径错误信息增强

- [ ] **T9.1 [RED]** 加测试：
  - `test_format_unauthorized_hint_lists_writable_dirs`
  - `test_format_unauthorized_hint_recommends_scratch`
  - `test_format_unauthorized_hint_read_action`
  - `test_format_unauthorized_hint_write_action`
  - `test_format_unauthorized_hint_no_authorized_dirs_falls_back_to_scratch`
  - `test_format_unauthorized_hint_truncates_to_5_dirs`
- [ ] **T9.2 [GREEN]** 实现 `backend/app/security/path_hint.py`：
  - `format_unauthorized_hint(rejected_path, action, authorized_paths, scratch_path) -> str`
  - 列出 ≤5 个可写目录
  - 推荐 `data/workspace/.scratch/{filename}`
  - 引导 dialog 授权
- [ ] **T9.3** 在 `session_sandbox._unauthorized_read_hint` / `_unauthorized_write_hint` 调用新 formatter，保留旧字符串模板为 fallback

## Phase 10：cli_execute 接入 RiskClassifier

- [ ] **T10.1 [RED]** 在 `test_cli_tools.py` 加：
  - `test_cli_execute_argv_mode_python_with_semicolon_passes`（核心 Bug C case）
  - `test_cli_execute_blocklist_returns_error`
  - `test_cli_execute_sandbox_mode_off_skips_path_check`
  - `test_cli_execute_sandbox_mode_manual_returns_hint`
- [ ] **T10.2 [GREEN]** 修改 `backend/app/tools/cli.py`：
  - 删除 `has_forbidden_args` 在 argv 上的循环调用
  - 改用 `risk_classifier.assess(command, build_cli_execute_context(...))`
  - 保留 `is_command_blocked` 检查（向后兼容）
- [ ] **T10.3 [REFACTOR]** 抽 `_assess_cli_command(command, thread_id) -> list[RiskAssessment]`

## Phase 11：safe_shell_backend 接入 RiskClassifier

- [ ] **T11.1 [RED]** 在 `test_safe_shell_backend.py` 加：
  - `test_powershell_get_process_user_trace_passes`（核心 Bug A case）
  - `test_powershell_semicolon_blocked`
  - `test_multiple_assessments_aggregated_in_output`
  - `test_sandbox_mode_off_skips_policies`
  - `test_legacy_blocklist_still_works`（向后兼容）
- [ ] **T11.2 [GREEN]** 重构 `backend/app/deepagent/safe_shell_backend.py::execute`：
  - 替换 4 条独立 if → 1 次 `risk_classifier.assess`
  - Git 写走审批分支单独处理
  - 其他命中聚合错误信息
- [ ] **T11.3 [REFACTOR]** 保留 `_extract_inner_command` 仍可被其他 Policy 复用

## Phase 12：路径错误信息集成

- [ ] **T12.1 [RED]** 在 `test_session_sandbox.py` 加：
  - `test_unauthorized_read_hint_lists_authorized_dirs`
  - `test_unauthorized_read_hint_recommends_scratch`
  - `test_unauthorized_write_hint_recommends_scratch_with_filename`
  - `test_unauthorized_hint_falls_back_on_formatter_error`
- [ ] **T12.2 [GREEN]** 修改 `backend/app/sandbox/session_sandbox.py`：
  - `_unauthorized_read_hint` / `_unauthorized_write_hint` 调用 `format_unauthorized_hint`
  - try/except fallback 到旧字符串

## Phase 13：全量回归

- [ ] T13.1 跑 `pytest tests/python/unit/ -q` 确认 100% 通过
- [ ] T13.2 跑 `pytest tests/python/unit/test_session_sandbox.py -q` 特别确认 6 个 sandbox_mode 新 case 仍绿
- [ ] T13.3 跑 `pytest tests/python/unit/test_security_command_filter.py -q` 确认旧 case + PS 新 case 全绿
- [ ] T13.4 跑 `pytest tests/python/unit/test_safe_shell_backend.py -q` 确认核心 case 全绿
- [ ] T13.5 检查 coverage：`pytest --cov=app/security --cov-report=term-missing` ≥ 80%

## Phase 14：code-quality-expert review

- [ ] T14.1 拉起 code-quality-expert skill 跑静态分析
- [ ] T14.2 修 review 反馈
- [ ] T14.3 再跑全量测试

## Phase 15：SSOT 文档

- [ ] T15.1 新增 `docs/agents/06-sandbox-policy.md`：
  - 4 层规则→RiskClassifier 映射
  - 3 模式 × 5 风险等级矩阵
  - PS 字面量白名单规则
  - 路径错误信息聚合示例
- [ ] T15.2 更新 `docs/agents/01-architecture-file-map.md` 加入 `app/security/risk.py` 等新文件
- [ ] T15.3 更新 `docs/agents/03-key-conventions.md` 加入 RiskLevel 约定

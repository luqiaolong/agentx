# Design: 沙箱与安全权限包重构

## Context

当前 AgentX 安全相关代码分散在 7 个位置，职责混乱且存在 22 个已确认 Bug（16 后端 + 6 前端）。
核心问题：

1. **`utils/security.py` 职责过载**：既做纯函数路径归一化（`_is_under` / `_critical_dirs`），
   又做有状态单例（`SessionSandbox` 管理 `authorized_dirs` dict），还 re-export `ApprovalDecision`
2. **审批流重复**：`work_supervisor.py`（L340-535）与 `coding.py`（L375-569）的
   interrupt_before 循环 + 危险工具判定 + 审批等待逻辑几乎完全相同（~200 行）
3. **审批状态机脆弱**：`approval/state.py` 用模块级 dict + asyncio.Lock，但无 TTL 清理
   （内存泄漏）、pause 存在 check-then-wait 竞态、多进程部署失效
4. **路径检查不一致**：审批层 `is_path_authorized` 不传 `base=workspace_path`，
   用 PROJECT_ROOT 解析相对路径；执行层 `check_write` 传 `base=workspace_path`，
   导致审批层与执行层路径基准不一致
5. **Linux 平台缺陷**：`_critical_dirs()` 在非 win32 把 `Path("/")` 纳入候选，
   `_is_under(resolved, "/")` 对任何绝对路径都成功 → Linux 下沙箱彻底不可用

## Goals / Non-Goals

**Goals:**
- 建立 `sandbox/` + `security/` 两个顶级包，与 `deep/` / `team/` / `tools/` 平行
- 修复全部 22 个已确认 Bug（16 后端 + 6 前端）
- 消除 `work_supervisor.py` / `coding.py` 审批逻辑重复
- `SessionSandbox` 并发安全（asyncio.Lock）
- `SandboxStore` SQLite WAL + timeout
- 审批状态机 TTL reaper 防内存泄漏
- 审批层与执行层路径基准一致（统一传 `base=workspace_path`）
- Team 模式子任务继承父 thread 的 workspace 授权
- `ApprovalDecision` Enum 化消除语义矛盾
- 前端 HTTP 错误正确传播 + 批量审批逐条展示

**Non-Goals:**
- 不实现多进程审批状态共享（保留单进程实现 + 文档标注，未来 Redis 后端另开提案）
- 不修改 SSE 事件契约（`approval_request` 事件格式不变）
- 不修改前端审批 UI 交互流程（仅修 bug + 适配批量展示）
- 不修改 `full_trust` 的权限语义（仍是会话级全放行，仅修复 directory_extension 预检查）
- 不新增 HTTP 端点

## Decisions

### Decision 1: `sandbox/` 与 `security/` 职责划分

**选择**：
- `sandbox/` 包 = **路径授权管理**（谁授权了什么路径可读/可写）
  - `SessionSandbox`：内存授权状态（per-thread_id）
  - `SandboxStore`：SQLite 持久化
  - `/api/sandbox/*` 路由
  - `path_guard.py`：纯函数路径归一化 + 关键目录保护

- `security/` 包 = **安全策略与审批**（什么操作危险、如何审批）
  - `approval/`：审批状态机（决策模型 + 跨请求状态）
  - `dangerous_tools.py`：危险工具分类 + runtime 危险集合计算
  - `command_filter.py`：CLI 命令黑名单 + 元字符过滤 + 参数脱敏
  - `approval_flow.py`：审批执行流（消除 supervisor/expert 重复）

**理由**：
- 路径授权（sandbox）是"数据层"——管理授权目录集合，无业务语义
- 安全策略（security）是"策略层"——判定什么操作需要审批、如何脱敏、如何等待决策
- 两者关注点正交：sandbox 不关心"为什么"授权，security 不关心"如何存储"授权
- 与 `deep/` / `team/` / `tools/` 平行，符合用户要求

**替代方案**：
- 合并为单一 `security/` 包包含 sandbox → 拒绝，职责过载，与 `tools/` 粒度不对齐
- 保留 `utils/security.py` 仅修 bug → 拒绝，用户明确要求抽包，且职责混乱是根因

### Decision 2: `approval_flow.py` 提取公共逻辑

**选择**：新建 `security/approval_flow.py`，提取 `work_supervisor.py` 和 `coding.py` 的公共审批循环：

```python
async def run_approval_loop(
    graph, config, thread_id, workspace_path, permission_mode,
    runtime_dangerous: set[str], mcp_untrusted_names: set[str],
    agent_tools: list, yield_event, is_aborted_fn, is_paused_fn,
    sandbox: SessionSandbox,
) -> AsyncIterator[dict]:
    """统一的 interrupt_before 审批循环。
    
    1. graph.astream 消费事件
    2. 检测 interrupt → 提取 pending tool_calls
    3. 判定 dangerous_calls（runtime_dangerous ∩ tool_names + 路径未全授权）
    4. dangerous_tool 审批：逐个 yield approval_request → _await_approval
    5. directory_extension 审批：收集越界只读路径 → 批量审批
    6. full_trust 模式：跳过所有审批
    7. 恢复执行或注入错误
    """
```

`work_supervisor.py` / `coding.py` 改为调用 `run_approval_loop()`，仅保留各自特有的
system prompt 构建、工具构建、委派逻辑。

**理由**：
- 消除 ~200 行重复代码，bug 修复只需改一处
- 审批逻辑集中后，路径基准不一致（Bug #7）、Team thread_id 不一致（Bug #6）、
  full_trust 预检查（Bug UX-1）等问题统一解决

**替代方案**：
- 保留各自实现仅修 bug → 拒绝，重复代码是 bug 复制的温床，违反 DRY
- 提取为基类继承 → 拒绝，Python 优先组合，且审批流是行为不是身份

### Decision 3: `ApprovalDecision` Enum 化

**选择**：

```python
from enum import Enum

class ApprovalDecision(str, Enum):
    APPROVE = "approve"      # 会话级批准（directory_extension）/ 批准（dangerous_tool）
    ONCE = "once"            # 仅本次批准（directory_extension）
    SESSION = "session"      # 会话级批准（directory_extension，等价 APPROVE）
    DENY = "deny"            # 拒绝

@dataclass
class ApprovalResult:
    decision: ApprovalDecision
    path: str | None = None
    writable: bool = False
    
    @property
    def approved(self) -> bool:
        return self.decision != ApprovalDecision.DENY
```

消除旧 `ApprovalDecision(approved=True, decision="deny")` 的矛盾组合。`approved` 变为
派生属性（`decision != DENY`），消费者只需检查 `decision`。

**理由**：
- 旧设计 `approved: bool` + `decision: str` 可矛盾（`approved=True, decision="deny"`）
- `decision` 为 `str` 无运行时校验，拼写错误静默漏判
- Enum 化后类型安全，IDE 补全，拼写错误编译期捕获

**兼容**：旧 `ApprovalDecision` 导入路径 `app.approval.decision` 改为
`app.security.approval.decision`，消费者 `if decision.approved` 改为
`if decision.approved`（property 保持兼容）。

### Decision 4: Team 模式 thread_id 授权继承

**选择**：`security/approval_flow.py` 的 `run_approval_loop` 接受可选 `parent_thread_id` 参数。
Team 模式下子任务（`code_thread_id` / `deep_thread_id`）传入 `parent_thread_id=原thread_id`，
授权检查时同时查询子 thread_id 和 parent_thread_id 的授权：

```python
def is_path_authorized(self, thread_id: str, path: str, *, writable: bool,
                       base: Path | None = None,
                       parent_thread_id: str | None = None) -> bool:
    if self._check_one(thread_id, path, writable, base):
        return True
    if parent_thread_id and self._check_one(parent_thread_id, path, writable, base):
        return True
    return False
```

`run_coding_expert` 在 Team 模式下传入 `parent_thread_id`。

**理由**：
- Team 模式为子任务生成独立 thread_id（`{thread_id}-team-code-{idx}`）用于 checkpointer 隔离
- 但 workspace 授权在父 thread_id 上，子任务查询自己的 thread_id 永远 miss
- parent_thread_id 映射让子任务继承父授权，无需为每个子任务重复授权

**替代方案**：
- 子任务复用父 thread_id → 拒绝，checkpointer 会混入子任务历史，破坏会话隔离
- 为子任务显式调 `sandbox.authorize(code_thread_id, workspace)` → 拒绝，每次 Team 都要
  额外授权调用，且 full_trust 状态无法继承

### Decision 5: `SessionSandbox` 并发安全

**选择**：`SessionSandbox` 所有数据结构用 `asyncio.Lock` 保护：

```python
class SessionSandbox:
    def __init__(self):
        self._lock = asyncio.Lock()
        self._authorized_dirs: dict[str, set[tuple[Path, bool]]] = {}
        self._full_trust_threads: set[str] = set()
        self._temp_authorized: dict[str, set[tuple[Path, bool]]] = {}
        self._parent_map: dict[str, str] = {}  # child_thread_id -> parent_thread_id

    async def authorize(self, thread_id, path, writable, source="manual"):
        async with self._lock:
            # ... 原逻辑
```

所有公共方法（`authorize` / `authorize_temp` / `revoke` / `check_read` / `check_write` /
`is_path_authorized` / `set_full_trust` / `snapshot` / `restore`）改为 async 并加锁。

**理由**：
- FastAPI 多协程并发下，`authorize`（重建 set）与 `check_read`（迭代 set）并发可触发
  `RuntimeError: set changed size during iteration`
- 对比 `approval/state.py` 已用 `asyncio.Lock`，`SessionSandbox` 无锁是显著不一致
- asyncio.Lock 而非 threading.Lock：FastAPI 单事件循环，协程级互斥足够

**影响**：所有调用方改为 `await sandbox.authorize(...)`。`router/graph.py` 的
`sandbox.authorize` 已在 async 函数内，加 `await` 即可。

### Decision 6: `SandboxStore` SQLite WAL + 事务一致性

**选择**：
```python
class SandboxStore:
    def _connect(self):
        conn = sqlite3.connect(self._db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn
```

双写一致性改为 **DB-first**：
```python
async def authorize(self, thread_id, path, writable, source="manual"):
    async with self._lock:
        # 1. 先写 DB（失败则抛异常，内存不更新）
        self._store.upsert(SandboxEntry(...))
        # 2. 再改内存
        self._authorized_dirs[thread_id].add((resolved, writable))
```

**理由**：
- WAL 模式：读写不互斥，并发性能大幅提升
- `busy_timeout=30000`：SQLite 等锁 30s 而非默认 5s，减少 `database is locked`
- DB-first：DB 写失败时内存保持旧状态，避免"内存已改但 DB 未持久化"的分叉
- 旧设计内存-first + best-effort DB（失败仅 warning）导致重启后数据复活

### Decision 7: 审批状态机 TTL reaper

**选择**：`security/approval/state.py` 新增后台 reaper 协程：

```python
_pending_approvals: dict[str, tuple[ApprovalResult, float]] = {}  # value 加 timestamp
_ACTIVITY_TTL = 1800  # 30 分钟

async def _reaper_loop():
    while True:
        await asyncio.sleep(300)  # 每 5 分钟扫描
        now = time.time()
        stale = [tid for tid, (_, ts) in _pending_approvals.items() 
                 if now - ts > _ACTIVITY_TTL]
        for tid in stale:
            _pending_approvals.pop(tid, None)
            _abort_flags.pop(tid, None)
            _abort_events.pop(tid, None)
            _pause_flags.pop(tid, None)
            _pause_events.pop(tid, None)
```

在 `app.main` lifespan 启动时 `asyncio.create_task(_reaper_loop())`。

**理由**：
- 旧设计五个 dict 无 TTL 清理，被遗弃的 thread_id 永久残留，长运行服务内存泄漏
- reaper 每 5 分钟扫描，30 分钟无活动的 thread_id 清理
- timestamp 记录最后活动时间（submit/pop/get 时更新）

### Decision 8: pause 竞态修复 — `wait_for_resume()` 原子原语

**选择**：提供原子"check-and-wait"原语，消除 check-then-wait 竞态：

```python
async def wait_for_resume(thread_id: str, timeout: float | None = None) -> bool:
    """原子地检查 pause 状态并等待恢复。返回 True 表示已恢复，False 表示超时。"""
    async with _state_lock:
        if thread_id not in _pause_flags or not _pause_flags[thread_id]:
            return True  # 未暂停，立即返回
        event = _pause_events.setdefault(thread_id, asyncio.Event())
    try:
        await asyncio.wait_for(event.wait(), timeout=timeout)
        return True
    except asyncio.TimeoutError:
        return False
```

消费者改为 `await wait_for_resume(thread_id)` 替代 `is_paused` + `get_pause_event` + `wait`。

**理由**：
- 旧流程 `is_paused`→True → `get_pause_event`→新建 unset Event → `await event.wait()`
  在 `is_paused` 返回后、`get_pause_event` 前 `clear_pause` 执行 → 新建 Event 永远 unset → 死锁
- 原子原语在锁内完成 check + event 获取，消除竞态窗口

## Risks / Trade-offs

| Risk | Mitigation |
|---|---|
| [Risk] `SessionSandbox` 改 async 后调用方需全改 | [Mitigation] 全部调用方已在 async 函数内，批量加 `await` 即可；编译期可检出漏改 |
| [Risk] `ApprovalDecision` Enum 化破坏旧 pickle/序列化 | [Mitigation] 审批决策不持久化（内存 dict），无 pickle 场景；SSE JSON 序列化用 `.value` |
| [Risk] reaper 协程误清理活跃 thread_id | [Mitigation] TTL=30 分钟远超审批超时（默认 300s）；timestamp 每次 submit/pop 更新 |
| [Risk] `approval_flow.py` 提取后 supervisor/expert 行为微差异丢失 | [Mitigation] `run_approval_loop` 接受 callback 参数（`yield_event` / `is_aborted_fn`），保留调用方定制点 |
| [Risk] parent_thread_id 映射导致授权泄漏（子任务退出后父授权仍可被复用） | [Mitigation] parent_map 仅在 Team 子任务存活期间有效；子任务结束后 `sandbox.clear(child_thread_id)` 不影响父 |
| [Risk] 大量 import 路径变更导致遗漏 | [Mitigation] 用 `ruff check` + 全量测试 + grep 双重验证；迁移完成后旧路径删除确保无残留 |

## Migration Plan

### Phase 1: 创建 `sandbox/` 包 + 修复沙箱 bug
- 新建 `sandbox/__init__.py` / `session_sandbox.py` / `store.py` / `api.py` / `schemas.py` / `path_guard.py`
- 迁移 `SessionSandbox`（加锁 + DB-first + parent_thread_id 支持）
- 迁移 `SandboxStore`（WAL + timeout）
- 迁移 `/api/sandbox/*` 路由（加审计日志）
- 修复 Linux `Path("/")` bug（`path_guard.py`）
- 编写 `sandbox/` 包单元测试

### Phase 2: 创建 `security/` 包 + 修复审批 bug
- 新建 `security/__init__.py` / `dangerous_tools.py` / `command_filter.py` / `approval/`
- 迁移 `ApprovalDecision`（Enum 化）
- 迁移 `approval/state.py`（TTL reaper + `wait_for_resume` 原语）
- 迁移 `DANGEROUS_TOOLS`（清理 `shell_exec`）+ `runtime_dangerous` 计算
- 迁移 CLI 命令过滤 + 参数脱敏到 `command_filter.py`
- 编写 `security/` 包单元测试

### Phase 3: 提取 `approval_flow.py` + 重构 supervisor/expert
- 新建 `security/approval_flow.py`，提取公共审批循环
- 重构 `work_supervisor.py` 调用 `run_approval_loop`
- 重构 `coding.py` 调用 `run_approval_loop`
- 修复路径基准不一致（传 `base=workspace_path`）
- 修复 Team 模式 thread_id 继承（传 `parent_thread_id`）
- 修复 `cli_execute` 无 cwd 自动免审批
- 修复 full_trust 跳过 directory_extension 预检查
- 编写 `approval_flow` 单元测试

### Phase 4: 前端 bug 修复
- 修复 `ChatView.tsx` revokedPaths（`storeState` → `session`）
- 修复 `http.ts` HTTP 错误处理（加 `r.ok` 检查）
- 修复 `useChatStream.ts` 批量审批队列
- 删除 `stores/permission.ts`，统一类型到 `shared/api-types.ts`
- 编写前端测试

### Phase 5: 全量 import 迁移 + 删除旧文件
- 更新所有 import 路径（`app.utils.security` → `app.sandbox` / `app.security`）
- 删除 `utils/security.py` / `api/sandbox.py` / `memory/sandbox_store.py` / `approval/` / `deep/approval.py`
- 更新 `AGENTS.md` §11 文件地图 + §14.3
- `ruff check` + 全量测试验证

### Phase 6: 集成测试 + 文档
- 端到端测试：work/coding/coding_team 三场景审批流
- 验证 full_trust / standard 两模式行为
- 验证 Team 模式子任务授权继承
- 更新 AGENTS.md 文档

## Open Questions

1. **`approval/state.py` 多进程部署怎么办？** → 当前保留单进程实现 + 文档标注
   "必须 `--workers 1` 部署"。多进程 Redis 后端另开提案（M2 里程碑）。
2. **`approval_flow.py` 是否也用于 CLI 模式？** → 是。CLI 模式直连 `run_router`，
   复用同一审批流，仅交互方式不同（终端阻塞 vs GUI 弹窗）。
3. **parent_thread_id 映射是否影响 checkpointer？** → 不影响。checkpointer 仍按
   子 thread_id 隔离，parent_thread_id 仅用于 sandbox 授权查询。

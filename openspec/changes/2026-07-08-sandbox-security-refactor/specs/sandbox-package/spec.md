# Spec: 沙箱路径授权包（sandbox-package）

## 概述

`backend/app/sandbox/` 包提供会话级文件系统沙箱授权管理，按 `thread_id` 隔离授权目录，
支持读写权限分级、SQLite 持久化、并发安全、跨会话恢复。与 `deep/` / `team/` / `tools/` 平行。

## 包结构

```
backend/app/sandbox/
├── __init__.py             ← 聚合导出
├── path_guard.py           ← 路径归一化 + 关键目录保护（纯函数）
├── session_sandbox.py      ← SessionSandbox（会话级授权状态，async + Lock）
├── store.py                ← SandboxStore（SQLite 持久化，WAL 模式）
├── schemas.py              ← AuthorizeRequest / RevokeRequest
└── api.py                  ← /api/sandbox/* 路由
```

## 公共 API

### `path_guard.py`

```python
class PathNotAuthorized(Exception): ...

def normalize_path(path: str | Path, base: Path | None = None) -> Path:
    """归一化路径：resolve() + 跨平台 drive 小写 + 正斜杠。"""

def is_under(child: Path, parent: Path) -> bool:
    """判断 child 是否在 parent 下（用 relative_to，非字符串前缀）。"""

def is_critical(path: Path) -> bool:
    """判断是否为系统关键目录。Linux 下 `/` 仅拒绝本身，不拒绝后代。"""

CRITICAL_DIRS: list[Path]  # 平台相关关键目录
DEFAULT_WHITELIST: list[Path]  # WORKSPACE_DIR + UPLOADS_DIR
```

### `session_sandbox.py`

```python
class SessionSandbox:
    def __init__(self, store: SandboxStore | None = None): ...
    
    async def authorize(self, thread_id: str, path: str | Path,
                        writable: bool, source: str = "manual") -> None:
        """授权目录（持久化）。DB-first：先写 DB 再改内存。"""
    
    async def authorize_temp(self, thread_id: str, path: str | Path,
                             writable: bool) -> None:
        """临时授权（仅内存，不持久化，用于 once 决策）。"""
    
    async def revoke(self, thread_id: str, path: str | Path) -> bool:
        """撤销授权。返回是否曾存在。DB + 内存同步。"""
    
    async def check_read(self, thread_id: str, path: str | Path,
                         base: Path | None = None,
                         parent_thread_id: str | None = None) -> Path:
        """校验读权限。返回 resolved path。未授权抛 PathNotAuthorized。"""
    
    async def check_write(self, thread_id: str, path: str | Path,
                          base: Path | None = None,
                          parent_thread_id: str | None = None) -> Path:
        """校验写权限。返回 resolved path。未授权抛 PathNotAuthorized。"""
    
    async def is_path_authorized(self, thread_id: str, path: str | Path,
                                 writable: bool, base: Path | None = None,
                                 parent_thread_id: str | None = None) -> bool:
        """预检查（不抛异常）。支持 parent_thread_id 继承父授权。"""
    
    async def set_full_trust(self, thread_id: str, enabled: bool) -> None:
        """开启/关闭 full_trust 模式（跳过所有授权检查，仅拒绝关键目录）。"""
    
    async def is_full_trust(self, thread_id: str) -> bool: ...
    
    async def register_parent(self, child_thread_id: str, parent_thread_id: str) -> None:
        """注册 parent_thread_id 映射（Team 模式子任务继承父授权）。"""
    
    async def clear(self, thread_id: str) -> None:
        """清除 thread 的所有授权（内存 + DB）。"""
    
    async def snapshot(self, thread_id: str) -> list[tuple[str, bool]]:
        """序列化授权状态（用于 checkpoint）。"""
    
    async def restore(self, thread_id: str, data: list[tuple[str, bool]]) -> None:
        """从 checkpoint 恢复授权状态。"""
    
    async def bootstrap_from_store(self) -> None:
        """启动时从 DB 加载所有授权到内存。"""

def get_sandbox() -> SessionSandbox:
    """模块级单例工厂。"""
```

### `store.py`

```python
@dataclass(frozen=True)
class SandboxEntry:
    thread_id: str
    resolved_path: str
    writable: bool
    source: str  # "manual" | "chip" | "legacy"

class SandboxStore:
    def __init__(self, db_path: Path | None = None): ...
    def upsert(self, entry: SandboxEntry) -> None: ...
    def delete_by_path(self, thread_id: str, resolved_path: str) -> bool: ...
    def delete_by_thread(self, thread_id: str) -> int: ...
    def list_by_thread(self, thread_id: str) -> list[SandboxEntry]: ...
    def bootstrap_all(self) -> list[SandboxEntry]: ...

def get_sandbox_store() -> SandboxStore:
    """模块级单例工厂。"""
```

### `api.py`

```python
def register_sandbox_routes(app: FastAPI) -> None:
    """注册 /api/sandbox/authorize | revoke | authorized/{thread_id} 路由。"""
```

## 并发安全

- `SessionSandbox` 所有公共方法为 `async`，内部用 `asyncio.Lock` 保护
- `_authorized_dirs` / `_full_trust_threads` / `_temp_authorized` / `_parent_map` 均在锁内访问
- `SandboxStore` 每次 `_connect()` 创建新连接（SQLite 连接不可跨线程共享），WAL 模式 + busy_timeout

## 持久化一致性

- **DB-first**：`authorize` 先写 DB（失败抛异常），再改内存
- `revoke` 先删 DB，再改内存（DB 返回值用于判断 existed）
- `bootstrap_from_store` 启动时从 DB 加载，确保重启后状态恢复

## parent_thread_id 机制

- Team 模式子任务用独立 `thread_id`（checkpointer 隔离），但需继承父 thread 的 workspace 授权
- `register_parent(child, parent)` 注册映射
- `check_read` / `check_write` / `is_path_authorized` 接受 `parent_thread_id` 参数，查询时同时检查子 thread_id 和 parent_thread_id
- `clear(child_thread_id)` 仅清理子映射，不影响父授权

## 关键目录保护

- Windows：`C:\Windows` / `C:\Program Files` / `C:\Program Files (x86)` / 系统盘根目录
- Linux：`/` / `/bin` / `/sbin` / `/usr` / `/etc` / `/boot` / `/dev` / `/proc` / `/sys`
- Linux `/` 仅拒绝本身（`path == Path("/")`），不拒绝后代（避免所有绝对路径被误拒）
- macOS：`/` / `/System` / `/Library` / `/usr` / `/private/etc`
- 白名单（始终可读写）：`WORKSPACE_DIR` / `UPLOADS_DIR`

## 错误处理

| 场景 | 行为 |
|---|---|
| 授权关键目录 | 抛 `ValueError("系统关键目录不可授权")` |
| 授权 `.` / `..` | 抛 `ValueError("不能授权当前/父目录引用")` |
| DB 写失败 | 抛异常，内存不更新（保持旧状态） |
| 未授权路径访问 | 抛 `PathNotAuthorized(path)` |
| full_trust 模式 | 跳过授权检查，仅拒绝关键目录 |
| parent_thread_id 查询 | 子未授权时查父，父也未授权才拒绝 |

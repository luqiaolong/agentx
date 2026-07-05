"""E2E 自定义子代理测试: env 注入 + Settings 解析 + build_custom_agent 工厂。"""
import asyncio
import json
import os
import sys

os.environ["AGENTX_MILVUS_AUTH_ENABLED"] = "false"
os.environ["AGENTX_HOST"] = "127.0.0.1"
os.environ["AGENTX_PORT"] = "8124"
os.environ["AGENTX_OPENAI_BASE_URL"] = "https://api.minimax.chat/v1"
os.environ["AGENTX_OPENAI_API_KEY"] = "sk-test-dummy"
os.environ["AGENTX_DEFAULT_MODEL"] = "minimax-m3"
os.environ["AGENTX_EMBEDDING_TIMEOUT"] = "2"

# 注入 2 个自定义子代理 + 1 个危险工具过滤测试
custom_cfg = {
    "code_helper": {
        "key": "code_helper",
        "name": "代码助手",
        "description": "专门用于辅助编码任务的子代理",
        "enabled": True,
        "temperature": 0.3,
        "system_prompt": "你是一个专注于代码任务的子代理。",
        "tools": ["read_file", "grep"],
        "keywords": "代码 编程 调试",
    },
    "doc_writer": {
        "key": "doc_writer",
        "name": "文档撰写专家",
        "description": "专门用于撰写技术文档",
        "enabled": True,
        "temperature": 0.5,
        "system_prompt": "你是一个专注于撰写技术文档的子代理。",
        "tools": ["read_file", "glob", "rag_retrieve"],
        "keywords": "文档 写文档 编写",
    },
    # 故意尝试绑定危险工具 — 验证被过滤
    "evil_agent": {
        "key": "evil_agent",
        "name": "恶意测试",
        "description": "测试危险工具过滤",
        "enabled": True,
        "temperature": 0.1,
        "system_prompt": "",
        "tools": ["read_file", "write_file", "edit_file", "shell_exec"],  # 全是危险工具
        "keywords": "",
    },
    # 故意尝试与内置 key 冲突 — 应被过滤
    "code": {
        "key": "code",
        "name": "should_be_filtered",
        "description": "冲突测试",
        "enabled": True,
        "tools": ["read_file"],
        "keywords": "",
    },
    # 故意使用非法 key — 应被过滤
    "bad key with spaces!": {
        "key": "bad key with spaces!",
        "name": "should_be_filtered_too",
        "tools": ["read_file"],
        "keywords": "",
    },
}
os.environ["AGENTX_CUSTOM_SUBAGENTS_CONFIG"] = json.dumps(custom_cfg)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.config import get_settings  # noqa: E402


def main():
    settings = get_settings()
    print("[1] env injected, parsing custom subagents...")
    parsed = settings.custom_subagents
    print(f"[2] parsed count: {len(parsed)}")
    for key, entry in parsed.items():
        tools_str = ",".join(entry.tools) if entry.tools else "(none)"
        print(f"    - {key} ({entry.name}): tools=[{tools_str}] temp={entry.temperature}")

    # 验证过滤
    print("\n[3] Filtering verification:")
    print(f"    'evil_agent' present (should be True, but tools empty): {'evil_agent' in parsed}")
    if "evil_agent" in parsed:
        print(f"        tools after filter: {parsed['evil_agent'].tools}")
    print(f"    'code' (冲突) NOT present (should be True): {'code' not in parsed}")
    print(f"    bad key NOT present (should be True): {'bad key with spaces!' not in parsed}")

    # 验证合并/默认覆盖
    builtin = settings.subagents
    print(f"\n[4] builtin subagents (sanity):")
    for k, v in builtin.items():
        print(f"    - {k}: enabled={v.enabled} temp={v.temperature}")

    # 检查 tools_enabled
    enabled = settings.tools_enabled
    print(f"\n[5] tools_enabled sample: web_search={enabled.get('web_search')} "
          f"rag_retrieve={enabled.get('rag_retrieve')} write_file={enabled.get('write_file')}")

    ok = (
        len(parsed) == 3  # code_helper + doc_writer + evil_agent (filtered but kept)
        and "code_helper" in parsed
        and "doc_writer" in parsed
        and "code" not in parsed
        and parsed.get("evil_agent", None) is not None
        and len(parsed["evil_agent"].tools) == 0  # 危险工具全部被过滤
    )
    print(f"\nRESULT: {'PASS' if ok else 'FAIL'}")
    return ok


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
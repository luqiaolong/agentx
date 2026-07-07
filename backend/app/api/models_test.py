"""模型连接测试路由（设置 → 模型面板「测试」按钮）。"""

from __future__ import annotations

import time
from urllib.parse import urljoin

import httpx
from fastapi import FastAPI

from app.api.schemas import ModelTestRequest, ModelTestResponse


def register_models_test_routes(app: FastAPI) -> None:
    """注册模型连接测试路由（``POST /api/models/test``）。"""

    @app.post("/api/models/test")
    async def models_test(req: ModelTestRequest) -> ModelTestResponse:
        """发送最小 chat completion 请求验证 provider / model / base_url / api_key 组合是否通。

        设计要点：
        - max_tokens=1 + temperature=0：最低调用成本 + 速度，仅验证连通性
        - 走 httpx 直接 POST，绕过 LangChain ChatOpenAI，避免拖入 LangSmith 追踪 / 长连接等
        - 10s 超时，防止用户被某个不可达服务长时间挂住
        - 不持久化任何状态：纯校验，不写入 store / env

        base_url 与 api_key 均由前端负责组装（renderer 从 MODEL_CATALOG 拿 preset 默认值，
        与用户输入/已存密钥合并后传入），后端不做 provider 路由，避免与 llm.py 路由逻辑双源。
        """
        base_url = (req.base_url or "").strip()
        if not base_url:
            return ModelTestResponse(
                ok=False,
                latency_ms=0,
                message="未指定 base_url（自定义 provider 必须填写）",
            )
        if not req.model.strip():
            return ModelTestResponse(
                ok=False,
                latency_ms=0,
                message="模型名称不能为空",
            )
        if not req.api_key.strip():
            return ModelTestResponse(
                ok=False,
                latency_ms=0,
                message="API Key 不能为空",
            )

        # 拼接 chat completions 端点：保留 base_url 原路径，仅拼接 "chat/completions"
        # 这样 GLM 的 /api/coding/paas/v4、kimi 的 /coding/v1、openai 的 /v1 都能正确路由
        endpoint = urljoin(base_url.rstrip("/") + "/", "chat/completions")

        body = {
            "model": req.model,
            "messages": [{"role": "user", "content": req.prompt}],
            "max_tokens": 1,
            "temperature": 0,
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {req.api_key}",
            "Content-Type": "application/json",
        }

        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(endpoint, headers=headers, json=body)
        except httpx.TimeoutException:
            latency = int((time.monotonic() - t0) * 1000)
            return ModelTestResponse(
                ok=False,
                latency_ms=latency,
                message=f"请求超时（10s）：{endpoint}",
            )
        except httpx.RequestError as exc:
            latency = int((time.monotonic() - t0) * 1000)
            return ModelTestResponse(
                ok=False,
                latency_ms=latency,
                message=f"网络错误：{exc}",
            )

        latency = int((time.monotonic() - t0) * 1000)
        status = resp.status_code

        if 200 <= status < 300:
            # 成功：尝试提取首个 choice 的 content（max_tokens=1 下可能为空字符串）
            response_text: str | None = None
            try:
                data = resp.json()
                choices = data.get("choices", [])
                if choices:
                    response_text = (
                        choices[0].get("message", {}).get("content", "") or ""
                    )
            except Exception:  # noqa: BLE001 — 响应解析兑底，不影响 ok 判定
                response_text = None
            return ModelTestResponse(
                ok=True,
                status_code=status,
                latency_ms=latency,
                message="连接成功",
                response_text=response_text,
            )

        # 失败：提取 OpenAI 风格错误 message 或截取 body 前 200 字符
        err_msg = ""
        try:
            err_body = resp.json()
            if isinstance(err_body, dict):
                err = err_body.get("error")
                if isinstance(err, dict):
                    err_msg = str(err.get("message") or err.get("type") or "")
                elif isinstance(err, str):
                    err_msg = err
                if not err_msg:
                    err_msg = str(err_body.get("message") or "")
        except Exception:
            pass
        if not err_msg:
            body_text = (resp.text or "").strip()
            err_msg = body_text[:200] if body_text else f"HTTP {status}"

        return ModelTestResponse(
            ok=False,
            status_code=status,
            latency_ms=latency,
            message=f"HTTP {status}：{err_msg}",
        )

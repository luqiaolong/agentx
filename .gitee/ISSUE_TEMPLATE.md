# Gitee Go Pipeline（兼容 .github/workflows/ci.yml）
#
# Gitee Go 与 GitHub Actions 工作流格式基本兼容。
# 本文件作为文档参考，实际流水线以 .github/workflows/ci.yml 为准。
# 如需在 Gitee Go 单独配置，可参考以下 stages：
#
# stages:
#   - name: test
#     steps:
#       - name: setup-python
#         image: python:3.13
#         commands: |
#           pip install uv
#           uv sync --extra dev
#       - name: unit-tests
#         image: python:3.13
#         commands: uv run pytest tests/python/unit -q
#       - name: typecheck
#         image: node:20
#         commands: |
#           npm ci
#           npm run typecheck
#
# E2E（同 .github/workflows/ci.yml 的 e2e job）：
#   1. 启动后端（用 secrets.AGENTX_OPENAI_API_KEY 等）
#   2. 运行 uv run pytest tests/python/e2e -v -m e2e
#   3. 失败时附 backend.log
#
# 提示：Gitee Go 当前不支持 label 触发 e2e job，需在 workflow 入口判断变量。
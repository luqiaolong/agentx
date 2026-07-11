@echo off
REM AgentX 健康探测（Windows CMD / PowerShell 友好入口）
REM
REM 用法（在项目根目录下）：
REM   agentx-health
REM   agentx-health -Wait 30
REM
REM 所有参数透传给 scripts/health-check.ps1。

pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0health-check.ps1" %*
exit /b %ERRORLEVEL%
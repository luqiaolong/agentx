@echo off
REM AgentX 停止（Windows CMD / PowerShell 友好入口）
REM
REM 用法（在项目根目录下）：
REM   agentx-stop
REM   agentx-stop -Force
REM
REM 所有参数透传给 scripts/stop.ps1。

pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop.ps1" %*
exit /b %ERRORLEVEL%
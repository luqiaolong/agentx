@echo off
REM AgentX 重启（Windows CMD / PowerShell 友好入口）
REM
REM 用法（在项目根目录下）：
REM   agentx-restart
REM   agentx-restart -NoWait
REM   agentx-restart -Force
REM
REM 所有参数透传给 scripts/restart.ps1。

pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0restart.ps1" %*
exit /b %ERRORLEVEL%
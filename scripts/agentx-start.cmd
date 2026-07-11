@echo off
REM AgentX 启动（Windows CMD / PowerShell 友好入口）
REM
REM 用法（在项目根目录下）：
REM   agentx-start
REM   agentx-start -NoWait
REM   agentx-start -Clean
REM
REM 所有参数透传给 scripts/start.ps1。

pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
exit /b %ERRORLEVEL%
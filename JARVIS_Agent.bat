@echo off
title JARVIS Laptop Agent
cd /d "%~dp0"
echo [JARVIS] Laptop Agent 시작 중...
echo [JARVIS] 서버: https://jarvis-yejun.duckdns.org
echo.
python backend/local/laptop_agent.py
pause

@echo off
chcp 65001 >nul
title 台股智慧更新系統
cd /d F:\stock
python scripts\smart_update.py %*

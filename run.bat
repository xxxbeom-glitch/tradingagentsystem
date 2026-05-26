@echo off
cd /d D:\project\tradingagentsystem
call .venv\Scripts\activate
python main.py >> logs\run.log 2>&1

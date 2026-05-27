@echo off
cd /d D:\project\tradingagentsystem
call venv\Scripts\activate
start "dashboard" python -m http.server 8000
timeout /t 2 /nobreak > nul
start http://localhost:8000/dashboard.html
python main.py >> logs\run.log 2>&1

@echo off
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
  call ".venv\Scripts\activate.bat"
) else (
  echo [INFO] .venv not found. Using system Python.
)
set "DATABASE_ENGINE=postgres"
if "%POSTGRES_HOST%"=="" set "POSTGRES_HOST=127.0.0.1"
if "%POSTGRES_PORT%"=="" set "POSTGRES_PORT=5432"
if "%POSTGRES_DB%"=="" set "POSTGRES_DB=perfumex_local"
if "%POSTGRES_USER%"=="" set "POSTGRES_USER=postgres"
if "%POSTGRES_PASSWORD%"=="" set "POSTGRES_PASSWORD=postgres"
python manage.py runserver 127.0.0.1:8000 --noreload
pause

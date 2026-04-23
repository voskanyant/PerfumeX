@echo off
setlocal EnableExtensions

cd /d "%~dp0"

if not exist "db_sync.config.cmd" (
  echo [ERROR] Missing scripts\db_sync.config.cmd
  echo Copy scripts\db_sync.config.example.cmd to scripts\db_sync.config.cmd and fill values.
  exit /b 1
)

call "db_sync.config.cmd"

if "%LOCAL_DUMP_PATH%"=="" (
  echo [ERROR] LOCAL_DUMP_PATH is empty in db_sync.config.cmd
  exit /b 1
)
if "%LOCAL_POSTGRES_DB%"=="" (
  echo [ERROR] LOCAL_POSTGRES_DB is empty in db_sync.config.cmd
  exit /b 1
)
if not exist "%LOCAL_DUMP_PATH%" (
  echo [ERROR] Dump file not found: %LOCAL_DUMP_PATH%
  exit /b 1
)

where dropdb >nul 2>nul
if errorlevel 1 (
  echo [ERROR] dropdb not found in PATH. Install PostgreSQL client tools or add bin folder to PATH.
  exit /b 1
)
where createdb >nul 2>nul
if errorlevel 1 (
  echo [ERROR] createdb not found in PATH. Install PostgreSQL client tools or add bin folder to PATH.
  exit /b 1
)
where pg_restore >nul 2>nul
if errorlevel 1 (
  echo [ERROR] pg_restore not found in PATH. Install PostgreSQL client tools or add bin folder to PATH.
  exit /b 1
)

echo [1/4] Recreating local DB: %LOCAL_POSTGRES_DB%
dropdb --if-exists "%LOCAL_POSTGRES_DB%"
createdb "%LOCAL_POSTGRES_DB%"
if errorlevel 1 (
  echo [ERROR] Failed to create local DB.
  exit /b 1
)

echo [2/4] Restoring dump into local DB...
pg_restore --clean --if-exists --no-owner --no-privileges -d "%LOCAL_POSTGRES_DB%" "%LOCAL_DUMP_PATH%"
if errorlevel 1 (
  echo [ERROR] pg_restore failed.
  exit /b 1
)

echo [3/4] Restore complete.
if /I "%RUN_MIGRATE_AFTER_RESTORE%"=="1" (
  echo [4/4] Running Django migrations...
  cd /d "%~dp0.."
  if exist ".venv\Scripts\python.exe" (
    call ".venv\Scripts\activate.bat"
    python manage.py migrate
    if errorlevel 1 (
      echo [ERROR] migrate failed.
      exit /b 1
    )
  ) else (
    python manage.py migrate
    if errorlevel 1 (
      echo [ERROR] migrate failed.
      exit /b 1
    )
  )
)

echo [OK] Local DB synced and ready.

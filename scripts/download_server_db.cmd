@echo off
setlocal EnableExtensions

cd /d "%~dp0"

if not exist "db_sync.config.cmd" (
  echo [ERROR] Missing scripts\db_sync.config.cmd
  echo Copy scripts\db_sync.config.example.cmd to scripts\db_sync.config.cmd and fill values.
  exit /b 1
)

call "db_sync.config.cmd"

if "%SERVER_USER%"=="" (
  echo [ERROR] SERVER_USER is empty in db_sync.config.cmd
  exit /b 1
)
if "%SERVER_HOST%"=="" (
  echo [ERROR] SERVER_HOST is empty in db_sync.config.cmd
  exit /b 1
)
if "%SERVER_PROJECT_DIR%"=="" (
  echo [ERROR] SERVER_PROJECT_DIR is empty in db_sync.config.cmd
  exit /b 1
)
if "%SERVER_ENV_FILE%"=="" (
  echo [ERROR] SERVER_ENV_FILE is empty in db_sync.config.cmd
  exit /b 1
)
if "%SERVER_BACKUP_DIR%"=="" (
  echo [ERROR] SERVER_BACKUP_DIR is empty in db_sync.config.cmd
  exit /b 1
)
if "%SERVER_DUMP_FILE%"=="" (
  echo [ERROR] SERVER_DUMP_FILE is empty in db_sync.config.cmd
  exit /b 1
)
if "%LOCAL_DUMP_PATH%"=="" (
  echo [ERROR] LOCAL_DUMP_PATH is empty in db_sync.config.cmd
  exit /b 1
)
if "%SERVER_SSH_PORT%"=="" set "SERVER_SSH_PORT=22"

echo [1/2] Creating fresh dump on server...
ssh -p "%SERVER_SSH_PORT%" %SERVER_USER%@%SERVER_HOST% "set -e; mkdir -p '%SERVER_BACKUP_DIR%'; cd '%SERVER_PROJECT_DIR%'; set -a; . '%SERVER_ENV_FILE%'; set +a; pg_dump -Fc -f '%SERVER_BACKUP_DIR%/%SERVER_DUMP_FILE%' \"$POSTGRES_DB\""
if errorlevel 1 (
  echo [ERROR] Failed to create dump on server.
  exit /b 1
)

echo [2/2] Downloading dump to local: %LOCAL_DUMP_PATH%
scp -P "%SERVER_SSH_PORT%" %SERVER_USER%@%SERVER_HOST%:%SERVER_BACKUP_DIR%/%SERVER_DUMP_FILE% "%LOCAL_DUMP_PATH%"
if errorlevel 1 (
  echo [ERROR] Failed to download dump file.
  exit /b 1
)

echo [OK] Dump downloaded: %LOCAL_DUMP_PATH%

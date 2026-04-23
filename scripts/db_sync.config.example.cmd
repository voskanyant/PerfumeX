@echo off
rem Copy this file to db_sync.config.cmd and fill values.

rem Server SSH
set "SERVER_USER=your_user"
set "SERVER_HOST=your.server.com"
set "SERVER_SSH_PORT=22"

rem Server paths
set "SERVER_PROJECT_DIR=/opt/perfumex/PerfumeX"
set "SERVER_ENV_FILE=/opt/perfumex/PerfumeX/.env"
set "SERVER_BACKUP_DIR=/opt/perfumex/backups"
set "SERVER_DUMP_FILE=perfumex_latest.dump"

rem Local dump target path
set "LOCAL_DUMP_PATH=%USERPROFILE%\Downloads\perfumex_latest.dump"

rem Local restore target DB
set "LOCAL_POSTGRES_DB=perfumex_local"
set "RUN_MIGRATE_AFTER_RESTORE=1"

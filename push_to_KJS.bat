@echo off
setlocal
cd /d "%~dp0"

set REPO=https://github.com/fetex-Team/Fetex.git
set BRANCH=KJS

echo === [1/6] .gitignore ===
if exist .ginore if not exist .gitignore ren .ginore .gitignore
if not exist .gitignore type nul > .gitignore
findstr /c:"# auto-added" .gitignore >nul 2>&1
if errorlevel 1 (
    >> .gitignore echo.
    >> .gitignore echo # auto-added
    >> .gitignore echo venv/
    >> .gitignore echo __pycache__/
    >> .gitignore echo *.pyc
    >> .gitignore echo *.zip
    >> .gitignore echo tree.txt
    >> .gitignore echo _result_combo*.json
    >> .gitignore echo ab_results/
    >> .gitignore echo logs/
    >> .gitignore echo *_backups/
    >> .gitignore echo _backup_custom_dispatch/
    >> .gitignore echo .env
)

echo.
echo === [2/6] files over 90MB (GitHub limit is 100MB) ===
powershell -NoProfile -Command "Get-ChildItem -Recurse -File -ErrorAction SilentlyContinue | Where-Object { $_.Length -gt 90MB -and $_.FullName -notmatch '\\venv\\' -and $_.FullName -notmatch '\\.git\\' } | ForEach-Object { '{0}  {1} MB' -f $_.FullName, [math]::Round($_.Length/1MB,1) }"
echo (nothing listed above = OK)

echo.
echo === [3/6] git init / remote ===
if not exist .git git init
git remote get-url origin >nul 2>&1
if errorlevel 1 (
    git remote add origin %REPO%
) else (
    git remote set-url origin %REPO%
)
git fetch origin
if errorlevel 1 (
    echo fetch failed. Check network / GitHub login.
    pause
    exit /b 1
)

echo.
echo === [4/6] switch to %BRANCH% ===
git ls-remote --exit-code --heads origin %BRANCH% >nul 2>&1
if errorlevel 1 (
    echo remote %BRANCH% not found - creating new branch
    git checkout -B %BRANCH%
) else (
    echo remote %BRANCH% found - keeping local files, syncing history
    git symbolic-ref HEAD refs/heads/%BRANCH%
    git reset origin/%BRANCH% >nul
)

echo.
echo === [5/6] stage ===
git add --ignore-removal .
git status --short
echo.
set /p OK=Commit and push to %BRANCH%? (y/n): 
if /i not "%OK%"=="y" (
    echo cancelled.
    pause
    exit /b 0
)

set MSG=
set /p MSG=Commit message (Enter = default): 
if "%MSG%"=="" set MSG=upload 20260916verMergeFinish

echo.
echo === [6/6] commit and push ===
git commit -m "%MSG%"
git push -u origin %BRANCH%
if errorlevel 1 (
    echo.
    echo push failed. Copy the error message above.
) else (
    echo.
    echo done: https://github.com/fetex-Team/Fetex/tree/%BRANCH%
)
pause
endlocal

@echo off
REM Run all frontend tests (no browser needed).
REM   web/logic.js      -> pure logic, tested by tests/web_logic.test.cjs
REM   web/app.js        -> DOM behaviour, tested by tests/web_dom.test.cjs (jsdom)
REM ASCII only: folder names contain Chinese characters, so keep this file plain.
setlocal
cd /d "%~dp0"
set NODE_PATH=%~dp0frontend\node_modules

echo === pure logic (Node) ===
"C:\Program Files\nodejs\node.exe" tests\web_logic.test.cjs
if errorlevel 1 goto fail

echo.
echo === DOM scenario (jsdom) ===
"C:\Program Files\nodejs\node.exe" tests\web_dom.test.cjs
if errorlevel 1 goto fail

echo.
echo === stylesheet (parse + key rules) ===
"C:\Program Files\nodejs\node.exe" tests\web_style.test.cjs
if errorlevel 1 goto fail

echo.
echo === real data render (skipped if no snapshot) ===
"C:\Program Files\nodejs\node.exe" tests\web_realdata.test.cjs
if errorlevel 1 goto fail

echo.
echo === syntax check ===
"C:\Program Files\nodejs\node.exe" --check web\app.js
if errorlevel 1 goto fail
"C:\Program Files\nodejs\node.exe" --check web\logic.js
if errorlevel 1 goto fail

echo.
echo All frontend checks passed.
goto :eof

:fail
echo.
echo FRONTEND CHECKS FAILED
exit /b 1

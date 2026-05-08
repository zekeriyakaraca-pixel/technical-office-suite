@echo off
setlocal

set "PROJECT_ROOT=%~dp0.."
set "PYTHON=%PROJECT_ROOT%\.venv\Scripts\python.exe"
set "WORKSPACE_ROOT=%PROJECT_ROOT%\.."
set "TEKLA_SITE_PACKAGES=%WORKSPACE_ROOT%\tekla-mcp-server\.venv\Lib\site-packages"
set "PYTHONPATH=%PROJECT_ROOT%\src;%PYTHONPATH%"
if exist "%TEKLA_SITE_PACKAGES%" (
  set "AUTOCAD_MCP_OPTIONAL_SITE_PACKAGES=%TEKLA_SITE_PACKAGES%"
)

if not exist "%PYTHON%" (
  echo Python environment not found: %PYTHON% 1>&2
  exit /b 1
)

"%PYTHON%" -m autocad_mcp.technical_office %*
exit /b %ERRORLEVEL%

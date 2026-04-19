@echo off
setlocal EnableExtensions
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
    set "PY=py -3"
) else (
    where python >nul 2>nul
    if %errorlevel% neq 0 (
        echo [ERROR] Python 3.10+ not found in PATH.
        pause
        exit /b 1
    )
    set "PY=python"
)

if defined BUILD_CHOICE goto ROUTE

:MENU
echo ==============================================
echo   SayaTech MIDI Studio 1.0.5 - Onedir Installer Build
echo ==============================================
echo [1] CPU精简版（安装包更小，GPU加速不可用）
echo [2] GPU完整版（需已安装CUDA版 torch）
echo [3] 两个版本都打包
echo [4] 退出
echo.
set /p BUILD_CHOICE=请选择要打包的版本： 

:ROUTE
if "%BUILD_CHOICE%"=="1" goto BUILD_CPU
if "%BUILD_CHOICE%"=="2" goto BUILD_GPU
if "%BUILD_CHOICE%"=="3" goto BUILD_BOTH
if "%BUILD_CHOICE%"=="4" exit /b 0
echo.
echo 输入无效，请重新选择。
echo.
goto MENU

:PREPARE
if not exist config.txt copy /Y config.example.txt config.txt >nul
%PY% -m pip install -r requirements.txt pyinstaller || goto FAIL
if exist build rmdir /s /q build
exit /b 0

:CHECK_INNO
set "ISCC=%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"
if exist "%ISCC%" exit /b 0
echo [ERROR] 未找到 Inno Setup 6：%ISCC%
pause
exit /b 1

:CHECK_GPU_ENV
set "GPU_CHECK=%TEMP%\sayatech_gpu_check.py"
>"%GPU_CHECK%" echo import importlib.util
>>"%GPU_CHECK%" echo spec = importlib.util.find_spec("torch")
>>"%GPU_CHECK%" echo print("[GPU CHECK] torch_installed=", bool(spec))
>>"%GPU_CHECK%" echo assert spec is not None, "torch is not installed"
>>"%GPU_CHECK%" echo import torch
>>"%GPU_CHECK%" echo print("[GPU CHECK] torch_version=", torch.__version__)
>>"%GPU_CHECK%" echo print("[GPU CHECK] torch_cuda_version=", getattr(torch.version, "cuda", None))
>>"%GPU_CHECK%" echo print("[GPU CHECK] cuda_available=", torch.cuda.is_available())
>>"%GPU_CHECK%" echo assert getattr(torch.version, "cuda", None), "torch is not a CUDA build"
>>"%GPU_CHECK%" echo assert bool(getattr(getattr(torch, "backends", None), "cuda", None) and torch.backends.cuda.is_built()), "torch CUDA backend is not built"
%PY% "%GPU_CHECK%"
set "GPU_CHECK_RC=%errorlevel%"
del /q "%GPU_CHECK%" >nul 2>nul
if not "%GPU_CHECK_RC%"=="0" goto FAIL
exit /b 0

:RUN_INNO_CPU
"%ISCC%" "installer_cpu.iss"
if errorlevel 1 goto FAIL
exit /b 0

:RUN_INNO_GPU
"%ISCC%" "installer_gpu.iss"
if errorlevel 1 goto FAIL
exit /b 0

:BUILD_CPU
call :PREPARE || goto FAIL
call :CHECK_INNO || goto FAIL
echo.
echo [CPU] 开始构建 CPU精简版...
%PY% -m PyInstaller --clean --noconfirm SayaTech_MIDI_Studio_onedir.spec || goto FAIL
call :RUN_INNO_CPU || goto FAIL
echo.
echo CPU精简版完成：dist\SayaTech_MIDI_Studio_CPU
echo 安装包：installer_output\SayaTech_MIDI_Studio_CPU_Setup_v1.0.5.exe
pause
exit /b 0

:BUILD_GPU
call :PREPARE || goto FAIL
call :CHECK_INNO || goto FAIL
call :CHECK_GPU_ENV || goto FAIL
echo.
echo [GPU] 开始构建 GPU完整版...
%PY% -m PyInstaller --clean --noconfirm SayaTech_MIDI_Studio_onedir_gpu.spec || goto FAIL
call :RUN_INNO_GPU || goto FAIL
echo.
echo GPU完整版完成：dist\SayaTech_MIDI_Studio_GPU
echo 安装包：installer_output\SayaTech_MIDI_Studio_GPU_Setup_v1.0.5.exe
pause
exit /b 0

:BUILD_BOTH
call :PREPARE || goto FAIL
call :CHECK_INNO || goto FAIL
echo.
echo [CPU] 开始构建 CPU精简版...
%PY% -m PyInstaller --clean --noconfirm SayaTech_MIDI_Studio_onedir.spec || goto FAIL
call :RUN_INNO_CPU || goto FAIL
call :CHECK_GPU_ENV || goto FAIL
echo.
echo [GPU] 开始构建 GPU完整版...
%PY% -m PyInstaller --clean --noconfirm SayaTech_MIDI_Studio_onedir_gpu.spec || goto FAIL
call :RUN_INNO_GPU || goto FAIL
echo.
echo 两个版本都已完成。
echo CPU安装包：installer_output\SayaTech_MIDI_Studio_CPU_Setup_v1.0.5.exe
echo GPU安装包：installer_output\SayaTech_MIDI_Studio_GPU_Setup_v1.0.5.exe
pause
exit /b 0

:FAIL
echo.
echo 构建失败，请查看上面的报错信息。
pause
exit /b 1

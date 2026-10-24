# 智慧教育 RAG 后端启动脚本（2026-10-24）
# 本机全局 PYTHONPATH 指向 D:\Lib\site-packages（pydantic 1.x），
# 会覆盖项目 venv 的 pydantic 2.x，导致 RAG 导入失败、/health/ready 500。
# 这里显式清空 PYTHONPATH，并固定使用项目 venv。

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = Join-Path $PSScriptRoot "venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "找不到项目虚拟环境：$python。请先运行 python -m venv venv 并安装 requirements.txt"
}

$env:PYTHONPATH = ""
Write-Host "启动后端：$python -m uvicorn api.app:app --host 0.0.0.0 --port 8000"
& $python -m uvicorn api.app:app --host 0.0.0.0 --port 8000 --log-level info

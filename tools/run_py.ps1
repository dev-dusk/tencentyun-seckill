param(
    [string]$Script = "",
    [string]$LogName = "run",
    [string[]]$ExtraArgs = @(),
    [switch]$Test
)
# 通用运行器: 用 .venv 的 python 运行项目脚本, 输出实时上屏并 Tee 落盘 logs/<LogName>.log
# 注意: 参数不叫 $Arguments —— 它与 PowerShell 自动变量 $args 大小写不敏感冲突;
#       参数一律不设 Mandatory, 缺参直接报错退出, 绝不交互式询问 (双击场景会卡死)
$ErrorActionPreference = "Continue"
if (-not $Script -and -not $Test) {
    Write-Output "[错误] 缺少参数. 用法: run_py.ps1 -Script xxx.py -LogName xxx [-ExtraArgs a,b] 或 -Test"
    exit 1
}
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
$OutputEncoding = [System.Text.Encoding]::UTF8

$Root = Split-Path -Parent $PSScriptRoot
$LogDir = Join-Path $Root "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Log = Join-Path $LogDir "$LogName.log"
$Py = Join-Path $Root ".venv\Scripts\python.exe"

# 兼容把多个参数合并成一个逗号串的传法 (如 '-ExtraArgs "--mode,check"')
$ExtraArgs = @($ExtraArgs | ForEach-Object { $_ -split ',' } | Where-Object { $_ -ne "" })

"===== [$LogName] 启动 $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') =====" | Tee-Object -FilePath $Log -Append
if (-not (Test-Path $Py)) {
    "[错误] 未找到虚拟环境 $Py" | Tee-Object -FilePath $Log -Append
    "[提示] 请先双击 install.bat 完成安装" | Tee-Object -FilePath $Log -Append
    exit 1
}

$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
$env:PYTHONUNBUFFERED = "1"

if ($Test) {
    & $Py -m pytest (Join-Path $Root "tests") -v --tb=short -o cache_dir=logs/pytest_cache --basetemp=logs/pytest_tmp @ExtraArgs 2>&1 | Tee-Object -FilePath $Log -Append
} else {
    & $Py (Join-Path $Root $Script) @ExtraArgs 2>&1 | Tee-Object -FilePath $Log -Append
}
$code = $LASTEXITCODE
"===== [$LogName] 结束 退出码=$code =====" | Tee-Object -FilePath $Log -Append
exit $code

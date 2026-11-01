# 安装脚本 (install.bat 调用): 检查 Python -> 建 .venv -> 装依赖 -> 装 Chromium -> 环境验证
# 全程输出实时上屏并 Tee 落盘 logs/install.log
$ErrorActionPreference = "Continue"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
$OutputEncoding = [System.Text.Encoding]::UTF8

$Root = Split-Path -Parent $PSScriptRoot
$LogDir = Join-Path $Root "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Log = Join-Path $LogDir "install.log"
$VenvPy = Join-Path $Root ".venv\Scripts\python.exe"

function Log([string]$msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $msg
    Write-Output $line
    Add-Content -Path $Log -Value $line -Encoding UTF8
}

function Run([string]$exe, [string[]]$argList, [string]$tag) {
    Log "运行: $exe $($argList -join ' ')"
    & $exe @argList 2>&1 | ForEach-Object {
        $t = ($_ | Out-String).TrimEnd("`r`n")
        if ($t) { Write-Output $t; Add-Content -Path $Log -Value $t -Encoding UTF8 }
    }
    if ($LASTEXITCODE -ne 0) {
        Log "[失败] $tag (退出码 $LASTEXITCODE)"
        return $false
    }
    Log "[成功] $tag"
    return $true
}

"===== install.bat $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') =====" | Out-File -FilePath $Log -Encoding UTF8
Log "项目目录: $Root"
Log "Windows: $([System.Environment]::OSVersion.VersionString) ($env:PROCESSOR_ARCHITECTURE)"

# ---------- 1. 检查 Python (优先 py 启动器, 兼容 3.11/3.12) ----------
$PyCmd = $null; $PyPre = @()
$found = & py -3 --version 2>&1
if ($LASTEXITCODE -eq 0 -and "$found" -match "Python 3\.(11|12|13|\d+)") {
    $PyCmd = "py"; $PyPre = @("-3")
    Log "检测到 Python Launcher: $found"
} else {
    $found2 = & python --version 2>&1
    if ($LASTEXITCODE -eq 0 -and "$found2" -match "Python 3\.\d+") {
        $PyCmd = "python"; $PyPre = @()
        Log "检测到 Python: $found2"
    }
}
if (-not $PyCmd) {
    Log "[缺失] 未找到 Python 3 (py / python 均不可用)"
    Log "将尝试用 winget 安装官方 Python 3.12 (来源: winget 官方源 Python.Python.3.12)"
    $ans = Read-Host "是否现在用 winget 安装 Python 3.12? (y/N)"
    if ($ans -eq "y" -or $ans -eq "Y") {
        $ok = Run "winget" @("install", "-e", "--id", "Python.Python.3.12", "--scope", "user") "winget 安装 Python"
        if ($ok) {
            Log "winget 安装完成。注意: 新装的 Python 需要重新打开本窗口后 PATH 才生效,"
            Log "请关闭本窗口后重新双击 install.bat。"
        } else {
            Log "winget 安装失败, 请手动安装 Python 3.12: https://www.python.org/downloads/"
        }
    } else {
        Log "已跳过自动安装。请安装 Python 3.11/3.12 后重新运行 install.bat"
        Log "官方下载: https://www.python.org/downloads/ (安装时勾选 Add python.exe to PATH)"
    }
    exit 1
}

# ---------- 2. 虚拟环境 ----------
if (Test-Path $VenvPy) {
    Log "虚拟环境已存在, 复用: $VenvPy"
} else {
    $ok = Run $PyCmd ($PyPre + @("-m", "venv", (Join-Path $Root ".venv"))) "创建虚拟环境 .venv"
    if (-not $ok) { exit 1 }
}
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

# ---------- 3. 依赖 ----------
$ok = Run $VenvPy @("-m", "pip", "install", "--upgrade", "pip") "升级 pip"
$req = Join-Path $Root "requirements.txt"
$ok = Run $VenvPy @("-m", "pip", "install", "-r", $req) "安装依赖 (默认源)"
if (-not $ok) {
    Log "默认源失败, 改用清华镜像源重试..."
    $ok = Run $VenvPy @("-m", "pip", "install", "-r", $req, "-i", "https://pypi.tuna.tsinghua.edu.cn/simple") "安装依赖 (清华镜像)"
    if (-not $ok) { exit 1 }
}

# ---------- 4. Playwright Chromium ----------
Log "安装/校验 Playwright Chromium (已存在则秒过; 首次约 200MB, 国内镜像优先)"
Log "下载中若进度条停在屏幕上属正常, 请耐心等待完成..."
# 国内网络优先 npmmirror 镜像 (快), 失败再回退官方 CDN
$env:PLAYWRIGHT_DOWNLOAD_HOST = "https://cdn.npmmirror.com/binaries/playwright"
$ok = Run $VenvPy @("-m", "playwright", "install", "chromium") "Playwright Chromium (npmmirror镜像)"
if (-not $ok) {
    Log "镜像下载失败, 回退官方 CDN 重试..."
    Remove-Item Env:\PLAYWRIGHT_DOWNLOAD_HOST -ErrorAction SilentlyContinue
    $ok = Run $VenvPy @("-m", "playwright", "install", "chromium") "Playwright Chromium (官方CDN)"
    if (-not $ok) {
        Log "Chromium 下载失败 (网络原因?), 可稍后手动执行: .venv\Scripts\python.exe -m playwright install chromium"
        exit 1
    }
}

# ---------- 5. 环境验证 ----------
$ok = Run $VenvPy @((Join-Path $Root "tools\verify_env.py")) "环境验证 (依赖/Chromium/JSON/北京时间)"
if (-not $ok) { exit 1 }

Log "全部安装与验证完成! 下一步: 双击 login.bat 扫码登录"
exit 0

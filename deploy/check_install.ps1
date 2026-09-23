<#
.SYNOPSIS
    concept memory 绿色包自检脚本：在任何解压位置都能运行，判断这个包能否直接使用。

.DESCRIPTION
    只使用 PowerShell 自身能力，不依赖包以外的任何程序，也不写死任何绝对路径。
    逐项检查：PKG_ROOT 推导、包内 Python 版本、关键模块导入、安装文件齐全性、
    以及包内是否残留打包机的绝对路径（可移植性回归检查）。
    全部通过时输出 PASS 并以退出码 0 结束；任何一项失败时输出 FAIL 并以非 0 退出。

.USAGE
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install\check_install.ps1
    （Windows PowerShell 5.1 与 PowerShell 7 均可运行；脚本以 UTF-8 BOM 保存，中文输出不会乱码。）
#>

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Continue'

$FailCount = 0
$WarnCount = 0

function Write-Pass {
    param([string]$Message)
    Write-Host "  [通过] $Message" -ForegroundColor Green
}

function Write-Fail {
    param([string]$Message)
    $script:FailCount++
    Write-Host "  [失败] $Message" -ForegroundColor Red
}

function Write-Warn {
    param([string]$Message)
    $script:WarnCount++
    Write-Host "  [警告] $Message" -ForegroundColor Yellow
}

function Write-Info {
    param([string]$Message)
    Write-Host "         $Message" -ForegroundColor Gray
}

Write-Host ""
Write-Host "==============================================================" -ForegroundColor Cyan
Write-Host " concept memory 安装包自检" -ForegroundColor Cyan
Write-Host "==============================================================" -ForegroundColor Cyan

# ---------------------------------------------------------------- ① PKG_ROOT
Write-Host ""
Write-Host "[1/6] 推导包根目录 PKG_ROOT" -ForegroundColor White

$pkgRoot = ''
try {
    $scriptDir = $PSScriptRoot
    if ([string]::IsNullOrWhiteSpace($scriptDir)) {
        # 兜底：直接从当前脚本路径推导，适配非常规调用方式
        $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    }
    if ([string]::IsNullOrWhiteSpace($scriptDir)) {
        throw '无法确定脚本自身所在目录'
    }
    $scriptDir = [System.IO.Path]::GetFullPath($scriptDir)
    $pkgRoot = [System.IO.Path]::GetFullPath((Join-Path $scriptDir '..'))
}
catch {
    Write-Fail "无法推导包根目录：$($_.Exception.Message)"
}

if ($pkgRoot -ne '') {
    if (Test-Path -LiteralPath $pkgRoot -PathType Container) {
        Write-Pass "PKG_ROOT = $pkgRoot"
        Write-Info "该路径由脚本自身位置推导（.. 目录），不含任何硬编码路径。"
        if ($pkgRoot -match '[ \u4e00-\u9fff]') {
            Write-Info "路径含空格或中文，属于受支持的场景。"
        }
    }
    else {
        Write-Fail "推导出的 PKG_ROOT 不存在或不是目录：$pkgRoot"
    }
}

# 统一用 Set-Location 进入包目录，后续全部使用相对路径，
# 避免路径中的空格与中文在原生程序调用时被拆开。
if ($pkgRoot -ne '' -and (Test-Path -LiteralPath $pkgRoot -PathType Container)) {
    Set-Location -LiteralPath $pkgRoot
}

$pythonExe = Join-Path $pkgRoot 'python\python.exe'
$sitePackages = Join-Path $pkgRoot 'python\Lib\site-packages'
$srcDir = Join-Path $pkgRoot 'src'

# -------------------------------------------------------- ① 附：包版本号核对
Write-Host ""
Write-Host "[1/6 附] 确认包版本号" -ForegroundColor White

$pkgVersion = ''
$versionFile = Join-Path $pkgRoot 'VERSION'
if (Test-Path -LiteralPath $versionFile -PathType Leaf) {
    $rawVersion = Get-Content -LiteralPath $versionFile -Raw -ErrorAction SilentlyContinue
    if ($null -ne $rawVersion) {
        $pkgVersion = $rawVersion.Trim()
    }
    if ([string]::IsNullOrWhiteSpace($pkgVersion)) {
        Write-Fail "VERSION 文件是空的：$versionFile"
    }
    else {
        Write-Pass "本包版本号：$pkgVersion（升级或报障时先报这个号）"
    }
}
else {
    Write-Fail "缺少 VERSION 文件：$versionFile（包不完整，请用完整 zip 重新解压）"
}

$initPath = Join-Path $pkgRoot 'src\memory_system\__init__.py'
if ($pkgVersion -ne '' -and (Test-Path -LiteralPath $initPath -PathType Leaf)) {
    $declaredVersion = ''
    # 必须显式 -Encoding UTF8：Windows PowerShell 5.1 默认按 ANSI/GBK 读，
    # 会把源码里的 UTF-8 中文注释与下一行粘在一起，导致匹配不到 __version__。
    foreach ($line in @(Get-Content -LiteralPath $initPath -Encoding UTF8 -ErrorAction SilentlyContinue)) {
        if ($line -match '^__version__\s*=\s*[''"]([^''"]+)[''"]') {
            $declaredVersion = $Matches[1]
            break
        }
    }
    if ($declaredVersion -eq '') {
        Write-Warn "源码里读不到 __version__，跳过一致性核对：$initPath"
    }
    elseif ($declaredVersion -ne $pkgVersion) {
        Write-Fail "版本号不一致：VERSION 是 $pkgVersion，源码 __version__ 是 $declaredVersion。包被手工改过，请用完整 zip 重新解压。"
    }
    else {
        Write-Pass "VERSION 与源码 __version__ 一致（$declaredVersion）"
    }
}

# ------------------------------------------------------------ ② Python 版本
Write-Host ""
Write-Host "[2/6] 检查包内 Python 解释器（需要 3.11 或更高）" -ForegroundColor White

$pythonVersionText = ''
if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) {
    Write-Fail "包内解释器缺失：$pythonExe"
    Write-Info "完整包应带有精简运行时 python\python.exe，请确认解压是否完整。"
}
else {
    Write-Pass "包内解释器存在：$pythonExe"
    $versionOutput = & $pythonExe --version 2>&1
    $pythonVersionText = ($versionOutput | Out-String).Trim()
    $versionMatch = [regex]::Match($pythonVersionText, '(\d+)\.(\d+)\.(\d+)')
    if (-not $versionMatch.Success) {
        Write-Fail "无法解析 Python 版本号，原始输出：$pythonVersionText"
    }
    else {
        $major = [int]$versionMatch.Groups[1].Value
        $minor = [int]$versionMatch.Groups[2].Value
        if (($major -gt 3) -or (($major -eq 3) -and ($minor -ge 11))) {
            Write-Pass "版本满足要求：$pythonVersionText"
        }
        else {
            Write-Fail "版本过低：$pythonVersionText（需要 3.11 或更高）"
        }
    }

    # 隔离模式会让 DLLs 目录脱离 sys.path，导致 import _sqlite3 失败
    $pthFile = Join-Path $pkgRoot 'python\python311._pth'
    if (Test-Path -LiteralPath $pthFile) {
        Write-Fail "发现 python311._pth：它会让解释器进入隔离模式，DLLs 目录脱离 sys.path，import _sqlite3 会失败，必须删除该文件。"
    }
    else {
        Write-Pass "未发现 python\python311._pth（正确，依赖由 PYTHONPATH 提供）"
    }

    $sqliteDll = Join-Path $pkgRoot 'python\DLLs\_sqlite3.pyd'
    if (Test-Path -LiteralPath $sqliteDll -PathType Leaf) {
        Write-Pass "SQLite 扩展存在：python\DLLs\_sqlite3.pyd"
    }
    else {
        Write-Fail "缺少 python\DLLs\_sqlite3.pyd，存储层无法工作。"
    }
}

# ------------------------------------------------------- ③ 模块导入（PYTHONPATH）
Write-Host ""
Write-Host "[3/6] 用 PYTHONPATH 启动包内解释器并逐个导入关键模块" -ForegroundColor White

if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) {
    Write-Fail "包内解释器不可用，跳过导入检查。"
}
else {
    $pythonPathValue = "$sitePackages;$srcDir"
    Write-Info "PYTHONPATH = $pythonPathValue"
    Write-Info "（两个目录用分号分隔；不依赖任何 ._pth 文件）"

    $previousPythonPath = $env:PYTHONPATH
    $previousPythonIo = $env:PYTHONIOENCODING
    $previousNoBytecode = $env:PYTHONDONTWRITEBYTECODE
    $env:PYTHONPATH = $pythonPathValue
    # 强制子进程用 UTF-8 输出，避免中文错误信息在管道里变成乱码
    $env:PYTHONIOENCODING = 'utf-8'
    # 自检不得污染被检查的包（否则会在 src 下写出 __pycache__）
    $env:PYTHONDONTWRITEBYTECODE = '1'

    $importSnippet = @'
import importlib
import sys

results = []
for name in ['sqlite3', 'mcp', 'dashscope', 'dotenv', 'memory_system.mcp_server']:
    try:
        importlib.import_module(name)
        results.append('OK|' + name + '|')
    except BaseException as exc:
        missing = getattr(exc, 'name', None)
        detail = type(exc).__name__ + ': ' + str(exc)
        if missing:
            detail = detail + ' [缺少的模块: ' + str(missing) + ']'
        results.append('FAIL|' + name + '|' + detail)

print('PYTHON|' + sys.version.split()[0])
print('EXECUTABLE|' + sys.executable)
print('CHECK|' + '|'.join(results))
'@

    # 只取标准输出：解释器可能往 stderr 打印可忽略的环境提示，不参与判定
    $probeOutput = & $pythonExe -c $importSnippet 2>$null
    $probeExit = $LASTEXITCODE

    if ($null -ne $previousPythonPath) { $env:PYTHONPATH = $previousPythonPath }
    else { Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue }
    if ($null -ne $previousPythonIo) { $env:PYTHONIOENCODING = $previousPythonIo }
    else { Remove-Item Env:PYTHONIOENCODING -ErrorAction SilentlyContinue }
    if ($null -ne $previousNoBytecode) { $env:PYTHONDONTWRITEBYTECODE = $previousNoBytecode }
    else { Remove-Item Env:PYTHONDONTWRITEBYTECODE -ErrorAction SilentlyContinue }

    $probeText = ($probeOutput | Out-String)
    Write-Info "解释器自报版本：$pythonVersionText；子进程退出码：$probeExit"

    $checkedModules = 0
    foreach ($rawLine in ($probeOutput -split "`r?`n")) {
        $line = $rawLine.Trim()
        if (-not $line.StartsWith('CHECK|')) { continue }
        # 单行里打包了全部模块结果：CHECK|状态|模块名|详情|状态|模块名|详情|...
        $fields = $line.Substring(6).Split('|')
        $index = 0
        while (($index + 1) -lt $fields.Count) {
            $status = $fields[$index]
            $moduleName = $fields[$index + 1]
            $detail = ''
            if (($index + 2) -lt $fields.Count) { $detail = $fields[$index + 2] }
            $index += 3
            if ([string]::IsNullOrWhiteSpace($moduleName)) { continue }
            $checkedModules++
            if ($status -eq 'OK') {
                Write-Pass "import $moduleName 成功"
            }
            else {
                Write-Fail "import $moduleName 失败：$detail"
            }
        }
    }

    if ($checkedModules -eq 0) {
        Write-Fail "导入检查没有产生任何结果，解释器输出："
        Write-Info $probeText.Trim()
    }
    else {
        $expected = 5
        if ($checkedModules -lt $expected) {
            Write-Fail "只收到 $checkedModules 项导入结果（应为 $expected 项）。"
        }
        elseif ($FailCount -eq 0) {
            Write-Pass "$checkedModules 个关键模块全部导入成功（sqlite3、mcp、dashscope、dotenv、memory_system.mcp_server）。"
        }
        if ($probeExit -ne 0 -and $checkedModules -eq $expected) {
            Write-Info "子进程退出码非 0，请结合上面的失败项一起看。"
        }
    }

    if ($probeText -match 'No module named') {
        Write-Info "出现 No module named，说明依赖未装全，请重新执行依赖安装步骤。"
    }
}

# -------------------------------------------------------------- ④ 文件齐全性
Write-Host ""
Write-Host "[4/6] 检查必备文件与目录是否齐全" -ForegroundColor White

function Test-RequiredItem {
    param(
        [string]$RelativePath,
        [string]$Kind,          # File 或 Directory
        [string]$Description
    )
    $full = Join-Path $pkgRoot $RelativePath
    $exists = $false
    if ($Kind -eq 'Directory') {
        $exists = Test-Path -LiteralPath $full -PathType Container
    }
    else {
        $exists = Test-Path -LiteralPath $full -PathType Leaf
    }
    if ($exists) {
        Write-Pass "$Description 就位：$RelativePath"
    }
    else {
        Write-Fail "$Description 缺失：$RelativePath"
    }
}

Test-RequiredItem -RelativePath 'install\INSTALL.md' -Kind 'File' -Description '安装说明'
Test-RequiredItem -RelativePath 'install\check_install.ps1' -Kind 'File' -Description '自检脚本'
Test-RequiredItem -RelativePath 'bin\memory-mcp.cmd' -Kind 'File' -Description 'MCP 启动脚本'
Test-RequiredItem -RelativePath 'bin\memory-web.cmd' -Kind 'File' -Description '概念网络页面启动脚本'
Test-RequiredItem -RelativePath 'bin\memory-concepts.cmd' -Kind 'File' -Description '命令行建索引启动脚本'
Test-RequiredItem -RelativePath '.env.example' -Kind 'File' -Description '环境变量示例'
Test-RequiredItem -RelativePath 'VERSION' -Kind 'File' -Description '包版本号文件'
Test-RequiredItem -RelativePath 'CHANGELOG.md' -Kind 'File' -Description '变更记录'
Test-RequiredItem -RelativePath 'install\agent-rules-template.md' -Kind 'File' -Description '代码定位规则模板'

# 配置样例目录允许出现在 deploy\ 或 install\ 两处之一
$exampleDir = ''
if (Test-Path -LiteralPath (Join-Path $pkgRoot 'deploy\mcp-config-examples') -PathType Container) {
    $exampleDir = 'deploy\mcp-config-examples'
}
elseif (Test-Path -LiteralPath (Join-Path $pkgRoot 'install\mcp-config-examples') -PathType Container) {
    $exampleDir = 'install\mcp-config-examples'
}

if ($exampleDir -ne '') {
    $exampleFiles = @(Get-ChildItem -LiteralPath (Join-Path $pkgRoot $exampleDir) -File -Filter '*.json' -ErrorAction SilentlyContinue)
    if ($exampleFiles.Count -gt 0) {
        Write-Pass "客户端配置样例目录就位：$exampleDir（$($exampleFiles.Count) 个 json 文件）"
        foreach ($file in $exampleFiles) {
            Write-Info "样例：$exampleDir\$($file.Name)"
        }
    }
    else {
        Write-Fail "配置样例目录为空：$exampleDir"
    }
}
else {
    Write-Fail "配置样例目录缺失：deploy\mcp-config-examples\ 或 install\mcp-config-examples\ 均不存在"
}

Write-Info "包内其它内容（供人工核对）："
foreach ($item in @('src\memory_system\mcp_server.py', 'python\Lib\site-packages', 'install', 'bin')) {
    $full = Join-Path $pkgRoot $item
    if (Test-Path -LiteralPath $full) {
        Write-Info "  存在：$item"
    }
    else {
        Write-Info "  缺失：$item"
    }
}

# --------------------------------------------------- ⑤ 打包机绝对路径残留扫描
Write-Host ""
Write-Host "[5/6] 扫描包内是否残留打包机的绝对路径（可移植性回归检查）" -ForegroundColor White

# 命中即视为残留：打包机上的用户目录（Users\ 或 Documents and Settings\）、
# 打包机上的 Python 安装位置（含 PATH 式写法）、以及写死的 PATH=... 配置。
# 说明：形如 <PKG_ROOT> 的占位符、以及 C:\Users\<你的用户名>\ 这类带尖括号的
# 文档示例不会命中，所以客户端配置样例可以安全地放在包内。
# 如需按本机情况调整，可在运行前设置环境变量 CONCEPT_MEMORY_FORBIDDEN_PATH_PATTERN
# 覆盖默认表达式（例如只想精确匹配打包机的用户名）。
$pathPatternSource = $env:CONCEPT_MEMORY_FORBIDDEN_PATH_PATTERN
$patternIsDefault = $false
if ([string]::IsNullOrWhiteSpace($pathPatternSource)) {
    $pathPatternSource = @'
[A-Za-z]:[\\/]{1,2}(Users|Documents and Settings)[\\/]{1,2}[^\\/<>"'|]+|Python3[0-9]{0,2}[\\/]{1,2}python\.exe|#!.*[A-Za-z]:[\\/]|PATH=[A-Za-z]:
'@
    $pathPatternSource = $pathPatternSource.Trim()
    $patternIsDefault = $true
}
$pathRegex = [regex]::new($pathPatternSource, [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)

if ($patternIsDefault) {
    Write-Info '使用默认检测规则：盘符 + Users\<用户名>、Python3xx\python.exe、PATH=<盘符>:'
}
else {
    Write-Info "使用环境变量提供的检测规则：$pathPatternSource"
}
Write-Info '本脚本自身会被跳过，避免检测规则文本自命中。'

$scanRelativePaths = @('bin', 'install', 'deploy', 'python\Lib\site-packages\bin')
$scanExtensions = @('.cmd', '.bat', '.ps1', '.py', '.json', '.md', '.txt', '.cfg', '.ini', '.toml', '.env', '.example', '.yaml', '.yml', '.exe', '.pth')
$maxScanBytes = 2MB

$scanTargets = New-Object System.Collections.ArrayList
foreach ($relativePath in $scanRelativePaths) {
    $fullPath = Join-Path $pkgRoot $relativePath
    if (-not (Test-Path -LiteralPath $fullPath)) { continue }
    foreach ($file in @(Get-ChildItem -LiteralPath $fullPath -Recurse -File -ErrorAction SilentlyContinue)) {
        if ($file.Length -gt $maxScanBytes) { continue }
        if ($scanExtensions -notcontains $file.Extension.ToLowerInvariant()) { continue }
        [void]$scanTargets.Add($file)
    }
}
foreach ($file in @(Get-ChildItem -LiteralPath $pkgRoot -File -ErrorAction SilentlyContinue)) {
    if ($file.Length -gt $maxScanBytes) { continue }
    if ($scanExtensions -notcontains $file.Extension.ToLowerInvariant()) { continue }
    [void]$scanTargets.Add($file)
}

# 跳过脚本自身：它的检测规则文本本身不含打包机路径，但字面上会命中规则
$selfPath = ''
try {
    if (-not [string]::IsNullOrWhiteSpace($PSCommandPath)) { $selfPath = $PSCommandPath }
    elseif ($null -ne $MyInvocation.MyCommand.Path) { $selfPath = $MyInvocation.MyCommand.Path }
    if (-not [string]::IsNullOrWhiteSpace($selfPath)) {
        $selfPath = [System.IO.Path]::GetFullPath($selfPath)
    }
}
catch {
    $selfPath = ''
}
if ($selfPath -ne '') {
    $scanTargets = @($scanTargets | Where-Object { $_.FullName -ne $selfPath })
}

if ($scanTargets.Count -eq 0) {
    Write-Fail "没有找到可扫描的文本文件，无法完成残留路径检查。"
}
else {
    Write-Info "共扫描 $($scanTargets.Count) 个文本与启动器文件（bin、install、deploy、site-packages\bin 及包根目录）"
    $hits = New-Object System.Collections.ArrayList
    foreach ($file in $scanTargets) {
        try {
            $content = Get-Content -LiteralPath $file.FullName -Raw -ErrorAction Stop
        }
        catch {
            continue
        }
        if ([string]::IsNullOrEmpty($content)) { continue }
        $lineNumber = 0
        foreach ($line in ($content -split "`r?`n")) {
            $lineNumber++
            $match = $pathRegex.Match($line)
            if ($match.Success) {
                $relative = $file.FullName
                if ($relative.StartsWith($pkgRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
                    $relative = $relative.Substring($pkgRoot.Length).TrimStart('\', '/')
                }
                [void]$hits.Add([pscustomobject]@{
                        Path = $relative
                        Line = $lineNumber
                        Text = $line.Trim()
                    })
            }
        }
    }

    if ($hits.Count -eq 0) {
        Write-Pass "未发现打包机绝对路径残留，包可整体改名或移动。"
    }
    else {
        Write-Fail "发现 $($hits.Count) 处疑似打包机绝对路径残留，会破坏可移植性，必须改成相对路径或占位符："
        $shown = 0
        foreach ($hit in $hits) {
            if ($shown -ge 20) {
                Write-Info "（其余 $($hits.Count - 20) 处已省略）"
                break
            }
            $snippet = $hit.Text
            if ($snippet.Length -gt 110) { $snippet = $snippet.Substring(0, 110) + '...' }
            Write-Info "  $($hit.Path):$($hit.Line)  $snippet"
            $shown++
        }
    }
}

# ------------------------------------------------------------------- ⑥ 总结
Write-Host ""
Write-Host "==============================================================" -ForegroundColor Cyan
if ($FailCount -eq 0) {
    Write-Host " 结论：PASS —— 这个包在当前位置可以运行。" -ForegroundColor Green
    Write-Host " 下一步：按 install\INSTALL.md 配置客户端，" -ForegroundColor Green
    Write-Host "         把 mcp-config-examples 里的 <PKG_ROOT> 替换为本包路径：" -ForegroundColor Green
    Write-Host "         $pkgRoot" -ForegroundColor Green
    if ($WarnCount -gt 0) {
        Write-Host " 另外有 $WarnCount 条警告，请人工看一眼。" -ForegroundColor Yellow
    }
    Write-Host "==============================================================" -ForegroundColor Cyan
    Write-Host ""
    exit 0
}
else {
    Write-Host " 结论：FAIL —— 共 $FailCount 项检查未通过。" -ForegroundColor Red
    Write-Host " 请先修复上面标为 [失败] 的项目，再重新运行本脚本。" -ForegroundColor Red
    if ($WarnCount -gt 0) {
        Write-Host " 另有 $WarnCount 条警告，请一并人工确认。" -ForegroundColor Yellow
    }
    Write-Host "==============================================================" -ForegroundColor Cyan
    Write-Host ""
    exit 1
}

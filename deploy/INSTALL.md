# concept-memory 离线包安装说明（读者：安装方 AI agent）

本包是 Windows x64 离线包，**自带 Python 3.11 运行时与全部依赖**：目标机器不需要联网、不需要预装 Python、不需要 pip，也不需要管理员权限（只要包目录可读写）。

你的任务：把本包接到用户使用的 MCP 客户端上，把代码定位规则写进用户项目，并让用户在目标项目里成功跑通一次扫描。按下面 7 步顺序执行，**每一步都有可判定的通过条件；上一步不通过，禁止进入下一步**。

---

## 步骤 0：先读这份包内结构

解压 zip 后，顶层只有一个文件夹 `concept-memory\`。下文出现的 `<PKG_ROOT>` 一律指**这个文件夹解压后的绝对路径**（不含结尾反斜杠），例如 `C:\tools\concept-memory`。

```
<PKG_ROOT>\
  python\python.exe                    包内 Python 解释器（没有 pip，不要用它跑 pip）
  python\Lib\site-packages\            全部第三方依赖
  python\DLLs\                         含 _sqlite3.pyd，存储层必需
  src\memory_system\                   服务源码（17 个 .py）
  bin\memory-mcp.cmd                   MCP 服务启动入口（stdio 传输）
  bin\memory-web.cmd                   概念网络可视化页面
  bin\memory-concepts.cmd              命令行建索引（等价 memory-concepts 入口）
  install\INSTALL.md                   本文件
  install\check_install.ps1            自动自检脚本（步骤 2 使用）
  install\agent-rules-template.md      代码定位规则模板（步骤 7 追加进用户项目 AGENTS.md）
  install\mcp-config-examples\         各客户端配置片段
  VERSION                              本包版本号（自检会打印，排查时先看它）
  CHANGELOG.md                         本包变更记录（升级前先看它）
  .env.example                         环境变量示例
```

**禁止事项（做了会直接坏掉，不要尝试"修复"）**：

- 不要在 `python\` 下创建 `python311._pth`：会让解释器进入隔离模式，`DLLs\` 脱离 `sys.path`，`import _sqlite3` 失败。
- 不要把 `python.exe` 拷到别处单独使用：运行必须依赖 `bin\*.cmd` 设置的 `PYTHONPATH`（包内 `python\Lib\site-packages` + 包内 `src` 两段，分号分隔）。
- 不要移动 `bin\`、`python\`、`src\` 三者的相对位置。
- 不要给 `bin\*.cmd` 里加 `echo`：MCP 走 stdio，任何写入 stdout 的文字都会破坏协议。

---

## 步骤 1：确定 PKG_ROOT 绝对路径

1. 确认用户已解压 zip（不要直接在压缩包里操作）。
2. 取包根目录的绝对路径：

```powershell
$PKG_ROOT = (Resolve-Path "C:\用户解压到的位置\concept-memory").Path
Write-Output $PKG_ROOT
```

**通过条件**：`$PKG_ROOT` 输出非空；`Test-Path "$PKG_ROOT\bin\memory-mcp.cmd"` 与 `Test-Path "$PKG_ROOT\src\memory_system\mcp_server.py"` 都返回 `True`。

**路径允许包含空格和中文**（已实测可用）。若路径包含 `&`、`^`、`%`，先把包移动到只含字母数字、空格、中文、下划线的目录再继续。

**后续所有配置里的占位符 `<PKG_ROOT>` 必须替换成这个真实路径**，不要留下占位符，也不要写成相对路径。写 JSON 时反斜杠要双写（`C:\\tools\\concept-memory`）或用正斜杠（`C:/tools/concept-memory`）。

---

## 步骤 2：运行自检（**硬性关卡**）

**先跑包内自带的自检脚本**（PowerShell，把 `<PKG_ROOT>` 换成真实路径）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "<PKG_ROOT>\install\check_install.ps1"
```

**通过条件**：结尾打印 `PASS` 且退出码为 `0`。它逐项检查解释器版本、`sqlite3`、`mcp`/`dashscope`/`dotenv`/`memory_system` 能否导入、包内文件是否齐全，以及包内是否残留打包机绝对路径。任何一项 FAIL 都按最后一行提示处理，**不要跳过**。

需要手工复核时（`<PKG_ROOT>` 同样要替换），**按你的 shell 二选一**——下面两段不能混用：

```cmd
:: cmd.exe 专用
set "PYTHONPATH=<PKG_ROOT>\python\Lib\site-packages;<PKG_ROOT>\src" && "<PKG_ROOT>\python\python.exe" -c "import sys;print(sys.version);import sqlite3;print(sqlite3.sqlite_version);import mcp,dashscope,dotenv;print('modules ok')"
```

```powershell
# PowerShell 专用（PowerShell 5.1 不支持 &&，必须这样写）
$env:PYTHONPATH = "$PKG_ROOT\python\Lib\site-packages;$PKG_ROOT\src"
$env:PYTHONDONTWRITEBYTECODE = "1"
& "$PKG_ROOT\python\python.exe" -c "import sys;print(sys.version);import sqlite3;print(sqlite3.sqlite_version);import mcp,dashscope,dotenv;print('modules ok')"
```

**手工复核的通过条件（三项都必须满足）**：

1. 第一行打印出 `3.11.x` 版本号；
2. 第二行打印出 sqlite 版本号（例如 `3.45.1`）——这证明 `_sqlite3` 正常；
3. 第三行打印 `modules ok`，进程退出码为 `0`。

再检查 MCP 服务能启动（它走 stdio，启动后会一直等待输入，这是正常的）：

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
$p = Start-Process -FilePath "$PKG_ROOT\bin\memory-mcp.cmd" -PassThru -WindowStyle Hidden
Start-Sleep -Seconds 3
if ($p.HasExited) {
    Write-Output "已退出，退出码=$($p.ExitCode)（非 0 说明启动失败）"
} else {
    Write-Output "运行中，正常"
    # 只 Kill cmd.exe 会留下子进程 python.exe，必须连子进程一起收掉
    Get-CimInstance Win32_Process -Filter "ParentProcessId=$($p.Id)" |
        Where-Object { $_.Name -eq 'python.exe' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    $p.Kill()
}
```

**通过条件**：输出「运行中，正常」，且清理后任务管理器里没有残留的包内 `python.exe`。

> **自检失败时禁止继续。** 不要靠改客户端配置、加环境变量、装系统 Python 去绕开自检失败；先按本文件最后的故障排查表定位，解决不了就停下来把原始报错交给用户，并明确说明安装未完成。

---

## 步骤 3：配置 DASHSCOPE_API_KEY

**关键事实（决定怎么填）**：MCP 服务 `python -m memory_system.mcp_server` **不读 `.env` 文件**，只从下面两个来源读 `DASHSCOPE_API_KEY`：

1. MCP 客户端配置里为该服务写的 `env` 字段（推荐，作用域最小）；
2. 进程的系统/用户环境变量（`setx DASHSCOPE_API_KEY "sk-xxx"`，之后必须重启客户端）。

Key 由用户在阿里云百炼（DashScope）控制台创建：登录 <https://bailian.console.aliyun.com/> → 右上角个人中心 → API-KEY 管理 → 创建 API-KEY。它形如 `sk-` 开头的长字符串。

**填法 A（推荐，写进客户端配置的 `env` 字段）**：

```json
"env": { "DASHSCOPE_API_KEY": "sk-用户提供的真实Key" }
```

**填法 B（系统环境变量）**：

```powershell
setx DASHSCOPE_API_KEY "sk-用户提供的真实Key"
```

**通过条件**：填法 A 检查配置 JSON 里 `env.DASHSCOPE_API_KEY` 值以 `sk-` 开头且不是占位符；填法 B 在新开的终端里 `echo %DASHSCOPE_API_KEY%` 能打印出该 Key。

**缺 Key 的后果**：MCP 服务仍能启动、工具列表也会出现，`scan_codebase` 仍有确定性的离线合成路径，但 **`search_concepts` 会直接失败并报 `Online retrieval requires the DashScope API key...`——它是纯在线检索，没有离线回退**。不要把缺失 Key 当成"安装成功但功能少一点"糊弄过去，要向用户说明。

---

## 步骤 4：套用客户端配置

先从 `install\mcp-config-examples\` 里挑对应文件，把其中所有 `<PKG_ROOT>` 替换为步骤 1 的真实路径，再写入客户端配置文件。四个客户端的文件位置与要点如下。

### 4.1 Claude Desktop

- 配置文件：`%APPDATA%\Claude\claude_desktop_config.json`（即 `C:\Users\<用户名>\AppData\Roaming\Claude\claude_desktop_config.json`）。
- 若文件不存在则新建；若已存在 `mcpServers` 节点，**只添加 `concept-memory` 这一项，不要覆盖用户已有的其它服务**。
- 顶级键必须是 `mcpServers`；写完保存为 UTF-8，不要带 BOM。
- 改完必须完全退出 Claude Desktop（托盘图标也要退出）再重新打开。

```json
{
  "mcpServers": {
    "concept-memory": {
      "command": "<PKG_ROOT>\\bin\\memory-mcp.cmd",
      "args": [],
      "env": { "DASHSCOPE_API_KEY": "sk-用户提供的真实Key" }
    }
  }
}
```

**通过条件**：用 PowerShell 解析该文件不报错——`Get-Content <路径> -Raw | ConvertFrom-Json` 成功返回对象。

### 4.2 Cursor

- 项目级配置：`<用户项目>\.cursor\mcp.json`；全局配置：`C:\Users\<用户名>\.cursor\mcp.json`。两者会合并，同名时项目级优先。
- 顶级键同样是 `mcpServers`，字段与 Claude Desktop 一致。
- 改完在 Cursor 里执行一次重载窗口，或退出重进。

**通过条件**：`Get-Content ... -Raw | ConvertFrom-Json` 成功；Cursor 的 MCP 面板里 `concept-memory` 状态不是红色报错（日志见 Output 面板 → `MCP Logs`）。

### 4.3 Cline（VS Code 扩展）

- 配置文件（当前版本）：`C:\Users\<用户名>\.cline\data\settings\cline_mcp_settings.json`。
- 旧版本路径为 VS Code 全局存储：`C:\Users\<用户名>\AppData\Roaming\Code\User\globalStorage\saoudrizwan.claude-dev\settings\cline_mcp_settings.json`。
- 两个路径都查一遍，**以实际存在文件的那个为准**；都不存在时创建当前版本路径。
- Cline 的条目额外带 `"disabled": false` 与 `"autoApprove": []`，其余字段相同。
- 改完在 VS Code 里执行 `Developer: Reload Window`，或在 Cline 的 MCP Servers 面板点 Restart。

```json
{
  "mcpServers": {
    "concept-memory": {
      "command": "<PKG_ROOT>\\bin\\memory-mcp.cmd",
      "args": [],
      "env": { "DASHSCOPE_API_KEY": "sk-用户提供的真实Key" },
      "disabled": false,
      "autoApprove": []
    }
  }
}
```

**通过条件**：`ConvertFrom-Json` 通过，且 Cline 的 MCP 面板显示该服务为已连接（绿色）。

### 4.4 通用 `mcpServers` JSON（其它支持 MCP 的客户端）

适用于任何接受标准 `mcpServers` JSON 的客户端（Roo Code、VS Code 的 `.vscode\mcp.json`、Continue、各类 CLI agent 等）。先用下面这份最小片段：

```json
{
  "mcpServers": {
    "concept-memory": {
      "command": "<PKG_ROOT>\\bin\\memory-mcp.cmd",
      "args": [],
      "env": { "DASHSCOPE_API_KEY": "sk-用户提供的真实Key" }
    }
  }
}
```

- 若客户端的配置键名不是 `mcpServers`（例如 `servers`、`mcp.servers`），按该客户端文档改名，**只改键名，不改 `command`/`args`/`env` 三者的值**。
- 若客户端要求 `command` 与 `args` 分离，可写成 `"command": "<PKG_ROOT>\\python\\python.exe"` + `"args": ["-m", "memory_system.mcp_server"]`，但**此时必须额外提供 `"env": { "PYTHONPATH": "<PKG_ROOT>\\python\\Lib\\site-packages;<PKG_ROOT>\\src", "DASHSCOPE_API_KEY": "sk-..." }`**，否则会报 `No module named memory_system`。用 `bin\memory-mcp.cmd` 则不需要手写 `PYTHONPATH`。
- 环境变量名大小写敏感；Windows 路径在 JSON 里必须双写反斜杠或改用正斜杠。

**通过条件**：配置文件能被 JSON 解析；`command` 指向的文件真实存在（`Test-Path` 返回 `True`）。

---

## 步骤 5：重启客户端并确认工具列表

1. 完全退出并重新打开客户端（Claude Desktop 需退出托盘进程；VS Code / Cursor 建议 `Reload Window`）。
2. 打开客户端的 MCP 服务或工具列表，确认 `concept-memory` 已连接。

**通过条件**：工具列表里同时出现 `scan_codebase` 与 `search_concepts` 两个工具名（大小写与下划线一致）。

**不通过时的第一动作**：在客户端里查看该 MCP 服务的原始 stderr 日志，或手动执行一次 `<PKG_ROOT>\bin\memory-mcp.cmd`，把报错原文对照最后的排查表；不要靠反复重启试运气。

---

## 步骤 6：让用户在目标项目上首跑扫描

1. 让用户在自己的**代码项目根目录**（不是本包目录）里，通过客户端调用 `scan_codebase`，参数传该项目的绝对路径。
2. `scan_codebase` 会**立即返回**（扫描在后台进行），返回体形如：

   ```json
   {"status": "scanning", "project_root": "<项目绝对路径>",
    "url": "http://127.0.0.1:<端口>",
    "database": "<项目绝对路径>\\.concept-memory\\concepts.sqlite",
    "note": "background scan in progress; search_concepts automatically waits for it to complete - no need to sleep or poll manually"}
   ```

   同时客户端所在机器会**自动弹出一个浏览器窗口**（`http://127.0.0.1:<端口>`）用于展示概念网络与扫描进度——这是预期行为，不要当成报错，也不要关掉承载它的服务进程。
3. 首次扫描需要联网调用阿里云 qwen-flash 做概念合成，**耗时与项目规模成正比**（中等项目几分钟到十几分钟属正常），期间不要中断进程、不要重复发起扫描。
4. **不要去轮询进度、也不要 sleep**：紧接着调用 `search_concepts`，它会自动等待后台扫描完成后再检索。

**通过条件（三项都满足才算安装完成）**：

1. `scan_codebase` 返回的 `status` 为 `scanning`，且返回体里带 `database` 字段（不是异常、不是空结果）；
2. `database` 指向的 `<用户项目>\.concept-memory\concepts.sqlite` 文件确实被创建（`Test-Path` 返回 `True`）；
3. 紧接着调用 `search_concepts`，传一个自然语言概念或关键词（中文即可，例如「重试逻辑」），返回非空的概念卡片列表。

**若第 3 项因为 API Key 无效或掉线而失败**（`search_concepts` 是纯在线检索，不做离线回退），先按步骤 3 修正 Key，再重试；此时不要重新解压包、也不要重装依赖。

**扫描数据只写入被扫描项目的 `.concept-memory\`**，本包安装目录本身不产生数据；不要引导用户把项目数据写进 `<PKG_ROOT>`。

---

## 步骤 7：把代码定位规则写进用户项目的 AGENTS.md

**目的**：让用户项目里的 coding agent 在定位代码时优先调用 concept memory，而不是直接 grep / 整文件读。少了这一步，工具虽然装好了，agent 也不会主动用它。

1. 打开包内 `install\agent-rules-template.md`，取其中「规则正文」整段。
2. 追加到**用户项目根目录**的 `AGENTS.md`：
   - 文件不存在：新建并写入规则正文；
   - 文件已存在：**只追加，绝不覆盖或重写用户原有内容**。
3. 若用户同时用多个 coding agent（例如 Codex、Cursor、Claude Code），确认它们读的规则文件是哪一个；读 `CLAUDE.md`、`.cursorrules` 这类别的文件名时，把同一段规则也复制过去。

**通过条件**：用户项目根目录的规则文件里能搜到 `scan_codebase` 与 `search_concepts` 两个工具名。

---

## 升级到新版本（不影响已有数据）

概念索引数据写在**被扫描项目**的 `.concept-memory\` 里，不在本包目录；包内顶层文件夹名固定为 `concept-memory`。所以升级就是覆盖安装目录，**客户端配置里的路径一个字都不用改**。

1. 先**完全退出**所有连了本 MCP 的客户端（Claude Desktop 要连托盘进程一起退）。
2. 下载新版本 zip，解压出的 `concept-memory\` 覆盖旧目录（同名文件夹，直接替换里面的内容）。
3. 重跑自检：`powershell -NoProfile -ExecutionPolicy Bypass -File "<PKG_ROOT>\install\check_install.ps1"`，确认结尾 PASS，且打印的`VERSION` 是刚装的新版本。
4. 重新打开客户端，工具列表里仍应出现 `scan_codebase` 与 `search_concepts`。
5. 在旧项目里再调用一次 `scan_codebase`：这是**增量重扫**，只重扫改动过的文件，概念卡片与真实使用连线都会保留。

**不要做的事**：不要把新包解压成另一个带版本号的目录再去改客户端配置（那样每次升级都得改配置）；不要为了升级删掉项目里的 `.concept-memory\`。

---

## 故障排查表

| 现象 | 常见原因 | 处理办法 |
| --- | --- | --- |
| 工具列表为空，看不到 `scan_codebase` / `search_concepts` | 配置写进了错误的文件；JSON 语法错误或带 BOM；客户端没重启 | 用 `ConvertFrom-Json` 验证文件；确认路径属当前客户端（4.1–4.4）；完全退出客户端进程后重启 |
| 启动报 `No module named memory_system` | 没有走 `bin\memory-mcp.cmd`，或手写 `command` 时漏了 `PYTHONPATH` | 改用 `<PKG_ROOT>\bin\memory-mcp.cmd`；若必须直接调 `python.exe -m memory_system.mcp_server`，则 `env` 里补 `PYTHONPATH=<PKG_ROOT>\python\Lib\site-packages;<PKG_ROOT>\src`（分号分隔，两段都要） |
| 启动报 `No module named mcp` | 依赖没装进包内 `site-packages`，或包被裁剪过 | 确认 `<PKG_ROOT>\python\Lib\site-packages\mcp\` 存在；重跑步骤 2 的自检；包不完整就用完整 zip 重新解压，不要手工补装 |
| 启动报 `No module named _sqlite3` | 生成了 `python311._pth`（隔离模式导致 `DLLs\` 脱离 `sys.path`），或 `DLLs\_sqlite3.pyd` 被删 | **删掉** `python\python311._pth`（本包不应存在该文件）；确认 `<PKG_ROOT>\python\DLLs\_sqlite3.pyd` 存在；重跑步骤 2 自检 |
| 扫描很慢、卡在合成阶段 | 概念合成是在线调用阿里云 qwen-flash，速度取决于网络与项目规模，属预期行为 | 向用户说明预期耗时；确认网络可访问 dashscope；不要重复发起扫描；大项目按目录分批扫描 |
| `scan_codebase` 刚返回 `status: scanning`，看似没做事 | 扫描在后台线程里跑，接口是立刻返回的 | 属预期。直接调用 `search_concepts`，它会自己等扫描结束，不要轮询、不要 sleep、不要重复发起扫描 |
| 调用 `scan_codebase` 后机器上弹出浏览器窗口 | 服务会顺带启动本地概念网络页面（`http://127.0.0.1:<端口>`） | 属预期行为，不是报错；不要为此杀进程或改配置 |
| `search_concepts` 报 `Online retrieval requires the DashScope API key...` | `search_concepts` 是纯在线检索，没有离线回退；Key 没送到该进程 | 按步骤 3 重填 Key（优先 `env` 字段），完全重启客户端后再试；`scan_codebase` 有离线退化路径，所以「能扫描」不代表 Key 配好了 |
| 提示 `DASHSCOPE_API_KEY` 缺失或调不通 | Key 没写、写错位置（写进了 `.env` 而 MCP 不读 `.env`）、或写进系统变量后没重启客户端 | 按步骤 3 用 `env` 字段重填，Key 以 `sk-` 开头；用 `env` 方式后必须整体重启客户端 |
| `search_concepts` 返回空或报错 | 索引尚未建立，或重排不可用 | 先成功跑完一次 `scan_codebase` 再检索；确认 `DASHSCOPE_API_KEY` 有效；重扫后仍失败才回退本地检索 |
| 命令窗一闪而过、无任何输出 | 正常现象：MCP 走 stdio，启动脚本不打印提示文字 | 不要给 `bin\*.cmd` 加 `echo`；要看日志请在客户端侧查看该服务的 stderr |
| `python.exe` 双击没反应 / 报缺少 DLL | 包被拷走时只复制了 `python\` 的一部分 | 整体复制 `concept-memory\` 目录；确认 `python\python311.dll`、`vcruntime140.dll`、`vcruntime140_1.dll` 都在 |
| 目标机器不是 Windows x64（如 macOS、Linux、Windows ARM64） | 包内是 Windows x64 解释器，不能跨平台使用 | 停止安装并如实告知用户：本离线包仅支持 Windows x64，不要尝试用别的解释器顶替 |

---

## 交付给用户的确认清单

安装完成前，逐项确认（全部为「是」才算完成）：

- [ ] `PKG_ROOT` 已确定，且包内 `bin\memory-mcp.cmd` 与 `src\memory_system\mcp_server.py` 存在。
- [ ] 步骤 2 自检三条判据全部通过（版本号、sqlite 版本、`modules ok`）。
- [ ] `DASHSCOPE_API_KEY` 已按步骤 3 的 A 或 B 填好，且不是占位符。
- [ ] 客户端配置文件已写入、JSON 可解析、`command` 指向存在的文件。
- [ ] 客户端已完全重启，工具列表里出现 `scan_codebase` 与 `search_concepts`。
- [ ] 用户在目标项目上首跑 `scan_codebase` 成功，项目下出现 `.concept-memory\`，`search_concepts` 返回非空结果。
- [ ] 步骤 7 已完成：用户项目根目录的规则文件（`AGENTS.md`）里已写入代码定位规则，含 `scan_codebase` 与 `search_concepts` 两个工具名。

**未全部满足时，请如实报告安装未完成，并附上原始报错文本，不要声称已完成。**

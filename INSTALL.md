# Concept Memory：一句会话指令安装（给执行安装的 agent）

已有完整本地目录时，用户在当前项目的 agent 会话中只需发送：

> 安装这个目录里的 Concept Memory。

执行安装的 agent 读取该目录中的本文件，从目录自身定位包根并接入当前宿主。目录是安装程序所在位置；被扫描项目默认是用户当前会话的工作目录，两者不必相同。

尚未下载时可以发送：

> 请读取 https://github.com/fangshuituilaji/concept-memory/blob/main/INSTALL.md ，为当前项目安装并接入 Concept Memory，完成自检后开始使用。

你负责下载、校验、安装、注册和验证。不要把安装步骤推回给用户手动执行。只在确实无法确定当前客户端、需要用户输入 Key、客户端要求重启或权限受限时让用户介入。

**版本说明**：新安装流程从 v0.3.0 起随分发包提供，v0.2.0 包仍是旧流程。仓库安装入口与源码不等同于完整发行包。完整的本地分发目录可以按本文直接接入，不需要依赖原源码工作区或验收记录。源码目录若不包含运行时则不等于完整分发目录。

## 1. 确认目标与当前宿主

- 当前项目默认是会话的工作目录；安装范围默认仅当前项目，包安装到 `%LOCALAPPDATA%\Programs\concept-memory`，索引留在项目的 `.concept-memory/`。
- 只支持 Windows x64；运行程序和依赖随包提供，不要求用户安装 Python、Node、uv 或 pip。下载、Key 验证与在线模型调用需要联网。
- 从会话、当前客户端官方文档和本地配置确认**正在执行你的客户端/harness**；不要凭“某客户端目录存在”就判断当前宿主，更不要批量修改所有客户端。
- 可以操作本机文件并注册本地 stdio MCP 的 agent 都可按本文执行；云端、沙箱无本机访问、或不允许注册 MCP 的宿主不能保证自动接入。明确说明具体限制。
- 无法确定当前宿主时，只询问用户正在使用哪个客户端，不让用户选择底层配置结构。

## 2. 取得可运行的本地包

**用户已经提供本地目录时，先检查目录自身**：若同一目录下存在 `python/python.exe`、`bin/memory-mcp.cmd`、`src/memory_system/mcp_server.py`、`install/configure_client.py`、`install/check_install.ps1` 和 `VERSION`，这个目录就是 `<PKG_ROOT>`。从本文件或用户提供目录的实际位置解析绝对路径，不搜索或使用另一个开发工作区。运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "<PKG_ROOT>\install\check_install.ps1"
```

必须返回退出码 0 且结论 PASS，再执行第 3 步注册。自检失败就停止；不得静默换成公开旧版本、别处的安装或预先生成的索引。使用用户提供的完整包时不必再次下载、复制或覆盖安装目录，后续启动配置直接指向该稳定目录。

**仅在用户没有提供完整本地包时，执行下面的下载流程**：

从本仓库取得 `deploy/bootstrap.ps1`。可直接下载官方 raw 文件；github.com 或 raw 被阻断时，读取：

`https://api.github.com/repos/fangshuituilaji/concept-memory/contents/deploy/bootstrap.ps1?ref=main`

将响应的 Base64 `content` 解码成临时 `.ps1` 文件，核对来源和文件内容，再执行。不要从搜索结果中的第三方镜像执行脚本；不要改为 `iex` 执行未保存的远程文本。仓库根入口同样可通过 Contents API 读取。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "<下载的 bootstrap.ps1 绝对路径>"
```

程序固定从本仓库 GitHub Releases 取得最新正式 Windows 包和对应 SHA256，通过 API 资产通道下载、核对文件名/校验和/版本、防止解压路径越界，运行包内自检后放入稳定安装目录。失败必须停止，不跳过校验、不靠另装 Python 绕过失败。已有安装运行时要求关闭该 MCP 连接后重试；不杀用户进程。覆盖安装前保留旧目录，并在安装后自检失败时恢复。成功输出 `package_ready` 和包根路径；**此时还没有完成客户端接入**。

`-InstallRoot` 可指定用户已有安装的稳定目录；`-Version X.Y.Z` 可锁定正式版本。已注册本服务时先读取该条目的原安装路径，沿用原路径，避免升级导致配置漂移。用户提供本地压缩包时可用 `-ArchivePath <zip> -ChecksumPath <对应 SHA256>`，同样不可跳过校验。

## 3. 只注册到当前客户端

包内 `install/configure_client.py` 只使用标准库，可由包内 Python 直接运行，无需设置 PYTHONPATH：

```powershell
& "<PKG_ROOT>\python\python.exe" "<PKG_ROOT>\install\configure_client.py" --package "<PKG_ROOT>" --project "<当前项目绝对路径>" --client <当前客户端> --dry-run
```

先检查 dry-run 的目标路径确实是当前客户端读取的配置，再去掉 `--dry-run` 执行。只更新 `concept-memory` 条目，保留其他服务、选项和已有环境变量；改写前生成可恢复备份。不得把 Key 复制进报告或聊天。无已有 Key 时不填非空占位符，由网页配置。

| 客户端标识 | 默认配置与范围 |
| --- | --- |
| `codex` | 项目 `.codex/config.toml`；需项目被信任。`--scope user` 使用 `$CODEX_HOME/config.toml` 或 `~/.codex/config.toml` |
| `claude-code` | 项目 `.mcp.json`；规则写入 `CLAUDE.md`。用户范围优先使用其官方 `claude mcp add` 命令 |
| `cursor` | 项目 `.cursor/mcp.json`；用户范围 `~/.cursor/mcp.json` |
| `zcode` | 项目 `.zcode/config.json` 的 `mcp.servers` |
| `vscode` | 项目 `.vscode/mcp.json` 的 `servers` |
| `opencode` | 项目 `opencode.json` 的 `mcp`，使用 local command 数组；支持用户范围 |
| `claude-desktop` | `%APPDATA%\Claude\claude_desktop_config.json`；该宿主只有用户级配置 |
| `cline` | 本机 Cline 配置；多份配置同时存在时先确定活动文件 |

`--config` 可指定已核实的活动配置路径；`--rules-file` 可指定宿主实际读取的项目规则文件。项目规则只追加一次，不覆盖已有内容。脚本会拒绝无法安全解析的 JSONC、损坏配置或不同传输类型的同名服务；这时用客户端的官方注册命令/API或按其文档作最小修改，保留注释和备份，不创建一份宿主不读取的“替代配置”。

**其他 agent/harness 不受上表限制**：

1. 获取与模型品牌无关的标准启动描述：上述程序改用 `--descriptor`。
2. 阅读该宿主官方 MCP 注册说明，将 `command`、`args`、`env` 登记到其真实配置或官方 CLI/API。描述采用包内 Python + stdio，不依赖某个客户端 SDK。
3. JSON 宿主可用 `--client generic --config <活动配置> --servers-key <已核实的对象路径，例如 mcp.servers>`；结构不同的宿主由 agent 转换描述，不能猜格式。
4. 按包内 `install/agent-rules-template.md` 将定位规则追加到该宿主实际读取的项目规则文件，保持幂等。

## 4. 在真正的 agent 会话里完成验收

注册程序输出 `pending_client_reload`，**不输出“安装完成”**。优先使用宿主提供的 MCP 重载操作；宿主确实只能重启时，保存配置并告知用户重启当前客户端，不擅自终止正在进行的会话。重启后读取本说明和同一配置继续第 4 步，不重复下载或重新填 Key。

依次确认：

1. **当前 agent 会话的实际工具列表**出现 `scan_codebase`、`search_concepts`。自己启动的临时 MCP 客户端、Python 探针、网页能打开、客户端配置写入成功，都不能替代这一步。
2. 从该会话调用 `scan_codebase(path=<当前项目绝对路径>)`。返回 `needs_api_key` 时，将返回的本地 URL 交给用户在网页输入 Key；有内置浏览器的宿主优先在内置浏览器打开。不让用户发 Key 到聊天，不传给 MCP 参数，不读 `.env` 凑 Key。
3. 保持同一 MCP 连接；有效 Key 验证保存后自动扫描。调用 `search_concepts(query=<项目中确实存在的概念>)`，它会等待扫描完成，无需 sleep 或轮询。缺 Key 阶段不能宣称已完成。
4. 从返回目录选择真实命中，调用 `search_concepts(card_ids=[...])` 取得完整卡片；核对文件和行号确实属于当前项目。
5. 确认项目规则已被正确追加；后续定位代码先扫描、再查询、再取卡。

完成以上各项才报告 `ready`。报告只需给出版本、稳定安装路径、当前客户端、真实调用结果。不把网页的绿色按钮等同客户端注册成功：它只表示最近有 MCP 调用，临时测试客户端也能触发。

## 参考依据

- [Context7 的统一 setup 入口](https://github.com/upstash/context7#installation)：统一命令，底层按客户端适配。
- [Playwright MCP 的客户端安装方式](https://github.com/microsoft/playwright-mcp#getting-started)：同一服务启动方式，各客户端使用不同注册命令或配置。
- [Serena 快速开始](https://github.com/oraios/serena#quick-start)：安装运行程序与接入 MCP 客户端分开处理。
- [Codex 官方 MCP 配置](https://developers.openai.com/codex/mcp/)：CLI 与 TOML，支持用户级及受信任项目配置。
- [OpenCode 官方 MCP 配置](https://opencode.ai/docs/mcp-servers/)：local command 数组。

说明、下载程序与分发包来自同一可信仓库；公网入口安装 v0.3.0 或后续正式版本。

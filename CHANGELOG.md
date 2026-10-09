# 变更记录

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)，格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

版本号口径（唯一版本源是 `pyproject.toml` 的 `[project] version`，`src/memory_system/__init__.py` 里的 `__version__` 必须与它一致）：

- **MAJOR**：破坏性变更——MCP 工具名或参数签名改变、`.concept-memory/` 数据结构不兼容、客户端配置字段改变；
- **MINOR**：新增能力，旧用法仍然可用；
- **PATCH**：修复缺陷，接口不变。

0.x 阶段：破坏性变更也走 MINOR，但必须在下面明确标注 **BREAKING**。

## [0.4.0] - 2026-10-09

### 新增

- 默认扫描 Python、Markdown、TypeScript/TSX、JavaScript/JSX、Java、Go、Rust、C/C++、C#；从本地解析器提取主要类型、函数、方法与准确行号。
- Windows x64 离线包自带并锁定全部 Tree-sitter grammar，目标机器无需安装语言运行时或编译器。
- 卡片保存规范 `language_id`，qwen-flash 概念生成输入使用正确的语言围栏；多语言解析器版本进入增量状态与概念缓存签名。
- 同一限定名下的声明、实现和重载保留各自源码位置；短名称跨作用域歧义时标为未锚定。

### BREAKING

- 概念生成与检索必须使用 qwen-flash；移除本地离线概念生成器与 `memory-concepts --offline`，缺 Key、模型失败或安全策略拒绝时明确报错，不生成本地替代概念。
- 首次升级会迁移文件状态表并重新分析缺少 parser signature 的文件；旧生成草稿不复用，扫描失败时保留旧卡片。

## [0.3.0] - 2026-10-03

### 新增

- 完整本地分发目录在包根提供正式安装入口；用户可直接说「安装这个目录里的 Concept Memory」，使用目录自身的运行时接入，不依赖开发工作区或专门的验收说明。
- 一句会话指令安装入口：agent 自动下载和 SHA256 校验 Windows 离线包，再接入当前客户端。提供可恢复覆盖安装、配置备份与合并、幂等规则追加及未知宿主的标准 stdio 启动描述；区分包就绪、待宿主重载和真实工具调用完成。
- 概念网络网页适配手机、平板、桌面和横屏；窄屏卡片从底部展开，提示与输入表单可适应小屏。支持单指旋转、双指缩放及点按节点，保留鼠标和滚轮操作。
- 首次 API Key 配置：扫描前检查环境变量及用户本机配置，缺 Key 时在概念网络网页提示输入；在线验证成功后保存并自动扫描。Windows 使用当前账户的 DPAPI 加密，重启和覆盖升级可复用。
- 14 项隔离测试覆盖缺 Key 前置门槛、错误输入与重试、跨进程读取、环境变量优先级、网页请求来源检查，以及 stdio 模块入口提交后继续扫描。

### 修复

- 通过 `python -m` 启动时，网页与 MCP 共享同一份扫描状态，避免提交 Key 后原会话仍等待配置。
- Windows 本地网页服务使用独占端口，避免多个进程绑定同一端口，导致配置请求送到错误的服务。
- 网页服务不再隐式加载项目 `.env`，安装说明和客户端示例改为默认在本地网页配置 Key。

## [0.2.0] - 2026-09-28

安装与升级体验版本：全部改动来自一次「删光本地环境 → 从 GitHub Releases 下载 v0.1.0 → 重新安装接入 ZCode」的端到端实测，无 MCP 接口与数据结构变更。

### 新增

- ZCode 客户端支持：`install/mcp-config-examples/zcode.json` 配置样例与 `INSTALL.md` 4.4 安装章节（工作区级 `.zcode\config.json`、`mcp.servers` 层级、包内 `python.exe` 直启 + `PYTHONPATH` 双段形态，实测可用）。
- `README.md` 新增完整升级步骤，以及 github.com 被屏蔽时经 `api.github.com` 资产接口下载 zip 与校验和的通道说明。

### 改进

- `INSTALL.md` 故障排查表补充「删除 `.concept-memory\` 报文件被占用（Device or resource busy）」一行：MCP 服务进程握着 SQLite 句柄，先退客户端或结束包内 `python.exe` 再删。
- `INSTALL.md` 步骤 4 补充实测坑位：路径含中文时，无 BOM 的 UTF-8 `.ps1` 会被 Windows PowerShell 5.1 按 ANSI 读取导致乱码。
- `INSTALL.md` 步骤 7 与 `agent-rules-template.md` 明确：`AGENTS.md` 已有同名章节时跳过追加，避免重复安装产生重复章节。

### 已知限制的变化

- v0.1.0 记录的「尚未在真实 coding agent 会话中完成端到端验证」已完成：Windows 目标机上从 Releases 下载、解压自检、接入 ZCode、含中文路径项目首跑扫描，到真实在线检索返回与 `card_ids` 取卡全链路通过（2026-09-28，stderr 零输出）。

## [0.1.0] - 2026-09-23

首个公开发布版本。发布形态是 Windows x64 离线包：解压即用，内置 Python 3.11 运行时，目标机器不需要装 Python、不需要联网，也不需要管理员权限。

### 新增

- MCP 服务（stdio）暴露两个工具：`scan_codebase`（建索引，立即返回、后台扫描）与 `search_concepts`（双模式：query 检索 / `card_ids` 取完整卡片）。
- 概念卡片与证据绑定：卡片带文件路径、符号、行号区间与源码摘要；检索返回的瘦身目录只给 `card_id`、概念名、文件行号与一行定义预览，不含源码摘录。
- 存储层：SQLite（含 FTS）作为本地检索派生层，JSON 作为交换格式。
- 概念网络页面（`memory-web`）：初始化进度、灰色节点、点击查看卡片；同文件连线与「真实使用连线」按共现次数加粗；红绿连接状态按钮；检索命中的节点变蓝（直接命中深蓝、扩散相关浅蓝）。
- 真实使用建边：一次 `card_ids` 取卡时卡片两两之间记一条共现边（计数累加），检索时沿该图按共现次数加权扩散；不使用模型推断语义关系。
- 在线检索：全部卡片目录直接送 qwen-flash 挑选并排序，只回传卡片 ID，本地再取完整内容；没有本地关键词粗筛，失败重试后明确报错，不做静默离线回退。
- 增量重扫：`file_state` 表按 mtime/size/内容摘要跳过未变文件；改动文件的卡片与同文件旧卡片匹配后沿用旧 `card_id`，历史使用边不被清零；已删除或改名文件的孤儿卡片会被清理。
- 就绪门槛：后台扫描未结束时 `search_concepts` 自动等待，不在半成品索引上返回结果。
- 离线分发包与安装说明：包内自带 `install/INSTALL.md`（读者是 agent）、`install/check_install.ps1` 自检脚本、各客户端 MCP 配置示例、`.env.example`。
- 代码定位规则模板 `install/agent-rules-template.md`，安装时追加进用户项目的 `AGENTS.md`。
- 发布链路：`deploy/release.py` 一条命令完成版本校验、测试、构建、包验收、校验和与 GitHub Release 上传。zip 文件名带版本，包内顶层文件夹名固定 `concept-memory`，升级时覆盖同名目录即可，客户端配置不用改。
- 许可证：MIT。

### 已知限制

- 概念合成与检索都需要用户自备阿里云百炼（DashScope）的 qwen-flash API Key；`search_concepts` 是纯在线检索，没有离线回退。
- 尚未在真实 coding agent 会话中完成端到端验证（含一次真实的在线检索返回）。
- TypeScript/JavaScript 的 Tree-sitter 支持属实验性扩展，不在第一版验收语言范围内。
- 仅提供 Windows x64 离线包。

[Unreleased]: ../../compare/v0.2.0...HEAD
[0.2.0]: ../../releases/tag/v0.2.0
[0.1.0]: ../../releases/tag/v0.1.0

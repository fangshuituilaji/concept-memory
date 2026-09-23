# 变更记录

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)，格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

版本号口径（唯一版本源是 `pyproject.toml` 的 `[project] version`，`src/memory_system/__init__.py` 里的 `__version__` 必须与它一致）：

- **MAJOR**：破坏性变更——MCP 工具名或参数签名改变、`.concept-memory/` 数据结构不兼容、客户端配置字段改变；
- **MINOR**：新增能力，旧用法仍然可用；
- **PATCH**：修复缺陷，接口不变。

0.x 阶段：破坏性变更也走 MINOR，但必须在下面明确标注 **BREAKING**。

## [Unreleased]

（下一次发布前在此累积改动；发布时把本节内容移入新的版本段并写上日期。）

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

[Unreleased]: ../../compare/v0.1.0...HEAD
[0.1.0]: ../../releases/tag/v0.1.0

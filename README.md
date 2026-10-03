# Concept Memory

给 coding agent 用的外接记忆服务：扫描你的项目，把代码编码成带行号索引的概念卡片。agent 定位代码时先查卡片、再按行读码，省 context、定位更准。

## 实现了什么

- **两个 MCP 工具**：`scan_codebase` 建索引，`search_concepts` 查索引 / 取卡片。
- **概念卡片**：概念 + 文件路径、符号、行号区间 + 源码摘要，可追溯。
- **检索**：把全部卡片目录交给 qwen-flash 挑选并排序，先返回瘦身目录（不含源码），取卡时再返回完整卡片。
- **概念连线只来自真实使用**：一次取多张卡片就记一条共现边，下次检索沿边扩散，越用越准。
- **概念网络页面**：本地网页看节点、连线与检索命中，扫描进度实时可见。
- **增量重扫**：只重扫改动过的文件，卡片标识与历史连线都保留。
- **离线包**：自带 Python 运行时与依赖，目标机器不装环境也能跑。

## 怎么用

如果你已经拿到包含 `python/`、`bin/`、`src/`、`install/` 的完整目录，只需在你的项目会话里告诉 agent：

> 安装这个目录里的 Concept Memory。

把目录交给 agent 即可；它读取目录里的 [INSTALL.md](INSTALL.md)，使用随包运行时自检并接入当前客户端。没有 Key 时在本地网页输入，不需要另一份开发工作区或专门的测试说明。源代码目录本身不含运行时，需要先取得完整分发包。

一句会话指令安装入口已在当前源码实现，**新版分发包尚未发布到 Releases**。现有公开 v0.2.0 包仍是旧安装流程；发布包含新安装程序的版本后，在项目的 agent 会话发送：

> 请读取 https://github.com/fangshuituilaji/concept-memory/blob/main/INSTALL.md ，为当前项目安装并接入 Concept Memory，完成自检后开始使用。

agent 会下载并校验官方包、接入当前客户端并验证实际工具调用。已有 Key 会复用；没有时只在本地网页输入。客户端需要重启时会明确提示。支持本机文件操作及 stdio MCP 的其他 agent 也可按统一说明接入，不受客户端名单限制。完整流程见 [安装入口](INSTALL.md)。

当前公开版本仍可到 [Releases](../../releases/latest) 下载、解压，再让 agent 读取包内 `install/INSTALL.md` 安装；v0.2.0 的 Key 配置方式以其包内说明为准。本工作区未发布源码已增加网页 Key 配置。

**github.com 打不开？** 新安装程序默认走 GitHub API 下载通道。旧包可先读取 `https://api.github.com/repos/fangshuituilaji/concept-memory/releases/latest` 获取 zip 与 `.zip.sha256` 的资产 id，再通过 `https://api.github.com/repos/fangshuituilaji/concept-memory/releases/assets/<id>`、请求头 `Accept: application/octet-stream` 下载并校验。

**升级**：

1. 完全退出所有连着它的客户端（ZCode / Claude Desktop 等，托盘进程一起退）。
2. 下载新版本 zip（github.com 被屏蔽时走上面的 API 通道），解压出的 `concept-memory\` 覆盖同名目录——客户端配置里的路径一个字不用改。
3. 跑一遍包内 `install\check_install.ps1`，确认结尾 PASS 且打印的版本号是新版本。
4. 重新打开客户端，工具列表里应仍有 `scan_codebase` 与 `search_concepts`。
5. 在项目里再调一次 `scan_codebase`：这是**增量重扫**，只重扫改动过的文件，概念卡片与真实使用连线都保留。索引数据存在**你的项目**的 `.concept-memory/` 下，升级不会丢。

**限制**：目前只提供 Windows x64 离线包；建索引与检索都要联网调用 qwen-flash。

## 许可证

[MIT](LICENSE)

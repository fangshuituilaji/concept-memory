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

1. 到 [Releases](../../releases/latest) 下载 `concept-memory-offline-win64-v<版本>.zip`，解压到任意目录。
   **github.com 打不开？** 网络屏蔽时可走 API 通道：先 `curl -s https://api.github.com/repos/fangshuituilaji/concept-memory/releases/latest`，在 `assets` 列表里查到 zip 与 `.zip.sha256` 各自的 `id`，再 `curl -sL -H "Accept: application/octet-stream" -o 包名.zip https://api.github.com/repos/fangshuituilaji/concept-memory/releases/assets/<id>` 下载，校验和文件同理。
2. 把包里 `install/INSTALL.md` 交给你的 coding agent，让它照着装 —— 这份说明的读者是 agent，每一步都有可判定的通过条件，最后一步会把代码定位规则写进你项目的 `AGENTS.md`。常见客户端（Claude Desktop、Cursor、Cline、ZCode）的配置样例都在包里 `install/mcp-config-examples/`。
3. 准备一个阿里云百炼（DashScope）的 API Key，安装时填进客户端配置。
4. 装好后，在项目里让 agent 调用 `scan_codebase`；之后正常提问即可，它会自己用 `search_concepts` 定位代码。

**升级**：

1. 完全退出所有连着它的客户端（ZCode / Claude Desktop 等，托盘进程一起退）。
2. 下载新版本 zip（github.com 被屏蔽时走上面的 API 通道），解压出的 `concept-memory\` 覆盖同名目录——客户端配置里的路径一个字不用改。
3. 跑一遍包内 `install\check_install.ps1`，确认结尾 PASS 且打印的版本号是新版本。
4. 重新打开客户端，工具列表里应仍有 `scan_codebase` 与 `search_concepts`。
5. 在项目里再调一次 `scan_codebase`：这是**增量重扫**，只重扫改动过的文件，概念卡片与真实使用连线都保留。索引数据存在**你的项目**的 `.concept-memory/` 下，升级不会丢。

**限制**：目前只提供 Windows x64 离线包；建索引与检索都要联网调用 qwen-flash。

## 许可证

[MIT](LICENSE)

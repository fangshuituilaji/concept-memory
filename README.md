# Concept Memory — Coding Agent 外接记忆服务

本项目是一个**供 Coding Agent 使用的外接记忆服务**：扫描工作目录生成概念记忆，当 agent 需要定位代码时，向模型返回带代码行索引的概念卡片，代替低精度的全文检索和大量整文件阅读。产品定义见 [`产品设计文档.md`](产品设计文档.md)。

它要解决 Coding Agent 的两个现实痛点：

- 文件读取占用大量模型上下文；
- 模型在陌生代码库中定位不精准。

## 当前实现与产品差距

已具备：`memory-mcp`（`scan_codebase` / `search_concepts` 两个工具，后者双模式：query 检索 / card_ids 取卡）、概念生成与证据绑定（含文件、符号、行号）、SQLite FTS 存储、`memory-web` 概念网络页面（初始化扫描、进度显示、灰节点、点击看卡片）。`scan_codebase` 立即返回并在后台扫描：自动启动 `memory-web` 并打开进度页（逐文件实时进度），文件级 Qwen 调用走线程池并行（默认 6 并发，120 秒超时，失败自动重试 3 次），扫描完成后清理已删除/改名文件的孤儿卡片。扫描进行期间 `search_concepts` 会**自动等待扫描完成**（就绪门槛，等待上限 30 分钟，超时明确报错），不在半成品索引上检索；索引为空时明确报错提示先调用 `scan_codebase`，agent 无需自行 sleep 轮询。**重扫是增量的**（`incremental.py`）：`file_state` 表记录每个文件的 mtime/size/内容摘要，未变文件直接复用已存卡片（不读、不解析、不查缓存），仅统计信息变化而内容相同的文件（如 git checkout 重写）经摘要复核后同样跳过，重扫成本从 O(全库) 降为 O(改动文件数)；改动文件重扫后，新卡片与该文件旧卡片做**同文件内匹配**（概念名完全相同，或名称相似度 ≥0.75 且证据符号重合 ≥1），匹配成功沿用旧 `card_id`（`metadata.derived_from` 记录新内容本应得到的内容寻址 ID，保证血缘可审计），**历史真实使用边因此不再被清零**；只有真正消失的概念才连带删边，文件改名经内容摘要比对后同样保留卡片与边。概念连线**只来自真实使用**：`search_concepts` 取卡（card_ids 模式）一次取走多张卡片时，卡片两两之间记一条共现边（计数累加），不调用模型推断语义关系；`search_concepts` 在线调用 qwen-flash 完成检索：**全部概念卡片的「ID+概念名+定义」目录直接送入模型上下文**，由它挑选相关卡片并按相关性排序、只返回卡片 ID，本地再按 ID 取回完整卡片；**没有本地关键词粗筛环节**，卡片目录过大时按批送模型初选、合并后再终排；检索失败自动重试 3 次，重试仍失败则明确报错，**绝不静默回退到纯离线检索**。`search_concepts` query 模式按"重排后的直接命中在前、沿真实使用边扩散的相关卡片在后"输出瘦身目录（card_id、概念名、文件行号、一行定义预览，不含源码摘录；关联卡片带 `hop` 跳数、`via` 传播路径和 `file_path`）；card_ids 模式一次取回多张完整卡片（定义全文+源码摘录），并两两累加真实使用边。概念网络页面渲染同文件淡色连线与真实使用连线（越粗共现次数越多，有上限），并已实现红/绿连接状态按钮（coding agent 最近调用过 MCP 工具时为绿色，超过 5 分钟无调用转红，网页自己触发的扫描不算）与检索命中可视化（直接命中深蓝、扩散相关浅蓝，命中集合之间的连线同时点亮）。当前架构图见 [`docs/architecture-diagram.html`](docs/architecture-diagram.html)。

距产品完成还差：

- 在真实 coding agent 会话中的端到端验证。

## 当前核心能力（第一阶段）

- 输入：本地代码文件或目录；第一版以 Python 代码为主。
- 源码事实：使用 AST 提取符号、签名、文档字符串和行号等可验证信息。
- 概念编码：模型阅读整文件，默认生成约 2～3 张、最多 9 张高层概念卡片。
- 证据绑定：本地解析器将概念绑定到文件、符号、行号和源码摘要。
- 存储检索：JSON 作为交换格式，SQLite FTS5 作为本地全文检索派生层。
- 输出：概念卡片、匹配解释和长度受限的源码证据；完整源码按需读取，不自动生成最终答案。

当前工作树中对 TypeScript/JavaScript 的 Tree-sitter 支持属于实验性扩展，尚未纳入第一版验收的语言范围。

检索连线仍只来自真实使用记录，图扩散激活检索基于该使用图实现（`activation.py`）；检索排序由 qwen-flash 在线完成（查询扩展 + 候选重排，`retrieval.py`），不做 embedding/向量数据库。

## 安装与运行

普通用户不需要装 Python，也不需要懂 MCP：下载离线包，解压，然后把包内的安装说明交给自己的 coding agent 执行。

1. 从 [Releases](../../releases/latest) 下载 `concept-memory-offline-win64-v<版本>.zip`。
2. 解压到任意目录（路径含空格或中文都可以，别在压缩包里直接运行）。
3. 把包内 `install/INSTALL.md` 交给你的 coding agent，让它照着装 —— 那份说明的读者是 agent，每一步都有可判定的通过条件：自检、写客户端配置、配 Key、首跑扫描，以及把你的代码定位规则写进项目 `AGENTS.md`。

包自带 Python 运行时与全部依赖，目标机器不需要装 Python、不需要 pip、不需要联网安装；但概念合成与检索需要你自备 DashScope（阿里云百炼）API Key。

**升级**：关掉客户端，把新版本 zip 解压出的 `concept-memory\` 覆盖旧目录即可。包内顶层文件夹名固定为 `concept-memory`，所以客户端配置里的路径一个字都不用改；概念索引数据存在**你的项目**的 `.concept-memory/` 下，升级不会丢，重跑一次 `scan_codebase` 是增量重扫。

**平台**：目前只提供 Windows x64 离线包。版本变更见 [CHANGELOG.md](CHANGELOG.md)。

### 从源码安装与运行（开发方式）

仓库内执行 `python deploy/build_offline_bundle.py --version <版本号>` 可以从当前源码重新产出离线包（打包机需有 Python 3.11，脚本会自动探测，也可用 `--runtime` 指定）；一条命令完成「校验 → 测试 → 构建 → 包验收 → 打 tag → 发 GitHub Release」用 `python deploy/release.py --version <版本号>`（先加 `--dry-run` 演练，不会碰 git、不发网络请求；正式发布需要环境变量 `GITHUB_TOKEN`）。

```bash
pip install -e '.[dashscope]'
export DASHSCOPE_API_KEY='在 shell 中设置，不要写入代码或仓库'
PYTHONPATH=src python3 -m memory_system.cli path/to/project \
  --model qwen-flash \
  --target-concepts 3 \
  --max-concepts 9 \
  --output concepts.json \
  --database concepts.sqlite
```

等价的控制台入口：`memory-concepts`（构建索引）、`memory-mcp`（MCP 服务，向 Coding Agent 暴露概念记忆）、`memory-web`（本地概念网络页面，需指定 `--database`）。

### 接入 ZCode

工作区配置 `.zcode/config.json` 已注册 `concept-memory` MCP 服务（stdio，`python -m memory_system.mcp_server`）。新开的 ZCode 会话会自动连接，工具为 `scan_codebase` / `search_concepts`（query 检索 / card_ids 取卡）。在线概念生成**和检索**都要求服务进程环境变量中存在 `DASHSCOPE_API_KEY`（MCP 服务不会读取 `.env`）；扫描未设置时使用离线回退，但 `search_concepts` 始终要求在线模型，未设置或模型不可达时报错。

Python API 示例：

```python
from memory_system import ConceptStore, analyze_path

cards = analyze_path("path/to/project")
with ConceptStore("concepts.sqlite") as store:
    store.upsert_cards(cards)
    results = store.search("用户")
```

没有 `DASHSCOPE_API_KEY` 时会使用离线安全回退，方便语法和数据流测试；配置在线模型时，普通源码默认可发送，敏感文件必须显式加入白名单。API key 只能从环境变量读取，且不应写入卡片、日志或仓库。当前敏感文件白名单的完整策略仍属于待实现的运行安全能力。

## 明确不在第一阶段

第一阶段暂不做：

- 通用用户偏好、跨项目个人记忆和完整对话记忆（产品级排除，最终形态也不做）；
- 自动回答代码问题；
- 未经验证的关系直接参与正式检索；
- embedding/向量数据库作为首要检索方案；
- hook 式拦截注入等侵入性集成（MCP 跑通后再考虑）；
- 通用记忆的写入、冲突、归档、遗忘和权限生命周期。

## 开发原则

- 先把产品链路跑通：扫描 → 检索 → 返回卡片 → agent 按行读码，这条链路优先于研究性扩展。
- 源码事实、模型解释和检索结果必须可区分、可追溯。
- 模型、提示词、源码摘要和检索配置应记录并可复现。

## 文档入口

- [产品设计文档](产品设计文档.md)
- [变更记录](CHANGELOG.md)
- [架构说明](docs/architecture.md)
- [架构图（交互式）](docs/architecture-diagram.html)

## 许可证

[MIT](LICENSE)

## 状态

当前已实现源码事实提取、文件级概念生成、证据绑定（文件/符号/行号）、JSON/SQLite 存储、缓存增量更新、文件级增量重扫（未变文件跳过、card_id 继承保住使用边）、孤儿卡片清理、敏感文件审计、MCP 两个工具、概念网络页面和基于真实使用边的扩散激活检索。当前状态是"产品骨架已通，『真实使用建边』机制已通过本会话真实 MCP 调用与页面视觉验证；按『当前实现与产品差距』一节补齐页面连接状态与检索高亮后，即进入真实 coding agent 会话验证"。

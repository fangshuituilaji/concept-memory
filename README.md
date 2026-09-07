# Concept Memory — Coding Agent 外接记忆服务

本项目是一个**供 Coding Agent 使用的外接记忆服务**：扫描工作目录生成概念记忆，当 agent 需要定位代码时，向模型返回带代码行索引的概念卡片，代替低精度的全文检索和大量整文件阅读。产品定义见 [`产品设计问文档.md`](产品设计问文档.md)。

它要解决 Coding Agent 的两个现实痛点：

- 文件读取占用大量模型上下文；
- 模型在陌生代码库中定位不精准。

## 当前实现与产品差距

已具备：`memory-mcp`（`scan_codebase` / `search_concepts` / `get_card` 三个工具）、概念生成与证据绑定（含文件、符号、行号）、SQLite FTS 存储、`memory-web` 概念网络页面（初始化扫描、进度显示、灰节点、点击看卡片）。`scan_codebase` 立即返回并在后台扫描：自动启动 `memory-web` 并打开进度页（逐文件实时进度），文件级 Qwen 调用走线程池并行（默认 6 并发，120 秒超时，失败自动重试 3 次），扫描完成后清理已删除/改名文件的孤儿卡片。概念连线**只来自真实使用**：`get_card` 一次取走多张卡片时，卡片两两之间记一条共现边（计数累加），不调用模型推断语义关系；`search_concepts` 按"直接命中在前、沿真实使用边扩散的相关卡片在后"输出有序卡片序列，关联卡片带 `hop` 跳数和 `via` 传播路径。概念网络页面渲染同文件淡色连线与真实使用连线（越粗共现次数越多，有上限）。

距产品完成还差：

- 页面红/绿连接状态与 coding agent 真实连接检测；
- 检索命中节点变蓝、检索连线实时高亮；
- 在真实 coding agent 会话中的端到端验证。

## 当前核心能力（第一阶段）

- 输入：本地代码文件或目录；第一版以 Python 代码为主。
- 源码事实：使用 AST 提取符号、签名、文档字符串和行号等可验证信息。
- 概念编码：模型阅读整文件，默认生成约 2～3 张、最多 9 张高层概念卡片。
- 证据绑定：本地解析器将概念绑定到文件、符号、行号和源码摘要。
- 存储检索：JSON 作为交换格式，SQLite FTS5 作为本地全文检索派生层。
- 输出：概念卡片、匹配解释和长度受限的源码证据；完整源码按需读取，不自动生成最终答案。

当前工作树中对 TypeScript/JavaScript 的 Tree-sitter 支持属于实验性扩展，尚未纳入第一版验收的语言范围。

向量检索与模型推断的概念语义关系均不做：连线只来自真实使用记录，图扩散激活检索基于该使用图实现（`activation.py`）。

## 安装与运行

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

工作区配置 `.zcode/config.json` 已注册 `concept-memory` MCP 服务（stdio，`python -m memory_system.mcp_server`）。新开的 ZCode 会话会自动连接，工具为 `scan_codebase` / `search_concepts` / `get_card`。在线概念生成要求服务进程环境变量中存在 `DASHSCOPE_API_KEY`（MCP 服务不会读取 `.env`；未设置时使用离线回退）。

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

- [产品设计问文档](产品设计问文档.md)
- [架构说明](docs/architecture.md)
- [架构图（交互式）](docs/architecture-diagram.html)

## 状态

当前已实现源码事实提取、文件级概念生成、证据绑定（文件/符号/行号）、JSON/SQLite 存储、缓存增量更新、孤儿卡片清理、敏感文件审计、MCP 三个工具、概念网络页面和基于真实使用边的扩散激活检索。当前状态是"产品骨架已通，『真实使用建边』机制已通过本会话真实 MCP 调用与页面视觉验证；按『当前实现与产品差距』一节补齐页面连接状态与检索高亮后，即进入真实 coding agent 会话验证"。

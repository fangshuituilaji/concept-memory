# 调研采集日志

## 时间与目标

- 日期：2026-08-22
- 目标：整理 GitHub、网络官方资料、论文和 benchmark 中热门或更新频繁的 AI Agent Memory / RAG Memory 系统。
- 主要交付：架构地图、系统对照、论文/评测路线、项目建议和停止标准。

## 已完成的检索路线

1. GitHub 官方仓库与官方 docs：Mem0、Letta、Graphiti、Cognee、Hindsight、Supermemory、Memori、LangMem、LlamaIndex、MemoryOS、MemOS、ReMe、LightMem、A-MEM、EverMemOS、memU，以及 Memobase、OpenMemory、MineContext、Memoria、AgentMem。
2. 论文/预印本：RAG、Generative Agents、MemoryBank、MemGPT、CoALA、HippoRAG、Zep、A-MEM、Mem0、MemOS、LightMem、EverMemOS。
3. Benchmark：LoCoMo、LongMemEval、HaluMem、MemoryAgentBench。
4. 候选发现：多个 Agent Memory / Agentic Memory 项目地图，用于漏项检查而非单独支撑核心事实。

## 五子 Agent 执行情况

用户要求并行出动 5 个子 Agent。第一个 GitHub 盘点 Agent 成功返回了官方仓库/API 证据；其余网络检索 Agent 在启动/重试时持续遭遇 `429 Too Many Requests`，包括尝试通过 `127.0.0.1:7892` 本地代理的重试。主机环境检查发现：

- 环境变量中没有 `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY`。
- 系统当时暴露过 `127.0.0.1:7892` 监听端口，但随后通过该端口的 `curl` 连接失败。
- 因此没有把“代理可用”当作事实，也没有伪造失败子 Agent 的搜索结果。

## 处理方式

- 采用成功的 GitHub Agent 结果作为活跃度补充。
- 保留主调研报告中已完成的论文、benchmark 和架构整理。
- 对无法由当前网络轮次重新核验的项目，明确标注“需复核”“官方自报”或“成熟度较早期”。
- 追加 GitHub 活跃度补充文档，避免把静态项目列表误当作当前状态。

## 停止标准

本轮在以下条件同时满足后停止继续扩张清单：

- 主要架构家族已覆盖：RAG、事实/画像、Agent runtime、时间图、图+向量、文件型、经验/程序、分层巩固、Memory OS、可验证/版本化记忆。
- 用户指定的重点项目和五类新增候选均已进入来源登记或补充审计。
- 论文和 benchmark 已覆盖写入、检索、更新、时间推理、选择性遗忘、幻觉和下游任务收益。
- 连续检索没有发现同时具备高影响/高活跃且代表全新架构家族的候选。

这意味着文档可以进入项目设计阶段；未来若要更新，应以固定日期重新跑一次 GitHub/API/论文核验，而不是无限追加项目名称。

# GitHub Agent Memory 项目活跃度补充

> 快照日期：2026-08-22  
> 证据优先级：官方 GitHub 页面/API > 官方文档 > 社区列表。  
> 说明：活跃度是项目筛选信号，不是技术质量、生产可靠性或安全性的证明。

## 1. 已纳入主报告的高信号项目

| 项目 | 官方仓库 | 本次可核验的活跃/影响信号 | 主要架构信号 |
|---|---|---|---|
| Mem0 | [mem0ai/mem0](https://github.com/mem0ai/mem0) | 约 63.7k stars、7.4k forks、2,600+ commits；官方 API 显示 2026-08-21 仍有更新 | 原子事实、user/session/agent scope、语义+BM25+实体混合检索 |
| Letta / MemGPT | [letta-ai/letta](https://github.com/letta-ai/letta) / [letta-code](https://github.com/letta-ai/letta-code) | `letta-code` 官方页面显示 183 个 Release，最新页面为 v0.26.2；代码与文档已分散到新仓库 | Memory Block、外部 Markdown/MemFS、Agent 自主管理、Git 审计 |
| Graphiti / Zep | [getzep/graphiti](https://github.com/getzep/graphiti) / [getzep/zep](https://github.com/getzep/zep) | 官方文档持续维护，强调增量图、时间查询、混合搜索和 MCP | 实体、事实边、episode、validity window、时间图 |
| Cognee | [topoteretes/cognee](https://github.com/topoteretes/cognee) | 官方 Release 与文档持续更新，新增 MCP、反馈、时间检索和 Agent Skill | `add → cognify → memify`、向量+图、DataPoint/NodeSet |
| Hindsight | [vectorize-io/hindsight](https://github.com/vectorize-io/hindsight) | 约 2,630 commits、56 个 Release；v0.7.0 页面日期为 2026-05-27 | Fact、Experience、Observation、Mental Model；多路召回+reflect |
| Supermemory | [supermemoryai/supermemory](https://github.com/supermemoryai/supermemory) | 约 1,639 commits、2,000+ forks；官方 changelog 仍持续更新 | Fact、Preference、Episode、Profile、updates/extends/过期 |
| Memori | [MemoriLabs/memori](https://github.com/MemoriLabs/memori) | 官方仓库持续维护 SDK、文档和 Cloud；页面有 Issues/PR 活动 | Entity、Process、Session、Event、Rule、Skill；后台增强 |
| LangMem | [langchain-ai/langmem](https://github.com/langchain-ai/langmem) | 约 1.6k stars、186 forks；有 Issues/PR，暂未发布正式 Release | LangGraph Store、热路径 memory tool、后台 memory manager |
| LlamaIndex | [run-llama/llama_index](https://github.com/run-llama/llama_index) | 约 51.8k stars、8k forks；核心仓库与集成持续维护 | 文档/Node/Vector/Property Graph/Chat Store/Agent Memory |
| MemoryOS | [BAI-LAB/MemoryOS](https://github.com/BAI-LAB/MemoryOS) | 约 1.6k stars、159 forks；2025—2026 持续加入 MCP、Docker、embedding 等 | Short/Mid/Long-term 分层、用户画像、MCP |
| MemOS | [MemTensor/MemOS](https://github.com/MemTensor/MemOS) | 有 Issues/PR；官方 README 记录到 2026-07-02 的 benchmark 更新 | MemCube、文本/树/偏好/技能/KV/LoRA、scheduler/lifecycle |
| ReMe | [agentscope-ai/ReMe](https://github.com/agentscope-ai/ReMe) | 官方 News 更新到 2026-08；2026-07 记录 ACL 2026 Findings 接收 | Markdown daily/digest、Wikilink、BM25+向量+RRF、程序记忆 |
| LightMem | [zjunlp/LightMem](https://github.com/zjunlp/LightMem) | 约 1.1k stars、104 forks；标注 ICLR 2026，含 LongMemEval/Coding Agent 示例 | Small LM、在线压缩、离线巩固、低成本 memory |
| A-MEM | [agiresearch/A-mem](https://github.com/agiresearch/A-mem) | 约 1.2k stars、121 forks、31 commits；暂无正式 Release | Zettelkasten note、标签、关键词、动态链接和演化 |
| EverMemOS | [NetMindAI-Open/EverMemOS](https://github.com/NetMindAI-Open/EverMemOS) | 约 554 commits；README 标注 v1.2.0，但当前仓库是 fork，不能直接把 stars 当原项目热度 | Episode、Fact、Preference、Profile、progressive profile |
| memU | [NevaMind-AI/memU](https://github.com/NevaMind-AI/memU) | 持续维护多种 Agent Host Adapter、本地/自托管/Cloud/doctor 流程 | Session log → Markdown memory/skill、progressive retrieval |

## 2. 值得补充跟踪的候选

| 项目 | 官方仓库 | 为什么值得跟踪 | 当前成熟度判断 |
|---|---|---|---|
| Memobase | [memodb-io/memobase](https://github.com/memodb-io/memobase) | 以 User Profile、Event Timeline、topic/subtopic memo 为中心；有 per-user buffer 降低频繁 LLM 调用 | 用户画像方向较清晰；不是通用 Agent 经验库 |
| OpenMemory | [CaviraOSS/OpenMemory](https://github.com/CaviraOSS/OpenMemory) | SQL-native、temporal graph、recency/frequency/importance；默认 SQLite，可接 Postgres | 官方明确提示正在 rewrite；适合实验性本地基础设施 |
| MineContext | [volcengine/MineContext](https://github.com/volcengine/MineContext) | 主动式、上下文感知的个人 Agent；有 Memory Bank Migration | 产品方向有价值，但底层生命周期与检索细节公开较少 |
| Memoria | [matrixorigin/Memoria](https://github.com/matrixorigin/Memoria) | 强调 working/persistent memory、实体、Git-like 版本和 MCP | 可验证/版本化治理方向；生态与第三方验证仍较早期 |
| AgentMem | [agentmem/agentmem](https://github.com/agentmem/agentmem) | 把 Agent 声称执行过的内容与实际事件、仓库状态、工具证据关联 | Coding Agent/审计型记忆；不是通用聊天画像系统 |

## 3. 选型时的核验提醒

1. `letta-ai/letta` 与 `letta-ai/letta-code` 需要按当前上游仓库分别核验，不能把旧 README 的状态直接套到新代码。
2. EverMemOS 存在 fork/上游仓库混用问题，应锁定论文、代码和 Release 对应的真实上游。
3. MemOS 与 MemoryOS 名称相近，但前者偏多形态 Memory OS，后者偏短/中/长期个性化记忆。
4. GitHub Star/Fork/Commit 数量会随时间变化；报告中的数字只能作为 2026-08-22 快照。
5. 官方 benchmark 可能使用托管平台优化、特定模型和特定 judge，不宜直接横向排名。
6. “支持 RAG”不等于“支持长期 Agent Memory”；必须检查是否有写入、更新、冲突、时间、删除和 provenance。

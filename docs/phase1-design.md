# 第一阶段设计：带源码证据的文件级代码语义索引

## 目标

第一阶段把一个本地代码文件转换成少量可审阅、可序列化、可检索的高层概念卡片。概念不是类、函数和方法的清单，而是对源码职责和实现逻辑的语义概括。

```text
代码文件 -> AST事实索引 -> Qwen-Flash阅读整文件 -> 2~3个高层概念 -> 本地证据锚定 -> JSON / SQLite FTS
```

## 概念粒度约束

- 一个代码文件最多输出 **9 个概念**。
- 默认目标是 **约 3 个概念**；普通文件通常应落在 2~3 个。
- 不为每个类、函数或方法单独建卡；它们作为可验证的证据符号保留。
- 概念名必须尽可能精简，优先使用 2~6 个汉字或 1~4 个英文词，最多 10 个词；删除可省略的泛化词。
- `background` 是模型阅读源码后提炼的中文背景说明，应解释实现路径、解决的问题和关键依赖。
- 源码位置和证据符号由本地 AST 事实锚定，不能由模型臆造。

## 设计决策

### 1. AST 只负责事实，模型负责概括

Python AST 提供符号限定名、类型、行号、签名和文档字符串，避免模型生成不存在的位置。模型负责阅读整个文件、选择 2~3 个高层概念、生成概念名和背景说明。模型解释不能冒充源码事实；只有本地解析器能定位的文件、符号和行号才构成源码证据。

### 2. 默认使用 Qwen-Flash，一次请求一个文件

`DASHSCOPE_API_KEY` 从环境变量读取，默认模型为 `qwen-flash`。一个文件只调用一次模型，模型输出严格限制为 JSON 概念数组；调用失败或未配置密钥时使用离线回退，不把密钥写入卡片、日志或源码。

在线模式的项目边界是：普通源码默认允许发送，敏感文件必须显式加入白名单。敏感文件白名单和脱敏策略仍属于待实现的运行安全能力。

### 3. 输出卡片必须可追溯

每张卡片包含：

- `name`：模型生成的概念名词；
- `definition`：概念的一句话定义；
- `background`：源码提炼出的实现背景；
- `background_concepts`：理解该概念需要的前置概念；
- `location`：文件、模块和证据覆盖的行范围；
- `metadata.evidence_symbols` / `metadata.evidence_locations`：支持该概念的类、函数、方法及行号位置；
- `metadata.validation_status`：证据锚定状态（`validated` / `partially_anchored` / `unanchored`）；
- `source_excerpt` / `source_digest`：来源摘要和稳定摘要哈希；
- `model_name`、`model_version`、`prompt_version`、`generation_config`：生成与验证元数据。

### 4. 按源码摘要增量更新并缓存

缓存键至少包含：

```text
source_digest
model_name / model_version
prompt_version
generation_config
```

源码文件未变化且生成条件未变化时复用卡片；任一条件变化时重新生成。文件删除时移除或标记对应卡片。

## 第一阶段公开 API

- `analyze_path(path)`：读取一个文件或目录，按文件输出少量概念卡片。
- `ConceptSynthesisConfig`：配置模型名、目标概念数、9 张硬上限和源码长度上限。
- `DashScopeQwenSynthesizer`：使用 DashScope Qwen-Flash 生成概念。
- `OfflineConceptSynthesizer`：无密钥时的确定性回退，供本地测试使用。
- `ConceptCard.to_dict()` / `ConceptCard.from_dict()`：稳定的 JSON 交换格式。
- `ConceptStore.upsert_cards(cards)` / `ConceptStore.search(query)`：SQLite FTS 存储和可解释检索。

## 运行

```bash
pip install -e '.[dashscope]'
export DASHSCOPE_API_KEY='在 shell 中设置，不要写入代码或仓库'
PYTHONPATH=src python3 -m memory_system.cli path/to/file.py \
  --model qwen-flash --target-concepts 3 --max-concepts 9 \
  --output concepts.json
```

## 明确不在本阶段

- 核心能力以独立 Python API 提供，不绑定任何具体 Coding Agent；MCP 服务是独立适配层。
- 不实现概念关系和概念组合。
- 不把模型生成的概念关系伪装成已经验证的事实。

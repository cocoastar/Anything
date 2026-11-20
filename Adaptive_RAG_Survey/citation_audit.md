# 引用核验记录

## 数量与覆盖

当前正文实际引用 **33 篇文献**，BibTeX 条目与正文引用键一致，无未引用条目。年份分布为：2020 年 3 篇、2023 年 6 篇、2024 年 12 篇、2025 年 7 篇、2026 年 5 篇。其中 29 篇按正式出版版本引用，4 篇按 arXiv 预印本引用。文献调研与状态核验截至 2026 年 11 月。

选题所需的主要机制均有文献支撑：

- 基础框架：REALM、DPR、RAG。
- 检索触发与查询：Adaptive-RAG、FLARE、DRAGIN、Self-RAG、HyDE、RQ-RAG。
- 证据评价与纠正：CRAG、RAG-Critic，以及长上下文和噪声研究。
- 迭代搜索与策略学习：IRCoT、Iter-RetGen、ReAct、Search-o1、Search-R1、ReSearch、RAG-Gym。
- 开放网络研究：DeepResearcher、WebThinker。
- 充分性与停止：S2G-RAG、MetaRAG、部分答案质量与效用预测。
- 评价与诊断：RAGAS、RAGChecker、RGB、CRAG Benchmark、AgenticRAG-FP。

对约 5900 字的课程综述，这一数量能够覆盖本文讨论范围。正文未追求穷举全部智能体搜索工作，而是保留承担机制比较或评价论证作用的论文。

## 2025—2026 年文献的出版状态

- `jin2025searchr1`：COLM 2025。所核原文 v5 首页明确标注 “Published as a conference paper at COLM 2025”，因此按会议论文引用。
- `li2025searcho1`：EMNLP 2025，ACL Anthology `2025.emnlp-main.276`。
- `chen2025research`：NeurIPS 2025，第 38 卷，94974--94994 页，DOI `10.52202/085713-2858`。
- `zheng2025deepresearcher`：EMNLP 2025，ACL Anthology `2025.emnlp-main.22`。
- `li2025webthinker`：NeurIPS 2025，第 38 卷，133164--133204 页，DOI `10.52202/085713-4011`。
- `dong2025ragcritic`：ACL 2025，ACL Anthology `2025.acl-long.179`。
- `xiong2026process`：Nature Communications，2026 年 10 月 28 日在线发表，DOI `10.1038/s41467-026-78807-z`；正文采用正式标题，不把它仅写成 RAG-Gym 预印本。
- `li2026s2g`：ACL 2026，ACL Anthology `2026.acl-long.1185`。
- `qi2026metarag`：EMNLP 2026，ACL Anthology `2026.emnlp-main.461`。
- `tian2026partial`：CIKM 2026，4648--4659 页，DOI `10.1145/3799682.3840904`。
- `pothuru2026failures`：REALM Workshop at EMNLP 2026，ACL Anthology `2026.realm-1.46`。作者字段依 ACL 官方 BibTeX。
- `aghajani2025fairrag`：arXiv:2510.22344，按预印本引用；正文只使用其 Structured Evidence Assessment 机制，不采用其横向性能宣称。

其余预印本为 `gao2024survey`、`yan2024crag` 和 `chan2024rqrag`，未虚构会议出版信息。

## 技术论断边界

- Search-R1 与 ReSearch 归为结果奖励驱动的搜索策略学习；RAG-Gym 用于说明过程监督、信用分配与域外泛化。
- DeepResearcher 和 WebThinker 用于说明真实网页环境、浏览与报告生成，不将其综合实验分数直接与固定语料问答比较。
- S2G-RAG、FAIR-RAG 已涉及显式证据缺口，因此正文将比较重点放在证据来源、冲突和任务依赖的充分性上，不把“证据槽位”表述为无人研究的方向。
- MetaRAG 与部分答案效用研究已直接处理停止，正文改为比较内部信念、证据覆盖与答案效用，并分析答案探针和评价指标对部分效用的影响，不声称停止问题尚未得到研究。
- 约 11% 的迭代减少与约 98% 的最终质量保留均来自 Tian 等论文的特定实验设置，未写成普遍性能保证。
- 式 (1)--(3) 是统一分析表示；式 (4) 对应 Tian 等定义的部分效用。不同论文的实验数值未拼接为统一排名。

## 检索纠错记录

- arXiv `2408.04020` 实际是核物理领域的 CTEQ-TEA 论文，不是 SeaKR。该误检结果未进入 BibTeX 或正文。
- `yan2024crag` 是 Corrective Retrieval Augmented Generation；`yang2024cragbench` 是 Comprehensive RAG Benchmark，二者用途不同。
- `gao2023hyde` 的正式标题是 *Precise Zero-Shot Dense Retrieval without Relevance Labels*，HyDE 是方法名称。
- RAGChecker 与 CRAG Benchmark 的 NeurIPS DOI 分别为 `10.52202/079017-0692` 和 `10.52202/079017-0335`。

核心原文及提取文本保存在 `paper_sources/`。提交者仍应亲自阅读与最终论点最相关的论文，尤其是第 3—5 节引用的工作。

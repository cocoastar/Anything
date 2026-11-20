# 引用核验记录

## 范围

正文引用 25 篇文献，BibTeX 条目数与正文引用键一致，无未引用条目。核心机制描述已对照 `paper_sources/` 中的论文 PDF/提取文本复核；其余出版信息通过 ACL Anthology、OpenReview、PMLR、NeurIPS 元数据、DOI 与 arXiv 核对。

## 需要正确理解的同名工作

- `yan2024crag` 是 Corrective Retrieval Augmented Generation，方法论文。
- `yang2024cragbench` 是 Comprehensive RAG Benchmark，评价基准。
- `gao2023hyde` 的正式标题是 *Precise Zero-Shot Dense Retrieval without Relevance Labels*，HyDE 是方法名称。
- `ma2023query` 的正式标题采用 *Query Rewriting for Retrieval-Augmented Large Language Models*，正文用 Rewrite-Retrieve-Read 指其框架。
- `gao2024survey`、`gao2024modular`、`yan2024crag`、`chan2024rqrag` 与 `singh2025agentic` 按 arXiv 预印本引用，未虚构会议出版信息。

## 论断边界

- 方法表格的“主要边界与代价”和第 6 节是本文基于方法的机制分析，不是统一实验排名。
- 式 (2)--(4) 是本文为比较方法建立的概念抽象，不归因于任何单篇论文。
- 未将不同论文在不同知识库、检索器和生成模型下的实验数值横向拼接。
- RAGChecker 与 CRAG Benchmark 的 NeurIPS 引用使用 DOI `10.52202/079017-0692` 和 `10.52202/079017-0335`。

提交者仍应阅读核心原文，尤其核对 Self-RAG、CRAG、DRAGIN、Adaptive-RAG、IRCoT 与评价框架的描述，并按自己的理解修改第 6 节。

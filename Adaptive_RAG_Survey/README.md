# 自适应 RAG 中文课程综述

题目：**从静态检索到自适应闭环：检索增强生成的检索决策、证据纠错与迭代推理**。

## 当前稿件

- 常规 A4 中文课程综述格式，不表示任何会议投稿。
- 正文六节：问题引入、技术基础、三类自适应决策、迭代检索与推理、评价、结论；文献比较与概念分析穿插在相应方法部分。
- 摘要及正文约 **5940 个汉字**（不含参考文献），含 4 组编号公式和 3 张中文比较表；当前未插入图片。
- **33 篇参考文献**均在正文实际引用，其中 2025—2026 年文献 12 篇；29 篇按正式出版版本引用，4 篇按预印本标注。调研和出版状态核验截至 2026 年 11 月。
- 已编译为 **11 页**：`main.pdf` 是原始版本，推荐阅读可搜索、可复制中文的 `main_readable.pdf`。
- 图片源文件保留在 `figures/`，但不参与当前正文；`figure_plan.md` 记录后续可选插图方案。

## 文件

- `main.tex`：主文件与版式；`sec/`：摘要和正文六节。
- `reference.bib`：文献条目；`citation_audit.md`：出版状态、覆盖范围与论断边界。
- `style_audit.md`：防御性写作、机械分期、模板化表达和篇幅压缩记录。
- `paper_sources/`：部分核心论文原文与提取文本，供复核，不参与编译。
- `figures/`：自绘 PDF/SVG/PNG 机制图；`tools/draw_framework.py` 可重建该图。
- `tools/fix_unicode_cmap.py`：补充 PDF 中文文本映射，不改变可见版面。

## 编译

常规 TeX Live（需要 XeLaTeX 和 BibTeX）：

```bash
xelatex main.tex
bibtex main
xelatex main.tex
xelatex main.tex
```

本环境使用 Tectonic：

```bash
/tmp/adaptive-rag-tex/tectonic -k --keep-logs main.tex
python tools/fix_unicode_cmap.py main.pdf main_readable.pdf
```

`/tmp` 中的编译器可能在系统清理后消失，届时可使用常规 TeX Live。第二个脚本需要 `pypdf` 和网络，仅补充 PDF 文本映射。

## 提交前

1. 在 `main.tex` 中填写姓名、学号与课程名称。
2. 按 `citation_audit.md` 阅读核心原文，尤其核对第 3—5 节中的比较分析，并按自己的理解调整相关表述。
3. 如需恢复插图，可重新选择图的内容与位置；当前版本不依赖任何图片。

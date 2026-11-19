# 自适应 RAG 中文课程综述

题目：**从静态检索到自适应闭环：检索增强生成的检索决策、证据纠错与迭代推理**。

## 当前稿件

- 常规 A4 中文课程综述格式，不表示任何会议投稿。
- 正文七节：问题引入、技术基础、三类自适应决策、迭代检索与推理、评价、个人理解、结论。
- 摘要及正文约 9800 个汉字（不含参考文献），含 4 组概念性公式和 2 张中文比较表。
- 25 篇参考文献均在正文实际引用；正式出版版本优先，预印本按预印本标注。
- 已编译为 14 页：`main.pdf` 是原始版本，推荐阅读可搜索、可复制中文的 `main_readable.pdf`。
- 当前未插图；`figure_plan.md` 给出了两张后续自绘图的位置、内容和图注建议。

## 文件

- `main.tex`：主文件与版式；`sec/`：摘要和正文七节。
- `reference.bib`：文献条目；`citation_audit.md`：引用边界和同名工作核验记录。
- `paper_sources/`：部分核心论文原文与提取文本，供复核，不参与编译。
- `figures/`：预留后续自绘插图；`tools/`：PDF 中文映射修复脚本。

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
/tmp/adaptive-rag-tex/tectonic -k main.tex
python tools/fix_unicode_cmap.py main.pdf main_readable.pdf
```

`/tmp` 中的编译器可能在系统清理后消失，届时可重新安装 Tectonic 或使用常规 TeX Live。第二个脚本需要 `pypdf` 和网络，仅补充 PDF 文本映射，不改变可见版面。

## 提交前

1. 在 `main.tex` 中填写姓名、学号与课程名称。
2. 按 `citation_audit.md` 对照原文复核关键论断，尤其仔细阅读第 6 节并改成自己的最终理解。
3. 若需要满足“图文并茂”，按 `figure_plan.md` 添加自绘图后重新编译并检查浮动位置。

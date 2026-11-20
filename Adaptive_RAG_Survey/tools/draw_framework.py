#!/usr/bin/env python3
"""Draw a compact, technical overview of Adaptive RAG using SimSun and Times."""
from pathlib import Path
import matplotlib as mpl
mpl.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
from matplotlib.path import Path as MplPath
from matplotlib.font_manager import FontProperties

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'figures'
OUT.mkdir(exist_ok=True)
FONT_DIR = Path('/nativemm2/share/cpfs/lijiangyang.ljy/font')
CN = FontProperties(fname=str(FONT_DIR / 'simsun.ttc'))
EN = FontProperties(fname=str(FONT_DIR / 'times.ttf'))

mpl.rcParams.update({
    'pdf.fonttype': 42, 'ps.fonttype': 42, 'svg.fonttype': 'none',
    'mathtext.fontset': 'stix', 'axes.unicode_minus': False,
})

NAVY = '#203746'
TEXT = '#2F424D'
MUTED = '#6B7A82'
BLUE = '#527E9B'
BLUE_BG = '#F1F5F8'
GREEN = '#668B7D'
GREEN_BG = '#F1F6F3'
ORANGE = '#B48754'
ORANGE_BG = '#FAF6F0'
GRAY_BG = '#F5F6F6'
BORDER = '#BECAD0'
LIGHT = '#E1E7EA'
WHITE = '#FFFFFF'

fig = plt.figure(figsize=(6.5, 4.12), facecolor=WHITE)
ax = fig.add_axes([0, 0, 1, 1])
ax.set_xlim(0, 16)
ax.set_ylim(0, 10)
ax.axis('off')


def prop(base, size):
    p = base.copy(); p.set_size(size); return p


def cn(x, y, s, size=7, color=TEXT, ha='left', va='center', **kw):
    return ax.text(x, y, s, fontproperties=prop(CN, size), color=color,
                   ha=ha, va=va, linespacing=1.35, **kw)


def en(x, y, s, size=7, color=TEXT, ha='left', va='center', **kw):
    return ax.text(x, y, s, fontproperties=prop(EN, size), color=color,
                   ha=ha, va=va, linespacing=1.25, **kw)


def math(x, y, s, size=9, color=NAVY, ha='left', va='center', **kw):
    return ax.text(x, y, s, fontsize=size, color=color, ha=ha, va=va, **kw)


def box(x, y, w, h, fc=WHITE, ec=BORDER, lw=0.75, radius=0.035, z=1):
    p = FancyBboxPatch((x, y), w, h,
        boxstyle=f'round,pad=0,rounding_size={radius}', facecolor=fc,
        edgecolor=ec, linewidth=lw, zorder=z)
    ax.add_patch(p)
    return p


def arrow(points, color=NAVY, lw=0.9, z=5, style='-|>'):
    path = MplPath(points, [MplPath.MOVETO] + [MplPath.LINETO]*(len(points)-1))
    p = FancyArrowPatch(path=path, arrowstyle=style, mutation_scale=7,
        linewidth=lw, color=color, shrinkA=0, shrinkB=0,
        joinstyle='miter', capstyle='butt', zorder=z)
    ax.add_patch(p)
    return p


def num(x, y, value, color):
    en(x, y, value, size=6.0, color=color)


def module(x, title_cn, title_en, formula, signals, methods, tone='blue'):
    colors = {'blue': (BLUE, BLUE_BG), 'green': (GREEN, GREEN_BG),
              'orange': (ORANGE, ORANGE_BG)}
    c, bg = colors[tone]
    y, w, h = 5.03, 3.30, 2.18
    box(x, y, w, h, fc=bg, ec=c, lw=0.75)
    ax.plot([x, x+w], [6.77, 6.77], color=c, lw=0.55, zorder=2)
    num(x+0.18, 6.99, title_cn[:2], c)
    cn(x+0.56, 6.99, title_cn[2:], size=7.8, color=NAVY)
    en(x+w-0.16, 6.47, title_en, size=4.9, color=MUTED, ha='right')
    math(x+0.18, 6.10, formula, size=8.0)
    cn(x+0.18, 5.70, signals, size=5.2, color=MUTED)
    en(x+0.18, 5.27, methods, size=4.9, color=c)


# Main policy canvas
box(0.20, 2.00, 15.60, 7.72, fc='#FDFEFE', ec=LIGHT, lw=0.7)
en(0.48, 9.43, 'ADAPTIVE RAG  /  UNIFIED CONTROL VIEW', size=6.2, color=NAVY)
cn(15.48, 9.43, '状态、控制算子与代表方法', size=6.3, color=MUTED, ha='right')
ax.plot([0.48, 15.48], [9.18, 9.18], color=LIGHT, lw=0.65)

# State band
box(0.52, 7.76, 14.96, 1.08, fc=GRAY_BG, ec=BORDER, lw=0.7)
en(0.76, 8.54, 'STATE', size=5.3, color=MUTED)
math(0.76, 8.12, r'$s_t=(x,\;G_t,\;E_t,\;b_t)$', size=10.0)
cn(4.15, 8.40, '输入问题', size=5.8)
cn(4.15, 8.04, '生成历史', size=5.8)
cn(5.70, 8.40, '当前证据', size=5.8)
cn(5.70, 8.04, '剩余预算', size=5.8)
ax.plot([7.30, 7.30], [7.94, 8.65], color=BORDER, lw=0.6)
en(7.62, 8.54, 'ACTION SPACE', size=5.3, color=MUTED)
cn(7.62, 8.10, '直接生成  /  查询修正  /  检索  /  证据纠正  /  停止', size=6.1)
math(14.98, 8.10, r'$s_t\rightarrow s_{t+1}$', size=8.0, color=BLUE, ha='right')

# Four technical control modules
module(0.52, '01检索触发', 'WHEN TO RETRIEVE',
       r'$a_t^R=\pi_R(s_t)\in\{0,1\}$',
       '不确定性  |  复杂度  |  反思标记',
       'FLARE · DRAGIN · Adaptive-RAG\nSelf-RAG', 'blue')
module(4.22, '02查询构建', 'WHAT TO RETRIEVE',
       r'$q_t=\pi_q(x,G_t,E_t)$',
       '改写  |  分解  |  消歧\n假设文档',
       'HyDE · RQ-RAG\nIRCoT · Iter-RetGen', 'blue')
module(7.92, '03证据评价', 'HOW TO REPAIR EVIDENCE',
       r'$E_{t+1}=\pi_E(E_t\cup R_t)$',
       '评价  |  过滤  |  补充  |  重排',
       'CRAG · Self-RAG', 'green')
module(11.62, '04停止控制', 'WHEN TO STOP',
       r'$a_t^S=\pi_S({\rm Cov}_t,\Delta E_t,b_t)$',
       '继续检索  |  生成  |  拒答',
       'COVERAGE · MARGINAL GAIN\nBUDGET', 'orange')

# Forward transitions and local query-correction feedback
arrow([(3.82, 6.12), (4.22, 6.12)], color=BLUE)
arrow([(11.22, 6.12), (11.62, 6.12)], color=ORANGE)
arrow([(7.92, 5.47), (7.52, 5.47)], color=ORANGE, lw=0.85)
arrow([(2.17, 7.76), (2.17, 7.21)], color=BLUE)

# Non-parametric memory and generator
box(4.22, 2.62, 7.00, 1.60, fc=WHITE, ec=GREEN, lw=0.75)
en(4.45, 3.94, 'NON-PARAMETRIC MEMORY', size=5.3, color=GREEN)
math(4.45, 3.46, r'$R_t=\operatorname{TopK}_{d\in\mathcal{D}}\,s_\phi(q_t,d)$', size=9.2)
cn(9.55, 3.46, '静态语料  /  搜索接口\n领域知识库', size=5.2, color=MUTED, ha='center')
en(4.45, 2.91, 'RETRIEVE  ·  RETURN CANDIDATE EVIDENCE', size=4.8, color=MUTED)

box(11.62, 2.62, 3.86, 1.60, fc=BLUE_BG, ec=BLUE, lw=0.75)
en(11.85, 3.94, 'GENERATOR', size=5.3, color=BLUE)
math(11.85, 3.46, r'$y_t\sim p_\theta(y\mid x,G_t,E_{t+1})$', size=7.4)
cn(11.85, 2.91, '生成答案及证据来源', size=5.9, color=MUTED)

# Query -> memory -> evidence; stop -> generator
arrow([(5.87, 5.03), (5.87, 4.22)], color=BLUE)
arrow([(9.57, 4.22), (9.57, 5.03)], color=GREEN)
arrow([(13.27, 5.03), (13.27, 4.22)], color=NAVY)

# Joint objective band
box(0.20, 0.18, 15.60, 1.50, fc='#FAFBFB', ec=LIGHT, lw=0.7)
en(0.48, 1.43, 'QUALITY–COST–RISK OBJECTIVE', size=5.5, color=NAVY)
math(0.48, 0.83, r'$\max_\pi\;\mathbb{E}_\pi\,[Q(y,E)-\lambda_r C_r-\lambda_l C_l-\lambda_u C_u]$', size=9.0)
ax.plot([7.58, 7.58], [0.40, 1.40], color=BORDER, lw=0.6)
en(7.92, 1.25, 'Q  /  QUALITY', size=5.4, color=BLUE)
cn(7.92, 0.72, '正确性 · 忠实性 · 证据覆盖', size=5.7, color=MUTED)
en(11.00, 1.25, 'C  /  COST', size=5.4, color=GREEN)
cn(11.00, 0.72, '检索 · Token · 延迟', size=5.4, color=MUTED)
en(13.85, 1.25, 'RISK', size=5.4, color=ORANGE)
cn(13.85, 0.72, '幻觉 · 无效循环', size=5.4, color=MUTED)

fig.savefig(OUT / 'adaptive_rag_framework.pdf', bbox_inches=None, pad_inches=0)
fig.savefig(OUT / 'adaptive_rag_framework.png', dpi=360, bbox_inches=None, pad_inches=0)
fig.savefig(OUT / 'adaptive_rag_framework.svg', bbox_inches=None, pad_inches=0)
print(OUT / 'adaptive_rag_framework.pdf')

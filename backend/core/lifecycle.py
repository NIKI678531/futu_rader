"""负面舆情类别的生命周期与关注程度（PRD §3.4 第 2 层、§4.2 P9）。

叶子模块：不 import provider。设计源 `design/radar-data.js:625–628` 的规则逐字移植：

    baseMn === 0 ? 'new' : (mn > baseMn*1.15 ? 'continuing' : (mn < baseMn*0.7 ? 'fading' : 'continuing'))
    sev = (share >= 25 && life !== 'fading') ? 'high' : (share >= 12 ? 'medium' : 'low')

## 基准期没标注 ⇒ 生命周期是未知，不是「新增」

设计源里 `baseMn` 永远有值（编的）。真库里基准期可能一条标注都没有（只跑了当前期），
这时 `base` 是 None：说不出这个类别是新增还是持续。返回 None 让页面渲染「暂不可用」，
而不是把「不知道」写成「新增」（铁律 2）。
"""

LIFE_LABEL = {"new": "新增", "continuing": "持续", "fading": "消退"}
SEVERITY_LABEL = {"high": "高", "medium": "中", "low": "低"}

# PRD §4.2 P9 逐字：关注程度 高≥25% / 中≥12% / 低。
SEVERITY_HIGH = 25.0
SEVERITY_MEDIUM = 12.0


def lifecycle(cur, base):
    """当前期条数 vs 基准期条数 → `new` / `continuing` / `fading` / None（基准未知）。"""
    if cur is None or base is None:
        return None
    if base == 0:
        return "new" if cur > 0 else None
    if cur > base * 1.15:
        return "continuing"
    if cur < base * 0.7:
        return "fading"
    return "continuing"


def severity(share_of_negative, life):
    """`share_of_negative` 是该类别占全部消极的百分比（0–100）。"""
    if share_of_negative is None:
        return None
    if share_of_negative >= SEVERITY_HIGH and life != "fading":
        return "high"
    if share_of_negative >= SEVERITY_MEDIUM:
        return "medium"
    return "low"

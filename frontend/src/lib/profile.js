/* KOL 画像聚合 —— 对**已下发的帖子子集**做计数，不走网络（ADR-0015）。
 *
 * ## 为什么这一个聚合留在前端
 *
 * ADR-0004 把 window.RADAR 的成员分成契约函数（后端）／口径常量（后端 /meta）／
 * 展示助手（前端）／生成器（必须消失）四类。`kolProfile` 一类都不属于，它是第五类：
 * **视图内聚合** —— 对接口已经发下来的数据再数一遍。
 *
 * 它不能是端点。PRD §4.3 M5 逐字要求声量排名「随 ETF 与类型筛选变化、不随 KOL 筛选
 * 变化」，页面要的是**当前可见帖子子集**上的画像；那个子集由四级级联筛选 × 类型多选 ×
 * `campRule` 开关在屏幕里算出来。把这套筛选语义在 Python 里再实现一遍，就是 CLAUDE.md
 * 明令禁止的「照着行为手推一遍」，任何一处对不齐逐字比对就红，而且极难定位。
 *
 * 它也不违反铁律 1。铁律 1 管的是口径公式 —— 讨论热度、评论去重、基准区间、情绪净值、
 * 积极／消极占比、环比、阶段合并、样本阈值。这里数的是「这批帖子里提自家的有几篇」，而「这批」
 * 就是筛选出来的可见集，按设计本来就该随筛选重算。真正的口径（全量 leaders）由后端在
 * kolImpact 里算好下发，KOL 详情页用的是那一份。
 *
 * ## 拷贝自 design/radar-data.js 的 kolProfile()，只有一处有意偏差
 *
 * 20% 的「兼」阈值、typeOrder 的排序、top 取互动最高的一篇 —— 这些都直接印在页面上
 * （styleTag「X为主 · 兼Y」是 PRD §4.4 M3 的逐字要求）。改设计源时照拷，别照着新行为重写。
 *
 * **偏差一：互动与评论的合计对 null 有传染性**（下面的 `add`）。设计源写的是
 * `eng += p.engagement`，而 JS 的 `+` 会把 null 当 0：一篇取不到评论数的帖子会被静悄悄
 * 算成「这篇 0 条评论」，合计照样出一个看起来很正常的数字，直接违反铁律 2 —— 而且比
 * 渲染成 NaN 更坏，因为 NaN 一眼就能看出坏了，少算的合计看不出来。演示数据里计数字段
 * 永远齐全，这一支从来不会走到，逐字比对因此仍然全绿；六态场景下才会分叉。
 *
 * **偏差二：`postType` 缺一篇，整份类型画像就是未知。** 设计源写的是 `tc[p.postType]++`，
 * 而 `postType` 为 null 时那是 `tc[null]`，`undefined++` 得到 NaN —— 不报错，但接着
 * 八个真类型的计数全是 0，排序后 `order[0]` 稳定落在 `showcase`，于是**每一位 KOL 都
 * 变成「晒单为主」**。DATA_PROVIDER=sql 下所有帖子的 postType 都是 null（ADR-0017），
 * 这一页会整版挂上一个我们从没算过的结论。所以这里与后端 `_leaders` 对齐：类型五项
 * （typeCounts / typeOrder / topType / topTypeLabel / styleTag）一起变 null。
 *
 * 判据是「**有一篇**不知道」，不是「全都不知道」—— 与 `add` 同一个语义：分布里少一票，
 * 谁是第一名就不确定了。
 */
import { POST_TYPES, TYPE_BY_KEY, descN } from './view'

/* 一位 KOL 的区间画像：篇数、自家 / 竞品 / 双方篇数、互动合计、类型分布与主要类型。campFn 可换判定口径 */
export function kolProfile(name, ps, campFn) {
  var cf = campFn || function (p) { return p.camp; };
  var tc = {}; POST_TYPES.forEach(function (t) { tc[t.k] = 0; });
  /* 有一篇不知道，合计就是不知道。`sum + null` 在 JS 里等于 `sum + 0`，那正是要防的事。 */
  var add = function (sum, v) { return sum == null || v == null ? null : sum + v; };
  var typeNa = false;
  var own = 0, peer = 0, both = 0, eng = 0, cm = 0;
  ps.forEach(function (p) {
    if (p.postType == null) typeNa = true; else tc[p.postType]++;
    eng = add(eng, p.engagement); cm = add(cm, p.comments);
    var c = cf(p);
    if (c === 'own') own++; else if (c === 'competitor') peer++; else if (c === 'both') both++;
  });
  var order = POST_TYPES.map(function (t) { return t.k; }).sort(function (a, b) { return tc[b] - tc[a]; });
  var n = ps.length;
  /* 未知的互动排最后，不是当成 0 排进「互动最少」那一段 —— 那会让 `top`（抽屉默认打开
     的那一篇）在真库下变成一篇我们并不知道互动量的帖子，而页面说它是最高的。 */
  var top = ps.slice().sort(function (a, b) { return descN(a.engagement, b.engagement); })[0] || null;
  var second = tc[order[1]] > 0 && tc[order[1]] >= n * 0.2 ? ' · 兼' + TYPE_BY_KEY[order[1]].label : '';
  return {
    kol: name, n: n, own: own, peer: peer, both: both, ownAny: own + both, peerAny: peer + both,
    engagement: eng, comments: cm,
    typeCounts: typeNa ? null : tc,
    typeOrder: typeNa ? null : order,
    topType: typeNa ? null : order[0],
    topTypeLabel: typeNa ? null : TYPE_BY_KEY[order[0]].label,
    styleTag: typeNa ? null : (n ? TYPE_BY_KEY[order[0]].label + '为主' + second : ''),
    top: top, posts: ps
  };
}

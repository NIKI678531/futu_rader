/* 展示助手 —— 纯渲染，不走网络（ADR-0004）。
 *
 * 这些函数只做「值 → 怎么显示」：配色、导航高亮、千分位、月-日切片。它们不算任何口径，
 * 所以不属于后端，为它们打一趟 API 毫无意义。把它们从设计源镜像里搬出来，是为了让门面
 * 最后能干净地拔掉镜像（工单 12）—— 先让改动变容易，再做那个容易的改动。
 *
 * ## 全部逐字拷贝自 design/radar-data.js
 *
 * 一个字符都没重写。这个仓库最容易出还原度事故的地方就是「照着行为手推一遍」：
 * `shortName` 的两条正则、`rgba` 的 16 进制切片、`pct1` 的先 round 再 toFixed（跟直接
 * toFixed(1) 在 .05 边界上结果不同），手写十有八九差一点，而逐字比对会红在离它十万八千里
 * 的地方。改设计源时也照拷，别照着新行为重写。
 *
 * ## 常量从哪儿来
 *
 * 分界线是**「它是实体，还是标签词汇表」**：
 *
 * - **标签词汇表** —— DOW / NAV / POST_TYPES / DIRECTIONS / CAMP 及其配色表。它们的内容
 *   是 label 加一串 CSS 变量名，存在的唯一目的是把后端下发的枚举键（`postType: 'showcase'`、
 *   `camp: 'own'`）渲染成一枚徽章。让后端知道 `var(--csop-blue-600)` 是荒谬的（ADR-0004），
 *   所以整表拷在这里，与 typeStyle / dirStyle 同住。**枚举键**本身是前后端共享契约。
 * - **实体与口径** —— 产品池、官号名单、预设区间、阈值、六态图例、口径说明文字。它们是
 *   数据和 PRD 约束，从门面 R 上读（背后是 GET /meta）。
 */
import R from '../data/radar'

/* ── 日期 ───────────────────────────────────────────────────────────── */

/* 注意是 UTC：设计源全程用 UTC 构造与读取，换成本地时区会让 dowOf 在某些时区整体偏一天。 */
function parse(s) { var a = s.split('-'); return new Date(Date.UTC(+a[0], +a[1] - 1, +a[2])); }
var DOW = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
export function dowOf(s) { return DOW[parse(s).getUTCDay()]; }
export function md(s) { return s.slice(5); }

/* ── 数值与颜色 ─────────────────────────────────────────────────────── */

export function rgba(hex, a) {
  var h = hex.replace('#', '');
  return 'rgba(' + parseInt(h.slice(0, 2), 16) + ',' + parseInt(h.slice(2, 4), 16) + ',' + parseInt(h.slice(4, 6), 16) + ',' + a + ')';
}

/* 铁律 2 的最后一道渲染关口：null 是「应有值但没取到」，落成 0 就是撒谎。
   长文案「数据暂不可用」用在数值位与环比位，与状态图例的短文案「暂不可用」**不可互换**
   （PRD §3.1、§3.6）。别顺手统一成一个。 */
export function num(v) { return v == null ? '数据暂不可用' : v.toLocaleString('en-US'); }

/* 同一道关口，不打千分位。设计源在表格窄列与悬浮卡里逐字用的是 `String(v)`（那些位置
   宽度紧张，千分位的逗号会把列撑开），headline 数字才用 toLocaleString。
   缺失文案与 num() 完全一致 —— 两者的差别只在千分位，别顺手合并成一个。 */
export function numRaw(v) { return v == null ? '数据暂不可用' : String(v); }

/* 同一道关口。**这里对设计源有一处有意偏离**，写清楚免得下次「照着镜像改回去」：
   设计源的 pct1 没有空值分支，因为镜像里的数是编出来的，永远不缺。接了真库之后
   `pct1(null)` 会算出 `NaN%` —— 那比缺一个数还糟：它长得像一个渲染 bug，而它其实
   在如实反映「这个百分比没取到」，于是没人会去查数据源。
   公式部分仍然逐字（先 round 再 toFixed，与直接 toFixed(1) 在 .05 边界上结果不同）。 */
export function pct1(v) { return v == null ? '数据暂不可用' : (Math.round(v * 10) / 10).toFixed(1) + '%'; }

/* 同一道关口，时间戳位（「数据截至」／「最近更新」／「更新时间」）。

   这一位原来不可能为空：`updatedAt` 手写在 `backend/fixtures/meta.json` 里，是个常量。
   把它改成随主数据下发之后（那才是它的本相 —— 见 `backend/core/meta.py` 模块头），
   库里没有锚点时它就会缺。缺了不写字的后果比数值位更隐蔽：`{undefined}` 在 React 里
   渲染成空，页面上只剩一个「数据截至」加一片空白，看着像样式没对齐，没人会去查数据源。

   用长文案「数据暂不可用」而不是短文案「暂不可用」：这三处都是**值位**（标签在左、
   值在右），与 num()／pct1() 同类，不是状态图例（PRD §3.1、§3.6）。 */
export function stamp(v) { return v == null ? '数据暂不可用' : String(v); }

/* 整块没取到 → 该契约形状的 `unavailable` 态。

   市场域有一串端点是 `{status, list}`（关联竞品、重点舆情、产品相关 KOL、阶段观点、
   K 线……）。`DATA_PROVIDER=sql` 下它们**整块**是 null —— 这些结论要 AI 标注或行情源，
   ADR-0017 说得很清楚。而屏幕代码直接 `res.list.map(…)`／`res.status === 'ok'`，
   接真库时第一次渲染就 TypeError，产品监控整页白屏，错误边界报「页面渲染失败」：
   一个数据缺失被报成了前端崩溃，查错方向从一开始就是反的。

   **这不是默认值兜底。** `status: 'unavailable'` 与后端在该态下发的值逐字相同
   （`core/envelope.py`），空集合只是让 `.map` 有东西可遍历 —— 它永远配着
   `status !== 'ok'` 一起出现，屏幕据此渲染「暂不可用」。把 null 说成 `'empty'` 才是
   撒谎：那句文案是「范围内已完成检查，没有符合条件的内容」（PRD §3.6），
   而我们根本没检查过。

   `keys` 给出该形状里的集合字段名，默认 `['list']`；阶段观点是 `['series', 'stages']`。
   不无脑铺一堆空数组：铺多了，屏幕里一个拼错的字段名会静默变成空列表而不是报错。 */
export function naBox(res, keys) {
  if (res != null) return res;
  var out = { status: 'unavailable' };
  (keys || ['list']).forEach(function (k) { out[k] = []; });
  return out;
}

/* 同一道关口的**求和**版。JS 里 `t + null === t`，于是
 * `list.reduce(function (t, x) { return t + x.f; }, 0)` 会把「这一条不知道」当成
 * 「这一条是 0」—— 合计小了一截，页面上却是个干干净净的数字。这比算出 NaN 糟得多：
 * NaN 会在页面上显眼地报出来，少加的那一项不会。
 *
 * 合计里有一项不知道，合计就是不知道（铁律 2）。语义与后端 `core` 的 `add_all`、
 * `fixtures/generate.mjs` 的 `addN` 完全一致 —— 三处必须同义，否则同一份数据在
 * 后端合计是 null、在前端合计是个数。
 *
 * 这不是口径公式（守卫④），它没有业务规则，只是 `num()` 的算术对偶。 */
export function sumN(list, pick) {
  var t = 0;
  for (var i = 0; i < list.length; i++) {
    var v = pick(list[i]);
    if (v == null) return null;
    t += v;
  }
  return t;
}

/* 降序比较，**未知排最后**。`b - a` 在有 null 的时候会把未知当成 0：`5 - null === 5`。
 * 结果不是崩溃，是一条「互动数不知道」的记录被排进了「互动数最少」那一段 —— 看上去像
 * 一条结论（这个官号没人互动），实际上是一条缺失。两者在榜单上长得一模一样。
 * 演示数据里没有 null，所以 `descN(a, b)` 与 `b - a` 逐字等价，与设计源不会有差异。 */
export function descN(av, bv) {
  if (av == null) return bv == null ? 0 : 1;
  if (bv == null) return -1;
  return bv - av;
}

/* ── 帖子类型与操作方向的标签词汇表 ─────────────────────────────────── */

/* 内容形式（双标签第一维）。`k` 是后端下发的枚举键，label / bar / def 是展示层的事。 */
export var POST_TYPES = [
  { k: 'showcase', label: '晒单', group: 'op', bar: '#C9A961', def: '贴出实际成交记录 / 持仓截图' },
  { k: 'action', label: '操作宣言', group: 'op', bar: '#A8853C', def: '宣布买入 / 卖出 / 调仓意向，无凭证' },
  { k: 'market', label: '行情解读', group: 'view', bar: '#2361AD', def: '对市场或标的走势的分析观点' },
  { k: 'promo', label: '产品推介', group: 'promo', bar: '#1F8A5B', def: '介绍或安利某只 ETF 的特点' },
  { k: 'edu', label: '教学科普', group: 'view', bar: '#7DA5DC', def: '知识型内容，不针对特定操作' },
  { k: 'event', label: '活动/福利', group: 'event', bar: '#7A5C9E', def: '转发平台或发行商活动、抽奖' },
  { k: 'qa', label: '问答/互动', group: 'event', bar: '#B39DC9', def: '向粉丝提问、征集观点、回应评论' },
  { k: 'other', label: '其他', group: 'other', bar: '#B7BFC9', def: '无法归入以上' }
];
export var TYPE_BY_KEY = {}; POST_TYPES.forEach(function (t) { TYPE_BY_KEY[t.k] = t; });
/* 标签配色：操作类琥珀、观点类蓝、推介类绿、活动类紫、其他灰 */
var TYPE_GROUP = {
  op: { bg: 'var(--warning-100)', fg: 'var(--warning-700)' },
  view: { bg: 'var(--csop-blue-50)', fg: 'var(--csop-blue-700)' },
  promo: { bg: 'var(--positive-100)', fg: 'var(--positive-700)' },
  event: { bg: '#F1ECF7', fg: '#5E4480' },
  other: { bg: 'var(--ink-100)', fg: 'var(--ink-600)' }
};
/* 「还没分类」的展示态。**不是** POST_TYPES 里的一员，所以不进 TYPE_BY_KEY：
   筛选菜单遍历的是 POST_TYPES，把它混进去会多出一个选不中任何东西的选项。 */
export var TYPE_NA = {
  k: null, label: '暂不可用', bg: 'var(--ink-100)', fg: 'var(--ink-400)',
  bar: '#C7CED6', def: '内容形式标注尚未生成',
};
/* 缺标注与「其他」是两件事。这里原来只有 `TYPE_BY_KEY[k] || TYPE_BY_KEY.other`，
   于是 `typeStyle(null)` 落成「其他」—— 而「其他」是一个**结论**（分过类了，八类里
   归不进前七类），不是「还没分过类」。DATA_PROVIDER=sql 下每篇帖子的 postType 都是
   null（ADR-0017），照原样渲染，整页 KOL 帖子会挂满我们从没做出过的判断（铁律 2）。

   非空但不认识的键仍然落「其他」：那是后端发来了一个前端不认的枚举值 —— 契约漂移，
   不是数据缺失，两者不该共用一个兜底。短徽章位用短文案「暂不可用」（PRD §3.6
   STATUS_LEGEND 逐字），不是数值位的长文案。 */
export function typeStyle(k) {
  if (k == null) return TYPE_NA;
  var t = TYPE_BY_KEY[k] || TYPE_BY_KEY.other, g = TYPE_GROUP[t.group];
  return { k: t.k, label: t.label, bg: g.bg, fg: g.fg, bar: t.bar, def: t.def };
}

/* 类型置信度的两位小数。`null.toFixed(2)` 直接抛 TypeError，而 sql provider 下它
   **恒为 null**：ADR-0017 §4 判定模型自报的 softmax 不是校准概率、不许冒充，所以这一列
   根本不下发。属于数值位 → 长文案（PRD §3.1）。 */
export function conf2(v) { return v == null ? '数据暂不可用' : v.toFixed(2); }

/* ── AI 结论的复核态徽章（ADR-0019 §2） ──────────────────────────────────
 *
 * AI 标注不再等人批准就上页面，所以徽章从「门槛」变成了「如实声明」：每一条结论都要
 * 说清楚它是模型生成的、能不能回到原文、模型自己有没有举手。
 *
 *   pending             AI 生成 · 可追溯原文（没有可定位证据时退为「AI 生成」）
 *   needs_review        AI 生成 · 待确认      ← 模型自报存疑，或证据定位失败
 *   approved/corrected  同 pending
 *   rejected            不显示（唯一的下线通道）
 *   null                不显示（这一条压根没标注过，由类型徽章的「暂不可用」去说）
 *
 * 三件事是这个函数的全部要点：
 *
 * 1. **没有「已核验」这一枚。** `approved` 只在库里留痕，页面不加。PRD §3.9 的徽章体系
 *    里没有它，而在「不做人工复核」的决定下它几乎永远不会出现 —— 为一个不会出现的
 *    状态造一枚 PRD 外的徽章，等于向读的人暗示这里有过人工确认。
 * 2. **「待确认」的唯一触发是 `needs_review`。** 原来它由 `confidence < 0.7` 驱动，而
 *    `calibrated_confidence` 整列是 NULL（ADR-0017 §4）；`null < 0.7` 在 JS 里是 true，
 *    于是真库下每一篇都会被标成「判过了但没把握」，实际上是「模型自己举了手」与
 *    「压根没跑」被混成了一句话。校准概率存在之前，0.7 这个阈值不生效。
 * 3. **`null` 不等于 `pending`。** 「不知道复核状态」不是「模型给了结论且未举手」。
 */
export function reviewBadge(state, hasEvidence) {
  if (state == null || state === 'rejected') return null;
  if (state === 'needs_review') return 'AI 生成 · 待确认';
  return hasEvidence ? 'AI 生成 · 可追溯原文' : 'AI 生成';
}

/* 「这条结论模型自己举手了吗」的三值判断：是（true）／否（false）／不知道（null）。
   页面上的「仅看待确认」筛选与 CSV 的那一列都读它。三值不能压成两值：压了之后
   没标注过的帖子会被算进「否」，于是筛选结果看起来像「已经全部确认过了」。 */
export function needsReview(state) {
  return state == null ? null : state === 'needs_review';
}

/* ── 页面级的如实声明（ADR-0019 §4） ────────────────────────────────────
 *
 * 逐条徽章说的是「这一条是怎么来的」，这句说的是「整页的 AI 结论被验证到了什么程度」。
 * 两句缺一不可：徽章写满一屏「AI 生成 · 可追溯原文」，读的人仍然会默认有人抽检过。
 *
 * 枚举由 `/meta` 的 `aiValidation` 下发（backend/core/meta.py），本期恒为 `none`。
 * 文案跟着枚举走而不是写死，是因为这句话必须和 `/meta`、和汇报口径**同一个来源** ——
 * 三处分头写死，改了一处另外两处就开始撒谎。
 *
 * `none` 是现成文案。`spot_check` 那句要带抽检条数、日期与两个准确率，这些数随 /meta 的
 * `aiValidationDetail` 一起下发（形状见 data/radar.js 的 AI_VALIDATION_DETAIL）；detail
 * 缺失或缺关键字段时**退回 `none` 那句** —— 不知道验证到哪一步，就不能说验证过
 * （往轻里说，不往重里说）。`gold` 与认不出来的值同样退回 `none`。
 *
 * 这句里没有、也不许有「已核验」（ADR-0019 §4）：抽检说的是「人工核对过 n 条、准确率
 * 多少」，是一个可复核的样本统计，不是对页面上每一条结论的确认。
 */
export var AI_VALIDATION_NOTE = {
  none: 'AI 结论由模型自动生成，未经人工验证；每条可回到原文。'
};

/* 准确率下发的是 0–1 的小数；保留一位小数。大于 1 的值按已经是百分数处理，免得
   契约在这一点上漂了之后页面上出现「8700.0%」。 */
function accPct(v) {
  var p = v > 1 ? v : v * 100;
  return (Math.round(p * 10) / 10).toFixed(1) + '%';
}

export function aiValidationNote(level, detail) {
  if (level === 'spot_check' && detail
    && detail.n != null && detail.attitude_accuracy != null && detail.relevance_accuracy != null) {
    return 'AI 结论由模型自动生成；人工核对 ' + detail.n + ' 条（' + stamp(detail.date) + '），'
      + '态度准确率 ' + accPct(detail.attitude_accuracy) + '、相关性准确率 ' + accPct(detail.relevance_accuracy)
      + '；学生模型蒸馏自 Luna 标注。';
  }
  return AI_VALIDATION_NOTE[level] || AI_VALIDATION_NOTE.none;
}

/* 徽章后缀：后端说这块汇总的底层标注已经更新、汇总还没重新生成（`stale: true`）。
   只认 `=== true`：demo 下没有这个键，`undefined` 不是「旧了」，不加字，逐字比对照旧。 */
export function staleSuffix(flag) { return flag === true ? ' · 待更新' : ''; }
export var STALE_TITLE = '标注已更新，汇总待重新生成';

/* 讨论热度的下限注记。`heatUnknownPosts` 是区间内转发数未知的帖子数（0＝无）：>0 时
   热度／互动／转发都是**下限**，得说出来。缺键（demo）或 0 都不加字。
   读的是后端数好的字段，不在前端重算公式（铁律 1）。 */
export function heatLowerBoundNote(n) {
  return n > 0 ? '（' + n + ' 帖转发数未知 · 下限）' : '';
}

/* 操作方向（双标签第二维）：加仓／建仓 绿、减仓／清仓 红、持有观望 灰 */
export var DIRECTIONS = [
  { k: 'add', label: '加仓', tone: 'pos' }, { k: 'open', label: '建仓', tone: 'pos' },
  { k: 'reduce', label: '减仓', tone: 'neg' }, { k: 'close', label: '清仓', tone: 'neg' },
  { k: 'hold', label: '持有观望', tone: 'neu' }
];
export var DIR_BY_KEY = {}; DIRECTIONS.forEach(function (d) { DIR_BY_KEY[d.k] = d; });
var DIR_TONE = {
  pos: { bg: 'var(--positive-100)', fg: 'var(--positive-700)' },
  neg: { bg: 'var(--negative-100)', fg: 'var(--negative-700)' },
  neu: { bg: 'var(--ink-100)', fg: 'var(--ink-600)' },
  pending: { bg: 'var(--warning-100)', fg: 'var(--warning-700)' }
};
/* 判不出方向 → 「方向待确认」，不是「持有观望」。把没判出来的说成判出来了是六态里
   「待确认」存在的全部理由（PRD §3.6）。 */
export function dirStyle(k, pending) {
  if (pending || !DIR_BY_KEY[k]) return { k: 'pending', label: '方向待确认', bg: DIR_TONE.pending.bg, fg: DIR_TONE.pending.fg, tone: 'pending' };
  var d = DIR_BY_KEY[k], t = DIR_TONE[d.tone];
  return { k: d.k, label: d.label, bg: t.bg, fg: t.fg, tone: d.tone };
}

/* 阵营徽章。**三分互斥**：一条动态只能归一类（PRD 账号域，ADR-0013 O4）。
   KOL 页问的是另一个问题（「这个 KOL 提没提我们」，提自家含 both），别顺手统一。 */
export var CAMP = {
  own: { k: 'own', label: '自家', bg: 'var(--csop-blue-600)', fg: '#fff' },
  competitor: { k: 'competitor', label: '竞品', bg: 'var(--csop-silver-200)', fg: 'var(--ink-700)' },
  both: { k: 'both', label: '双方', bg: 'var(--csop-blue-100)', fg: 'var(--csop-blue-800)' },
  none: { k: 'none', label: '未提及', bg: 'var(--ink-100)', fg: 'var(--ink-500)' }
};

/* ── 产品名缩写 ─────────────────────────────────────────────────────── */

export function shortName(name) {
  var s = String(name || '').replace(/^南方[東东]英/, '').replace(/(指數|指数)?ETF$/, '');
  if (!s) s = String(name || '');
  return s.length > 10 ? s.slice(0, 10) + '…' : s;
}

/* ── 导航与外壳 ─────────────────────────────────────────────────────── */

/* 域的 key 是 `portfolio` / `accounts`，不是显示名「市场」「账号」。屏幕传的就是这两个
   字符串，写错的话 shell() 里那句 NAV.filter(...)[0].items 直接炸。 */
var NAV = [
  { k: 'portfolio', name: '市场', items: [
    { k: 'sector', name: '板块总览', href: 'sector-overview.dc.html' },
    { k: 'product', name: '产品监控', href: 'product-monitor.dc.html' } ] },
  { k: 'accounts', name: '账号', items: [
    { k: 'kol', name: 'KOL 影响力', href: 'kol-activity.dc.html' },
    { k: 'official', name: '官号动态', href: 'official-activity.dc.html' } ] }
];

/* 双层导航（浅色）：一级为域（市场／账号）分段标签，二级为当前域的页面标签 */
export function navGroups(domainKey, subKey) {
  return NAV.map(function (d) {
    var on = d.k === domainKey;
    return {
      name: d.name, href: d.items[0].href,
      fw: on ? 600 : 500,
      fg: on ? 'var(--csop-navy-900)' : 'var(--ink-500)',
      bg: on ? '#fff' : 'transparent',
      sh: on ? '0 1px 2px rgba(14,42,82,0.10), 0 0 0 1px var(--border-1)' : 'none',
      subDisplay: on ? 'flex' : 'none',
      items: d.items.map(function (it) {
        var hit = on && it.k === subKey;
        return {
          name: it.name, href: it.href, fw: hit ? 600 : 400,
          fg: hit ? 'var(--csop-blue-700)' : 'var(--ink-600)',
          bc: hit ? 'var(--csop-blue-600)' : 'transparent'
        };
      })
    };
  });
}

/* 账号域沿用的公共外壳。
   唯一一处会间接触网的展示助手：range 与 presets 来自门面（已迁到后端）。它自己不算
   区间，只把后端下发的 text/from/to 摆到外壳上。

   **比设计源少两个输出：chips 与 statusLegend。** 用 shell() 的只有账号域三个屏，
   三个都不渲染这两样（板块芯片与六态图例都在板块总览页，那边自己拼）。而 chips 背后是
   SECTOR_AGG —— 全市场板块聚合，要把 61 只产品逐个 observe 一遍。留着它，三个账号页
   每次渲染都会去拉一份自己从不显示的市场数据。删掉是有意的取舍，不是漏拷。 */
export function shell(domainKey, subKey, st, go) {
  var range = R.buildRange(st.rangeKey || R.DEFAULT_RANGE);
  return {
    navGroups: navGroups(domainKey, subKey),
    domains: NAV.map(function (d) {
      var on = d.k === domainKey;
      return {
        name: d.name, href: d.items[0].href,
        fw: on ? 600 : 400,
        fg: on ? '#fff' : 'rgba(255,255,255,0.66)',
        bg: on ? 'rgba(255,255,255,0.10)' : 'transparent',
        bc: on ? '#fff' : 'transparent'
      };
    }),
    subItems: NAV.filter(function (d) { return d.k === domainKey; })[0].items.map(function (it) {
      var on = it.k === subKey;
      return {
        name: it.name, href: it.href,
        fw: on ? 600 : 400,
        fg: on ? 'var(--csop-blue-600)' : 'var(--ink-600)',
        bc: on ? 'var(--csop-blue-600)' : 'transparent'
      };
    }),
    presets: R.PRESETS.map(function (p) {
      var on = p.k === range.key;
      return {
        label: p.label, go: function () { go({ rangeKey: p.k }); },
        fw: on ? 600 : 400,
        fg: on ? 'var(--csop-blue-700)' : 'var(--ink-600)',
        bg: on ? 'var(--csop-blue-50)' : '#fff'
      };
    }),
    rangeText: range.text, rangeFrom: range.from, rangeTo: range.to, updated: stamp(R.UPDATED)
  };
}

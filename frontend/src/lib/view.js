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

export function pct1(v) { return (Math.round(v * 10) / 10).toFixed(1) + '%'; }

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
export function typeStyle(k) {
  var t = TYPE_BY_KEY[k] || TYPE_BY_KEY.other, g = TYPE_GROUP[t.group];
  return { k: t.k, label: t.label, bg: g.bg, fg: g.fg, bar: t.bar, def: t.def };
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
    rangeText: range.text, rangeFrom: range.from, rangeTo: range.to, updated: R.UPDATED
  };
}

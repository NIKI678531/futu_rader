/* 六态渲染红线 —— 断言页面上没把 `null` 说成 `0`（PRD §3.6、CLAUDE.md 铁律 2）。
 * 每接一个页面就在 PAGES 里加一项，把它的六态样本一起钉住。
 *
 * 每页外加一条对照断言：**服务连不上不是一种「字段状态」**，它必须长得完全不一样。
 *
 * ## 为什么后端测过了这里还要再测一遍
 *
 * 后端测的是「有没有说实话」：null 原样上线、空集是 empty 不是 unavailable。
 * 这里测的是「页面有没有照实说」。两件事都可能单独出错，而且第二件更容易发生 ——
 * 一句 `value || 0`、一个 `.toFixed(2)`、一处 `Number(x)`，就把「没采到数」变成了
 * 「市场上没人讨论」。对一个给产品团队看的只读工作台来说，这是最坏的一种失效。
 *
 * ## 怎么跑
 *
 * 单独起一套「六态后端 + 指向它的前端」，不动常规的 8008/5173：
 *
 *   backend  DEMO_SCENARIO=sixstate APP_PORT=8018   → fixtures/sixstate/
 *   frontend VITE_API_BASE=…:8018 vite --port 5175
 *
 * 六态样本是手工构造的（backend/fixtures/make_sixstate.py），每条只演一态，全部写死，
 * 不靠随机 —— 红线断言必须每次都断在同一个地方。
 *
 * 用法：npm run six-state
 */
import { spawn, spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import { chromium } from 'playwright'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const FRONTEND = path.resolve(HERE, '..')
const REPO = path.resolve(FRONTEND, '..')

const API_PORT = 8018
const WEB_PORT = 5175
const API = `http://localhost:${API_PORT}`
const WEB = `http://localhost:${WEB_PORT}`

/* ── 断言 ───────────────────────────────────────────────────────────────
 *
 * 断言必须**限定在一张卡片之内**。整页 includes('赞 0') 毫无意义 —— 页面上总有别的卡
 * 真的就是 0；固定长度的窗口也不行，会溢到下一张卡上去误报。所以先划窗口，再在窗口里判断：
 *
 *   marker  按卡片切开（split，默认「原帖 ↗」），取含这句独有摘要的那一张
 *   window  [起, 止] 之间的一段，用于表格行这种没有天然分隔符的地方
 *   都不给  整页
 *
 * 页级 `body` 会先把页头切掉（见 PAGES 里的注释）；个别断言要看的恰恰是页头里的东西，
 * 给它挂 `page: true` 就在完整页面文本上划窗。
 */
const OFFICIAL = [
  {
    name: '① 真零仍然显示 0（反向断言）',
    why: '`0` 是「已取得数据且确实为零」的专用值。矫枉过正把真零也抹掉，等于换一个方向撒谎。',
    marker: '互动为零的帖子',
    want: ['赞 0 · 评 0 · 转 0'],
    reject: ['赞 数据暂不可用'],
  },
  {
    name: '② null 显示「数据暂不可用」，绝不显示 0 —— 本文件的红线',
    why: '铁律 2。这条挂了，整套护栏就等于没有。',
    marker: '平台计数字段未回传',
    want: ['赞 数据暂不可用 · 评 数据暂不可用 · 转 数据暂不可用'],
    reject: ['赞 0', '评 0', '转 0', '赞  · 评'],
  },
  {
    name: '③ 低置信度标「待确认」',
    why: 'PRD §3.6：AI 标注 confidence < 0.7 的，标签照挂但必须标出来没把握。',
    marker: '类型置信度 0.42',
    want: ['待确认 0.42'],
    reject: [],
  },
  {
    name: '④ 置信度达标的不标「待确认」',
    why: '反面断言。见谁都标一遍，这个徽章就没信息量了。',
    marker: '类型置信度 0.96',
    want: ['赞 88 · 评 9 · 转 4'],
    reject: ['待确认 0.96'],
  },
  {
    name: '⑤ 没摘要是「暂无内容」，不是「暂不可用」',
    why: '「查过了确实没有」和「应该有但没取到」是两套文案，不可互换（PRD §3.6）。',
    marker: '暂无摘要 · 图片帖，第一期不覆盖图片内容',
    want: ['赞 5 · 评 0 · 转 0'],
    reject: ['数据暂不可用'],
  },
  {
    name: '⑥ 结构性不适用显示「—」，不是「0 只 · 0 次」',
    why: '这个位置上的 0 会被读成「提了 0 只 ETF」，可字段压根不适用（PRD §3.6 的「—」态）。',
    want: ['— 区间内未提及 ETF'],
    reject: ['0 只 · 0 次'],
  },
  {
    name: '⑦ 长短两套缺失文案没有被顺手统一',
    why: '数值位用长文案「数据暂不可用」（PRD §3.1、§5 delta 契约）；状态图例与短徽章用'
      + '「暂不可用」「暂无内容」（PRD §3.6 逐字）；空态用「暂无相关内容」（PRD §4.1 逐字）。'
      + '三套并存、不可互换。官号页只出现第一套，出现另外两套就说明有人「统一」过了。',
    want: ['数据暂不可用'],
    reject: [],
  },
]

/* KOL 影响力页。发帖记录表的一行以「原帖 ↗」结尾，与官号页的卡片一样能切。
   计数列渲染成「赞 / 评 / 转」三格连排，所以断言里的分隔符是 ` / ` 而不是官号页的 ` · `。 */
const KOL = [
  {
    name: '① 真零仍然显示 0（反向断言）',
    why: '同官号页：真零必须还是 0，否则就是换一个方向撒谎。',
    marker: '互动为零的帖子',
    want: ['0 / 0 / 0'],
    reject: ['数据暂不可用'],
  },
  {
    name: '② null 显示「数据暂不可用」，绝不显示 0',
    why: '铁律 2。',
    marker: '平台计数字段未回传',
    want: ['数据暂不可用 / 数据暂不可用 / 数据暂不可用'],
    reject: ['0 /', '/ 0'],
  },
  {
    name: '③ 方向判不出来标「方向待确认」，不是「持有观望」',
    why: 'PRD §4.3 双标签口径逐字：「判不出方向的操作类帖子标『方向待确认』」。'
      + '把没判出来的说成「持有观望」，是在编造一个模型没给出的结论 —— 而且这个结论'
      + '会被产品团队当成 KOL 的真实态度读。这是六态里「待确认」存在的全部理由。',
    marker: '操作类帖子，方向置信度不足',
    want: ['方向待确认'],
    reject: ['持有观望', '加仓', '减仓', '建仓', '清仓'],
  },
  {
    name: '④ 低置信度标「待确认 0.42」',
    why: 'PRD §3.6：confidence < 0.7 的标签照挂，但必须标出来没把握。',
    marker: '类型置信度 0.42',
    want: ['待确认 0.42'],
    reject: [],
  },
  {
    name: '⑤ 没摘要是「暂无内容」，不是「暂不可用」',
    why: '「查过了确实没有」和「应该有但没取到」是两套文案，不可互换（PRD §3.6）。',
    marker: '暂无摘要 · 图片帖，第一期不覆盖图片内容',
    want: ['5 / 0 / 0'],
    reject: ['数据暂不可用'],
  },
  {
    /* 这一条盯的是**聚合**，不是某个字段的渲染。声量排名那一列由 lib/profile.js 的
       kolProfile 在前端把可见帖子的评论数加起来（ADR-0015：它随筛选重算，不能是端点）。
       `sum += null` 在 JS 里等于 `sum += 0`，于是「有一篇没采到」会被悄悄算成「那篇 0 条」，
       合计照样是个看着很正常的数字 —— 比渲染成 NaN 坏得多，因为根本看不出来。
       窗口取「自家/竞品 篇数」到「详情 →」之间，正好是排名表的那一行。 */
    name: '⑥ 合计里混进 null 要传染成「数据暂不可用」，不能当 0 加进去',
    why: '铁律 2 在聚合层的样子。少算一篇的合计不会报错、不会变形，只是偏小 —— '
      + '这是最难被发现的一种撒谎，也是唯一需要专门断言的一种。',
    window: ['6/0', '详情 →'],
    want: ['数据暂不可用'],
    reject: ['10', '12'],
  },
  {
    /* 这一条与⑥共用一个窗口（排名表的那一行），盯的却是隔壁一格：**主要类型**。
       它由 kolProfile 的 `tc[p.postType]++` 数出来，而 postType 为 null 时那是
       `tc[null]`，`undefined++` 得到 NaN —— 不报错，接着八个真类型的计数全是 0，
       排序后 `order[0]` 稳定落在第一个键上，于是**每一位 KOL 都变成同一种「为主」**。 */
    name: '⑦ 有一篇没标注 → 主要类型是「暂不可用」，不是被 NaN 顶出来的某一类',
    why: '这一格是「我们对这位 KOL 的判断」，不是一个计数。少一票就选不出第一名，'
      + '而 NaN 排序不会告诉你它选不出来 —— 它会非常自信地给你一个答案。'
      + 'DATA_PROVIDER=sql 下每篇 postType 都是 null（ADR-0017），这一列会整列挂上'
      + '我们从没算过的结论。',
    window: ['6/0', '详情 →'],
    want: ['暂不可用'],
    reject: ['行情解读', '晒单', '其他'],
  },
  {
    name: '⑧ 整块标注未生成 → 类型徽章「暂不可用」，不是「其他」，摘要另有一句话',
    why: '「其他」是个**结论**（分过类了，八类里归不进前七类），「暂不可用」是「还没分过类」；'
      + '「暂无摘要 · 图片帖」是「查过了这篇只有图」，「摘要暂不可用」是「还没查」。'
      + '两对里各合并一次，一屏从没被标注过的帖子就会读成一屏已经判完的帖子。'
      + '同时钉住两件事没被带走：三个计数照常显示（标注缺失与计数缺失是两条链路），'
      + '徽章位用的是**短**文案（PRD §3.6），不是数值位的「数据暂不可用」。',
    marker: '摘要暂不可用 · AI 标注尚未生成',
    want: ['暂不可用', '9 / 2 / 1'],
    reject: ['其他', '待确认', '数据暂不可用', '暂无摘要 · 图片帖，第一期不覆盖图片内容'],
  },
  {
    /* 抽屉是另一套模板（表格行只有摘要一格，抽屉里摘要、判定依据、原文分三处说），
       所以不能靠上面那条顺带覆盖 —— 与⑥／KOL 详情那对「两处实现、同一个坑」同理。
       窗口取抽屉内部：「帖子内容卡」是抽屉标题，「提及产品」是它下一块，全页各仅一处。 */
    name: '⑨ 抽屉：判定依据不许指向一句不存在的高亮',
    why: '`evidenceIdx >= 0` 在 JS 里对 null 是 **true**（null 被当成 0），于是'
      + '「判定依据的原句已在下方原文中标出」会挂在一篇根本没标注过的帖子上 —— '
      + '而紧挨着的原文面板同时在说「正文分句尚未生成」。两句话当场自相矛盾，'
      + '看的人只会以为是页面坏了，然后接着相信上面那个类型标签。',
    window: ['帖子内容卡', '提及产品'],
    want: [
      '摘要暂不可用：AI 标注尚未生成。',
      '类型「暂不可用」置信度 数据暂不可用 · 判定依据尚未生成',
      '原文暂不可用：正文分句尚未生成。',
    ],
    reject: ['判定依据的原句已在下方原文中标出', '暂无摘要：该帖仅含图片或截图，第一期不覆盖图片内容。', '待确认'],
  },
]

/* KOL 详情页。同一份六态样本（fixtures/sixstate/kol_impact.json）在这一页换了个渲染模板：
   计数列写成「赞/评/转」三格连排、无空格，「待确认」徽章不带数字。所以断言不能照抄上面
   那一组 —— 抄过来会变成「摘要里正好有这几个字」的假绿。
   多演一态：头部的**互动合计**。它是 5 篇的和，其中一篇取不到，合计就得是「数据暂不可用」。 */
const KOL_DETAIL = [
  {
    name: '① 真零仍然显示 0（反向断言）',
    why: '真零必须还是 0，否则就是换一个方向撒谎。',
    marker: '互动为零的帖子',
    want: ['0/0/0'],
    reject: ['数据暂不可用'],
  },
  {
    name: '② null 显示「数据暂不可用」，绝不显示 0',
    why: '铁律 2。',
    marker: '平台计数字段未回传',
    want: ['数据暂不可用/数据暂不可用/数据暂不可用'],
    reject: ['0/', '/0'],
  },
  {
    name: '③ 方向判不出来标「方向待确认」，不是「持有观望」',
    why: 'PRD §4.3 双标签口径：把没判出来的说成「持有观望」，是在编造一个模型没给出的结论。',
    marker: '操作类帖子，方向置信度不足',
    want: ['方向待确认'],
    reject: ['持有观望', '加仓', '减仓', '建仓', '清仓'],
  },
  {
    name: '④ 低置信度标「待确认」',
    why: 'PRD §3.6：confidence < 0.7 的标签照挂，但必须标出来没把握。',
    marker: '类型置信度 0.42',
    want: ['待确认'],
    reject: [],
  },
  {
    name: '⑤ 没摘要是「暂无内容」，不是「暂不可用」',
    why: '「查过了确实没有」和「应该有但没取到」是两套文案，不可互换（PRD §3.6）。',
    marker: '暂无摘要 · 图片帖，第一期不覆盖图片内容',
    want: ['5/0/0'],
    reject: ['数据暂不可用', '待确认'],
  },
  {
    /* 这一条盯的是头部 KPI 的**合计**，走的是屏内那段 add()（KolDetail.jsx），
       与 KOL 影响力页那条盯的 lib/profile.js 是两处实现、同一个坑。
       reject 里的数字是「把 null 当 0 加」会得到的那几个：赞 0+31+12+5+9=57、
       互动 0+40+16+5+12=73。它们看上去完全正常，这正是这条断言存在的理由。 */
    name: '⑥ 头部互动合计里混进 null 要传染成「数据暂不可用」',
    why: '铁律 2 在聚合层的样子。少算一篇的合计不报错、不变形，只是偏小 —— 最难被发现。',
    page: true,
    window: ['互动合计', '这段时间发的帖'],
    want: ['数据暂不可用', '赞 数据暂不可用 · 评 数据暂不可用 · 转 数据暂不可用'],
    reject: ['赞 0', '57', '73'],
  },
  {
    name: '⑦ 其他产品观点表：真零是 0',
    why: '这张表只有互动量一个数值位，两条断言分别钉住它的两种含义：'
      + '全填 0 会被 ⑧ 抓住，全填「数据暂不可用」会被这一条抓住。',
    marker: '互动量确实是 0',
    // 制表符是 innerText 序列化表格单元格的分隔符。带上它才能把「互动量这一格是 0」
    // 与同一行里的 9998、2026、14:08 这些含 0 的字串分开。
    want: ['\t0\t'],
    reject: ['数据暂不可用'],
  },
  {
    name: '⑧ 其他产品观点表：null 是「数据暂不可用」',
    why: '同一列、同一个渲染分支，正反两面都得钉住，否则「全填 0」和「全填暂不可用」都能过。',
    marker: '互动量未回传',
    want: ['数据暂不可用'],
    reject: [],
  },
  {
    /* 这一行是页面配的 open 点开的那一篇（openUnannotated），所以窗口里有原文面板。 */
    name: '⑨ 整块标注未生成：类型、摘要、原文、判定依据四处各说各的',
    why: '这四处在 DATA_PROVIDER=sql 下**同时**缺失，却各有各的正确说法，'
      + '而它们原来的写法各自会编出一句话来：`typeStyle(null)` → 「其他」，'
      + '`null < 0.7` → 「待确认」，`!hasSummary` → 「图片帖」，`null.map` 直接抛 —— '
      + '最后一处还算幸运，前三处都不报错，只是把一篇没人看过的帖子说得有鼻子有眼。'
      + '一条断言盯四处，是因为它们必须**一起**改口：留下任意一处，'
      + '页面上就会出现「类型：其他」配「原文暂不可用」这种自相矛盾的组合。',
    marker: '摘要暂不可用 · AI 标注尚未生成',
    want: ['暂不可用', '9/2/1', '原文暂不可用：正文分句尚未生成。', '类型置信度 数据暂不可用 · 判定依据尚未生成'],
    reject: ['其他', '待确认', '高亮句为判定依据', '暂无摘要 · 图片帖，第一期不覆盖图片内容'],
  },
  {
    name: '⑩ 有一篇没标注 → 类型构成整块「暂不可用」，不是一张全 0 的构成图',
    why: '八类里少一票，没有任何一格是可信的。画成全 0 的构成图，长得和'
      + '「这段时间他真没发过这几类」一模一样 —— 而后者是个结论。'
      + '这里同时钉住小字跟着改口：留着「全部类型置信度达标」，'
      + '等于替一批从没跑过的标注宣布了质检通过。',
    window: ['帖子类型构成', '其他产品观点及操作'],
    want: ['类型标注尚未生成', '暂不可用', '帖子类型标注尚未生成，构成比例无法计算'],
    reject: ['全部类型置信度达标', '行情解读', '晒单', '0%'],
  },
  {
    /* 整页断言（没有 window）：「AI 画像」全页仅此一处，就是那枚徽章自己。 */
    name: '⑪ 主要类型未知时不挂「AI 画像」徽章 —— 那是个结论，不是一个缺省值',
    why: '徽章里写的「X为主 · 兼Y」是 PRD §4.4 M3 的逐字文案，也是这一页对一位真人'
      + '最强的一句断言。它由 `styleTag` 来，而 `styleTag` 与 typeCounts 同生共死：'
      + '分布不可信的时候它也不可信。挂一枚写着「晒单为主」的徽章在一位从没被标注过的'
      + 'KOL 头上，比整页空着糟得多 —— 空着看得出没数据，徽章看不出。',
    page: true,
    want: ['画像由区间内类型分布自动生成'],
    reject: ['AI 画像'],
  },
]

/* 板块总览。这一页的六态几乎全在顶部两张卡上 —— 它们是**聚合位**：
   下面榜单里每一格都对着一只产品，错了还能一眼看出来；合计错了看不出来，它只是偏小。
   窗口都取两张卡内部的相邻标签之间，避免溢到隔壁格上去。 */
const SECTOR = [
  {
    name: '① 合计里混进 null 要传染成「数据暂不可用」，不能当 0 加进去',
    why: '铁律 2 在聚合层的样子。样本池里有一只产品的讨论热度取不到，合计就是未知；'
      + '把它当 0 加进去会得到一个看着完全正常、只是偏小的数 —— 这是最难被发现的一种撒谎。',
    window: ['CSOP产品讨论热度', '仅统计 CSOP 自家'],
    want: ['数据暂不可用', '暂不可用'],
    reject: ['0'],
  },
  {
    name: '② 真零仍然显示 0，环比是结构性的「—」（反向断言）',
    why: '负面舆情确实一条都没有 —— 这个 0 是数出来的，不能被隔壁那个 null 带成'
      + '「数据暂不可用」。当期与基准期都是 0 时环比无从谈起，是「—」而不是「+0%」。',
    window: ['CSOP 自家产品', '负面舆情'],
    want: ['0', '—'],
    reject: ['数据暂不可用', '暂不可用'],
  },
  {
    name: '③ 数值位与环比位各说各的：值是确切的，环比取不到',
    why: '正面好评的条数是数出来的，它的基准期没取到。两个位置必须能各自表态 —— '
      + '一个显示数字、一个显示「暂不可用」；任何一边把另一边带走都是错的。'
      + '顺带钉住两套文案没被统一：环比位这里是**短徽章**「暂不可用」，不是长文案。',
    window: ['负面舆情', '正面好评'],
    want: ['暂不可用'],
    reject: ['数据暂不可用'],
  },
  {
    name: '④ 合规关注条数取不到 → 长文案，不是「0 条」',
    why: '有一只自家产品没做过合规扫描。设计源那句 `|| 0` 把「没扫过」读成「零条」，'
      + '于是这行小字言之凿凿地少数了一只（screen-diff 白名单里唯一一条豁免说的就是它）。'
      + '「N 条」整体换成长文案，量词得跟着数字走，不能留下「需合规关注 数据暂不可用 条」。',
    window: ['正面好评', '近期热议飙升的CSOP产品'],
    want: ['其中需合规关注 数据暂不可用'],
    reject: ['其中需合规关注 0 条'],
  },
  {
    name: '⑤ 「数据截至」取不到 → 长文案，不是一片空白',
    why: '这一位原来是 `fixtures/meta.json` 里的手写常量，永远不缺，于是页面从来没演过'
      + '它缺的样子。改成随主数据下发之后（那才是它的本相：这批数据最后一条帖子的时间），'
      + '库里没锚点它就会缺 —— 而 `{undefined}` 在 React 里渲染成**空**，页面上只剩'
      + '「最近更新」四个字加一片空白，看着像样式没对齐，没人会去查数据源。'
      + '这比渲染成 0 还难发现：0 至少还是个能被质疑的数字。',
    window: ['最近更新', 'CSOP产品讨论热度'],
    want: ['数据暂不可用'],
    reject: ['HKT'],
  },
]

/* 打开当前舆情总结的原文证据侧栏。等待的对象必须是**只有打开后才有**的字：点了个没
   反应的元素同样会得到一屏没有红线违规的文本，那是假绿（screen-diff 上踩过两次）。
   两个页面等的不是同一句 —— 有证据的那页等证据行的「展开单帖详情」，空态那页等空态
   本身，它们恰恰互斥。 */
const openEvidence = (settled) => async (page) => {
  await page.getByText(/^查看原文证据 \d+ 条 →$/).first().click()
  await page.getByText(settled, { exact: false }).first().waitFor({ timeout: 30_000 })
}

/* 展开「整块 AI 标注未生成」的那一篇。两页各有一套自己的渲染 —— KOL 影响力是右侧抽屉，
   KOL 详情是行内展开 —— 同一个坑要各钉一次。等待的对象同样必须是**只有展开后才有**的
   字（理由见 openEvidence）：点了个没反应的元素会得到一屏没有红线违规的文本，那是假绿。
   两页等的是同一句，因为两处缺失态文案本来就该一致。 */
const openUnannotated = async (page) => {
  await page.getByText('摘要暂不可用 · AI 标注尚未生成').first().click()
  await page.getByText('原文暂不可用：正文分句尚未生成。').first().waitFor({ timeout: 30_000 })
}

/* 产品监控 · 账号口径未核验的产品（?code= 直接定位，不靠点击）。 */
const PRODUCT_ACCOUNTS = [
  {
    name: '① 活跃账号数取不到 → 「数据暂不可用」，且说清楚为什么',
    why: 'PRD §5 点名了这个字段：账号口径没核验过的产品，这一格不能显示 0。'
      + '0 会被读成「区间内没有账号讨论过它」，而实际是我们还没算这只产品的账号数。'
      + '注脚也要跟着换 —— 留着「区间内发布或评论过的独立账号」等于给一个不存在的数配了口径说明。',
    window: ['活跃账号数', '全市场评论量排名'],
    want: ['数据暂不可用', '暂不可用', '该产品的账号口径尚未核验'],
    reject: ['0', '区间内发布或评论过的独立账号'],
  },
  {
    name: '② 隔壁字段没被带走（反向断言）',
    why: '一个字段取不到不代表整张卡片都不可信。讨论热度那一格必须照常显示数字 —— '
      + '整条 KPI 带一片「暂不可用」，看的人会以为这只产品整体没采到数。',
    window: ['区间内被识别为讨论该 ETF 的评论条数', '活跃账号数'],
    want: ['讨论热度'],
    reject: ['数据暂不可用'],
  },
  {
    name: '③ KOL 提及整张表取不到 → 「数据暂不可用」＋说清楚为什么',
    why: 'KOL 身份名单／账号映射还没核验，这张表查不了。它与「查过了，名单里没人提过它」'
      + '（3442 那页演的 empty 态）在响应里都是空列表，只有 status 分得开；页面上必须是'
      + '两句不同的话。渲染成空表或「暂无相关内容」，就等于替我们宣布了一个没查过的结论。',
    window: ['已识别 KOL 范围', '积极 ／ 消极整体态度'],
    want: ['数据暂不可用 — KOL 身份名单或账号映射尚未核验。'],
    reject: ['暂无相关内容 — 当前区间未发现已识别 KOL'],
  },
  {
    name: '④ 没有行情源 → 徽章 + 脚注都改口，K 线不画',
    why: '这一格是铁律 2 在整个项目里代价最高的落点：K 线的 OHLC 是 null，补成 0 就会画出'
      + '一根掉到零的实体柱 —— 一只 68 港元的 ETF 在图上崩盘再弹回来。它不报错、不缺格，'
      + '只是一张看着很有信息量的错图，而且是会被截图发出去的那种。'
      + '脚注也必须跟着换：留着「行情粒度为日 K、价格单位 HKD」等于给一条不存在的价格线'
      + '配了单位说明；而「不以指数或其他产品价格替代」这句是在明说我们没有拿别的价格顶上。',
    /* 右边界取下一块面板的标题。趋势面板是整页最长的一段文字，不划窗的话
       reject 会撞上页面别处（KPI 带里就有一枚「数据暂不可用」）。 */
    window: ['实线为当前区间', '热度变化与阶段观点'],
    want: ['价格数据暂不可用', '当前产品价格数据暂不可用，不以指数或其他产品价格替代'],
    reject: ['价格 HKD', '非交易时段与休市日不补造 K 线'],
  },
  {
    name: '⑤ 阶段观点尚未生成 → 「数据暂不可用」，不是「没有讨论」',
    why: '热度是采集来的，阶段观点是 AI 归纳的，两件事分开失败。折线照常画、色带画不出来，'
      + '这一态说的是「还没跑」；说成「区间内该 ETF 没有讨论」就是替一只真有人在聊的产品'
      + '宣布了没人聊 —— 而它的热度折线明明就在同一块面板里有高度。',
    window: ['热度变化与阶段观点', '产品话题情绪'],
    want: ['数据暂不可用 — 阶段观点尚未生成，热度序列与分时段观点会在下一批次采集后输出。'],
    reject: ['暂无相关内容 — 当前日期范围内该 ETF 没有讨论，不输出阶段观点。', '样本不足'],
  },
]

/* 产品监控 · 态度样本全零的产品。这一只同时演三态：真零、样本不足、结构性「—」。 */
const PRODUCT_ATTITUDE = [
  {
    name: '① 真零：积极与消极都确实是 0',
    why: '数过了确实没有，与「没采到」是两回事。这一格必须还是 0，'
      + '否则就是朝反方向撒谎 —— 把一个确切的结论说成不知道。',
    window: ['积极 ／ 赞', '消极 ／ 踩'],
    want: ['0'],
    reject: ['数据暂不可用', '暂不可用'],
  },
  {
    name: '② 赞踩比分母为 0 → 占比是「—」，不是「0.0%」',
    why: 'PRD §3.6 的「—」态：字段结构性不适用。0÷0 算不出占比，'
      + '写「0.0%」等于宣称「积极占 0%」，那是一个我们并没有得出的结论。',
    window: ['积极 ／ 赞', '消极 ／ 踩'],
    want: ['—'],
    reject: ['0.0%'],
  },
  {
    name: '③ 样本不足是一句结论，不是一个零',
    why: 'PRD §3.5：有效样本低于阈值就不输出倾向结论。这一页必须把这句话说出来，'
      + '而不是让人对着一排 0 自己揣摩「是没人讨论，还是我们不敢下结论」。',
    want: ['样本不足 · 不输出倾向结论', '样本不足 · 低于', '条阈值，不输出倾向结论'],
    reject: [],
  },
  {
    name: '④ KOL 提及查过了确实没有 → 「暂无相关内容」，不是「数据暂不可用」',
    why: '与 3469 那页的 unavailable 态是**同样的空列表**，页面上却必须是两句不同的话：'
      + '一句是「查过了，名单里没人提过它」，一句是「这张表我们查不了」。合并成一句，'
      + '产品团队就分不出「这只 ETF 没有 KOL 讨论」和「我们还不知道有没有」。',
    window: ['已识别 KOL 范围', '积极 ／ 消极整体态度'],
    want: ['暂无相关内容 — 当前区间未发现已识别 KOL 在相关评论区提及该产品。'],
    reject: ['KOL 身份名单或账号映射尚未核验'],
  },
  {
    /* 这一条要求侧栏是打开的（页面配了 open），所以它读的是抽屉里的字。 */
    name: '⑤ 证据为空数组 → 空态「暂无相关内容」，不是一张空卡片',
    why: '`[]` 的意思是「查过了，这个筛选范围内没有可展示的原文」。渲染成空白抽屉，'
      + '看的人会以为是没加载出来接着刷新；渲染成「数据暂不可用」，则是把一个确切的'
      + '结论说成不知道。空态那句话是这里唯一诚实的说法（PRD §4.1 逐字）。',
    /* 侧栏是整页最后一段，后面没有可以当右边界的字，所以这条走整页，靠三句话自己收紧：
       「0 条结果」证明它数过了（closed 态压根不渲染这枚徽章），空态那句是 PRD §4.1 逐字，
       整页找不到「展开单帖详情」证明一张卡都没渲染。 */
    want: ['0 条结果', '暂无相关内容 — 当前筛选范围内没有可展示的原文证据。'],
    reject: ['展开单帖详情'],
  },
  {
    name: '⑥ 阶段样本不足 → 说「样本不足」，既不是「暂不可用」也不是「没有讨论」',
    why: '同一个 `sentiment: null` 在这块面板上有三种说法，这一条钉的是选对了哪一种。'
      + '「暂不可用」是阶段观点没跑出来（3469 那页演的），「没有讨论」是区间内真没人聊，'
      + '而这一段是**跑过了、有讨论、只是不够下结论**（PRD §3.5）。合并任意两种，'
      + '产品团队就分不出「我们不知道」和「我们知道但不说」。'
      + '这一态还必须**照常渲染阶段行**：整段藏起来等于把一段真实存在的讨论从时间轴上抹掉。',
    window: ['热度变化与阶段观点', '产品话题情绪'],
    want: ['样本不足', '样本不足，暂无主流观点'],
    reject: ['暂不可用', '暂无相关内容'],
  },
]

/* 产品监控 · 原文证据与 KOL 主要态度。这一页的六态全在两处：产品相关 KOL 表的「主要
   态度」列（<3 条不输出），和打开的证据侧栏里每张卡的计数位。 */
const PRODUCT_EVIDENCE = [
  {
    name: '① 主要态度样本足 → 照常给结论（反向断言）',
    why: '没有这一条，「整列都填暂不可用」也能过。红线是双向的：该说的时候要说。',
    split: ' 条 →',
    marker: '有效提及五条以上',
    want: ['积极'],
    reject: ['暂不可用', '样本不足'],
  },
  {
    name: '② 主要态度 < 3 条 → 「暂不可用」，不是 0、不是空白、不是随手挑一个方向',
    why: 'PRD §5 表 P8「主要态度（<3 条不输出）」＋ §3.6：字段级 null 一律是「暂不可用」。'
      + '少数几条帖子推不出一个人的倾向，硬填一个极性就是替这位 KOL 说了句他没说过的话。'
      + '这里同时钉住长短两套文案没被顺手统一 —— 这一格是**短徽章**「暂不可用」，'
      + '不是数值位的长文案「数据暂不可用」。',
    split: ' 条 →',
    marker: '有效提及不足三条',
    want: ['暂不可用'],
    reject: ['数据暂不可用', '样本不足', '积极', '消极', '中性'],
  },
  {
    name: '③ 证据卡：真零仍然显示 0（反向断言）',
    why: '这条原帖确实没人评论没人互动。矫枉过正把真零也抹掉，等于换个方向撒谎。',
    split: '展开单帖详情',
    marker: '互动为零的原帖',
    want: ['评论 0', '互动 0'],
    reject: ['暂不可用'],
  },
  {
    name: '④ 证据卡：计数取不到 → 短徽章「暂不可用」，绝不是 0',
    why: '铁律 2 在证据侧栏的落点。这两格是页面上离原帖最近的数字，填 0 会被直接读成'
      + '「这条帖子没人理」——而它可能是这个区间里最热的一条。',
    split: '展开单帖详情',
    marker: '平台计数字段未回传',
    want: ['评论 暂不可用', '互动 暂不可用'],
    reject: ['评论 0', '互动 0', '数据暂不可用'],
  },
  {
    name: '⑤ 同一张卡上长短两套文案并存，没有被顺手统一',
    why: '作者名取不到时渲染的是长文案「数据暂不可用」，而隔壁计数位是短徽章「暂不可用」'
      + '（PRD §3.6 与 §3.1／§5 delta 契约，两套不可互换）。把它们统一成一句是很自然的'
      + '「整理」，而那会让页面上再也分不出这两个位置的语气差别。',
    split: '展开单帖详情',
    marker: '作者名未回传',
    want: ['数据暂不可用', '评论 7', '互动 19'],
    reject: [],
  },
  {
    name: '⑥ 有行情源的产品照常画 K 线（反向断言）',
    why: '红线是双向的。没有这一条，「所有产品都挂价格数据暂不可用」也能让 3469 那页全绿 ——'
      + '而那是把整个产品线的价格轨道一起关掉。这里同时钉住脚注跟着状态换：'
      + 'ok 态说的是「非交易时段与休市日不补造 K 线」（周末那两根空 K 是**对的**），'
      + '不是「不以指数或其他产品价格替代」（那是在解释我们为什么一根都画不出来）。',
    window: ['实线为当前区间', '热度变化与阶段观点'],
    want: ['价格 HKD', '行情粒度为日 K、价格单位 HKD，非交易时段与休市日不补造 K 线'],
    reject: ['价格数据暂不可用', '不以指数或其他产品价格替代'],
  },
]

const PAGES = [
  {
    label: '板块总览',
    path: '/sector',
    cases: SECTOR,
    offline: ['数据暂不可用', '暂无相关内容', 'CSOP产品讨论热度', '全市场活跃 ETF'],
  },
  {
    /* 两条 /product 用查询串直接定位到样本产品，不靠点击 —— 点击要先等候选列表渲染完，
       而候选列表本身就是被断言的东西之一，用它当前置条件会把失败原因搅在一起。 */
    label: '产品监控 · 账号口径未核验',
    path: '/product?code=3469',
    cases: PRODUCT_ACCOUNTS,
    offline: ['数据暂不可用', '暂无相关内容', '全市场评论量排名', '讨论热度'],
  },
  {
    label: '产品监控 · 态度样本全零',
    path: '/product?code=3442',
    open: openEvidence('暂无相关内容 — 当前筛选范围内没有可展示的原文证据。'),
    cases: PRODUCT_ATTITUDE,
    offline: ['数据暂不可用', '暂无相关内容', '全市场评论量排名', '讨论热度'],
  },
  {
    label: '产品监控 · KOL 提及与原文证据',
    path: '/product?code=3033',
    open: openEvidence('展开单帖详情'),
    cases: PRODUCT_EVIDENCE,
    offline: ['数据暂不可用', '暂无相关内容', '全市场评论量排名', '讨论热度'],
  },
  {
    label: '官号动态',
    path: '/official',
    cases: OFFICIAL,
    /* 屏级错误时页面上不该再有的东西：这几句各自代表「某处渲染出了真数据」。 */
    offline: ['数据暂不可用', '暂无相关内容', '原帖 ↗', '提及 ETF'],
  },
  {
    label: 'KOL 影响力',
    path: '/kol',
    open: openUnannotated,
    cases: KOL,
    /* 从表头之后开始看。发帖记录是表格，第一行没有自己的起始分隔符，按「原帖 ↗」切开时
       会连着整个页头一起成为第一段 —— 而页头的 KPI 那句就写着「加仓 0 · 减仓 0 ·
       方向待确认 1」，行级的反向断言会当场被页头的字绊倒（而且是假绿／假红都可能）。
       「赞 / 评 / 转」是发帖记录的表头，全页仅此一处（排名表写「评论量」，抽屉写「点赞」）。 */
    body: '赞 / 评 / 转',
    offline: ['数据暂不可用', '暂无相关内容', '原帖 ↗', '详情 →'],
  },
  {
    label: 'KOL 详情',
    path: '/kol/detail',
    open: openUnannotated,
    cases: KOL_DETAIL,
    /* 「时间 · ETF」是发帖记录的表头，全页仅此一处。理由同 KOL 影响力页：不切掉页头，
       第一行的反向断言会撞上头部 KPI 里那句「赞 数据暂不可用 · 评 …」。
       ⑥ 要看的正是被切掉的那段，所以它带 page:true 走整页。 */
    body: '时间 · ETF',
    offline: ['数据暂不可用', '暂无相关内容', '原帖 ↗', '导出 CSV'],
  },
]

async function main() {
  const procs = []
  let browser
  const fail = []

  try {
    procs.push(await boot('六态后端', API, python(), [path.join(REPO, 'backend', 'app.py')], {
      cwd: path.join(REPO, 'backend'),
      env: { DEMO_SCENARIO: 'sixstate', APP_PORT: String(API_PORT), APP_ENV: 'production' },
    }))
    procs.push(await boot('六态前端', WEB, npm(), ['run', 'dev', '--', '--port', String(WEB_PORT), '--strictPort'], {
      cwd: FRONTEND,
      env: { VITE_API_BASE: `${API}/api/v1` },
    }))

    browser = await chromium.launch()
    let total = 0
    for (const pg of PAGES) {
      await checkPage(browser, pg, fail)
      await screenLevelError(browser, pg, fail)
      total += pg.cases.length + 1
    }

    report(fail, total)
  } finally {
    if (browser) await browser.close()
    for (const p of procs) if (p) kill(p)
  }
  process.exit(fail.length ? 1 : 0)
}

async function checkPage(browser, pg, fail) {
  const page = await browser.newPage({ viewport: { width: 1600, height: 1400 } })
  const errors = []
  page.on('pageerror', (e) => errors.push(String(e)))
  try {
    await page.goto(WEB + pg.path, { waitUntil: 'networkidle', timeout: 60_000 })
    /* 展开动作要在稳定等待**之前**做：抽屉里的内容是点开之后才请求的，先等 1.5 秒再点，
       等的是上一屏。screenLevelError 不跑 open —— 接口全被 abort 掉时没有东西可展开。 */
    if (pg.open) await pg.open(page)
    await page.waitForTimeout(1500)

    const text = await page.evaluate(() => document.body.innerText)
    for (const e of errors) fail.push({ name: `${pg.label} · JS 报错`, detail: e })

    if (text.includes('后端服务连不上')) {
      fail.push({ name: `${pg.label} · 页面根本没渲染`, detail: '出现了屏级错误条，六态后端没连上' })
      return
    }

    let body = text
    if (pg.body) {
      const at = text.indexOf(pg.body)
      if (at < 0) {
        fail.push({ name: `${pg.label} · 找不到正文起点`, detail: `页面上没有「${pg.body}」`, win: text })
        return
      }
      body = text.slice(at + pg.body.length)
    }

    for (const c of pg.cases) {
      const name = `${pg.label} ${c.name}`
      const win = windowOf(c.page ? text : body, c)
      if (win == null) {
        const how = c.marker ? `定位标记「${c.marker}」` : `窗口「${c.window.join('」…「')}」`
        fail.push({ name, detail: `找不到${how}——六态样本没渲染出来`, why: c.why })
        continue
      }
      for (const w of c.want) {
        if (!win.includes(w)) fail.push({ name, detail: `期望出现「${w}」，实际没有`, why: c.why, win: excerpt(win, c.marker) })
      }
      for (const r of c.reject) {
        if (win.includes(r)) fail.push({ name, detail: `不该出现「${r}」，但它出现了`, why: c.why, win: excerpt(win, r) })
      }
    }
  } finally {
    await page.close()
  }
}

/* 报告里贴命中处的上下文，不是窗口开头。窗口可以很长（第一段还粘着整个页头），
   命中在第 900 个字符时贴前 200 字等于什么都没说，而这正是最需要看清现场的时候。 */
function excerpt(win, needle) {
  const i = needle ? win.indexOf(needle) : -1
  if (i < 0) return win.slice(0, 200)
  return win.slice(Math.max(0, i - 90), i + needle.length + 90)
}

/* 三种划窗方式，见 OFFICIAL 上方的注释。找不到窗口返回 null（与「窗口是空串」区分开）。 */
function windowOf(text, c) {
  if (c.marker) {
    /* 发帖卡／表格行都以「原帖 ↗」结尾，按它切开就是一张一段。 */
    const cards = text.split(c.split || '原帖 ↗')
    return cards.find((card) => card.includes(c.marker)) ?? null
  }
  if (c.window) {
    const [from, to] = c.window
    const a = text.indexOf(from)
    if (a < 0) return null
    const b = text.indexOf(to, a + from.length)
    return b < 0 ? null : text.slice(a + from.length, b)
  }
  return text
}

/* 后端连不上 → 屏级错误条，而不是一屏 0 或一屏「暂不可用」。
 *
 * 这是上面七条的**对照组**。六态说的是「这个字段怎么了」，前提是接口答了；接口压根没答
 * 的时候，页面上一个数字都不能信，只能整屏说清楚。两者在 UI 上必须是两种东西：把断网
 * 渲染成满屏「数据暂不可用」，市场团队会以为是数据源缺字段，接着照常看那些还在的数字。
 *
 * 不停后端来测 —— 那会连带影响别的断言，也没法在 CI 上稳定复现。改成在浏览器层把
 * 所有 /api/v1/** 请求 abort 掉：对前端来说与服务器不响应完全一样，且只影响这一个 page。
 */
async function screenLevelError(browser, pg, fail) {
  const name = `${pg.label} ⓧ 后端连不上是屏级错误，不是字段级「暂不可用」`
  const why = '接口没响应时页面上的数字一个都不能信。渲染成一屏 0 或一屏「暂不可用」，'
    + '会被读成「采到了数据，只是缺几个字段」，那是最坏的一种误导（spec D6、工单 05）。'

  const page = await browser.newPage({ viewport: { width: 1600, height: 1400 } })
  try {
    await page.route('**/api/v1/**', (route) => route.abort())
    await page.goto(WEB + pg.path, { waitUntil: 'domcontentloaded', timeout: 60_000 })
    await page.waitForTimeout(1500)

    /* 刨掉错误条自己再看剩下的：错误条的文案里就引用了「数据暂不可用」（它正是在说
       「这不是数据暂不可用」），拿整页文本判就会被自己的话绊倒。 */
    const [bar, rest] = await page.evaluate(() => {
      const body = document.body.innerText
      const el = document.querySelector('[data-screen-error]')
      /* 字符串相减，不是把节点摘掉再取文本：innerText 依赖排版，游离节点上会退化成
         textContent，空白与换行全变样，reject 里的「原帖 ↗」这类带空格的串就匹配不上了。 */
      return el ? [el.innerText, body.replace(el.innerText, '')] : ['', body]
    })

    if (!bar.includes('后端服务连不上')) {
      fail.push({ name, detail: '没有屏级错误条', why, win: bar || rest })
      return
    }
    /* 错误条出来了还渲染出发帖卡／表头，说明半屏是真数据半屏是错误提示，更糟。 */
    for (const r of pg.offline) {
      if (rest.includes(r)) fail.push({ name, detail: `屏级错误时不该出现「${r}」，但它出现了`, why, win: rest })
    }
  } finally {
    await page.close()
  }
}

function report(fail, total) {
  if (!fail.length) {
    console.log(`\x1b[32m✓\x1b[0m 六态渲染红线 ${total} 条全过 —— 页面上没有把 null 说成 0`)
    return
  }
  console.log(`\x1b[31m✗\x1b[0m 六态渲染红线 ${fail.length} 条不过\n`)
  for (const f of fail) {
    console.log(`  \x1b[31m${f.name}\x1b[0m`)
    console.log(`    ${f.detail}`)
    if (f.why) console.log(`    为什么在意：${f.why}`)
    if (f.win) console.log(`    实际渲染：${JSON.stringify(f.win)}`)
    console.log()
  }
}

/* ── 进程 ───────────────────────────────────────────────────────────── */

function python() {
  return path.join(REPO, 'backend', '.venv', 'Scripts', process.platform === 'win32' ? 'python.exe' : 'python')
}
function npm() {
  return process.platform === 'win32' ? 'npm.cmd' : 'npm'
}

async function boot(label, origin, cmd, args, { cwd, env }) {
  if (await alive(origin)) {
    throw new Error(`${origin} 已经被别的进程占着（${label} 需要独占它，端口是特意跟常规站点岔开的）`)
  }
  const child = spawn(cmd, args, {
    cwd,
    env: { ...process.env, ...env },
    stdio: 'ignore',
    shell: process.platform === 'win32',
  })
  for (let i = 0; i < 160; i++) {
    await new Promise((r) => setTimeout(r, 500))
    if (await alive(origin)) return child
  }
  kill(child)
  throw new Error(`${label} 起不来：${origin}`)
}

async function alive(origin) {
  try {
    await fetch(origin, { signal: AbortSignal.timeout(1500) })
    return true
  } catch {
    return false
  }
}

/* Windows 上 shell:true 起的是一层 cmd，kill 父进程留下孤儿会占住端口，下次直接起不来。
   必须用 spawnSync：异步 kill 会被紧随其后的 process.exit() 抢在前面，进程活得好好的。 */
function kill(child) {
  if (process.platform === 'win32') {
    spawnSync('taskkill', ['/pid', String(child.pid), '/f', '/t'], { stdio: 'ignore' })
  } else {
    child.kill()
  }
}

await main()

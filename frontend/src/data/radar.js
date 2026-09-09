/* 数据门面 —— 屏幕唯一的取数入口。
 *
 * ## 现在处于混合态（expand–contract 的 expand 一步）
 *
 * 把 window.RADAR 一次性换成 API，五个屏幕会同时变红，没有一张工单能单独落绿。所以：
 *
 *   1. 门面先变成混合态：已迁移的契约函数走后端，其余仍读设计源镜像（← 现在在这里）
 *   2. 按工单逐组迁移
 *   3. 全部迁完后删掉镜像 import（工单 12 的 contract 一步）
 *
 * ## 铁律
 *
 * - **函数签名一个都不变。** 屏幕里的调用点原封不动，改的只是这些函数背后从哪儿取数。
 * - **同步语义不变。** read() 未命中会抛 Promise，由 ScreenBoundary 的 Suspense 接住；
 *   屏幕的 renderVals() 不需要变成 async（ADR-0005）。
 * - **不做任何默认值兜底。** 没有 `?? 0`、`|| 0`、`|| []`：后端的 null 必须原样穿到
 *   渲染层去触发「暂不可用」，在这一层抹平就是撒谎（铁律 2）。
 *   下面出现的 `key || 'd7'` 是**参数默认值**，与设计源 `rangeKey || DEFAULT_KEY` 逐字
 *   一致，改的是「你没告诉我要哪段区间」，不是「后端没给我值」—— 两回事。
 */
import '../../../design/radar-data.js'
import { read, qs } from '../lib/api'

const MIRROR = window.RADAR

if (!MIRROR) {
  throw new Error('radar-data.js did not define window.RADAR')
}

/* 设计源的 DEFAULT_KEY。屏幕不传区间时用它。 */
const DEFAULT_RANGE = 'd7'

/* ── 已迁移到后端的契约函数（PRD §5） ────────────────────────────────
   覆盖镜像上的同名实现。签名与设计源逐字一致。 */
const migrated = {
  /* buildRange(key) → GET /ranges/{key}
     区间、时间桶、基准区间、各处文案全部后端下发。PRD §5 逐字：前端不自行算桶。 */
  buildRange(key) {
    return read(`/ranges/${encodeURIComponent(key || DEFAULT_RANGE)}`)
  },

  /* officialPosts(range) → GET /officials/posts?range= */
  officialPosts(rangeKey) {
    return read('/officials/posts' + qs({ range: rangeKey || DEFAULT_RANGE }))
  },

  /* etfMentionsFor(account, range) → GET /officials/{account}/etf-mentions?range=
     口径按出现次数累加（ETF_MENTION_RULE），**与市场域的评论去重语义相反**，别混用。 */
  etfMentionsFor(account, rangeKey) {
    return read(
      `/officials/${encodeURIComponent(account)}/etf-mentions` +
        qs({ range: rangeKey || DEFAULT_RANGE }),
    )
  },
}

const R = Object.assign({}, MIRROR, migrated)

export default R

/* 演示 provider 的取数器：把设计源 design/radar-data.js 的契约函数返回值导出成 JSON。
 *
 * 为什么是「跑设计源」而不是「用 Python 重写一遍生成器」——
 * 验收标准是 React 版与设计源静态站的 textContent 逐字相同（ADR-0006）。把 hash / rnd /
 * pickN / spread / toFixed / sort 稳定性这些逐位行为在 Python 里重新实现一遍，等于
 * CLAUDE.md 明令禁止的「照着设计手推一遍」：JS 的 >>> 、charCodeAt 的 UTF-16 语义、
 * Math.round 的 half-up（Python 是 banker's rounding）、Array.sort 的稳定性——任何一处
 * 对不齐，逐字比对就红，而且极难定位。
 *
 * 这里没有第二份口径实现：数值**逐字**来自设计源，本文件只负责枚举参数、序列化。
 * 真实数据的口径实现在 backend/core/（SQL），与本文件无关（铁律 1 不受影响）。
 *
 * 用法：node backend/fixtures/generate.mjs
 * 产出：backend/fixtures/demo/<function>.json，形如 {"<参数键>": <该函数返回值>}
 */
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs'
import { createContext, runInContext } from 'node:vm'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const repo = join(here, '..', '..')
const outDir = join(here, 'demo')

/* design/radar-data.js 是浏览器 IIFE，只依赖一个 window 全局（已核对：无 document /
   navigator / localStorage）。给它一个空 window 就能在 Node 里原样求值。 */
const ctx = createContext({ window: {}, console })
runInContext(readFileSync(join(repo, 'design', 'radar-data.js'), 'utf8'), ctx, {
  filename: 'design/radar-data.js',
})
const R = ctx.window.RADAR
if (!R) throw new Error('design/radar-data.js 没有定义 window.RADAR')

const RANGE_KEYS = R.PRESETS.map((p) => p.k)

mkdirSync(outDir, { recursive: true })

let files = 0
let entries = 0

/** 把 {参数键: 返回值} 写成一个 JSON 文件。参数键即 URL 上的查询组合，后端按同样规则拼。 */
function dump(name, pairs) {
  const obj = {}
  for (const [k, v] of pairs) obj[k] = v
  // 缩进 0：这些文件是机器读的，d30 的官号帖子流有上千条，缩进会让体积翻倍。
  writeFileSync(join(outDir, name + '.json'), JSON.stringify(obj), 'utf8')
  files++
  entries += pairs.length
  console.log(`  ${name}.json  ${pairs.length} 条`)
}

console.log('从设计源导出演示数据：')

/* ── buildRange(key) ── 5 个预设区间。前端不自行算桶（PRD §5 逐字），桶在这里下发。 */
dump('ranges', RANGE_KEYS.map((k) => [k, R.buildRange(k)]))

/* ── officialPosts(range) ── 官号帖子级内容流。 */
dump('official_posts', RANGE_KEYS.map((k) => [k, R.officialPosts(k)]))

/* ── etfMentionsFor(account, range) ── 官号 × ETF 提及统计。
   参数键 "<account>|<range>"；账号全集来自 OFFICIAL 主数据，20 × 5 = 100 条。 */
dump(
  'etf_mentions',
  R.OFFICIAL.flatMap((o) => RANGE_KEYS.map((k) => [o[0] + '|' + k, R.etfMentionsFor(o[0], k)])),
)

console.log(`完成：${files} 个文件，${entries} 条。`)

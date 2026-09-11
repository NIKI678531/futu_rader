/* 静态守卫 —— 五条廉价的 grep 断言，守五条容易被顺手违反的约定。
 *
 * 都属于「写的时候没觉得有什么，几个月后没人记得为什么不能这么写」的类型，所以钉成测试。
 * 每条守卫都必须说清**违反了会怎样**，否则下一个人只会把它注释掉。
 *
 * ## 守卫 ① 曾经是红的（工单 04 → 07）
 *
 * 建起来那天它就报红：官号动态页在用 `R.hash` 算主页地址，KOL 详情页在用 `R.addDays`
 * 算日历轴。两处分别由工单 05 与 07 拔掉，改成后端下发 `url` 与 `range.dates`。
 * 全程**没有设豁免名单**——豁免名单一旦有了就不会有人再删，红线也就永远停在那儿。
 *
 * ## 在 `npm test` 里排第一（2026-09-11 改，原来排最后）
 *
 * 原来的理由是「六态红线和逐字比对更值得先看」。那是在按**价值**排序，而顺序该按
 * **成本**排：guards 是纯 grep，几百毫秒，不起服务、不下浏览器；六态与逐字比对各要
 * 拉起一套后端＋前端＋Playwright，合计以分钟计。一条 `?? 0` 让 guards 红的时候，
 * 按原顺序你要先等完那几分钟才看得到它 —— 而那几分钟跑的东西，本来就会因为同一个
 * `?? 0` 一起红。先跑最便宜、最可能红的那条，是省时间，不是降低它的分量。
 *
 * 用法：npm run guards（也在 npm test 里，第一个）
 */
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const FRONTEND = path.resolve(HERE, '..')
const REPO = path.resolve(FRONTEND, '..')

const GUARDS = [
  {
    id: '①',
    name: '屏幕代码里不得残留演示数据生成器',
    roots: ['frontend/src/screens'],
    pattern: /\bR\.(hash|rnd|pick|pickN|addDays)\b/g,
    why:
      '这些是设计源用来编演示数据的伪随机生成器（ADR-0004）。镜像在工单 12 已经拔掉，'
      + '所以现在写 R.hash 是第一次渲染就炸——这条守卫的价值从「拦住假数字」变成了'
      + '「在 grep 阶段就告诉你为什么它不存在」：报错只会说 R.hash is not a function，'
      + '不会说这个值本该由后端下发，而后者才是要改的东西。',
    fix: '这个值应该由后端下发。看 PRD 第 5 章里对应的契约函数。',
  },
  {
    id: '②',
    name: '门面与垫片层不得有默认值兜底',
    roots: ['frontend/src/data', 'frontend/src/lib'],
    // 只认「兜成零值」的那几种：?? 0 / || 0 / || [] / || {}。`|| DEFAULT_KEY` 这类
    // 参数默认值不在此列 —— 它改的是「你没说要哪段区间」，不是「后端没给我值」。
    pattern: /(\?\?|\|\|)\s*(0\b|\[\s*\]|\{\s*\})/g,
    why:
      '它们正是把 null 变成 0 的那把刀（ADR-0005、铁律 2）。后端的 null 必须原样穿到'
      + '渲染层去触发「暂不可用」；在门面里抹平，产品团队会把「没采到数」读成'
      + '「市场上没人讨论」。',
    fix: '把 null 原样往下传。缺失的显示形态由渲染层按 PRD §3.6 决定，不在取数层决定。',
  },
  {
    id: '③',
    name: '业务逻辑里不得读系统时间',
    roots: ['frontend/src', 'backend/core', 'backend/providers', 'backend/api', 'worker'],
    pattern: /\bnew Date\(\s*\)|\bdatetime\.now\(|\bdate\.today\(/g,
    why:
      '演示锚点冻结在 ANCHOR=2026-09-01 / NOW=2026-09-02 09:00 HKT（ADR-0012）。'
      + '业务逻辑一读系统时间，同一份代码今天和明天算出的区间就不一样，逐字比对会随机变红，'
      + '而且那种红几乎无法定位——它跟你改的东西毫无关系。',
    fix:
      '区间与时间桶由后端下发（PRD §5：前端不自行算桶），锚点从数据取（meta_kv.data_max_ts），'
      + '不从系统时间取。展示层纯格式化不在此列。若你要的是「这次运行发生在什么时候」'
      + '这类审计时间戳，走 worker/clock.py 那扇门，理由写在那个模块的文档里。',
    // 展示层格式化除外：dc.js 只做样式，screen-diff/six-state 是测试脚本本身。
    //
    // worker/clock.py 除外，这是 2026-09-11 加的第三处，需要说清楚它凭什么：
    // 标注管线要记「这次 run 什么时候开始」「这个租约什么时候过期」。那不是口径，
    // 是进程的审计痕迹 —— 用冻结的假时钟去记，记下来的是假话，而租约会永不过期，
    // 崩掉的 worker 领走的任务再没人捡得回来（ADR-0017）。
    // 所以规则从「不许读钟」收窄成「读钟只能走这一扇门」。收窄之后仍然挡得住本条
    // 守卫真正要挡的东西：口径代码里冒出来的一个 now()。挡不住的是有人往 clock.py
    // 里塞口径逻辑 —— 那个模块只有一个函数，多出任何东西都该在评审时被问一句。
    // 这仍然是一条豁免，不是零豁免。写在这里是为了让下一个人能判断它是否还成立，
    // 而不是让它悄悄变成一份会越来越长的名单。
    skip: (rel) => rel.includes('scripts/') || rel.endsWith('lib/dc.js')
      || rel === 'worker/clock.py',
  },
  {
    id: '④',
    name: '屏幕代码里不得调用口径公式',
    roots: ['frontend/src/screens'],
    // delta（环比）与 heatOf（讨论热度）是 PRD 第 3 章的全局口径，不是展示助手。
    // shortName / num / pct1 这类纯格式化在 lib/view.js，不在此列。
    pattern: /\bR\.(delta|heatOf)\b/g,
    why:
      'PRD 第 3 章整章是「全局口径（全系统唯一定义，禁止在前端重复实现）」，唯一实现处是'
      + ' backend/core/（铁律 1）。屏幕里再算一遍，等后端口径改了——阈值调了、样本不足的'
      + '判定变了——页面上会有一部分数字悄悄停留在旧口径上，而两边都显示得理直气壮。',
    fix:
      '环比由后端内嵌在它所描述的那个数旁边下发（benchmark 的每个字段、benchmark.buckets'
      + ' 的逐桶 delta、pool.own 的 dHeat/dNeg/dPos），热度是观测上的 discussionHeat 字段。',
  },
  {
    id: '⑤',
    name: '前端不得从设计源镜像取数',
    roots: ['frontend/src'],
    // tokens.css 里那条 @import 不在此列：它引的是设计系统的样式，不是数据。
    pattern: /design\/radar-data|window\.RADAR/g,
    why:
      '这是工单 12 收口的那条线。它一旦被接回来，前端就又有了一条通往本地演示数据的路，'
      + '而这条路的坏处在于它不报错：漏迁或迁错一个字段会静悄悄地回落到镜像，页面照常渲染、'
      + '逐字比对照常绿——因为镜像里的数**就是**逐字比对的对照组。整个后端接线会看起来做完了。',
    fix:
      '要的字段应该有一个 PRD 第 5 章的契约函数。没有就先加端点，'
      + '不要从镜像上借一个长得差不多的值。',
  },
]

const EXT = new Set(['.js', '.jsx', '.mjs', '.py'])

/* 注释要先挖掉再 grep。这几条守卫的正文里全都逐字引用了它们禁止的写法（「不得出现
   `?? 0`、`|| 0`」「业务逻辑里没有 datetime.now()」），不挖的话守卫第一个就把自己告了，
   然后大家学到的是「这守卫老是误报」，接着就把它关了。
   挖的时候用等长空格替换，行号和列都不动。 */
function stripComments(src, ext) {
  const blank = (m) => m.replace(/[^\n]/g, ' ')
  if (ext === '.py') {
    return src
      .replace(/"""[\s\S]*?"""|'''[\s\S]*?'''/g, blank)
      .replace(/#[^\n]*/g, blank)
  }
  return src
    .replace(/\/\*[\s\S]*?\*\//g, blank)
    .replace(/(^|[^:])\/\/[^\n]*/g, (m, p) => p + blank(m.slice(p.length)))
}

function walk(dir, out = []) {
  let entries
  try {
    entries = readdirSync(dir)
  } catch {
    return out
  }
  for (const e of entries) {
    if (e === 'node_modules' || e === '.venv' || e === '__pycache__' || e === 'dist') continue
    const p = path.join(dir, e)
    if (statSync(p).isDirectory()) walk(p, out)
    else if (EXT.has(path.extname(p))) out.push(p)
  }
  return out
}

let failed = 0

for (const g of GUARDS) {
  const hits = []
  for (const root of g.roots) {
    for (const file of walk(path.join(REPO, root))) {
      const rel = path.relative(REPO, file).replace(/\\/g, '/')
      if (g.skip && g.skip(rel)) continue
      const lines = stripComments(readFileSync(file, 'utf8'), path.extname(file)).split('\n')
      lines.forEach((line, i) => {
        g.pattern.lastIndex = 0
        if (g.pattern.test(line)) hits.push({ rel, n: i + 1, line: line.trim() })
      })
    }
  }

  if (!hits.length) {
    console.log(`\x1b[32m✓\x1b[0m 守卫${g.id} ${g.name}`)
    continue
  }

  failed++
  console.log(`\x1b[31m✗\x1b[0m 守卫${g.id} ${g.name} —— ${hits.length} 处`)
  for (const h of hits) console.log(`    ${h.rel}:${h.n}  ${h.line.slice(0, 120)}`)
  console.log(`    为什么在意：${g.why}`)
  console.log(`    怎么改：${g.fix}\n`)
}

console.log(
  failed ? `\n\x1b[31m${failed} 条守卫不过\x1b[0m` : `\n\x1b[32m${GUARDS.length} 条静态守卫全过\x1b[0m`,
)
process.exit(failed ? 1 : 0)

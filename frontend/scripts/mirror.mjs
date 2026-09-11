/* 把仓库源码同步到本地磁盘的镜像里，前端工具链在那边跑。

   为什么要有这么一层：`npm install` 在 P: 这块 SMB 网络盘上**装不上**（不是慢，
   是 EPERM／EBUSY／ENOENT 失败），于是 P: 上根本没有 `node_modules`，
   `build`／`test`／`six-state`／`diff` 一条都跑不了。完整理由与备选方案见
   `docs/adr/0018-local-mirror-for-npm.md`。

     cd frontend && npm run mirror        # 只同步
     cd frontend && npm run mirror:test   # 同步后在镜像里跑 npm test

   三条规矩（ADR-0018）：
   1. 只往一个方向同步，P: → 镜像。镜像是可丢弃的构建目录，不是工作副本。
   2. 改完源码必须先同步再验证 —— 不同步就去镜像里跑测试，跑的是上一版代码，
      **而且是绿的**。这是这套做法唯一真正危险的地方。
   3. 这个脚本自己不跑构建。同步失败与测试失败要在输出里长得不一样。 */

import { spawnSync } from 'node:child_process'
import { existsSync, mkdirSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..')
const MIRROR = process.env.RADAR_MIRROR || path.join(os.homedir(), '.futu-radar', 'mirror')

/* 只同步源码。**`node_modules` 与 `dist` 不在这里** —— `/MIR` 会镜像删除，
   把它们扫进来就等于每次同步都把依赖删掉再重装。 */
const DIRS = [
  'frontend/src',
  'frontend/scripts',
  'frontend/public',
  'backend/api',
  'backend/core',
  'backend/providers',
  'backend/fixtures',
  'backend/tests',
  'radar_db',
  'design',
]

const FILES = [
  ['frontend', ['package.json', 'index.html', 'vite.config.js', 'vite.design.config.js', 'vite.env.js']],
  ['backend', ['app.py', 'requirements.txt', 'conftest.py', 'pytest.ini']],
]

function main() {
  if (process.platform !== 'win32') {
    console.error('这个脚本用 robocopy，只在 Windows 上跑。别的平台不需要镜像 —— 直接在仓库里 npm install。')
    process.exit(1)
  }
  if (!existsSync(MIRROR)) mkdirSync(MIRROR, { recursive: true })

  let copied = 0
  for (const d of DIRS) {
    const src = path.join(REPO, d)
    if (!existsSync(src)) continue
    copied += robocopy(src, path.join(MIRROR, d), ['/MIR'])
  }
  for (const [dir, names] of FILES) {
    const src = path.join(REPO, dir)
    const present = names.filter((n) => existsSync(path.join(src, n)))
    if (present.length) copied += robocopy(src, path.join(MIRROR, dir), present)
  }

  /* 打出数字而不是静默成功：0 个文件与 30 个文件，肉眼要能区分 ——
     「忘了同步」和「同步了但没变化」在别处看起来一模一样。 */
  console.log(`镜像：${MIRROR}`)
  console.log(`同步了 ${copied} 个文件。node_modules 与 dist 不在同步范围内（ADR-0018）。`)

  const web = path.join(MIRROR, 'frontend')
  if (!existsSync(path.join(web, 'node_modules'))) {
    console.log('\n镜像里还没有 node_modules，先装一次：')
    console.log(`  cd "${web}" && npm install`)
    return
  }

  /* `--test` 在镜像里接着跑 `npm test`。写在脚本里而不是写成
     `npm test --prefix %USERPROFILE%/...` 那种 package.json 串：那串只在 cmd.exe
     下展开，在 Git Bash／PowerShell 里 `%USERPROFILE%` 是一个字面目录名，
     npm 会去建它然后报「找不到 package.json」—— 错得毫无提示。 */
  if (process.argv.includes('--test')) {
    console.log(`\n在镜像里跑 npm test …\n`)
    const r = spawnSync('npm.cmd', ['test'], { cwd: web, stdio: 'inherit', shell: true })
    process.exit(r.status ?? 1)
  }
}

/* robocopy 的退出码不是 POSIX 语义：0 = 没有文件需要复制，1 = 复制了文件，
   2 = 有多余文件被删，3 = 两者都有。**< 8 全是成功**。照 shell 惯例判 `!== 0`
   会把每一次正常同步都当成失败。 */
function robocopy(src, dst, args) {
  const r = spawnSync('robocopy', [src, dst, ...args, '/NFL', '/NDL', '/NJH', '/NP', '/NS', '/NC'], {
    encoding: 'utf8',
  })
  const code = r.status ?? 16
  if (code >= 8) {
    console.error(`robocopy 失败（退出码 ${code}）：${src} → ${dst}`)
    console.error(r.stdout || r.stderr || '')
    process.exit(1)
  }
  const m = /Copied\s*:\s*\d+\s+(\d+)|复制的\s*:\s*\d+\s+(\d+)/.exec(r.stdout || '')
  return m ? Number(m[1] ?? m[2] ?? 0) : (code & 1 ? 1 : 0)
}

main()

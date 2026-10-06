/* Start Vite from a local disk even when the source checkout is on SMB/DFS.

   Vite package resolution and file watching are not reliable from the project's mapped
   network drive. ADR-0018 therefore keeps P: as the source of truth but runs the frontend
   toolchain from a disposable local mirror. This launcher makes that rule automatic so
   the ordinary `npm run dev` command cannot accidentally start Vite on the share. */

import { spawn, spawnSync } from 'node:child_process'
import { existsSync, realpathSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

const FRONTEND = path.resolve(import.meta.dirname, '..')

export function isNetworkDrive(
  directory,
  {
    platform = process.platform,
    run = spawnSync,
    portableRealpath = realpathSync,
    nativeRealpath = realpathSync.native,
  } = {},
) {
  if (platform !== 'win32') return false

  const resolved = path.win32.resolve(directory)
  if (resolved.startsWith('\\\\')) return true

  const drive = path.win32.parse(resolved).root.replace(/[\\/]$/, '')
  if (/^[A-Za-z]:$/.test(drive)) {
    try {
      if (run('net.exe', ['use', drive], { stdio: 'ignore', windowsHide: true }).status === 0) {
        return true
      }
    } catch {
      // Fall through to the realpath comparison used by vite.env.js.
    }
  }

  try {
    return nativeRealpath(directory) !== portableRealpath(directory)
  } catch {
    return false
  }
}

export function createDevPlan({ frontendDir, mirrorRoot, networkDrive, args }) {
  const viteCwd = networkDrive ? path.join(mirrorRoot, 'frontend') : frontendDir
  return {
    sync: networkDrive,
    viteCwd,
    viteEntry: path.join(viteCwd, 'node_modules', 'vite', 'bin', 'vite.js'),
    args: [...args],
  }
}

function syncMirror(frontendDir) {
  const mirrorScript = path.join(frontendDir, 'scripts', 'mirror.mjs')
  const result = spawnSync(process.execPath, [mirrorScript], {
    cwd: frontendDir,
    env: process.env,
    stdio: 'inherit',
    windowsHide: true,
  })

  if (result.error) throw result.error
  if (result.status !== 0) process.exit(result.status ?? 1)
}

async function run() {
  const mirrorRoot = process.env.RADAR_MIRROR || path.join(os.homedir(), '.futu-radar', 'mirror')
  const networkDrive = isNetworkDrive(FRONTEND)
  const plan = createDevPlan({
    frontendDir: FRONTEND,
    mirrorRoot,
    networkDrive,
    args: process.argv.slice(2),
  })

  if (plan.sync) {
    console.log(`检测到网络盘源码目录：${FRONTEND}`)
    console.log(`先同步并从本地镜像启动：${plan.viteCwd}\n`)
    syncMirror(FRONTEND)
  }

  if (!existsSync(plan.viteEntry)) {
    console.error(`找不到本地 Vite：${plan.viteEntry}`)
    console.error(`请先在 ${plan.viteCwd} 执行 npm install。`)
    process.exit(1)
  }

  const child = spawn(process.execPath, [plan.viteEntry, ...plan.args], {
    cwd: plan.viteCwd,
    env: process.env,
    stdio: 'inherit',
  })

  const exitCode = await new Promise((resolve, reject) => {
    child.once('error', reject)
    child.once('exit', code => resolve(code ?? 1))
  })
  process.exitCode = exitCode
}

const invokedPath = process.argv[1] ? pathToFileURL(path.resolve(process.argv[1])).href : ''
if (import.meta.url === invokedPath) await run()

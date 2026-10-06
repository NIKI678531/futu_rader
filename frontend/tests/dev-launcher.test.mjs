import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import test from 'node:test'

import { createDevPlan, isNetworkDrive } from '../scripts/dev.mjs'

const packageJson = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8'))

test('npm run dev uses the network-aware launcher', () => {
  assert.equal(packageJson.scripts.dev, 'node scripts/dev.mjs')
})

test('mapped Windows drives are detected without relying on realpath timing', () => {
  const calls = []
  const detected = isNetworkDrive('P:\\NIKI\\futu-radar\\frontend', {
    platform: 'win32',
    run(command, args) {
      calls.push([command, args])
      return { status: 0 }
    },
    portableRealpath: value => value,
    nativeRealpath: value => value,
  })

  assert.equal(detected, true)
  assert.deepEqual(calls, [['net.exe', ['use', 'P:']]])
})

test('network checkout syncs and launches Vite from the local mirror', () => {
  const plan = createDevPlan({
    frontendDir: 'P:\\NIKI\\futu-radar\\frontend',
    mirrorRoot: 'C:\\Users\\tester\\.futu-radar\\mirror',
    networkDrive: true,
    args: ['--host', '0.0.0.0'],
  })

  assert.equal(plan.sync, true)
  assert.equal(plan.viteCwd, path.join('C:\\Users\\tester\\.futu-radar\\mirror', 'frontend'))
  assert.equal(plan.viteEntry, path.join(plan.viteCwd, 'node_modules', 'vite', 'bin', 'vite.js'))
  assert.deepEqual(plan.args, ['--host', '0.0.0.0'])
})

test('local checkout launches its own Vite without mirroring', () => {
  const plan = createDevPlan({
    frontendDir: 'C:\\work\\futu-radar\\frontend',
    mirrorRoot: 'C:\\Users\\tester\\.futu-radar\\mirror',
    networkDrive: false,
    args: [],
  })

  assert.equal(plan.sync, false)
  assert.equal(plan.viteCwd, 'C:\\work\\futu-radar\\frontend')
})

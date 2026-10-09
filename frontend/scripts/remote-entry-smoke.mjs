import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'
import webpack from 'webpack'

const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const DIST = path.join(FRONTEND, 'dist')
const PREFIX = '/remote/opinion-radar/'
const TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'application/javascript; charset=utf-8',
  '.ttf': 'font/ttf',
}
const HOST_HTML = '<!doctype html><html><head></head><body><h1 id="host-sentinel">Host</h1><div id="root"></div><script src="/host.js"></script></body></html>'
let hostDist

async function sendFile(response, filePath) {
  try {
    const body = await readFile(filePath)
    response.writeHead(200, { 'Content-Type': TYPES[path.extname(filePath)] || 'application/octet-stream' })
    response.end(body)
    return true
  } catch {
    return false
  }
}

const server = createServer(async (request, response) => {
  const pathname = new URL(request.url, 'http://127.0.0.1').pathname
  if (pathname === '/host') {
    response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' })
    response.end('<!doctype html><html><head></head><body><div id="root"></div></body></html>')
    return
  }
  if (pathname.startsWith(PREFIX)) {
    const relativePath = pathname.slice(PREFIX.length)
    const filePath = path.resolve(DIST, relativePath)
    if (!filePath.startsWith(`${DIST}${path.sep}`)) {
      response.writeHead(400).end()
      return
    }
    if (!await sendFile(response, filePath)) response.writeHead(404).end()
    return
  }
  if (hostDist && path.extname(pathname)) {
    const filePath = path.resolve(hostDist, pathname.slice(1))
    if (filePath.startsWith(`${hostDist}${path.sep}`) && await sendFile(response, filePath)) return
    response.writeHead(404).end()
    return
  }
  response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' })
  response.end(HOST_HTML)
})

async function buildHost(origin) {
  const root = await mkdtemp(path.join(os.tmpdir(), 'opinion-radar-host-'))
  const indexPath = path.join(root, 'index.js')
  const bootstrapPath = path.join(root, 'bootstrap.js')
  hostDist = path.join(root, 'dist')
  await writeFile(indexPath, "import('./bootstrap.js')\n")
  await writeFile(bootstrapPath, `
import React, { Suspense } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Route, Routes } from 'react-router-dom'

const RemoteApp = React.lazy(() => import('opinionRadar/App'))
const remote = React.createElement(Suspense, { fallback: React.createElement('div', null, 'loading') }, React.createElement(RemoteApp, { allowAdmin: true }))
const routes = React.createElement(Routes, null, React.createElement(Route, { path: '/opinion-radar/*', element: remote }))
createRoot(document.getElementById('root')).render(React.createElement(BrowserRouter, null, routes))
`)
  const { ModuleFederationPlugin } = webpack.container
  const config = {
    mode: 'development',
    context: FRONTEND,
    entry: indexPath,
    devtool: false,
    output: {
      path: hostDist,
      filename: 'host.js',
      chunkFilename: '[name].js',
      publicPath: '/',
      clean: true,
    },
    resolve: {
      modules: [path.join(FRONTEND, 'node_modules')],
      extensions: ['.js'],
    },
    plugins: [
      new ModuleFederationPlugin({
        name: 'smokeHost',
        remotes: { opinionRadar: `opinionRadar@${origin}${PREFIX}remoteEntry.js` },
        shared: {
          react: { singleton: true, requiredVersion: '^18.2.0' },
          'react-dom': { singleton: true, requiredVersion: '^18.2.0' },
          'react-router-dom': { singleton: true, requiredVersion: '^6.8.0' },
        },
      }),
    ],
  }
  await new Promise((resolve, reject) => {
    webpack(config, (error, stats) => {
      if (error) return reject(error)
      if (stats.hasErrors()) return reject(new Error(stats.toString({ all: false, errors: true })))
      resolve()
    })
  })
  return root
}

await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
const { port } = server.address()
const origin = `http://127.0.0.1:${port}`
let browser
let hostRoot

try {
  browser = await chromium.launch({ headless: true })
  const page = await browser.newPage()
  await page.goto(`${origin}/host`)
  await page.addScriptTag({ url: `${origin}${PREFIX}remoteEntry.js?v=smoke` })
  const contract = await page.evaluate(async () => {
    const container = window.opinionRadar
    if (!container) return { container: false }
    await container.init({})
    const factory = await container.get('./App')
    const exposed = factory()
    const css = Array.from(document.querySelectorAll('style'), element => element.textContent).join('\n')
    return {
      container: true,
      init: typeof container.init,
      get: typeof container.get,
      defaultExport: typeof exposed.default,
      scopedCss: css.includes('.opinion-radar-app h1') && css.includes('.opinion-radar-app {'),
      leaksBodyStyle: /(^|})\s*body\s*\{/.test(css),
    }
  })
  assert.deepEqual(contract, {
    container: true,
    init: 'function',
    get: 'function',
    defaultExport: 'function',
    scopedCss: true,
    leaksBodyStyle: false,
  })
  console.log('remoteEntry contract OK:', contract)

  hostRoot = await buildHost(origin)
  const hostPage = await browser.newPage()
  await hostPage.goto(`${origin}/opinion-radar/admin`)
  await hostPage.waitForSelector('.opinion-radar-app .admin-shell')
  assert.equal(await hostPage.getAttribute('.admin-actions a', 'href'), '/opinion-radar/product')
  assert.equal(await hostPage.locator('#host-sentinel').evaluate(element => getComputedStyle(element).fontSize), '32px')
  console.log('host integration OK: nested router mounted and internal links kept /opinion-radar')
} finally {
  if (browser) await browser.close()
  await new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve()))
  if (hostRoot) await rm(hostRoot, { recursive: true, force: true })
}

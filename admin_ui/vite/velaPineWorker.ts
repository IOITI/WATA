// Vela-pinets runs Pine scripts in a Web Worker whose code it carries as a string
// and starts from a blob: address. The admin's Content-Security-Policy refuses blob:
// workers, and PineTS evaluates the code it generates, which only a worker file of
// our own may do (src/admin/app.py, WORKER_CONTENT_SECURITY_POLICY). This plugin
// takes that string out of the pinned package and serves it as such a file, under a
// name the admin recognises: /assets/vela-pine-worker-<hash>.js.
//
// The page gets the address from the virtual module and hands it to
// `new PineWorkerEngine({ workerUrl })`. The worker carries a copy of PineTS, which
// keeps a formatter per time zone there too (vite/pinetsZoneFormat.ts), and continues
// a chart's run with its new bars rather than running it whole again
// (vite/velaContinuedRuns.ts).

import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, join } from 'node:path'

import { parse } from 'acorn'
import type { Plugin } from 'vite'

import { patchPinets } from './pinetsZoneFormat.ts'
import { patchContinuedRuns } from './velaContinuedRuns.ts'

export const VIRTUAL_ID = 'virtual:vela-pine-worker'
const RESOLVED_ID = `\0${VIRTUAL_ID}`
// The name tsup gives the inlined worker in @luxalgo/vela-pinets' build.
const WORKER_VARIABLE = 'worker_default'

/** The ESM build of the installed @luxalgo/vela-pinets. */
export function velaPinetsBundlePath(): string {
  const require = createRequire(import.meta.url)
  // The package exports its CommonJS build to require(); the ESM one sits beside it.
  return join(dirname(require.resolve('@luxalgo/vela-pinets')), 'index.js')
}

/**
 * The worker's source, read from the package's top-level `var worker_default = '…'`.
 * Throws when the package no longer has it, so that the build stops rather than
 * shipping charts whose indicators can never run.
 */
export function extractWorkerSource(bundle: string): string {
  const program = parse(bundle, { ecmaVersion: 'latest', sourceType: 'module' })
  for (const statement of program.body) {
    if (statement.type !== 'VariableDeclaration') continue
    for (const declaration of statement.declarations) {
      if (declaration.id.type !== 'Identifier' || declaration.id.name !== WORKER_VARIABLE) continue
      const init = declaration.init
      if (init?.type === 'Literal' && typeof init.value === 'string' && init.value.length > 0) {
        return init.value
      }
    }
  }
  throw new Error(
    `@luxalgo/vela-pinets no longer carries its worker as "${WORKER_VARIABLE}": ` +
      'the Technical Analysis charts need it served as a file (see admin_ui/vite/velaPineWorker.ts)',
  )
}

/**
 * The package's code with its copy of the worker left empty: the page always starts the
 * worker from its file (`workerUrl`), and the string would otherwise ship twice (930 KB).
 */
export function withoutInlinedWorker(bundle: string): string {
  const program = parse(bundle, { ecmaVersion: 'latest', sourceType: 'module' })
  for (const statement of program.body) {
    if (statement.type !== 'VariableDeclaration') continue
    for (const declaration of statement.declarations) {
      if (declaration.id.type !== 'Identifier' || declaration.id.name !== WORKER_VARIABLE) continue
      const init = declaration.init
      if (init?.type === 'Literal' && typeof init.value === 'string') {
        return `${bundle.slice(0, init.start)}""${bundle.slice(init.end)}`
      }
    }
  }
  return bundle
}

/** Where the worker is served, named after its content so that a new version is fetched again. */
export function workerFileName(source: string): string {
  const hash = createHash('sha256').update(source).digest('hex').slice(0, 12)
  return `assets/vela-pine-worker-${hash}.js`
}

export function velaPineWorker(bundlePath: string = velaPinetsBundlePath()): Plugin {
  let source: string | null = null
  let fileName = ''
  const load = () => {
    if (source === null) {
      source = patchContinuedRuns(patchPinets(extractWorkerSource(readFileSync(bundlePath, 'utf8'))))
      fileName = workerFileName(source)
    }
    return source
  }

  return {
    name: 'wata-vela-pine-worker',
    resolveId(id) {
      return id === VIRTUAL_ID ? RESOLVED_ID : null
    },
    load(id) {
      if (id !== RESOLVED_ID) return null
      load()
      return `export default ${JSON.stringify(`/${fileName}`)}`
    },
    transform(code, id) {
      if (id.split('?')[0].replaceAll('\\', '/') !== bundlePath.replaceAll('\\', '/')) return null
      load()
      return { code: withoutInlinedWorker(code), map: null }
    },
    generateBundle() {
      this.emitFile({ type: 'asset', fileName, source: load() })
    },
    configureServer(server) {
      server.middlewares.use((request, response, next) => {
        load()
        if (request.url?.split('?')[0] !== `/${fileName}`) {
          next()
          return
        }
        response.setHeader('Content-Type', 'text/javascript; charset=utf-8')
        response.end(source)
      })
    },
  }
}

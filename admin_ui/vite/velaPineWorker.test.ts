import { readFileSync } from 'node:fs'

import { describe, expect, it } from 'vitest'

import { extractWorkerSource, velaPinetsBundlePath, withoutInlinedWorker, workerFileName } from './velaPineWorker.ts'

describe('the worker of Vela-pinets, as a file of our own', () => {
  it('is found in the installed package: the build stops if it ever is not', () => {
    const source = extractWorkerSource(readFileSync(velaPinetsBundlePath(), 'utf8'))

    expect(source.length).toBeGreaterThan(100_000)
    // The worker runs PineTS, which compiles scripts: what the worker's own policy allows.
    expect(source).toContain('new Function')
    expect(workerFileName(source)).toMatch(/^assets\/vela-pine-worker-[0-9a-f]{12}\.js$/)
  })

  it('is taken out of the package\'s code, which always starts it from the file', () => {
    const bundle = readFileSync(velaPinetsBundlePath(), 'utf8')

    const stripped = withoutInlinedWorker(bundle)

    expect(bundle.length - stripped.length).toBeGreaterThan(100_000)
    expect(stripped).toContain('var worker_default = ""')
    expect(stripped).toContain('if (workerUrl) return new Worker(workerUrl)')
  })

  it('refuses a package without it', () => {
    expect(() => extractWorkerSource('export const nothing = 1')).toThrow('no longer carries its worker')
    expect(withoutInlinedWorker('export const nothing = 1')).toBe('export const nothing = 1')
  })
})

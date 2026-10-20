import { validatePineScript } from 'pinescript-v6-validator'
import { describe, expect, it } from 'vitest'

import { answer } from '../engine/workerCore'
import type { CompileResponse } from '../engine/pineWorkerClient'
import { checkScript, tickerChecks } from './diagnostics'
import type { CheckOptions } from './diagnostics'

// The real compile, as the worker does it, without a worker.
const compile = (source: string) =>
  answer({ id: 1, op: 'compile', source }, async () => []) as Promise<CompileResponse>

const LIBRARY = `//@version=6
library("Helpers")

export double(float value) =>
    value * 2
`

function options(overrides: Partial<CheckOptions> = {}): CheckOptions {
  return {
    libraryName: null,
    resolve: async (name) => (name === 'Helpers' ? { id: 'h', name, version: 3, source: LIBRARY } : null),
    compile,
    validate: validatePineScript,
    ...overrides,
  }
}

describe('checkScript', () => {
  it('passes a script that compiles, with its inputs, libraries inlined', async () => {
    const result = await checkScript(
      '//@version=6\nindicator("x")\nimport someone/Helpers/1 as h\nlength = input.int(5, "Length")\nplot(h.double(ta.sma(close, length)))\n',
      options(),
    )

    expect(result.diagnostics).toEqual([])
    expect(result.prepared?.libraries).toEqual([{ id: 'h', name: 'Helpers', version: 3 }])
    expect(result.compiled?.inputs.map((input) => input.title)).toEqual(['Length'])
  })

  it('marks a syntax error PineTS finds on the line it was written on, past the inlined library', async () => {
    const result = await checkScript(
      '//@version=6\nindicator("x")\nimport someone/Helpers/1 as h\nx = h.double(close\nplot(x)\n',
      options(),
    )

    const pine = result.diagnostics.filter((diagnostic) => diagnostic.source === 'PineTS')
    expect(pine).toHaveLength(1)
    expect(pine[0]).toMatchObject({ severity: 'error' })
    // The library's lines went in after the declaration: the error is still placed in the script.
    expect(pine[0].line).toBeGreaterThanOrEqual(4)
    expect(pine[0].line).toBeLessThanOrEqual(6)
  })

  it('marks what TradingView would refuse, and leaves v5 scripts to PineTS', async () => {
    const v6 = await checkScript('//@version=6\nindicator("x")\nplot(close, colour = color.red)\n', options())
    expect(v6.diagnostics).toContainEqual(
      expect.objectContaining({ source: 'TradingView rules', line: 3, severity: 'error', message: expect.stringContaining('colour') }),
    )

    const v5 = await checkScript('//@version=5\nindicator("x")\nplot(close)\n', options())
    expect(v5.diagnostics.filter((diagnostic) => diagnostic.source === 'TradingView rules')).toEqual([])
  })

  it('says where an import cannot be resolved, and what a library leaves out', async () => {
    const missing = await checkScript('//@version=6\nindicator("x")\nimport someone/Missing/1 as m\nplot(close)\n', options())
    expect(missing.diagnostics).toEqual([
      expect.objectContaining({ line: 3, message: expect.stringContaining('No library "Missing"'), severity: 'error' }),
    ])
  })

  it('checks a library inside an empty indicator', async () => {
    const result = await checkScript(LIBRARY + 'plot(close)\n', options({ libraryName: 'Helpers' }))

    expect(result.compiled?.declaration).toBe('indicator')
    expect(result.diagnostics).toContainEqual(
      expect.objectContaining({ severity: 'info', message: expect.stringContaining('left out') }),
    )
  })

  it('stops at a source PineTS would run as JavaScript', async () => {
    const result = await checkScript('//@PineTS\nindicator("x")\n', options())

    expect(result.diagnostics).toEqual([expect.objectContaining({ severity: 'error', message: expect.stringContaining('//@PineTS') })])
    expect(result.prepared).toBeNull()
  })
})

describe('tickerChecks', () => {
  it('marks the symbols WATA has not, not those it has nor the chart\'s own', () => {
    const source = `//@version=6
indicator("x")
a = input.symbol('SKILLING:US100', "Provider 2")
b = request.security("DUKASCOPY_US100", "1", close)
c = request.security(syminfo.tickerid, "5", close)
d = request.security("BINANCE:BTCUSDT", "1", close)
`
    expect(tickerChecks(source)).toEqual([
      expect.objectContaining({ line: 3, column: 18, severity: 'warning', message: expect.stringContaining('"SKILLING:US100"') }),
      expect.objectContaining({ line: 6, column: 22, severity: 'error', message: expect.stringContaining('"BINANCE:BTCUSDT"') }),
    ])
  })
})

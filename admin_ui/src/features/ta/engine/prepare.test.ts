/// <reference types="node" />
import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

import { describe, expect, it } from 'vitest'

import { tokenize } from './lexer'
import { checkedVersion, originOf, prepareLibraryCheck, prepareScript, PrepareError, SCRIPT_FILE } from './prepare'
import type { LibrarySource } from './prepare'

// Made-up libraries shaped like the ones a real strategy imports: a v5 library of
// kernels with a private helper and defaults, and a v6 one with named arguments.
const KERNELS = `//@version=5

// @description Kernel functions.
library("Kernels", true)

sq(float value) =>
    math.pow(value, 2)

export gaussian(float distance, float bandwidth) =>
    h = bandwidth == 0 ? 1e-6 : bandwidth
    math.exp(-sq(distance / h) / 2) / math.sqrt(2 * math.pi)

export regression(series float source, int length, string style = "gaussian") =>
    float total = 0.0
    float weights = 0.0
    for i = 0 to length - 1
        w = style == "gaussian" ? gaussian(i / length, 1.0) : 1.0
        total += source[i] * w
        weights += w
    total / weights

plot(regression(close, 10), "Shown when the library itself is on a chart")
`

const RISK = `//@version=6
library("Risk")

export risky(simple int candles = 10, simple float multiplier = 2.0) =>
    float average = ta.sma(high - low, candles)
    math.abs(open - close[1]) > average * multiplier ? 1 : 0
`

const STRATEGY = `// This is the strategy.
//@version=6
indicator('Kernel strategy', overlay = true,
     max_labels_count = 500)

import someone/Kernels/1 as kf
import someone/Risk/3 as riskLib

length = input.int(15, 'Length')
kernel = kf.regression(close, length)
risk = riskLib.risky(candles = 10, multiplier = 2.0)
plot(kernel, 'Kernel')
alertcondition(risk == 0 and ta.crossover(close, kernel), 'Enter Long', '{"action": "long"}')
`

function libraries(...sources: [string, string][]): (name: string) => Promise<LibrarySource | null> {
  const byName = new Map(sources.map(([name, source], index) => [name, { id: `lib${index}`, name, version: index + 1, source }]))
  return async (name) => byName.get(name) ?? null
}

describe('tokenize', () => {
  it('keeps comments, strings, numbers, colors and operators apart, with their line and column', () => {
    const tokens = tokenize('a := "x // y" // c\nb = #FF00AA + 1e-6 => .5')
    expect(tokens.filter((token) => token.kind !== 'space').map((token) => [token.kind, token.text, token.line, token.column]))
      .toEqual([
        ['identifier', 'a', 1, 1],
        ['operator', ':=', 1, 3],
        ['string', '"x // y"', 1, 6],
        ['comment', '// c', 1, 15],
        ['newline', '\n', 1, 19],
        ['identifier', 'b', 2, 1],
        ['operator', '=', 2, 3],
        ['color', '#FF00AA', 2, 5],
        ['operator', '+', 2, 13],
        ['number', '1e-6', 2, 15],
        ['operator', '=>', 2, 20],
        ['number', '.5', 2, 23],
      ])
  })

  it('ends an unclosed string with its line', () => {
    const tokens = tokenize('"open\nnext')
    expect(tokens.map((token) => token.text)).toEqual(['"open', '\n', 'next'])
  })
})

describe('checkedVersion', () => {
  it.each([
    ['', 'empty'],
    ['//@PineTS\nindicator("x")', '//@PineTS'],
    ['indicator("x")', '//@version=5 or //@version=6'],
    ['//@version=4\nstudy("x")', 'version 4 is not supported'],
  ])('refuses what PineTS would not run as Pine: %j', (source, refusal) => {
    expect(() => checkedVersion(source)).toThrow(refusal)
  })

  it('reads the version', () => {
    expect(checkedVersion('//@version=5\nindicator("x")')).toBe(5)
  })
})

describe('prepareScript', () => {
  it('inlines each library once, its private names and exports renamed, in place of the imports', async () => {
    const prepared = await prepareScript(STRATEGY, libraries(['Kernels', KERNELS], ['Risk', RISK]))

    expect(prepared.kind).toBe('indicator')
    expect(prepared.title).toBe('Kernel strategy')
    expect(prepared.libraries).toEqual([
      { id: 'lib0', name: 'Kernels', version: 1 },
      { id: 'lib1', name: 'Risk', version: 2 },
    ])
    const source = prepared.source
    expect(source).not.toMatch(/^import /m)
    expect(source).not.toContain('library(')
    expect(source).toContain('Kernels__sq(float value) =>')
    expect(source).toContain('math.exp(-Kernels__sq(distance / h) / 2) / math.sqrt(2 * math.pi)')
    expect(source).toContain('Kernels__regression(series float source, int length, string style = "gaussian") =>')
    expect(source).toContain('w = style == "gaussian" ? Kernels__gaussian(i / length, 1.0) : 1.0')
    // Built-ins and named arguments are left alone.
    expect(source).toContain('math.pow(value, 2)')
    expect(source).toContain('kernel = Kernels__regression(close, length)')
    expect(source).toContain('risk = Risk__risky(candles = 10, multiplier = 2.0)')
    expect(source).not.toContain('export ')
    // The library's own chart code is not imported.
    expect(source).not.toContain('Shown when the library itself is on a chart')
    // The library's own declaration is not "left out": it is what makes it a library.
    expect(prepared.warnings).toEqual([
      'Kernels, line 22: left out (only declarations are imported from a library)',
      'Kernels is Pine v5 and runs here as v6, the script\'s version',
    ])
  })

  it('puts the libraries after the declaration, and maps every line back to where it was written', async () => {
    const prepared = await prepareScript(STRATEGY, libraries(['Kernels', KERNELS], ['Risk', RISK]))
    const lines = prepared.source.split('\n')

    expect(lines[2]).toBe("indicator('Kernel strategy', overlay = true,")
    expect(lines[4]).toMatch(/^\/\/ ── library Kernels, version 1/)
    const gaussian = lines.findIndex((line) => line.startsWith('Kernels__gaussian('))
    expect(originOf(prepared, gaussian + 1)).toEqual({ file: 'Kernels', line: 9 })
    const kernel = lines.findIndex((line) => line.startsWith('kernel = '))
    expect(originOf(prepared, kernel + 1)).toEqual({ file: SCRIPT_FILE, line: 10 })
    expect(lines.length).toBe(prepared.lineMap.length)
  })

  it('inlines a library\'s own imports first, once', async () => {
    const outer = `//@version=6
library("Outer")
import someone/Kernels/1 as k

export smooth(series float source) =>
    k.regression(source, 5)
`
    const script = `//@version=6
indicator("x")
import someone/Outer/1 as outer
import someone/Kernels/1 as kernels
plot(outer.smooth(close) + kernels.gaussian(0.5, 1.0))
`
    const prepared = await prepareScript(script, libraries(['Kernels', KERNELS], ['Outer', outer]))

    expect(prepared.libraries.map((library) => library.name)).toEqual(['Kernels', 'Outer'])
    expect(prepared.source).toContain('Outer__smooth(series float source) =>\n    Kernels__regression(source, 5)')
    expect(prepared.source).toContain('plot(Outer__smooth(close) + Kernels__gaussian(0.5, 1.0))')
  })

  it('refuses a name the library does not export, where it is written', async () => {
    const script = '//@version=6\nindicator("x")\nimport someone/Kernels/1 as kf\nplot(kf.sq(close))\n'

    await expect(prepareScript(script, libraries(['Kernels', KERNELS]))).rejects.toMatchObject({
      message: 'kf.sq: the library Kernels exports no "sq"',
      file: SCRIPT_FILE,
      line: 4,
      column: 9,
    })
  })

  it('refuses an unknown library, a cycle and a script that is not a library', async () => {
    const script = '//@version=6\nindicator("x")\nimport someone/Missing/1 as m\nplot(close)\n'
    await expect(prepareScript(script, libraries())).rejects.toThrow('No library "Missing" in WATA')

    const a = '//@version=6\nlibrary("A")\nimport someone/B/1 as b\nexport f() =>\n    b.g()\n'
    const b = '//@version=6\nlibrary("B")\nimport someone/A/1 as a\nexport g() =>\n    a.f()\n'
    const cyclic = '//@version=6\nindicator("x")\nimport someone/A/1 as a\nplot(a.f())\n'
    await expect(prepareScript(cyclic, libraries(['A', a], ['B', b]))).rejects.toThrow('A → B → A')

    const notLibrary = '//@version=6\nindicator("Not")\nplot(close)\n'
    const importer = '//@version=6\nindicator("x")\nimport someone/Not/1 as n\nplot(close)\n'
    await expect(prepareScript(importer, libraries(['Not', notLibrary]))).rejects.toThrow('is not a library')
  })

  it('refuses methods and names used both at the top and as a parameter', async () => {
    const methods = '//@version=6\nlibrary("M")\nexport method twice(float self) =>\n    self * 2\n'
    const shadow = '//@version=6\nlibrary("S")\nwidth(float value) =>\n    value\nexport band(float width) =>\n    width * 2\n'
    const script = (name: string) => `//@version=6\nindicator("x")\nimport someone/${name}/1 as l\nplot(close)\n`

    await expect(prepareScript(script('M'), libraries(['M', methods]))).rejects.toThrow('Methods in a library are not supported')
    await expect(prepareScript(script('S'), libraries(['S', shadow]))).rejects.toMatchObject({
      file: 'S',
      line: 5,
      message: expect.stringContaining('"width" names both'),
    })
  })

  it('renames a library type and its uses, not its fields', async () => {
    const shapes = `//@version=6
library("Shapes")

export type Box
    float top
    float bottom

export height(Box box) =>
    box.top - box.bottom
`
    const script = `//@version=6
indicator("x")
import someone/Shapes/1 as shapes
b = shapes.Box.new(high, low)
plot(shapes.height(b))
`
    const prepared = await prepareScript(script, libraries(['Shapes', shapes]))

    expect(prepared.source).toContain('type Shapes__Box\n    float top\n    float bottom')
    expect(prepared.source).toContain('Shapes__height(Shapes__Box box) =>\n    box.top - box.bottom')
    expect(prepared.source).toContain('b = Shapes__Box.new(high, low)')
  })

  it('leaves a script without imports as it was written', async () => {
    const script = '//@version=6\nindicator("x")\nplot(close)\n'

    const prepared = await prepareScript(script, libraries())

    expect(prepared.source).toBe(script)
    expect(prepared.libraries).toEqual([])
  })

  it('says where a declaration is missing', async () => {
    await expect(prepareScript('//@version=6\nplot(close)\n', libraries())).rejects.toBeInstanceOf(PrepareError)
  })
})

describe('prepareLibraryCheck', () => {
  it('checks a library inside an empty indicator, its lines mapped to the library itself', async () => {
    const prepared = await prepareLibraryCheck(RISK, 'Risk', libraries())

    expect(prepared.kind).toBe('library')
    expect(prepared.source).toContain('indicator("Risk (library check)")')
    expect(prepared.source).toContain('Risk__risky(simple int candles = 10, simple float multiplier = 2.0) =>')
    const line = prepared.source.split('\n').findIndex((text) => text.startsWith('Risk__risky'))
    expect(originOf(prepared, line + 1)).toEqual({ file: SCRIPT_FILE, line: 4 })
  })
})

// The live strategy's scripts, read where they live when they are on this machine
// (a sibling checkout of wata-tradingview-scripts, or WATA_TV_SCRIPTS), never copied
// into this repository: the real acceptance test of the inlining.
function tradingViewScripts(): string {
  if (process.env.WATA_TV_SCRIPTS) return process.env.WATA_TV_SCRIPTS
  let directory = process.cwd()
  for (let depth = 0; depth < 8; depth += 1) {
    const candidate = join(directory, 'wata-tradingview-scripts')
    if (existsSync(candidate)) return candidate
    directory = join(directory, '..')
  }
  return ''
}
const TV_SCRIPTS = tradingViewScripts()
const LIVE = join(TV_SCRIPTS, 'pinescripts', 'kernel-multi-vwap.pine')

describe.skipIf(!existsSync(LIVE))('the live strategy of wata-tradingview-scripts', () => {
  it('prepares with both its libraries inlined', async () => {
    const read = (path: string) => readFileSync(join(TV_SCRIPTS, 'pinescripts', path), 'utf8')
    const prepared = await prepareScript(
      read('kernel-multi-vwap.pine'),
      libraries(
        ['KernelFunctions', read('librairies/ioiti/KernelFunctions.pine')],
        ['CandleRiskActivity', read('librairies/ioiti/CandleRiskActivity.pine')],
      ),
    )

    expect(prepared.libraries.map((library) => library.name)).toEqual(['KernelFunctions', 'CandleRiskActivity'])
    expect(prepared.source).toContain('KernelFunctions__f_multi_kernel_regression(kernel_source, kernel_bandwidth, kernel_kernel_style)')
    expect(prepared.source).toContain('CandleRiskActivity__isRiskyCandle(')
  })
})

import { readdirSync, readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, join } from 'node:path'

import { parse } from 'acorn'
import { describe, expect, it } from 'vitest'

import { patchVela, VELA_CHUNK_MARK, VELA_SECONDS_PATCHES, velaSeconds } from './velaSeconds.ts'

/** The chunk of the installed Vela whose code the patches change. */
function velaChunk(): string {
  const require = createRequire(import.meta.url)
  const dist = dirname(require.resolve('@luxalgo/vela'))
  const chunks = readdirSync(dist)
    .filter((name) => name.startsWith('chunk-') && name.endsWith('.js'))
    .map((name) => readFileSync(join(dist, name), 'utf8'))
    .filter((code) => code.includes(VELA_CHUNK_MARK))
  expect(chunks).toHaveLength(1)
  return chunks[0]
}

// What the patched functions need, taken from the chunk itself: run as Vela runs them.
const NAMES = [
  'NAMED_TF_MS', 'timeframeToMs', 'tzOffsetMs', 'zonedDate', 'SEC', 'MIN', 'HOUR', 'DAY', 'WEEK', 'MONTH', 'YEAR',
  'STEP_LADDER', 'MONTHS', 'WEEKDAYS', 'pad2', 'formatTimeStamp', 'pickStep', 'timeTicks', 'calendarTicks', 'formatStampParts',
]

interface Vela {
  timeframeToMs: (timeframe: string) => number
  formatTimeStamp: (ms: number, timeZone: string, barIntervalMs: number) => string
  timeTicks: (fromMs: number, toMs: number, target?: number, offsetMs?: number) => { time: number; label: string }[]
  formatStampParts: (ms: number, timeZone: string, barIntervalMs?: number) => { date: string; time: string }
}

function functionsOf(code: string): Vela {
  const program = parse(code, { ecmaVersion: 'latest', sourceType: 'module' })
  const pieces = new Map<string, string>()
  for (const statement of program.body) {
    if (statement.type === 'FunctionDeclaration' && statement.id) pieces.set(statement.id.name, code.slice(statement.start, statement.end))
    if (statement.type === 'VariableDeclaration') {
      for (const declaration of statement.declarations) {
        if (declaration.id.type === 'Identifier') pieces.set(declaration.id.name, code.slice(statement.start, statement.end))
      }
    }
  }
  const missing = NAMES.filter((name) => !pieces.has(name))
  expect(missing).toEqual([])
  const body = `${NAMES.map((name) => pieces.get(name)).join('\n')}\nreturn { timeframeToMs, formatTimeStamp, timeTicks, formatStampParts }`
  return new Function(body)() as Vela
}

const AT = Date.UTC(2026, 9, 20, 9, 0, 0)

describe('Vela taught bars of seconds', () => {
  it('finds each change in the installed Vela once, and makes them all', () => {
    const code = velaChunk()

    const patched = patchVela(code)

    for (const patch of VELA_SECONDS_PATCHES) {
      expect(patched).toContain(patch.replace)
      expect(patched).not.toContain(patch.find)
    }
  })

  it('reads a timeframe of seconds as so many seconds, where it read an hour', () => {
    const before = functionsOf(velaChunk())
    const vela = functionsOf(patchVela(velaChunk()))

    expect(before.timeframeToMs('10S')).toBe(3_600_000)
    expect(['10S', '45S', '90s', '1', '60', '240', 'D', 'W'].map(vela.timeframeToMs)).toEqual([
      10_000, 45_000, 90_000, 60_000, 3_600_000, 14_400_000, 86_400_000, 604_800_000,
    ])
  })

  it('writes the seconds of a bar that is no whole minutes, at the crosshair and in the data window', () => {
    const vela = functionsOf(patchVela(velaChunk()))
    const at = AT + 45_000

    expect(vela.formatTimeStamp(at, 'UTC', 10_000)).toBe("Tue 20 Oct '26 09:00:45")
    expect(vela.formatTimeStamp(at, 'UTC', 90_000)).toBe("Tue 20 Oct '26 09:00:45")
    expect(vela.formatTimeStamp(AT, 'UTC', 60_000)).toBe("Tue 20 Oct '26 09:00")
    expect(vela.formatTimeStamp(AT, 'Europe/Paris', 86_400_000)).toBe("Tue 20 Oct '26")
    expect(vela.formatStampParts(AT, 'Europe/Paris', 10_000)).toEqual({ date: '2026-10-20', time: '11:00:00' })
    expect(vela.formatStampParts(AT, 'Europe/Paris', 60_000)).toEqual({ date: '2026-10-20', time: '11:00' })
    // Without the bar length (what is read aloud): the seconds when there are some.
    expect(vela.formatStampParts(at, 'UTC').time).toBe('09:00:45')
    expect(vela.formatStampParts(AT, 'UTC').time).toBe('09:00')
  })

  it('labels the time axis in seconds within a minute, and in minutes as before', () => {
    const vela = functionsOf(patchVela(velaChunk()))

    // Two minutes over eight ticks: a tick every fifteen seconds.
    expect(vela.timeTicks(AT, AT + 120_000).map((tick) => tick.label)).toEqual([
      '09:00', '09:00:15', '09:00:30', '09:00:45', '09:01', '09:01:15', '09:01:30', '09:01:45', '09:02',
    ])
    // Midnight is the day, whatever the step; its seconds are the time.
    const midnight = Date.UTC(2026, 9, 21)
    expect(vela.timeTicks(midnight, midnight + 30_000).map((tick) => tick.label).slice(0, 2)).toEqual(['Oct 21', '00:00:05'])
    expect(vela.timeTicks(AT, AT + 3_600_000).map((tick) => tick.label).slice(0, 3)).toEqual(['09:00', '09:15', '09:30'])
  })

  it('stops the build when Vela changed', () => {
    expect(() => patchVela('function timeframeToMs(timeframe) {}')).toThrow(
      '@luxalgo/vela changed: "timeframeToMs reads a number of seconds ("10S")" expects its code once, found 0 times',
    )
    const twice = `${VELA_SECONDS_PATCHES[0].find}\n${VELA_SECONDS_PATCHES[0].find}`
    expect(() => patchVela(twice, [VELA_SECONDS_PATCHES[0]])).toThrow('found 2 times')
  })

  it('changes Vela\'s chunk alone', () => {
    const plugin = velaSeconds()
    const transform = plugin.transform as (code: string, id: string) => { code: string } | null
    const code = velaChunk()

    expect(transform(code, '/app/node_modules/@luxalgo/vela/dist/chunk-UNUVPQFW.js?v=1')?.code).toBe(patchVela(code))
    expect(transform('export const nothing = 1', '/app/node_modules/@luxalgo/vela/dist/index.js')).toBeNull()
    expect(transform(code, '/app/src/features/ta/chart/TaChart.tsx')).toBeNull()
  })
})

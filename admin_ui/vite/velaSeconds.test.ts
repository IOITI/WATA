import { readdirSync, readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, join } from 'node:path'
import { pathToFileURL } from 'node:url'

import { createTranslator } from '@luxalgo/vela'
import { parse } from 'acorn'
import { describe, expect, it } from 'vitest'

import { SECONDS_FORMATTERS } from '../src/features/ta/chart/secondsFormatters.ts'
import { patchVela, VELA_CHUNK_MARK, VELA_SECONDS_PATCHES, velaSeconds } from './velaSeconds.ts'

function velaDist(): string {
  const require = createRequire(import.meta.url)
  return dirname(require.resolve('@luxalgo/vela'))
}

/** The chunks of the installed Vela that hold a piece of code. */
function chunksWith(piece: string): string[] {
  const dist = velaDist()
  return readdirSync(dist)
    .filter((name) => name.startsWith('chunk-') && name.endsWith('.js'))
    .filter((name) => readFileSync(join(dist, name), 'utf8').includes(piece))
}

/** The chunk of the installed Vela whose code the patches change. */
function velaChunk(): string {
  const chunks = chunksWith(VELA_CHUNK_MARK)
  expect(chunks).toHaveLength(1)
  return readFileSync(join(velaDist(), chunks[0]), 'utf8')
}

/** What the core chunk imports for its times: the zone's offset and the built-in formatters, from Vela's locale chunk. */
async function localeChunk(): Promise<{ zoneOffsetMs: unknown; ENGLISH_FORMAT: unknown }> {
  const chunks = chunksWith('function zoneOffsetMs(')
  expect(chunks).toHaveLength(1)
  return import(pathToFileURL(join(velaDist(), chunks[0])).href)
}

// What the functions tested need, taken from the chunk itself: run as Vela runs them.
const NAMES = [
  'NAMED_TF_MS', 'UNIT_MS', 'timeframeToMs', 'DAY_MS', 'displayZone', 'tzOffsetMs', 'zonedDate', 'SEC', 'MIN', 'HOUR', 'DAY',
  'WEEK', 'MONTH', 'YEAR', 'STEP_LADDER', 'formatTimeStamp', 'pickStep', 'timeTicks', 'calendarTicks', 'monthStart', 'yearStep',
  'formatStampParts',
]

type Format = ReturnType<typeof createTranslator>['format']

interface Vela {
  timeframeToMs: (timeframe: string) => number
  formatTimeStamp: (ms: number, timeZone: string, barIntervalMs: number, fmt?: Format) => string
  timeTicks: (fromMs: number, toMs: number, target?: number, offsetMs?: number, fmt?: Format) => { time: number; label: string }[]
  formatStampParts: (ms: number, timeZone: string, barIntervalMs?: number) => { date: string; time: string }
}

async function functionsOf(code: string): Promise<Vela> {
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
  const { zoneOffsetMs, ENGLISH_FORMAT } = await localeChunk()
  return new Function('zoneOffsetMs', 'ENGLISH_FORMAT', body)(zoneOffsetMs, ENGLISH_FORMAT) as Vela
}

/** What a chart of the pages formats its axis and crosshair with: Vela's, given SECONDS_FORMATTERS. */
const PAGES = createTranslator({ formatters: SECONDS_FORMATTERS }).format

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

  it('reads a timeframe of seconds as so many seconds by itself (since 0.10.5)', async () => {
    const vela = await functionsOf(velaChunk())

    expect(['10S', '45S', '90s', '1', '60', '240', 'D', 'W'].map(vela.timeframeToMs)).toEqual([
      10_000, 45_000, 90_000, 60_000, 3_600_000, 14_400_000, 86_400_000, 604_800_000,
    ])
  })

  it('writes the seconds of a bar that is no whole minutes at the crosshair, through the pages\' formatters', async () => {
    const vela = await functionsOf(velaChunk())
    const at = AT + 45_000

    // Vela alone stops at the minute.
    expect(vela.formatTimeStamp(at, 'UTC', 10_000)).toBe("Tue 20 Oct '26 09:00")
    expect(vela.formatTimeStamp(at, 'UTC', 10_000, PAGES)).toBe("Tue 20 Oct '26 09:00:45")
    expect(vela.formatTimeStamp(at, 'UTC', 90_000, PAGES)).toBe("Tue 20 Oct '26 09:00:45")
    expect(vela.formatTimeStamp(AT, 'UTC', 60_000, PAGES)).toBe("Tue 20 Oct '26 09:00")
    expect(vela.formatTimeStamp(AT, 'Europe/Paris', 86_400_000, PAGES)).toBe("Tue 20 Oct '26")
  })

  it('writes the seconds of a bar that is no whole minutes in the data window, once patched', async () => {
    const vela = await functionsOf(patchVela(velaChunk()))
    const at = AT + 45_000

    expect(vela.formatStampParts(AT, 'Europe/Paris', 10_000)).toEqual({ date: '2026-10-20', time: '11:00:00' })
    expect(vela.formatStampParts(AT, 'Europe/Paris', 60_000)).toEqual({ date: '2026-10-20', time: '11:00' })
    // Without the bar length (what is read aloud): the seconds when there are some.
    expect(vela.formatStampParts(at, 'UTC').time).toBe('09:00:45')
    expect(vela.formatStampParts(AT, 'UTC').time).toBe('09:00')
  })

  it('labels the time axis in seconds within a minute, and in minutes as before', async () => {
    const vela = await functionsOf(velaChunk())
    const labels = (from: number, to: number, fmt?: Format) => vela.timeTicks(from, to, 8, 0, fmt).map((tick) => tick.label)

    // Two minutes over eight ticks: a tick every fifteen seconds, which Vela alone writes to the minute.
    expect(labels(AT, AT + 120_000).slice(0, 3)).toEqual(['09:00', '09:00', '09:00'])
    expect(labels(AT, AT + 120_000, PAGES)).toEqual([
      '09:00', '09:00:15', '09:00:30', '09:00:45', '09:01', '09:01:15', '09:01:30', '09:01:45', '09:02',
    ])
    // Midnight is the day, whatever the step; its seconds are the time.
    const midnight = Date.UTC(2026, 9, 21)
    expect(labels(midnight, midnight + 30_000, PAGES).slice(0, 2)).toEqual(['Oct 21', '00:00:05'])
    expect(labels(AT, AT + 3_600_000, PAGES).slice(0, 3)).toEqual(['09:00', '09:15', '09:30'])
  })

  it('stops the build when Vela changed', () => {
    expect(() => patchVela('function formatStampParts(ms) {}')).toThrow(
      '@luxalgo/vela changed: "the data window writes the seconds of a bar that is no whole minutes" expects its code once, found 0 times',
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

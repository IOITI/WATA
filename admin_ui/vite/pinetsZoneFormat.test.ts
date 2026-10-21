import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { afterEach, describe, expect, it } from 'vitest'

import { PINETS_HELPER, PINETS_ZONE_PATCHES, patchPinets, pinetsZoneFormat } from './pinetsZoneFormat.ts'
import { extractWorkerSource, velaPinetsBundlePath } from './velaPineWorker.ts'

/** A build of the installed PineTS, by its file name. */
function pinetsBuild(name: string): string {
  const require = createRequire(import.meta.url)
  // The package gives require() its CommonJS build; the others sit beside it.
  return readFileSync(join(dirname(require.resolve('pinets')), name), 'utf8')
}

interface PineContext {
  plots: Record<string, { data: { time: number; value: number }[] }>
}

interface PineModule {
  PineTS: new (candles: unknown[]) => { run: (indicator: unknown) => Promise<PineContext> }
  Indicator: new (source: string) => unknown
}

/** PineTS's Node build, as it is or patched, imported from a file of its own (where the test runner imports from). */
async function pinetsOf(code: string): Promise<PineModule> {
  const folder = join(dirname(fileURLToPath(import.meta.url)), '..', 'node_modules', '.vite-temp')
  mkdirSync(folder, { recursive: true })
  const own = mkdtempSync(join(folder, 'pinets-'))
  const path = join(own, 'pinets.mjs')
  writeFileSync(path, code)
  try {
    return (await import(/* @vite-ignore */ path)) as PineModule
  } finally {
    rmSync(own, { recursive: true, force: true })
  }
}

const SCRIPT = `//@version=6
indicator("Time zones")
plot(hour(time, "Europe/Paris"), "Paris hour")
plot(minute(time, "Europe/Paris"), "Paris minute")
plot(dayofweek(time, "Europe/Paris"), "Paris weekday")
plot(hour(time, "America/New_York"), "New York hour")
plot(timestamp("Europe/Paris", year, month, dayofmonth, 8, 0, 0), "Paris 8:00")
plot(timestamp("America/New_York", year, month, dayofmonth, 9, 30, 0), "New York 9:30")
`

// Half-hour bars from six hours before to six hours after each clock change of 2026:
// Europe on 29 March and 25 October at 01:00 UTC, New York on 8 March at 07:00 and 1 November at 06:00.
const CHANGES = [Date.UTC(2026, 2, 8, 7), Date.UTC(2026, 2, 29, 1), Date.UTC(2026, 9, 25, 1), Date.UTC(2026, 10, 1, 6)]
const HALF_HOUR = 1_800_000
const CANDLES = CHANGES.flatMap((change) =>
  Array.from({ length: 24 }, (_, index) => {
    const openTime = change - 12 * HALF_HOUR + index * HALF_HOUR
    return { openTime, closeTime: openTime + HALF_HOUR, open: 100, high: 101, low: 99, close: 100, volume: 1 }
  }),
)

const TITLES = ['Paris hour', 'Paris minute', 'Paris weekday', 'New York hour', 'Paris 8:00', 'New York 9:30']

/** Each plot of the script by its title, its values by their bar's time (PineTS's drawings left out). */
async function plotsOf(pinets: PineModule): Promise<Record<string, Map<number, number>>> {
  const context = await new pinets.PineTS(CANDLES).run(new pinets.Indicator(SCRIPT))
  return Object.fromEntries(
    TITLES.map((title) => [title, new Map(context.plots[title].data.map((point) => [point.time, point.value]))]),
  )
}

const OriginalFormat = Intl.DateTimeFormat

/** How many formatters `run` makes. */
async function formattersMade<T>(run: () => Promise<T>): Promise<[T, number]> {
  let made = 0
  class Counted extends OriginalFormat {
    constructor(...parts: ConstructorParameters<typeof Intl.DateTimeFormat>) {
      super(...parts)
      made += 1
    }
  }
  Intl.DateTimeFormat = Counted as typeof Intl.DateTimeFormat
  try {
    return [await run(), made]
  } finally {
    Intl.DateTimeFormat = OriginalFormat
  }
}

describe('PineTS keeping a formatter per time zone', () => {
  afterEach(() => {
    Intl.DateTimeFormat = OriginalFormat
  })

  it('finds each change once in every copy the admin ships, and makes them all', () => {
    const copies = [
      pinetsBuild('pinets.min.es.js'),
      pinetsBuild('pinets.min.browser.es.js'),
      extractWorkerSource(readFileSync(velaPinetsBundlePath(), 'utf8')),
    ]

    for (const code of copies) {
      const patched = patchPinets(code)
      for (const patch of PINETS_ZONE_PATCHES) {
        expect(patched).toContain(patch.replace)
        expect(patched).not.toContain(patch.find)
      }
      expect(patched.endsWith(PINETS_HELPER)).toBe(true)
    }
  })

  it('gives every hour, minute, weekday and timestamp PineTS gave, around each clock change, making a few formatters', async () => {
    const build = pinetsBuild('pinets.min.es.js')
    const [before, madeBefore] = await formattersMade(async () => plotsOf(await pinetsOf(build)))
    const [after, madeAfter] = await formattersMade(async () => plotsOf(await pinetsOf(patchPinets(build))))

    for (const [title, values] of Object.entries(before)) {
      expect(values.size).toBe(CANDLES.length)
      expect([...after[title].entries()]).toEqual([...values.entries()])
    }
    // Before, at least one a bar for each call; after, one for each time zone and kind (a few made by PineTS itself aside).
    expect(madeBefore).toBeGreaterThan(5 * CANDLES.length)
    expect(madeAfter).toBeLessThan(20)

    // Read on Intl itself: Paris goes from summer to winter time at 01:00 UTC on 25 October...
    const autumn = CHANGES[2]
    expect(after['Paris hour'].get(autumn - HALF_HOUR)).toBe(2)
    expect(after['Paris hour'].get(autumn + HALF_HOUR)).toBe(2)
    expect(after['Paris minute'].get(autumn + HALF_HOUR)).toBe(30)
    // ... a Sunday (Pine counts from Sunday, 1), and 08:00 in Paris is 06:00 UTC the day before, 07:00 UTC that day.
    expect(after['Paris weekday'].get(autumn)).toBe(1)
    expect(after['Paris 8:00'].get(autumn - 6 * HALF_HOUR)).toBe(Date.UTC(2026, 9, 24, 6))
    expect(after['Paris 8:00'].get(autumn + 6 * HALF_HOUR)).toBe(Date.UTC(2026, 9, 25, 7))
    // New York at 06:00 UTC on 1 November: 01:30 twice, the second in winter time.
    const newYork = CHANGES[3]
    expect([after['New York hour'].get(newYork - HALF_HOUR), after['New York hour'].get(newYork + HALF_HOUR)]).toEqual([1, 1])
    expect(after['New York 9:30'].get(newYork + 6 * HALF_HOUR)).toBe(Date.UTC(2026, 10, 1, 14, 30))
  })

  it('stops the build when PineTS changed', () => {
    expect(() => patchPinets('export const nothing = 1')).toThrow(
      'pinets changed: "the parts of a moment in a time zone (hour(), minute(), dayofweek()...)" expects its code once, found 0 times',
    )
    const twice = `${PINETS_ZONE_PATCHES[0].find}\n${PINETS_ZONE_PATCHES[0].find}`
    expect(() => patchPinets(twice, [PINETS_ZONE_PATCHES[0]])).toThrow('found 2 times')
  })

  it("changes PineTS's builds alone", () => {
    const transform = pinetsZoneFormat().transform as (code: string, id: string) => { code: string } | null
    const code = pinetsBuild('pinets.min.browser.es.js')

    expect(transform(code, '/app/node_modules/pinets/dist/pinets.min.browser.es.js?v=1')?.code).toBe(patchPinets(code))
    expect(transform(code, '/app/src/features/ta/engine/workerCore.ts')).toBeNull()
    expect(transform('export const nothing = 1', '/app/node_modules/pinets/package.json')).toBeNull()
  })
})

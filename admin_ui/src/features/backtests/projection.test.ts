import { describe, expect, it } from 'vitest'

import type { ProjectionDay, ProjectionSkipped } from '@/lib/types'

import {
  chartPlaces,
  dayOfPlace,
  daysOff,
  daysSentence,
  fanPoints,
  geometricMean,
  horizonIndex,
  leftOutSentence,
  markers,
  mulberry32,
  nextAccount,
  simulate,
  startNote,
  tickLabel,
} from './projection'

/** Every weekday from ``from`` to ``to``, a few closing early. */
function weekdays(from: string, to: string, early: string[] = []): ProjectionDay[] {
  const days: ProjectionDay[] = []
  for (let day = new Date(`${from}T12:00:00Z`); day.toISOString().slice(0, 10) <= to; day.setUTCDate(day.getUTCDate() + 1)) {
    if (day.getUTCDay() === 0 || day.getUTCDay() === 6) continue
    const iso = day.toISOString().slice(0, 10)
    days.push(early.includes(iso) ? { day: iso, kind: 'early_close', estimated: false, name: 'Early', close_new_york: '13:00' } : { day: iso, kind: 'full', estimated: false })
  }
  return days
}

const DAYS = weekdays('2026-11-30', '2028-11-29', ['2026-12-24', '2027-11-26'])

describe('the paths', () => {
  it('are the same for the same seed and other for another', () => {
    const input = { days: DAYS, growths: [1.02, 0.99, 1.01, 0.97, 1.03], earlyGrowths: [1.005], seed: 7 }
    const once = simulate(input, 1000, null, 200)
    const again = simulate(input, 1000, null, 200)
    const other = simulate({ ...input, seed: 8 }, 1000, null, 200)

    expect(again.horizons).toEqual(once.horizons)
    expect(other.horizons[0].levels).not.toEqual(once.horizons[0].levels)
    const random = mulberry32(1)
    const drawn = Array.from({ length: 1000 }, random)
    expect(Math.min(...drawn)).toBeGreaterThanOrEqual(0)
    expect(Math.max(...drawn)).toBeLessThan(1)
  })

  it('follow the run’s one day exactly when it has only one', () => {
    const simulated = simulate({ days: DAYS.slice(0, 10), growths: [1.01], earlyGrowths: [], seed: 1 }, 1000, null, 10)

    expect(simulated.pace[10]).toBeCloseTo(1000 * 1.01 ** 10, 6)
    expect(simulated.levels.get(10)).toEqual(Array(5).fill(simulated.pace[10]))
    expect(simulated.horizons[0].belowStart).toBe(0)
  })

  it('take an early close’s day from the mornings', () => {
    const days = DAYS.slice(0, 20)
    const simulated = simulate({ days, growths: [1.01], earlyGrowths: [1], seed: 1 }, 1000, null, 5)
    const christmasEve = days.findIndex((day) => day.day === '2026-12-24') + 1

    expect(simulated.pace[christmasEve]).toBeCloseTo(simulated.pace[christmasEve - 1], 9)
    expect(simulated.pace[christmasEve + 1]).toBeCloseTo(simulated.pace[christmasEve] * 1.01, 9)
  })

  it('grow by the cap’s result past the cap, and compound under it', () => {
    expect(nextAccount(1000, 1.1, null)).toBeCloseTo(1100)
    expect(nextAccount(5000, 1.1, 2000)).toBeCloseTo(5200)
    expect(nextAccount(1000, 1.1, 2000)).toBeCloseTo(1100)
    expect(nextAccount(100, 0, null)).toBe(0)

    const capped = simulate({ days: DAYS.slice(0, 40), growths: [1.05], earlyGrowths: [], seed: 1 }, 1000, 2000, 5)
    const steps = capped.pace.slice(-5).map((value, index, all) => (index === 0 ? null : value - all[index - 1])).slice(1)
    for (const step of steps) expect(step).toBeCloseTo(100, 6)
  })

  it('say how many ended under the start and how deep they fell', () => {
    const simulated = simulate({ days: DAYS, growths: [1.1, 0.5], earlyGrowths: [], seed: 3 }, 1000, null, 500)
    const [threeMonths] = simulated.horizons

    expect(threeMonths.belowStart).toBeGreaterThan(0.9)
    expect(threeMonths.fallMedian).toBeGreaterThan(50)
    expect(threeMonths.fallBad).toBeGreaterThanOrEqual(threeMonths.fallMedian)
    expect(geometricMean([1.1, 0.5])).toBeCloseTo(Math.sqrt(0.55), 9)
    expect(geometricMean([1.1, 0])).toBe(0)
  })
})

describe('the horizons', () => {
  it('count the trading days within so many months', () => {
    expect(horizonIndex(DAYS, '2026-11-30', 24)).toBe(DAYS.length)
    const threeMonths = horizonIndex(DAYS, '2026-11-30', 3)
    expect(DAYS[threeMonths - 1].day <= '2027-02-27').toBe(true)
    expect(DAYS[threeMonths].day >= '2027-02-28').toBe(true)
    // From a 31st, a month on is its shorter month's last day.
    expect(horizonIndex(weekdays('2027-01-29', '2027-03-31'), '2027-01-31', 1)).toBe(weekdays('2027-01-29', '2027-02-27').length)
  })

  it('are drawn on about as many places, the first and the last kept', () => {
    expect(chartPlaces(63)).toEqual(Array.from({ length: 64 }, (_, place) => place))
    const twoYears = chartPlaces(504)
    expect(twoYears[0]).toBe(0)
    expect(twoYears.at(-1)).toBe(504)
    expect(twoYears.length).toBeLessThan(140)
  })

  it('give the chart its points in money or in percent of the start', () => {
    const simulated = simulate({ days: DAYS, growths: [1.01], earlyGrowths: [], seed: 1 }, 2000, null, 10)
    const [horizon] = simulated.horizons
    const money = fanPoints(simulated, horizon, 'money')
    const percent = fanPoints(simulated, horizon, 'percent')

    expect(money[0]).toMatchObject({ x: 0, p5: 2000, pace: 2000 })
    expect(percent[0]).toMatchObject({ x: 0, p5: 0, pace: 0 })
    expect(percent.at(-1)?.pace).toBeCloseTo((1.01 ** horizon.index - 1) * 100, 6)
  })
})

describe('the words and the axis', () => {
  it('read the day of a place, rounded and kept within the days', () => {
    expect(dayOfPlace(DAYS, '2026-11-30', 0)).toBe('2026-11-30')
    expect(dayOfPlace(DAYS, '2026-11-30', 1.4)).toBe(DAYS[0].day)
    expect(dayOfPlace(DAYS, '2026-11-30', 9_999)).toBe(DAYS.at(-1)?.day)
    expect(tickLabel('2027-03-03', 24)).toBe('Mar 2027')
  })

  it('mark the years and where the holidays are estimated', () => {
    const days = DAYS.map((day) => (day.day >= '2028-01-03' ? { ...day, estimated: true } : day))
    const simulated = simulate({ days, growths: [1], earlyGrowths: [], seed: 1 }, 1000, null, 2)
    const twoYears = simulated.horizons[3]

    expect(markers(days, twoYears, '2028-01-03').map((mark) => mark.label)).toEqual(['2027', '2028', 'Holidays estimated from here'])
    expect(markers(days, simulated.horizons[0], '2028-01-03')).toEqual([])
  })

  it('say the days counted, the days off and the days left out', () => {
    const skipped: ProjectionSkipped[] = [
      { day: '2026-12-25', why: 'us_closed', name: 'Christmas Day', estimated: false },
      { day: '2027-01-01', why: 'us_closed', name: 'New Year’s Day', estimated: false },
      { day: '2027-07-05', why: 'us_closed', name: 'Independence Day (observed)', estimated: false },
    ]
    const simulated = simulate({ days: DAYS, growths: [1], earlyGrowths: [], seed: 1 }, 1000, null, 2)
    const [threeMonths] = simulated.horizons

    expect(daysSentence(DAYS, skipped, threeMonths)).toMatch(/^\d+ trading days to .*, 1 closes early, drawn as mornings, 2 weekdays off/)
    expect(daysOff(DAYS, skipped, threeMonths).map((day) => day.day)).toEqual(['2026-12-24', '2026-12-25', '2027-01-01'])
    expect(leftOutSentence([])).toBeNull()
    expect(
      leftOutSentence([
        { day: '2026-11-26', why: 'us_closed', name: 'Thanksgiving Day', estimated: false },
        { day: '2026-12-01', why: 'after_guard', name: null, estimated: false },
        { day: '2026-12-02', why: 'after_guard', name: null, estimated: false },
      ]),
    ).toBe('Left out of its pace: 1 day a US holiday, traded at nothing (Thanksgiving Day, 26 Nov); 2 days after the account guard paused its entries.')
  })

  it('warn of a start far from the run’s own capital', () => {
    expect(startNote(1000, 1000)).toBeNull()
    expect(startNote(1999, 1000)).toBeNull()
    expect(startNote(21.82, 1000)).toMatch(/^The run started with/)
    expect(startNote(10_000, 1000)).toMatch(/Make the run again/)
  })
})

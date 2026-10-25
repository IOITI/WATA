import { describe, expect, it } from 'vitest'

import type { TaHistory } from '@/lib/types'

import { engineDays, historyHelp, historyRequest, historySentence, lastRestart } from './history'

const DRAFT = { epic: 'US100', side: 'bid' as const, barsType: 'time' as const, timeframe: '1', ticks: '60', historyDays: '' }

/** One-minute bars of US100, a day before each restart; the week's engine days end on Fri 16 to Thu 22 Oct, Friday's last minutes on Saturday's. */
const HISTORY: TaHistory = {
  asset: { provider: 'capital', key: 'US100' },
  side: 'bid',
  bars: { type: 'time', timeframe: '1' },
  history_days: 1,
  restart_time: '21:15',
  restarted_at: '2026-10-22T21:15:00+00:00',
  starts_at: '2026-10-21T21:15:00+00:00',
  at_restart: 1374,
  now: 1412,
  engine_days: [1380, 45, 0, 1380, 1378, 1380, 1374].map((bars, index) => ({
    start: new Date(Date.UTC(2026, 9, 15 + index, 21, 15)).toISOString(),
    end: new Date(Date.UTC(2026, 9, 16 + index, 21, 15)).toISOString(),
    bars,
  })),
}

describe('the days of bars before each day', () => {
  it('ask the count of the bars the rule would run on, once its fields are a rule’s', () => {
    expect(historyRequest(DRAFT)).toEqual({ epic: 'US100', side: 'bid', timeframe: '1' })
    // A length typed as people write it goes as Pine names it; the days only when the rule has its own.
    expect(historyRequest({ ...DRAFT, timeframe: '2m', historyDays: '3' })).toEqual({ epic: 'US100', side: 'bid', timeframe: '2', days: 3 })
    expect(historyRequest({ ...DRAFT, timeframe: '45s', side: 'ask', historyDays: '0' })).toEqual({
      epic: 'US100', side: 'ask', timeframe: '45S', days: 0,
    })
    expect(historyRequest({ ...DRAFT, barsType: 'range' })).toEqual({ epic: 'US100', side: 'bid', ticks: 60 })
    for (const wrong of [
      { timeframe: '7s' },
      { barsType: 'range' as const, ticks: '0' },
      { barsType: 'range' as const, ticks: '1.5' },
      { historyDays: '31' },
      { historyDays: '-1' },
      { epic: '' },
    ]) {
      expect(historyRequest({ ...DRAFT, ...wrong })).toBeNull()
    }
  })

  it('say when the engine starts the rule again, in the trading timezone as it is that day', () => {
    expect(lastRestart('21:15', new Date('2026-10-25T21:15:00Z'))).toEqual(new Date('2026-10-25T21:15:00Z'))
    expect(lastRestart('21:15', new Date('2026-10-25T21:14:59Z'))).toEqual(new Date('2026-10-24T21:15:00Z'))

    // The next restart: on the evening of 24 October Paris is still on summer time, on the 25th no longer.
    const summer = historyHelp('21:15', 1, 'Europe/Paris', new Date('2026-10-24T12:00:00Z'))
    const winter = historyHelp('21:15', 0, 'Europe/Paris', new Date('2026-10-25T12:00:00Z'))

    expect(summer).toEqual([
      'Each day at 23:15 (21:15 UTC) the engine starts the rule again on the bars of these days before: it runs the ' +
        'script over them once, without sending any signal, then adds each new bar. The script runs on these days of ' +
        'bars just after the restart, and on one day more by the next one.',
      'A day is 24 hours counted back from the restart: a weekend or a holiday counts, and brings no bar. More days give ' +
        'its indicators a longer warm-up, and cost more: the engine runs the script over all of its bars at the restart and ' +
        'every ten minutes to check its run (at each new bar when the script cannot continue its run), and holds them in ' +
        'memory. Left empty: the engine’s history_days, 1 day.',
    ])
    expect(winter[0]).toMatch(/^Each day at 22:15 \(21:15 UTC\) /)
    expect(winter[1]).toMatch(/Left empty: the engine’s history_days, 0 days\.$/)
  })

  it('say how many bars the script was started on at the last restart, and would run on now', () => {
    expect(historySentence(HISTORY, 'Europe/Paris')).toBe(
      'The script was started on 1,374 bars at the last restart (Thu 22 Oct, 23:15), and would run on 1,412 bars now.',
    )
    // Restarted on a Sunday evening: the day before it was the weekend's.
    const sunday = { ...HISTORY, restarted_at: '2026-10-25T21:15:00+00:00', at_restart: 0, now: 38 }
    expect(historySentence(sunday, 'Europe/Paris')).toBe(
      'The script was started on no bar at the last restart (Sun 25 Oct, 22:15): the day before it had none. It would run on 38 bars now.',
    )
    expect(historySentence({ ...sunday, history_days: 2 }, 'Europe/Paris')).toMatch(/: the 2 days before it had none\./)
    expect(historySentence({ ...sunday, history_days: 0, now: 1 }, 'Europe/Paris')).toBe(
      'With no day before, the script starts at each restart on no bar, and would run on 1 bar now, made since the last one (Sun 25 Oct, 22:15).',
    )
  })

  it('name each engine day of the week by the day it ends on', () => {
    expect(engineDays(HISTORY, 'Europe/Paris').map((day) => [day.label, day.bars])).toEqual([
      ['Fri 16', '1,380'], ['Sat 17', '45'], ['Sun 18', '0'], ['Mon 19', '1,380'], ['Tue 20', '1,378'], ['Wed 21', '1,380'], ['Thu 22', '1,374'],
    ])
  })
})

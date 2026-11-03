import { describe, expect, it } from 'vitest'

import { PrepareError, prepareScript } from './prepare'
import type { LibrarySource } from './prepare'
import { liveProbe, ruleCheckOf, verdictOf, withAlerts } from './ruleCheck'
import type { RuleCheck } from './ruleCheck'
import { wholeBecause } from './wholeRuns'

// Made up in the shape of LuxAlgo's "Market Structure (Breakers)": its breaks drawn, no alert.
const BREAKS = `//@version=6
indicator("Breaks", overlay = true, max_lines_count = 500)
length = input.int(20, 'Swings Period', minval = 2)
var phx = 0
var phcross = false
n = bar_index
ph = fixnan(ta.pivothigh(length, length))
if ph != ph[1]
    phx := n - length
    phcross := false
breakUp = false
if close > ph and not phcross
    line.new(phx, ph, n, ph)
    phcross := true
    breakUp := true
pl = fixnan(ta.pivotlow(length, length))
breakDown = close < pl
plotchar(breakUp, 'Bull Break', '▲', location.belowbar, #089981)
plotchar(breakDown, 'Bear Break', '▼', location.abovebar, #f23645)
plotchar(ph, 'Value', '', location.top)
plotcandle(high, high, low, low
  , 'Support Events'
  , na
  , na
  , bordercolor = breakUp ? #089981 : na)
`

// And of its "Breaker Blocks with Signals": signals only over the last bars.
const LAST_BARS = `//@version=6
indicator("Last bars")
per = last_bar_index - bar_index <= 2000
up = ta.crossover(ta.ema(close, 9), ta.ema(close, 21))
signals = array.from(false, false)
if up and per
    signals.set(0, true)
alertcondition(signals.get(0), 'Long', 'Long')
`

const DASHBOARD = `//@version=6
indicator("Dashboard")
fast = ta.ema(close, 9)
slow = ta.ema(close, 21)
var table board = table.new(position.top_right, 2, 2)
if barstate.islast
    table.cell(board, 0, 0, str.tostring(fast))
alertcondition(ta.crossover(fast, slow), 'Long', '{"action": "long"}')
alertcondition(ta.crossunder(fast, slow), 'Short', '{"action": "short"}')
`

const STRATEGY = `//@version=6
strategy("Cross")
longCondition = ta.crossover(ta.sma(close, 14), ta.sma(close, 28))
if longCondition
    strategy.entry("Long", strategy.long)
`

const SERIES = `//@version=6
indicator("Other series")
ahead = request.security("CAPITAL_US100", "60", close, lookahead = barmerge.lookahead_on)
hour1 = request.security("CAPITAL_US100", "60", close[1], lookahead = barmerge.lookahead_on)
dukascopy = request.security("DUKASCOPY_US100", "5", close)
alertcondition(close > ahead, 'Above', 'Above')
alertcondition(close > dukascopy and close > hour1, 'Above both', 'Above both')
`

const SERVED = `//@version=6
indicator("Served")
tick = syminfo.mintick
inSession = not na(time(timeframe.period, "0930-1600"))
up = close - open > 10 * tick and inSession and volume > 0
plot(up ? 1 : 0, "Up")
alertcondition(up, 'Up', '{"action": "long", "confidence": {{plot("Conf")}}}')
alertcondition(up, 'Up', 'again')
alertcondition(close > open, str.format("{0}", 1), 'x')
`

const ids = (check: RuleCheck) => check.findings.map((finding) => `${finding.level} ${finding.id}`)
const said = (check: RuleCheck, id: string) => check.findings.find((finding) => finding.id === id)

describe('the rule check of a script, read without running it', () => {
  it('says a script that only draws its events needs alerts, and offers them from what it draws', () => {
    const check = ruleCheckOf(BREAKS)

    expect(check.verdict).toBe('needs_alerts')
    expect(ids(check)).toEqual(['blocks no-alerts', 'note pivot-lag'])
    expect(said(check, 'no-alerts')?.message).toBe(
      'Nothing in it can become a signal: it has no alertcondition() and no alert(). It draws 2 events an alert could be raised on: “Bull Break” and “Bear Break”.',
    )
    expect(said(check, 'pivot-lag')?.message).toBe(
      'It finds swings with ta.pivothigh(), each known `length` bars after it: a signal from one comes that late, live as in a backtest, while what it draws is drawn back at the swing.',
    )
    // plotchar(value, title, '') only shows a value; the support candles' colour is the same condition as a break's.
    expect(check.candidates.map(({ title, condition, call }) => ({ title, condition, call }))).toEqual([
      { title: 'Bull Break', condition: 'breakUp', call: 'plotchar' },
      { title: 'Bear Break', condition: 'breakDown', call: 'plotchar' },
    ])
    expect(check.plots).toEqual(['Bull Break', 'Bear Break', 'Value', 'Support Events'])
  })

  it('writes each alert right after the plot it is read from', () => {
    const check = ruleCheckOf(BREAKS)
    const written = withAlerts(BREAKS, check.candidates).split('\n')

    expect(written.slice(17, 21)).toEqual([
      "plotchar(breakUp, 'Bull Break', '▲', location.belowbar, #089981)",
      'alertcondition(breakUp, "Bull Break", "Bull Break") // added for a signal rule',
      "plotchar(breakDown, 'Bear Break', '▼', location.abovebar, #f23645)",
      'alertcondition(breakDown, "Bear Break", "Bear Break") // added for a signal rule',
    ])
    expect(ruleCheckOf(written.join('\n')).verdict).toBe('ready')
    expect(withAlerts(BREAKS, [])).toBe(BREAKS)
  })

  it('takes an order or an alert() under an if for an event, written before the if that may change it', () => {
    const strategy = ruleCheckOf(STRATEGY)
    expect(strategy.verdict).toBe('needs_alerts')
    expect(said(strategy, 'strategy')?.message).toBe('A strategy(): its orders are not signals, and it has no alert to raise one.')
    expect(withAlerts(STRATEGY, strategy.candidates, ['Long']).split('\n').slice(2, 5)).toEqual([
      'longCondition = ta.crossover(ta.sma(close, 14), ta.sma(close, 28))',
      'alertcondition(longCondition, "Long 2", "Long 2") // added for a signal rule',
      'if longCondition',
    ])

    const alerts = ruleCheckOf(`//@version=6
indicator("Alerts")
bull = ta.crossover(close, ta.ema(close, 20))
if bull
    alert('Bull cross', alert.freq_once_per_bar)
`)
    expect(alerts.verdict).toBe('needs_alerts')
    expect(ids(alerts)).toEqual(['blocks alert-messages', 'note alert-freq'])
    expect(said(alerts, 'alert-messages')?.message).toBe(
      'Its alert() calls send “Bull cross”: as messages they make no signal, WATA\'s being JSON such as {"action": "long"}.',
    )
    expect(alerts.candidates.map(({ title, condition, at }) => ({ title, condition, at }))).toEqual([
      { title: 'Bull cross', condition: 'bull', at: { line: 4, where: 'before' } },
    ])
  })

  it('reads the condition a shape or a colour is drawn on', () => {
    const check = ruleCheckOf(`//@version=6
indicator("Shapes")
bull = close > open
plotshape(bull ? low : na, "Bull", shape.triangleup, location.absolute)
bgcolor(close < open ? color.new(color.red, 80) : na, title = "Bear")
`)
    expect(check.candidates.map(({ title, condition }) => ({ title, condition }))).toEqual([
      { title: 'Bull', condition: 'bull' },
      { title: 'Bear', condition: 'close < open' },
    ])
  })

  it('gives two events drawn under one title a title each', () => {
    const check = ruleCheckOf(`//@version=6
indicator("Exits")
exitShort = close > open
exitAndRisk = high - low > 10
plotshape(exitShort, title = 'Exit Short', location = location.belowbar)
plotshape(exitAndRisk, title = 'Exit Short', location = location.belowbar)
`)
    expect(check.candidates.map(({ title, condition }) => ({ title, condition }))).toEqual([
      { title: 'Exit Short', condition: 'exitShort' },
      { title: 'Exit Short 2', condition: 'exitAndRisk' },
    ])
  })

  it('follows what may repaint to the alerts, through what carries it', () => {
    const check = ruleCheckOf(LAST_BARS)

    expect(check.verdict).toBe('may_repaint')
    expect(ids(check)).toEqual(['risk bar-end', 'note whole-runs'])
    expect(said(check, 'bar-end')).toMatchObject({
      message: 'Its alerts read last_bar_index: the current bar live, the engine day\'s last bar in a backtest. A window such as `last_bar_index - bar_index <= 2000` hides early signals from backtests.',
      fix: 'Replace such a window by `true`, or by an input.',
      places: [{ file: null, line: 3, column: 7 }],
      excerpt: 'per = last_bar_index - bar_index <= 2000',
      through: ['per', 'signals'],
    })
  })

  it('only notes what its drawings alone read', () => {
    const check = ruleCheckOf(DASHBOARD)

    expect(check.verdict).toBe('ready')
    expect(ids(check)).toEqual(['note bar-state', 'note whole-runs'])
    expect(said(check, 'bar-state')?.message).toBe('Reads barstate.islast for its drawings only: its alerts do not depend on it.')
    expect(said(check, 'whole-runs')?.message).toBe(
      'Each new bar runs it whole: its script reads barstate.islast. That is slower than going on from the bar before; the run test measures what a run costs.',
    )
  })

  it('says alerts read on the clock repaint: no run made later can see them', () => {
    const check = ruleCheckOf(`//@version=6
indicator("Clock")
fresh = timenow - time < 60000
alertcondition(fresh and close > open, 'Now', 'Now')
`)
    expect(check.verdict).toBe('repaints')
    expect(said(check, 'clock')).toMatchObject({ level: 'repaints', through: ['fresh'] })
  })

  it('tells another series read ahead, read live as it is, and one WATA has only in backtests', () => {
    const check = ruleCheckOf(SERIES)

    expect(check.verdict).toBe('blocked')
    expect(ids(check)).toEqual(['repaints lookahead', 'risk other-series', 'blocks live-ticker', 'note whole-runs'])
    expect(said(check, 'lookahead')?.places.map((place) => place.line)).toEqual([3])
    expect(said(check, 'other-series')?.message).toBe(
      'Its alerts read another series (request.security): live, it holds only bars that are over, so a bar of a longer timeframe still open is missing (na), where a backtest has the whole day\'s.',
    )
    expect(said(check, 'other-series')?.places.map((place) => place.line)).toEqual([4, 5])
    expect(said(check, 'live-ticker')?.message).toBe(
      'Reads "DUKASCOPY_US100": WATA\'s backtests have it, but live a rule reads only Capital.com\'s series, and would fail.',
    )
  })

  it('says what WATA serves otherwise, and what a rule cannot map', () => {
    const check = ruleCheckOf(SERVED)

    expect(check.verdict).toBe('to_change')
    expect(ids(check)).toEqual([
      'change mintick', 'change utc-time', 'note volume', 'change title-expression', 'change title-duplicate', 'change message-plot',
    ])
    expect(said(check, 'mintick')).toMatchObject({
      message: 'Its alerts read syminfo.mintick, na on WATA: whatever they compute from it is na too.',
      through: ['tick', 'up'],
    })
    expect(said(check, 'utc-time')?.message).toBe(
      'Its alerts read the bar\'s time or day with no time zone (time()): WATA\'s days are UTC days, so it is read in UTC, not in the exchange\'s zone.',
    )
    expect(said(check, 'volume')?.message).toBe(
      'Its alerts read volume: on WATA, the count of price changes in each ten seconds, 0 on many bars, not traded volume.',
    )
    expect(said(check, 'title-duplicate')?.message).toBe('2 alertconditions are titled “Up”: a rule maps them alike, and live sends only one of them a bar.')
    expect(said(check, 'message-plot')?.message).toBe('A message reads {{plot("Conf")}}, and no plot is titled so: it is left as it is.')

    const zoned = ruleCheckOf(`//@version=6
indicator("Zoned")
inSession = not na(time(timeframe.period, "0930-1600", "America/New_York")) and hour(time, "America/New_York") < 16
alertcondition(inSession and close > open, 'Up', 'Up')
`)
    expect(zoned.verdict).toBe('ready')
  })

  it('refuses a library, a Pine WATA does not take, and says a library it lacks', () => {
    expect(ids(ruleCheckOf('//@version=6\nlibrary("Tools")\nexport f(x) => x * 2\n'))).toEqual(['blocks library'])
    const old = ruleCheckOf('//@version=4\nstudy("Old")\nplot(close)\n')
    expect(old).toMatchObject({ verdict: 'blocked', pineVersion: null })
    expect(said(old, 'version')?.message).toBe('Pine Script version 4 is not supported: versions 5 and 6 are.')

    const missing = ruleCheckOf(DASHBOARD, null, new PrepareError('No library "ta" in WATA: create it on the Indicators page', 'script', 3, 1))
    expect(missing.verdict).toBe('blocked')
    expect(said(missing, 'library-missing')?.places).toEqual([{ file: null, line: 3, column: 1 }])
  })

  it("finds what a library reads at the library's own line, and follows it through its function", async () => {
    const library = '//@version=6\nlibrary("Gate")\nexport gate(bool cond) => cond and barstate.isrealtime\n'
    const script = '//@version=6\nindicator("Uses gate")\nimport user/Gate/1 as g\nup = g.gate(close > open)\nalertcondition(up, \'Up\', \'Up\')\n'
    const resolve = async (name: string): Promise<LibrarySource | null> =>
      name === 'Gate' ? { id: 'g1', name: 'Gate', version: 1, source: library } : null
    const check = ruleCheckOf(script, await prepareScript(script, resolve))

    expect(check.verdict).toBe('may_repaint')
    expect(said(check, 'bar-state')).toMatchObject({
      places: [{ file: 'Gate', line: 3 }],
      through: ['gate', 'up'],
      excerpt: 'gate(bool cond) => cond and barstate.isrealtime',
    })
  })

  it('names what makes each new bar run the script whole by the engine\'s own words', () => {
    const whole = new Set(['bar-state', 'isconfirmed', 'bar-end', 'clock', 'random', 'visible-range', 'other-series', 'lookahead', 'no-data', 'strategy'])
    const sources = [
      BREAKS, LAST_BARS, DASHBOARD, STRATEGY, SERIES, SERVED,
      '//@version=6\nindicator("a")\nalertcondition(barstate.isconfirmed and close > open, "A", "A")\n',
      '//@version=6\nindicator("b")\nx = request.financial(syminfo.tickerid, "EPS", "FQ")\nalertcondition(close > x, "B", "B")\n',
      '//@version=6\nindicator("c")\nalertcondition(chart.left_visible_bar_time < time, "C", "C")\n',
      '//@version=6\nindicator("d")\nalertcondition(math.random() > 0.5 and session.islastbar, "D", "D")\n',
      '//@version=6\nindicator("e")\n// barstate.islast in a comment\nalertcondition(barstate.isfirst or close > open, "E \\"barstate.islast\\"", "E")\n',
    ]
    for (const source of sources) {
      const check = ruleCheckOf(source)
      const runsWhole = wholeBecause({ source, kind: check.kind ?? 'indicator' }) !== null
      expect(check.findings.some((finding) => whole.has(finding.id)), source).toBe(runsWhole)
    }
  })

  it('puts the worst first in its verdict', () => {
    expect(verdictOf([{ id: 'no-alerts', level: 'blocks' }, { id: 'clock', level: 'repaints' }])).toBe('needs_alerts')
    expect(verdictOf([{ id: 'no-alerts', level: 'blocks' }, { id: 'ticker', level: 'blocks' }])).toBe('blocked')
    expect(verdictOf([{ id: 'clock', level: 'repaints' }, { id: 'bar-end', level: 'risk' }])).toBe('repaints')
    expect(verdictOf([{ id: 'bar-end', level: 'risk' }, { id: 'mintick', level: 'change' }])).toBe('may_repaint')
    expect(verdictOf([{ id: 'mintick', level: 'change' }, { id: 'varip', level: 'note' }])).toBe('to_change')
    expect(verdictOf([{ id: 'varip', level: 'note' }])).toBe('ready')
  })
})

describe('the probe of the run test', () => {
  it('reads every bar as the last of its run, as live reads each new bar, and nothing else', () => {
    expect(liveProbe(`//@version=6
indicator("Live")
// barstate.islast in a comment stays
up = ta.crossover(close, open) and barstate.isrealtime and not barstate.ishistory
per = last_bar_index - bar_index <= 50 and time > last_bar_time - 60000
label = "barstate.islast"
alertcondition(up and per and session.islastbar, "Long", "Long")
`)).toBe(`//@version=6
indicator("Live")
// barstate.islast in a comment stays
up = ta.crossover(close, open) and true and not false
per = bar_index - bar_index <= 50 and time > time - 60000
label = "barstate.islast"
alertcondition(up and per and true, "Long", "Long")
`)
    // What changes nothing on WATA, or nothing a run's end gives otherwise, is left as written.
    expect(liveProbe('//@version=6\nindicator("x")\nalertcondition(barstate.isconfirmed and barstate.isfirst, "A", "A")\n')).toBeNull()
  })
})

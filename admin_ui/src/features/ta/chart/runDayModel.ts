// A day of a backtest on Vela, with the script that made its signals (RunDayChart.tsx): the bars the
// script ran on, the inputs it was given, and the run's own layer, its signals and trades, handed to
// the chart as the model of a host engine (runMarksEngine.ts). Pure, so that it is tested alone.

import type { Background, DataProvider, DrawingLabel, InputValue, OHLCV, TradeExecution } from '@luxalgo/vela'

import { signalMarkers, tradeFocus } from '@/features/backtests/dayChart'
import { zoneOffsetSeconds } from '@/features/prices/chartData'
import type { Locator, MarkerTone } from '@/features/prices/chartData'
import type { BacktestDayIndicator, BacktestSignal, BacktestTrade, TaRuleInput } from '@/lib/types'

import { parseTicker, tickerText } from '../engine/tickers'
import type { ProviderId } from '../engine/tickers'
import { TIMEFRAMES, timeframeOf } from '../engine/timeframes'

type Row = BacktestDayIndicator['bars'][number]

/** The colours of the run's layer, read from the theme's tokens by the page. */
export type RunColors = Record<MarkerTone, string> & { warmUp: string }

/** What the host engine draws: the run's signals as labels, its trades as Vela's trade markers. */
export interface RunLayer {
  title: string
  /** Its name in Vela's legend: the run's unless said (a rule's page says "The rule"). */
  short?: string
  labels: DrawingLabel[]
  trades: TradeExecution[]
  backgrounds: Background[]
}

/** A layer made on the bars it is drawn on: the run's (runLayer), or a rule's journal on its page. */
export type LayerOf = (rows: readonly Row[], colors: RunColors, dayStart: number) => RunLayer

export function velaBars(rows: readonly Row[]): OHLCV[] {
  return rows.map(([time, open, high, low, close, volume]) => ({ time, open, high, low, close, volume }))
}

/**
 * The bar a moment (epoch seconds) belongs to, by its open time in seconds: the one whose span holds
 * it, or the first after it when it falls between two (a pause of the feed), as Vela places a mark;
 * null outside the bars. Labels are placed between bars otherwise.
 */
export function barLocator(rows: readonly Row[]): Locator {
  return (instant) => {
    const moment = instant * 1000
    if (rows.length === 0 || moment < rows[0][0] || moment >= rows[rows.length - 1][6]) return null
    let low = 0
    let high = rows.length - 1
    // The last bar that opened at or before the moment.
    while (low < high) {
      const middle = Math.ceil((low + high) / 2)
      if (rows[middle][0] <= moment) low = middle
      else high = middle - 1
    }
    const bar = rows[low]
    if (moment < bar[6] || low === rows.length - 1) return bar[0] / 1000
    return rows[low + 1][0] / 1000
  }
}

/**
 * The run's inputs as Vela takes them. By the input's variable name when it has one: Vela keeps a
 * script's values under it (a value under the title is lost then), and finds it in another version
 * of the script too. Else by its id (in_0...) on the version it was set for alone; otherwise left out.
 */
export function inputOverrides(
  inputs: readonly TaRuleInput[],
  setFor: number | null,
  drawn: number,
): { values: Record<string, InputValue>; left: string[] } {
  const values: Record<string, InputValue> = {}
  const left: string[] = []
  for (const input of inputs) {
    if (input.var) values[input.var] = input.value
    else if (setFor === drawn) values[input.id] = input.value
    else left.push(input.title ?? input.id)
  }
  return { values, left }
}

function instant(iso: string | null | undefined): number | null {
  if (!iso) return null
  const value = Date.parse(iso) / 1000
  return Number.isFinite(value) ? value : null
}

const ACTION_WORD: Record<string, string> = { long: 'Long', short: 'Short', 'close-position': 'Close' }

/** What a signal's mark says on hover: its number, action, moment and what became of it. */
function signalTip(signal: BacktestSignal): string {
  const action = signal.source === 'schedule' ? 'The close of the day' : (ACTION_WORD[signal.action ?? ''] ?? signal.action ?? 'An alert')
  const fate = signal.fate === 'executed' ? `bought${signal.trade_id ? ` (#${signal.trade_id})` : ''}` : signal.fate.replaceAll('_', ' ')
  const why = signal.reason ? `: ${signal.reason}` : ''
  return `#${signal.id} ${action}${signal.alert?.title ? ` (${signal.alert.title})` : ''}, ${fate}${why}`
}

/**
 * The run's layer on the bars it is drawn on: each signal at its bar (the arrows, sides, texts and
 * tones of the day page's chart), each trade as a purchase and a sale at the asset's price when it
 * was filled (the bar's close when unknown), and the days of history before the day shaded.
 */
export function runLayer(
  signals: readonly BacktestSignal[],
  trades: readonly BacktestTrade[],
  rows: readonly Row[],
  colors: RunColors,
  dayStart: number,
): RunLayer {
  const locate = barLocator(rows)
  const tips = new Map(signals.map((signal) => [`signal-${signal.id}`, signalTip(signal)]))
  const labels: DrawingLabel[] = signalMarkers(signals, locate).map((marker) => ({
    id: marker.id,
    paneId: 'unrouted',
    xloc: 'bar_time',
    x: marker.time * 1000,
    y: 0,
    yloc: marker.position === 'belowBar' ? 'belowbar' : 'abovebar',
    text: marker.text,
    style: marker.shape === 'arrowUp' ? 'arrowup' : marker.shape === 'arrowDown' ? 'arrowdown' : 'circle',
    color: colors[marker.tone],
    textColor: colors[marker.tone],
    size: 'small',
    textAlign: 'center',
    fontFamily: 'default',
    tooltip: tips.get(marker.id),
  }))

  const closeAt = new Map(rows.map((row) => [row[0], row[4]]))
  const executions: TradeExecution[] = []
  for (const trade of trades) {
    const tradeId = String(trade.id)
    const opened = instant(trade.entry.filled_at)
    const openBar = opened === null ? null : locate(opened)
    if (openBar !== null) {
      executions.push({
        time: openBar * 1000,
        price: trade.entry.asset_price ?? closeAt.get(openBar * 1000)!,
        side: trade.direction === 'long' ? 'buy' : 'sell',
        kind: 'entry',
        label: `#${trade.id} ${trade.direction}`,
        tradeId,
      })
    }
    const closed = instant(trade.exit.filled_at)
    const closeBar = closed === null ? null : locate(closed)
    if (closeBar !== null) {
      const result = `${trade.performance_percent > 0 ? '+' : ''}${trade.performance_percent.toFixed(2)} %`
      executions.push({
        time: closeBar * 1000,
        price: trade.exit.asset_price ?? closeAt.get(closeBar * 1000)!,
        side: trade.direction === 'long' ? 'sell' : 'buy',
        kind: 'exit',
        label: result,
        tradeId,
      })
    }
  }

  const first = rows[0]?.[0]
  const backgrounds: Background[] =
    first !== undefined && first < dayStart
      ? [{ id: 'warm-up', paneId: 'unrouted', from: first, to: dayStart, color: colors.warmUp }]
      : []
  return { title: 'The run: signals and trades', labels, trades: executions, backgrounds }
}

/** The start of a day in a timezone, epoch ms: its local midnight. */
export function dayStartIn(day: string, timeZone: string): number {
  const utcMidnight = Date.parse(`${day}T00:00:00Z`) / 1000
  return (utcMidnight - zoneOffsetSeconds(utcMidnight, timeZone)) * 1000
}

/** What the chart opens on: the day's bars, or a trade with a quarter of an hour either side. */
export function visibleRangeOf(
  rows: readonly Row[],
  dayStart: number,
  trade: BacktestTrade | null,
): { from: number; to: number } | null {
  if (trade) {
    const focus = tradeFocus(trade, barLocator(rows))
    if (focus) return { from: focus.from * 1000, to: focus.to * 1000 }
  }
  const ofDay = rows.filter((row) => row[0] >= dayStart && row[0] < dayStart + 86_400_000)
  if (ofDay.length === 0) return null
  return { from: ofDay[0][0], to: ofDay[ofDay.length - 1][0] }
}

/** The symbol the chart loads: the provider's name, then the ticker the replay handed the runner. */
export function runSymbol(answer: Pick<BacktestDayIndicator, 'chart' | 'ticker'>): string {
  return `${answer.chart.asset.provider}:${answer.ticker}`
}

/**
 * The chart's data, registered under each provider's name: the run's ticker served from the bars its
 * script ran on, whatever Vela asks; another asset (a script's request.security) from `others`, held
 * to the same window (not built from the window's start as the replay builds it: the page says so).
 * Every symbol's info is what the engine's runner gives PineTS (engine/provider.ts): a ticker and no
 * time zone, so that a script's days are UTC's here as they were for the run.
 */
export function runProvider(
  answer: Pick<BacktestDayIndicator, 'chart' | 'ticker' | 'timeframe' | 'bars' | 'window'>,
  others: Partial<Record<ProviderId, DataProvider>>,
  onOther: (ticker: string) => void = () => undefined,
): DataProvider {
  const own = parseTicker(answer.ticker)
  const ownSeconds = timeframeOf(answer.timeframe)?.seconds
  const bars = velaBars(answer.bars)
  const anchor = Date.parse(answer.window.anchor)
  const end = Date.parse(answer.window.end)
  const isOwn = (text: string, timeframe: string) => {
    const asked = parseTicker(text)
    return (
      asked !== null && own !== null && tickerText(asked) === tickerText(own) && asked.provider === own.provider &&
      (own.rangeTicks !== null || timeframeOf(timeframe)?.seconds === ownSeconds)
    )
  }
  return {
    info: () => ({
      name: answer.chart.asset.provider,
      displayName: 'The run',
      supportedTimeframes: TIMEFRAMES.map((length) => length.pine),
      capabilities: { enumerate: false, stream: false, symbolInfo: true },
    }),
    async getBars(text, timeframe, range) {
      if (isOwn(text, timeframe)) {
        // Vela's first load asks for what it shows (the day, a trade): the script runs on every bar it gets,
        // so it gets them all. Only a later ask for the bars before or after some is answered with those.
        if (range.from !== undefined && range.to === undefined) return bars.filter((bar) => bar.time >= range.from!)
        if (range.to !== undefined && range.from === undefined) return bars.filter((bar) => bar.time <= range.to!)
        return bars
      }
      const asked = parseTicker(text)
      const other = asked ? others[asked.provider] : undefined
      if (!other) return []
      onOther(asked ? tickerText(asked) : text)
      const from = Math.max(range.from ?? anchor, anchor)
      const to = Math.min(range.to ?? end, end)
      return from > to ? [] : other.getBars(text, timeframe, { from, to })
    },
    async getSymbolInfo(text) {
      const asked = parseTicker(text)
      const ticker = asked ? tickerText(asked) : text
      return { ticker, tickerid: ticker, description: ticker }
    },
  }
}

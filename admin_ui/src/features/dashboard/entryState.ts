import { formatDuration, formatPercent } from '@/lib/format'
import type { Overview } from '@/lib/types'

export type EntryBlockerCode =
  | 'paused'
  | 'saxo_login'
  | 'closed_date'
  | 'hours'
  | 'risky_hours'
  | 'macro_news'
  | 'day_target'
  | 'day_stop'
  | 'trade_limit'
  | 'cooldown'

export interface EntryBlocker {
  code: EntryBlockerCode
  /** Why a signal that would open a position is refused right now. */
  reason: string
}

function clock(iso: string, timeZone: string): string {
  return new Intl.DateTimeFormat('en-GB', { timeZone, hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(iso))
}

function hourAndMinute(now: number, timeZone: string): [number, number] {
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone,
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).formatToParts(new Date(now))
  const read = (type: string) => Number(parts.find((part) => part.type === type)?.value ?? Number.NaN)
  // Midnight comes out as "24" in some engines.
  return [read('hour') % 24, read('minute')]
}

/**
 * What holds back a long or short signal at this moment, the operator's own
 * pause first. The same conditions as the trader's rules (src/trade/rules.py),
 * on the figures the dashboard already shows: keep the two in step.
 *
 * Not looked at here, because they depend on the signal itself: its age, its
 * confidence, its indice, and whether a position is already open in its direction.
 * The window around a US release comes from the server (``news``), which reads
 * the calendar the trader reads.
 */
export function entryBlockers(overview: Overview, now: number): EntryBlocker[] {
  const { limits, day, cooldown } = overview
  const blockers: EntryBlocker[] = []

  if (overview.entries_paused_since !== null) {
    blockers.push({ code: 'paused', reason: 'Paused by the operator' })
  }

  // The trader's own refusal while Saxo refuses to renew its login (its heartbeat, through the server).
  const failingSince = overview.saxo_login?.renewal_failing_since
  if (failingSince) {
    blockers.push({
      code: 'saxo_login',
      reason: `Saxo refuses to renew WATA's login since ${clock(failingSince, overview.timezone)}: no new position until it is renewed`,
    })
  }

  const marketDay = overview.market_day ?? null
  if (overview.market_closed_today) {
    blockers.push({
      code: 'closed_date',
      reason: marketDay?.kind === 'closed' ? `The US market is closed today (${marketDay.name})` : 'Today is a closed date',
    })
  } else if (limits.trading_start_hour !== null && limits.trading_end_hour !== null) {
    // The hours of market_hours, in the timezone it names (the trading one otherwise).
    const zone = limits.market_timezone || overview.timezone
    const [hour, minute] = hourAndMinute(now, zone)
    const inZone = zone === overview.timezone ? '' : ` ${zone.split('/').pop()?.replace('_', ' ')}`
    if (!(limits.trading_start_hour <= hour && hour < limits.trading_end_hour)) {
      blockers.push({
        code: 'hours',
        reason: `Outside trading hours (${limits.trading_start_hour}h to ${limits.trading_end_hour}h${inZone})`,
      })
    } else if (
      limits.risky_trading_start_hour !== null &&
      limits.risky_trading_start_minute !== null &&
      hour * 60 + minute >= limits.risky_trading_start_hour * 60 + limits.risky_trading_start_minute
    ) {
      blockers.push({ code: 'risky_hours', reason: 'Too close to the end of the trading day' })
    } else if (marketDay?.kind === 'early_close' && marketDay.entries_end_at && Date.parse(marketDay.entries_end_at) <= now) {
      blockers.push({
        code: 'risky_hours',
        reason: `The US market closes early today (${marketDay.name}, ${clock(marketDay.closes_at ?? marketDay.entries_end_at, overview.timezone)}): no entry from ${clock(marketDay.entries_end_at, overview.timezone)}`,
      })
    }
  }

  const news = overview.news?.window
  if (news && Date.parse(news.start) <= now && now < Date.parse(news.end)) {
    blockers.push({
      code: 'macro_news',
      reason: `${news.event} at ${clock(news.released_at, overview.timezone)}: no entry until ${clock(news.end, overview.timezone)}`,
    })
  }

  if (limits.stop_entries_above_percent !== null && day.performance_percent >= limits.stop_entries_above_percent) {
    blockers.push({
      code: 'day_target',
      reason: `Day performance reached ${formatPercent(limits.stop_entries_above_percent, true)}: no new entry today`,
    })
  }
  if (limits.max_day_loss_percent !== null && day.performance_percent <= limits.max_day_loss_percent) {
    blockers.push({
      code: 'day_stop',
      reason: `Day loss reached ${formatPercent(limits.max_day_loss_percent, true)}: no new entry today`,
    })
  }

  if (limits.max_trades_per_day > 0 && day.trade_count >= limits.max_trades_per_day) {
    blockers.push({ code: 'trade_limit', reason: `${limits.max_trades_per_day} trades today: the daily limit` })
  }

  if (limits.cooldown_after_loss_minutes > 0 && cooldown.ends_at) {
    const remaining = new Date(cooldown.ends_at).getTime() - now
    if (remaining > 0) {
      blockers.push({ code: 'cooldown', reason: `Cooldown after a loss: ${formatDuration(remaining)} left` })
    }
  }

  return blockers
}

/** The window around a US release still to come today, said before it starts; null when there is none. */
export function upcomingNews(overview: Overview, now: number): string | null {
  const news = overview.news?.window
  if (!news || Date.parse(news.start) <= now) return null
  const zone = overview.timezone
  return `No entry around ${news.event} (${clock(news.released_at, zone)}) from ${clock(news.start, zone)} to ${clock(news.end, zone)}`
}

/** The directions in which a position is open: a signal in one of them is refused as a duplicate. */
export function openDirections(overview: Overview): string[] {
  const directions = overview.open_positions.map((position) => position.action).filter((action) => action !== null)
  return [...new Set(directions)]
}

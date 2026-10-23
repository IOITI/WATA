/**
 * What the Engine monitor's routes answer (src/admin/ta_monitor.py), for the tests of its page:
 * a live rule and one of Claude's in shadow.
 */

import type { TaMonitor, TaMonitorNow, TaMonitorRule } from '@/lib/types'

export const NOW: TaMonitorNow = {
  checked_at: '2026-10-22T10:00:00Z',
  window_minutes: 15,
  recording: { days: 7, configured_days: 7, since: '2026-10-15T08:00:00Z', written_at: '2026-10-22T09:59:57Z', waiting: 0, dropped: 0, error: null },
  limits: {
    tick_ms: 10_000, run_timeout_ms: 30_000, max_signal_age_ms: 60_000, stale_feed_ms: 90_000, share: 0.5, memory_share: 0.85,
    slow_ticks: 3, runner_memory_mb: 512, shadow_runner_memory_mb: 256, shadow_ms_per_tick: 5000, shadow_day_ms: 2000,
  },
  last_tick: {
    tick_at: '2026-10-22T09:59:51.5Z', ended_at: '2026-10-22T09:59:51.7Z', milliseconds: 182, read_ms: 4, rows_read: 6, write_ms: 3,
    covered_until: '2026-10-22T09:59:39Z', rules_on: 2, runs: 2, loads: 0, failed: 0, deferred_rules: [], skipped: 0, restarted: false,
    error: null, runner_starts: 3, shadow_runner_starts: 2, live_rss_mb: 214.3, live_peak_mb: 260.1, shadow_rss_mb: 150, shadow_peak_mb: 190,
    engine_rss_mb: 92.4, memory_used_mb: 521.7, memory_limit_mb: 1024, oom_kills: 0, feed_lag_ms: 12_500,
  },
  window: {
    ticks: 90, tick_p50_ms: 140, tick_max_ms: 3800, steady_tick_max_ms: 820, slow_ticks: 0, feed_lag_max_ms: 13_400,
    memory_used_max_mb: 530.2, memory_limit_mb: 1024, oom_kills: 0, deferred: 4, runs: 105, checks: 2, checks_differed: 0, failed: 0, ended: 0,
    live_age_max_ms: 1580, live_delay_max_ms: 1620, live_signals: 2, live_not_sent: 0,
  },
  slowest_run: { rule_id: 'aaaaaaaaaaaa', name: 'EMA cross', ms: 312, limit_ms: 30_000 },
  checks: [
    { id: 'sent', label: 'Live signals sent', state: 'pass', detail: '2 live signals, none refused.' },
    { id: 'runs', label: 'Runs within their limits', state: 'fail', detail: 'A run of “EMA cross” took 16 s, over half of its limit (30 s).' },
    { id: 'memory', label: 'Memory', state: 'open', detail: 'The container’s memory cannot be read here.' },
  ],
  notes: ['“Claude · cross” waited 4 ticks for the shadow rules’ share of a tick (5 s): its bars waited with it, none was lost.'],
}

export function monitorRule(changes: Partial<TaMonitorRule> = {}): TaMonitorRule {
  return {
    rule_id: 'aaaaaaaaaaaa', name: 'EMA cross', mode: 'live', made_by: null, bars: { type: 'time', timeframe: '1' }, limit_ms: 30_000, runs: 1380, loads: 1,
    continued: 1380, checks: 144, differed: 0, check_ms_max: 2900, last_difference: null, failed: 0,
    ended: 0, deferred: 0, asks: 0, run_ms: { p50: 41, p95: 80, max: 312 }, pinets_ms_avg: 36, load_ms_max: 2900, bars_max: 2760,
    delay_ms: { p50: 1610, p95: 1720, max: 3200 }, age_max_ms: 1580, last_run_at: '2026-10-22T09:59:51Z', last_failure: null,
    signals: { raised: 5, sent: 4, not_sent: 1, shadow: 0 },
    ...changes,
  }
}

/** A day of the Engine monitor: a live rule and one of Claude's in shadow, a signal delivered, a position. */
export function monitorPeriod(changes: Partial<TaMonitor> = {}): TaMonitor {
  return {
    hours: 24,
    periods: [1, 6, 24, 72, 168],
    bucket_seconds: 600,
    start: '2026-10-21T10:00:00Z',
    end: '2026-10-22T10:00:00Z',
    rule: null,
    recording: NOW.recording,
    limits: NOW.limits,
    rules: [
      monitorRule(),
      monitorRule({
        rule_id: 'bbbbbbbbbbbb', name: 'Claude · cross', mode: 'shadow', made_by: 'claude', bars: { type: 'time', timeframe: '10S' }, limit_ms: 10_000, runs: 8100,
        deferred: 412, failed: 1, last_failure: "Undeclared identifier 'slow' (script, line 12)", run_ms: { p50: 640, p95: 1100, max: 7200 },
        signals: { raised: 12, sent: 0, not_sent: 0, shadow: 12 },
      }),
    ],
    series: {
      ticks: [
        {
          t: 1792058400, ticks: 60, tick_p50_ms: 120, tick_max_ms: 800, slow: 0, feed_lag_p50_ms: 11_500, feed_lag_max_ms: 13_200,
          live_rss_mb: 214, shadow_rss_mb: 151, engine_rss_mb: 92, memory_used_mb: 522, memory_limit_mb: 1024, restarted: false, deferred: 3,
        },
      ],
      runs: [{ t: 1792058400, runs: 70, loads: 0, failed: 0, run_p50_ms: 40, run_p95_ms: 61, run_max_ms: 70, delay_p50_ms: 1600, delay_max_ms: 2100 }],
      signals: [{ t: 1792058400, sent: 1, not_sent: 0, shadow: 2 }],
      marks: [{ t: 1792098900, kind: 'daily_restart', label: 'Daily restart' }],
    },
    delivery: {
      rule: null,
      sent: 4,
      steps: [
        { step: 'close_to_tick', label: "Bar close → the engine's tick", total: false, signals: 4, p50: 1500, p95: 1580, max: 1610 },
        { step: 'run', label: "The script's run", total: false, signals: 4, p50: 41, p95: 60, max: 62 },
        { step: 'order_to_saxo', label: 'Order left → Saxo accepted it', total: false, signals: 3, p50: 300, p95: 410, max: 420 },
        { step: 'sent_to_decided', label: "Sent → the trader's outcome, without an order", total: false, signals: 0, p50: null, p95: null, max: null },
        { step: 'close_to_position', label: 'Bar close → position confirmed', total: true, signals: 3, p50: 4400, p95: 5100, max: 5200 },
      ],
      engine: [
        { key: 'sent', label: 'Sent', count: 4 },
        { key: 'not_sent', label: 'Not sent', count: 1 },
        { key: 'sending', label: 'Never completed (the engine stopped as it sent)', count: 0 },
        { key: 'close_with_entry', label: 'Closes on a bar that raised an entry (by design)', count: 2 },
        { key: 'shadow', label: 'In shadow (journaled, never sent)', count: 12 },
      ],
      refusals: [{ key: 'too_old', label: 'Its bar closed too long before', count: 1, example: 'The bar closed 75 s ago, more than 60 s' }],
      shadow_late: 0,
      trader: [
        { key: 'executed', label: 'Executed', reason: null, count: 3 },
        { key: 'refused', label: 'Refused', reason: 'Max trades per day reached', count: 1 },
      ],
    },
    results: [{ rule_id: 'aaaaaaaaaaaa', positions: 3, open: 1, won: 1, lost: 1, profit_loss: 12.46, average_percent: 0.812 }],
    ...changes,
  }
}

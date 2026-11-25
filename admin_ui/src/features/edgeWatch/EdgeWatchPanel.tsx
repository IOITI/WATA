import { Alert, AlertDescription, AlertTitle, Badge, FanChart, formatDay, formatPercent, KpiStrip, Panel, QueryState } from 'ioiti-ui'
import { Link } from 'react-router'

import type { EdgeOwner, EdgeTrack } from '@/lib/types'

import {
  drawdownText,
  fanPoints,
  mainTrack,
  progressText,
  rankText,
  sampleText,
  streakText,
  testShare,
  VERDICT_LABEL,
  VERDICT_TONE,
  verdictLine,
} from './edgeWatch'
import { useEdgeWatch, useEdgeWatchOwner } from './useEdgeWatch'

const percent = (value: number) => formatPercent(value, true)

/** The verdict's reasons as one sentence. */
function reasonsText(track: EdgeTrack): string {
  const said = track.reasons.join('; ')
  return said ? `${said[0].toUpperCase()}${said.slice(1)}.` : ''
}

/** How unusual a track's trades are: judged, its result, its deepest fall, its test. Also a forward check's. */
export function TrackFigures({ track }: { track: EdgeTrack }) {
  const test = testShare(track)
  return (
    <KpiStrip
      columns={4}
      items={[
        { label: 'Trades judged', value: track.n, hint: progressText(track) },
        { label: 'Result', value: percent(track.cumulative), hint: rankText(track) ?? undefined },
        { label: 'Deepest fall', value: percent(track.drawdown), hint: drawdownText(track) ?? undefined },
        {
          label: 'Test',
          value: test === null ? '—' : `${Math.round(test * 100)} %`,
          hint:
            test === null
              ? 'No test: its reference has no edge over random entries to watch.'
              : track.alarm_at !== null
                ? `Past its threshold at trade ${track.alarm_at}: trades like the reference’s get there 5 % of the time at most.`
                : `Of its threshold: at 100 %, its trades look like random entries’ rather than the reference’s. ${streakText(track) ?? ''}`,
        },
      ]}
    />
  )
}

/** What may make the verdict mean less than it says. */
function Flags({ owner, track }: { owner: EdgeOwner; track: EdgeTrack }) {
  const flags: string[] = []
  const sample = sampleText(owner.model?.reference.sample)
  if (sample) flags.push(sample)
  if (owner.model?.reference.h1 === 'zero') {
    flags.push('Its reference has no random entries: the test is against an edge of zero, which takes longer to tell.')
  }
  if (track.settings_differ.length > 0) {
    flags.push(
      `The live settings are not its reference’s (${track.settings_differ.slice(0, 4).join(', ')}): make the reference again under them${
        owner.main === 'real' ? '; the edge guard does not act meanwhile' : ''
      }.`,
    )
  }
  if (track.stale_days && track.stale_days.length > 0) {
    flags.push(`${track.stale_days.length} day${track.stale_days.length === 1 ? '' : 's'} on paper made with older backtest code: made again once the market is closed.`)
  }
  if (owner.reference?.stale) flags.push('Its reference was made with older backtest code: make it again.')
  if (flags.length === 0) return null
  return (
    <ul className="list-disc space-y-1 pl-5 text-xs text-muted-foreground">
      {flags.map((flag) => (
        <li key={flag}>{flag}</li>
      ))}
    </ul>
  )
}

/** A track's path over the cone of its reference's paths. Also a forward check's, over its candidate's. */
export function TrackCone({ cone, track, label }: { cone: EdgeOwner['cone']; track: EdgeTrack; label: string }) {
  const data = fanPoints(cone, track)
  if (!cone || data.length < 2) return null
  return (
    <FanChart
      aria-label={`${label} against where its reference’s paths went`}
      data={data}
      bands={[
        { lower: 'p5', upper: 'p95', label: '90 % of the paths' },
        { lower: 'p25', upper: 'p75', label: 'Half of the paths' },
      ]}
      lines={[
        { key: 'median', label: 'Expected (median)', dashed: true },
        { key: 'actual', label },
      ]}
      references={[{ value: 0 }]}
      markers={track.alarm_at !== null ? [{ x: track.alarm_at, label: 'Broken' }] : []}
      xLabel="Trades since its reference"
      formatX={(trades) => (trades === 0 ? 'Start' : `After trade ${trades}`)}
      formatXTick={(trades) => String(trades)}
      formatValue={percent}
      formatTick={(value) => `${Math.round(value)}%`}
      height={240}
    />
  )
}

/** The guard's state, for real trades: the live trades', or a live rule's. */
function Guard({ owner }: { owner: EdgeOwner }) {
  if (!owner.tracks.real || !owner.guard) return null
  return (
    <p className="text-xs text-muted-foreground">
      {owner.guard.on
        ? `The edge guard is on: a broken verdict on ${owner.guard.least_trades} real trades or more pauses new entries until you resume them.`
        : 'The edge guard is off (Configuration › Pauses, trades and breakers): a broken verdict is said, never acted on.'}
    </p>
  )
}

/** What a live rule without a reference of its own is told: how to get one, and who judges its real trades meanwhile. */
function NoRuleReference({ ruleId, liveRule, liveJudges }: { ruleId: string; liveRule: boolean; liveJudges: boolean }) {
  if (!liveRule) {
    return (
      <>
        No reference run: once a run made from this rule is set as its reference (
        <a className="font-medium text-foreground underline-offset-4 hover:underline" href="#paper">
          On paper
        </a>
        ), Edge Watch judges its paper trades against it.
      </>
    )
  }
  return (
    <>
      No reference run: neither its real trades nor its paper trades are judged against a backtest of its own, and the
      edge guard cannot act on them.{' '}
      <Link className="font-medium text-foreground underline-offset-4 hover:underline" to={`/backtests/new?rule=${ruleId}`}>
        Make a run from the rule
      </Link>
      , then “Set as the rule’s reference” on its page.
      {liveJudges &&
        ' Meanwhile its real trades are judged with WATA’s other real trades, against the live reference: a replay of the signals WATA received, maybe of another strategy.'}
    </>
  )
}

/**
 * Edge Watch of one owner: the live trades (``live``), or a signal rule: its paper trades and, live, its real trades,
 * against its reference run. Its verdict and why, the figures that say how unusual its trades are, and the cone of
 * its reference's paths with its own path over it.
 */
export function EdgeWatchPanel({ owner: ownerId, enabled, className, liveRule = false }: {
  owner: string
  enabled: boolean
  className?: string
  /** The rule is live: its real trades are what is judged, once it has a reference. */
  liveRule?: boolean
}) {
  const query = useEdgeWatchOwner(ownerId, enabled)
  // Whether the live reference judges a live rule's real trades while it has none of its own.
  const list = useEdgeWatch(enabled && liveRule)
  if (!enabled) return null
  const owner = query.data
  const track = owner ? mainTrack(owner) : undefined
  const live = ownerId === 'live'
  const real = owner?.main === 'real'
  const label = real ? 'Real trades' : 'Paper trades'

  return (
    <Panel
      id="edge-watch"
      className={className}
      title="Edge Watch: broken, or just unlucky?"
      description={
        live
          ? 'WATA’s real trades since the live reference, set against where that backtest’s trades went when its days are drawn again, and against what random entries made of the same days.'
          : real || liveRule
            ? 'Its real trades since it went live and since its reference, set against where that backtest’s trades went when its days are drawn again, and against what random entries made of the same days; its paper trades beside them.'
            : 'Its paper trades since its reference, set against where that backtest’s trades went when its days are drawn again, and against what random entries made of the same days.'
      }
      action={track ? <Badge tone={VERDICT_TONE[track.verdict]}>{VERDICT_LABEL[track.verdict]}</Badge> : undefined}
    >
      <QueryState isPending={query.isPending} error={query.error} hasData={query.data !== undefined}>
        {owner === null || !owner || !track ? (
          <p className="text-sm text-muted-foreground">
            {live ? (
              <>
                No live reference: on the page of a replay of the signals WATA received, “Set as the live reference”
                makes it the backtest the live trades are judged against.
              </>
            ) : (
              <NoRuleReference ruleId={ownerId} liveRule={liveRule} liveJudges={Boolean(list.data?.live)} />
            )}
          </p>
        ) : (
          <div className="space-y-4">
            {track.verdict === 'broken' && (
              <Alert tone="danger">
                <AlertTitle>{verdictLine(track)}</AlertTitle>
                <AlertDescription>{reasonsText(track)}</AlertDescription>
              </Alert>
            )}
            {track.verdict !== 'broken' && <p className="text-sm">{reasonsText(track)}</p>}
            <p className="text-xs text-muted-foreground">
              Judged against{' '}
              {owner.reference ? (
                <Link className="font-medium text-foreground underline-offset-4 hover:underline" to={`/backtests/${owner.reference.run_id}`}>
                  {owner.reference.name ?? 'its reference'}
                </Link>
              ) : (
                'its reference'
              )}
              {owner.model?.reference ? `, ${owner.model.reference.trades} trades over ${owner.model.reference.days} days` : ''}
              {real && track.since
                ? `; real trades since ${formatDay(track.since)}`
                : owner.since
                  ? `; trades since ${formatDay(owner.since)}`
                  : ''}
              .
            </p>
            {owner.model?.usable && <TrackFigures track={track} />}
            <TrackCone cone={owner.cone} track={track} label={label} />
            <Flags owner={owner} track={track} />
            {live && owner.tracks.paper && owner.tracks.paper.n > 0 && (
              <p className="text-xs text-muted-foreground">
                The signals received, on paper: {verdictLine(owner.tracks.paper).toLowerCase()} — what the trader would have had
                while entries are paused.
              </p>
            )}
            {!live && real && owner.tracks.paper && (
              <p className="text-xs text-muted-foreground">
                On paper since {owner.since ? formatDay(owner.since) : 'its reference'}: {verdictLine(owner.tracks.paper).toLowerCase()}.
              </p>
            )}
            <Guard owner={owner} />
          </div>
        )}
      </QueryState>
    </Panel>
  )
}

// A script's runs (its page's Backtests tab): every version's, or one version's, in the table of the
// Backtests page with its verdicts. A run of the newest version is asked from the page's header.

import { useQuery } from '@tanstack/react-query'
import { QueryState, Select } from 'ioiti-ui'
import { useMemo } from 'react'
import { useSearchParams } from 'react-router'

import { isActive } from '@/features/backtests/model'
import { RunsTable } from '@/features/backtests/RunsTable'
import { judgedRuns } from '@/features/backtests/verdict'
import { api } from '@/lib/api'
import type { TaScriptSummary } from '@/lib/types'

import { versionOf } from './scriptPageModel'

const tagHref = (tag: string) => `/backtests?tag=${encodeURIComponent(tag)}`

export function ScriptBacktestsTab({ script }: { script: TaScriptSummary }) {
  const [params, setParams] = useSearchParams()
  const version = versionOf(params.get('version'), script)
  const runsQuery = useQuery({
    queryKey: ['backtests', 'script', script.id],
    queryFn: () => api.scriptBacktests(script.id),
    // A run that waits or runs changes state by itself: follow it closely.
    refetchInterval: (query) => (query.state.data?.items.some((run) => isActive(run.status.state)) ? 2_000 : 30_000),
  })
  const runs = runsQuery.data?.items
  // Judged with every run of the script: a stress of a run is a run of its script too.
  const judged = useMemo(() => judgedRuns(runs ?? []), [runs])
  const rows = useMemo(
    () => (version === null ? (runs ?? []) : (runs ?? []).filter((run) => run.source.script?.version === version)),
    [runs, version],
  )
  const counts = useMemo(() => {
    const found = new Map<number, number>()
    for (const run of runs ?? []) {
      const number = run.source.script?.version
      if (number !== undefined) found.set(number, (found.get(number) ?? 0) + 1)
    }
    return found
  }, [runs])
  const setVersion = (value: string) =>
    setParams(
      (current) => {
        const next = new URLSearchParams(current)
        if (value) next.set('version', value)
        else next.delete('version')
        return next
      },
      { replace: true },
    )

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-xs">
          <span className="text-text-secondary">Version</span>
          <Select
            value={version === null ? '' : String(version)}
            onChange={(event) => setVersion(event.target.value)}
            options={[
              { value: '', label: 'Every version' },
              ...[...script.versions].reverse().map((entry) => ({
                value: String(entry.number),
                label: `v${entry.number} (${counts.get(entry.number) ?? 0} run${counts.get(entry.number) === 1 ? '' : 's'})`,
              })),
            ]}
          />
        </label>
      </div>
      <QueryState isPending={runsQuery.isPending} error={runsQuery.error} hasData={Boolean(runs)}>
        {runs && (
          <RunsTable
            rows={rows}
            all={runs}
            judged={judged}
            tableKey={String(version ?? '')}
            tagHref={tagHref}
            emptyTitle={runs.length > 0 ? `No run of version ${version}` : 'No backtest of this script yet'}
            emptyDescription={
              runs.length > 0
                ? '“Every version” shows them all.'
                : `“Backtest v${script.version}” trades it as WATA would have, on the turbos the price recorder kept.`
            }
          />
        )}
      </QueryState>
    </div>
  )
}

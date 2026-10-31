// A script's versions (its page's History tab), newest first: when each was saved, who wrote it, the runs
// made on it and the rules that pin it, what changed from the one before, and any of them put back in the
// editor, unsaved (saving it then makes a new version: nothing is lost).

import { useQuery } from '@tanstack/react-query'
import { Button, formatBytes, formatDateTime, Panel } from 'ioiti-ui'
import { useState } from 'react'
import { Link } from 'react-router'

import { ClaudeBadge } from '@/components/status'
import { useSession } from '@/features/auth/auth-context'
import { ScriptDiff } from '@/features/ta/editor/ScriptDiff'
import { rulePath } from '@/features/ta/signals/ruleModel'
import { api } from '@/lib/api'
import type { BacktestRunListed, TaRule, TaScriptSummary } from '@/lib/types'

import { scriptTabHref, versionUses } from './scriptPageModel'

/** What changed from the version before, read only once asked for. */
function Changes({ script, number }: { script: TaScriptSummary; number: number }) {
  const version = (n: number) => ({
    queryKey: ['ta-script-version', script.id, n],
    queryFn: () => api.taScriptVersion(script.id, n),
    staleTime: Infinity,
  })
  const before = useQuery(version(number - 1))
  const after = useQuery(version(number))
  const error = before.error ?? after.error
  if (error) {
    return (
      <p role="alert" className="text-sm text-negative">
        {error.message}
      </p>
    )
  }
  if (!before.data || !after.data) return <p className="text-sm text-muted-foreground">Reading both versions…</p>
  if (before.data.source === after.data.source) return <p className="text-sm text-muted-foreground">The same text as version {number - 1}.</p>
  return (
    <div className="flex flex-col gap-1.5">
      {/* Over the two halves, which side is which. */}
      <div className="grid grid-cols-2 gap-3 text-xs font-medium">
        <p>Version {number - 1}</p>
        <p>Version {number}</p>
      </div>
      <ScriptDiff className="h-96" before={before.data.source} after={after.data.source} />
    </div>
  )
}

function VersionEntry({
  script,
  number,
  savedAt,
  size,
  claude,
  runs,
  rules,
  backtests,
  onRestore,
}: {
  script: TaScriptSummary
  number: number
  savedAt: string
  size: number
  claude: boolean
  runs: number
  rules: TaRule[]
  backtests: boolean
  onRestore: (source: string, version: number) => void
}) {
  const { timezone } = useSession()
  const [showing, setShowing] = useState(false)
  const [taking, setTaking] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const restore = async () => {
    setTaking(true)
    setError(null)
    try {
      const version = await api.taScriptVersion(script.id, number)
      onRestore(version.source, number)
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure))
    } finally {
      setTaking(false)
    }
  }
  return (
    <li className="flex flex-col gap-2 border-b border-border/60 py-3 last:border-b-0">
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-2 text-sm">
            <span className="font-mono font-medium">v{number}</span>
            {number === script.version && <span className="text-muted-foreground">saved last</span>}
            {claude && <ClaudeBadge />}
          </p>
          <p className="text-xs text-muted-foreground">
            {formatDateTime(savedAt, timezone, false)} · {formatBytes(size)}
          </p>
          <p className="flex flex-wrap gap-x-2 text-xs text-muted-foreground">
            {backtests && (
              <span>
                {runs > 0 ? (
                  <Link to={scriptTabHref('backtests', number)} className="underline-offset-2 hover:underline">
                    {runs} run{runs === 1 ? '' : 's'}
                  </Link>
                ) : (
                  'No run'
                )}
              </span>
            )}
            <span>
              {rules.length === 0 ? (
                'no rule pins it'
              ) : (
                <>
                  pinned by{' '}
                  {rules.map((rule, index) => (
                    <span key={rule.id}>
                      {index > 0 && ', '}
                      <Link to={rulePath(rule.id)} className="underline-offset-2 hover:underline">
                        {rule.name}
                      </Link>
                    </span>
                  ))}
                </>
              )}
            </span>
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {number > 1 && (
            <Button variant="ghost" size="sm" aria-expanded={showing} onClick={() => setShowing((open) => !open)}>
              {showing ? 'Hide the changes' : `Changes from v${number - 1}`}
            </Button>
          )}
          <Button variant="outline" size="sm" isLoading={taking} onClick={() => void restore()}>
            Into the editor
          </Button>
        </div>
      </div>
      {error && (
        <p role="alert" className="text-sm text-negative">
          {error}
        </p>
      )}
      {showing && <Changes script={script} number={number} />}
    </li>
  )
}

export function ScriptHistoryTab({
  script,
  runs,
  rules,
  backtests,
  onRestore,
}: {
  script: TaScriptSummary
  /** The runs of the script, when the backtests are on. */
  runs: readonly BacktestRunListed[]
  rules: readonly TaRule[]
  backtests: boolean
  onRestore: (source: string, version: number) => void
}) {
  const uses = versionUses(script, runs, rules)
  return (
    <Panel
      title="Versions"
      description="Each save of a changed text is a version, never written again. One put back in the editor stays unsaved there: saving it makes a new version."
    >
      <ul aria-label="Versions">
        {[...script.versions].reverse().map((entry) => (
          <VersionEntry
            key={entry.number}
            script={script}
            number={entry.number}
            savedAt={entry.saved_at}
            size={entry.size}
            claude={entry.made_by === 'claude'}
            runs={uses.get(entry.number)?.runs ?? 0}
            rules={uses.get(entry.number)?.rules ?? []}
            backtests={backtests}
            onRestore={onRestore}
          />
        ))}
      </ul>
    </Panel>
  )
}

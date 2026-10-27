// A signal rule's days of bars before each day, with what they are: how the TA engine
// uses them, and how many bars they make of the rule's asset, side and bars, counted by
// the server as the engine would load them.

import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { cn, Input } from 'ioiti-ui'
import { useEffect, useId, useState } from 'react'

import { useSession } from '@/features/auth/auth-context'
import { api, isNotFound } from '@/lib/api'

import { engineDays, historyHelp, historyRequest, historySentence } from './history'
import type { HistoryRequest } from './history'
import { barsLabel, MAX_HISTORY_DAYS } from './ruleModel'
import type { RuleDraft } from './ruleModel'

/** A value once it has stayed the same for a while: the count is not asked for at each key typed. */
function useSettled(value: string, delay = 400): string {
  const [settled, setSettled] = useState(value)
  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(value), delay)
    return () => window.clearTimeout(timer)
  }, [value, delay])
  return settled
}

export function HistoryDaysField({
  draft,
  update,
  engineHistoryDays,
  restartTime,
}: {
  draft: RuleDraft
  update: (changes: Partial<RuleDraft>) => void
  /** The engine's history_days, what an empty field leaves the rule. */
  engineHistoryDays: number
  /** The engine's restart_time, UTC. */
  restartTime: string
}) {
  const { timezone } = useSession()
  const helpId = useId()
  const request = historyRequest(draft)
  const key = useSettled(request ? JSON.stringify(request) : '')
  const asked = key ? (JSON.parse(key) as HistoryRequest) : null
  const count = useQuery({
    queryKey: ['ta-history', key],
    queryFn: () => api.taHistory(asked!),
    enabled: asked !== null,
    staleTime: 60_000,
    retry: false,
    placeholderData: keepPreviousData,
  })
  const history = asked ? count.data : undefined
  const days = history ? engineDays(history, timezone) : []

  // A cell of the step's grid for the field, and a row under it for what it is.
  return (
    <>
      <label className="flex flex-col gap-1">
        <span className="text-xs text-text-secondary">Days of bars before each day</span>
        <Input
          type="number"
          min={0}
          max={MAX_HISTORY_DAYS}
          step={1}
          value={draft.historyDays}
          placeholder={`The engine’s: ${engineHistoryDays}`}
          aria-describedby={helpId}
          onChange={(event) => update({ historyDays: event.target.value })}
        />
      </label>
      <div className="space-y-2 sm:col-span-2">
        <div id={helpId} className="space-y-1 text-xs leading-relaxed text-muted-foreground">
          {historyHelp(restartTime, engineHistoryDays, timezone).map((text) => (
            <p key={text}>{text}</p>
          ))}
        </div>
        {asked && (
          <section aria-label="Its bars now" aria-live="polite" className="space-y-2 rounded-lg border border-border/70 px-3 py-2 text-xs">
            {history ? (
              <div className={cn('space-y-2', count.isPlaceholderData && 'opacity-60')}>
                <p>
                  <span className="font-medium">
                    {history.asset.key}, {history.side}, {barsLabel(history.bars)}:
                  </span>{' '}
                  {historySentence(history, timezone)}
                </p>
                <div className="space-y-1">
                  <p className="text-muted-foreground">Bars of each engine day of the week, restart to restart:</p>
                  <dl className="grid grid-cols-4 gap-1 text-center sm:grid-cols-7">
                    {days.map((day) => (
                      <div key={day.end} className="rounded-md bg-surface/60 px-1 py-1">
                        <dt className="text-muted-foreground">{day.label}</dt>
                        <dd className="font-medium tabular-nums">{day.bars}</dd>
                      </div>
                    ))}
                  </dl>
                </div>
              </div>
            ) : count.isError ? (
              <p className="text-muted-foreground">
                {isNotFound(count.error)
                  ? `${count.error.message}: no bar of it to count yet.`
                  : `The bars could not be counted: ${count.error.message}`}
              </p>
            ) : (
              <p className="text-muted-foreground">Counting the bars the engine would run the script on…</p>
            )}
          </section>
        )}
      </div>
    </>
  )
}

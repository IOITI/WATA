import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Pause, Play, Search } from 'lucide-react'
import { useDeferredValue, useState } from 'react'

import { PageHeader } from '@/components/Panel'
import { QueryState } from '@/components/status'
import { Badge } from '@/components/ui/badge'
import type { BadgeTone } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/form'
import { SpringSelect } from '@/components/ui/spring-select'
import { useSession } from '@/features/auth/auth-context'
import { api } from '@/lib/api'
import { formatDateTime } from '@/lib/format'
import type { LogEntry } from '@/lib/types'
import { cn } from '@/lib/utils'

const LINE_COUNT = 300

const LEVEL_TONE: Record<string, BadgeTone> = {
  DEBUG: 'neutral',
  INFO: 'neutral',
  WARNING: 'warning',
  ERROR: 'danger',
  CRITICAL: 'danger',
}

function LogLine({ entry }: { entry: LogEntry }) {
  return (
    <li className="grid grid-cols-[auto_auto_1fr] items-baseline gap-x-3 px-4 py-1.5 font-mono text-xs leading-relaxed">
      <span className="whitespace-nowrap text-muted-foreground">{entry.timestamp ?? ''}</span>
      <span className="w-[4.5rem]">
        {entry.level && <Badge tone={LEVEL_TONE[entry.level] ?? 'neutral'}>{entry.level}</Badge>}
      </span>
      <span className="min-w-0 whitespace-pre-wrap break-words text-foreground/90">
        {entry.logger && <span className="text-muted-foreground">{entry.logger} </span>}
        {entry.message}
      </span>
    </li>
  )
}

export function LogsPage() {
  const { timezone } = useSession()
  const [service, setService] = useState('wata-trader-async')
  const [level, setLevel] = useState('ALL')
  const [search, setSearch] = useState('')
  const [following, setFollowing] = useState(true)
  // The request follows the typing at its own pace instead of firing on every key.
  const deferredSearch = useDeferredValue(search.trim())

  const servicesQuery = useQuery({ queryKey: ['log-services'], queryFn: api.logServices, staleTime: Infinity })
  const tailQuery = useQuery({
    queryKey: ['log', service, level, deferredSearch],
    queryFn: () =>
      api.logTail(service, { lines: LINE_COUNT, level: level === 'ALL' ? null : level, q: deferredSearch || null }),
    refetchInterval: following ? 3_000 : false,
    placeholderData: keepPreviousData,
  })
  const tail = tailQuery.data

  const serviceOptions = (servicesQuery.data?.services ?? [{ id: service, name: service }]).map((entry) => ({
    value: entry.id,
    label: entry.name,
    description: entry.id,
  }))
  const levelOptions = [
    { value: 'ALL', label: 'All levels' },
    ...(servicesQuery.data?.levels ?? []).map((name) => ({ value: name, label: `${name} and above` })),
  ]

  return (
    <>
      <PageHeader
        title="Logs"
        description="The end of each service's log file. Timestamps are the server's own clock, as written by the service."
      />

      <div className="flex flex-wrap items-end gap-3">
        <SpringSelect className="w-56" label="Service" options={serviceOptions} value={service} onChange={setService} />
        <SpringSelect className="w-48" label="Level" options={levelOptions} value={level} onChange={setLevel} />
        <div className="min-w-[14rem] flex-1">
          <Input
            aria-label="Search the log"
            placeholder="Search…"
            leftIcon={<Search className="size-3.5" />}
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </div>
        <Button
          variant="outline"
          size="md"
          leftIcon={following ? <Pause className="size-4" /> : <Play className="size-4" />}
          onClick={() => setFollowing(!following)}
        >
          {/* Not "Pause" alone: elsewhere that word stops new entries. */}
          {following ? 'Pause updates' : 'Resume updates'}
        </Button>
      </div>

      <QueryState isPending={tailQuery.isPending} error={tailQuery.error} hasData={!!tail}>
        {tail && (
          <section
            className={cn(
              'overflow-hidden rounded-2xl border border-border/70 bg-surface/50 transition-opacity dark:bg-surface/25',
              tailQuery.isPlaceholderData && 'opacity-60',
            )}
          >
            <header className="flex flex-wrap items-center justify-between gap-2 border-b border-border/50 px-4 py-2.5 text-xs text-muted-foreground">
              <span>
                {tail.exists
                  ? `${tail.entries.length} entr${tail.entries.length === 1 ? 'y' : 'ies'}, newest first`
                  : 'This service has not written a log file yet'}
              </span>
              {tail.modified_at && <span>File last written {formatDateTime(tail.modified_at, timezone)}</span>}
            </header>
            {tail.entries.length === 0 ? (
              <p className="py-10 text-center text-sm text-muted-foreground">
                {tail.exists ? 'No entry matches.' : 'Nothing to show.'}
              </p>
            ) : (
              <ol className="max-h-[65vh] divide-y divide-border/30 overflow-y-auto">
                {tail.entries.toReversed().map((entry, index) => (
                  <LogLine key={`${entry.timestamp}-${index}`} entry={entry} />
                ))}
              </ol>
            )}
          </section>
        )}
      </QueryState>
    </>
  )
}

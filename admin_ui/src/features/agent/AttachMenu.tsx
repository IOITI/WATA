// A composer's paperclip: a picker over WATA's things and the operator's files (attachItems.ts), searched as the
// palette is. It stays open to attach several, each ticked as it is attached and taken back when chosen again;
// ioiti-ui draws it in document.body, so that a sheet it is opened from, moved by a transform, does not hold it.
// LuxAlgo's Library is a list of its own, searched by LuxAlgo for what is typed in it alone.

import { useQuery } from '@tanstack/react-query'
import { Button, Hint, SpotlightSearch } from 'ioiti-ui'
import { Paperclip } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'

import { useSession } from '@/features/auth/auth-context'
import { useHiddenRuns } from '@/features/backtests/pending-removals-context'
import { api } from '@/lib/api'
import { REMEMBERED } from '@/lib/remembered'
import type { AgentSubject } from '@/lib/types'

import { attachFooter, attachItems, LUXALGO_FROM_CHARS, LUXALGO_LIST } from './attachItems'
import { ACCEPT, fullSaid, MAX_ATTACHED } from './attachments'
import { useAgentState } from './useConversation'

const DAY_MS = 86_400_000
// The positions and trading days offered: those of the last month; the signals: those of the last week.
const POSITION_DAYS = 30
const SIGNAL_DAYS = 7
// LuxAlgo's Library is searched once typing in its list paused: each search is a call to LuxAlgo.
const LUXALGO_WAIT_MS = 400

function daysBefore(days: number): string {
  return new Date(Date.now() - days * DAY_MS).toISOString().slice(0, 10)
}

export function AttachMenu({ attached, pending, room, onAttach, onDetach, onFiles, disabled = false }: {
  /** The keys of everything attached already (attachmentKey): what the conversation holds is not offered again. */
  attached: ReadonlySet<string>
  /** The keys of what this message attaches: ticked in the picker, taken back when chosen again. */
  pending: ReadonlySet<string>
  /** How many more may be attached. */
  room: number
  onAttach: (subject: AgentSubject) => void
  onDetach: (key: string) => void
  onFiles: (files: File[]) => void
  disabled?: boolean
}) {
  const { backtests_enabled: backtestsEnabled, timezone } = useSession()
  const hiddenRuns = useHiddenRuns()
  const [open, setOpen] = useState(false)
  // What is typed at the top, and in LuxAlgo's list while it is the one shown.
  const [query, setQuery] = useState('')
  const [inLuxAlgo, setInLuxAlgo] = useState(false)
  const [luxQuery, setLuxQuery] = useState('')
  // The file chooser, kept as it is drawn (a callback ref), clicked once the picker has gone.
  const [chooser, setChooser] = useState<HTMLInputElement | null>(null)
  const runs = useQuery({ queryKey: ['backtests'], queryFn: api.backtests, enabled: open && backtestsEnabled })
  const scripts = useQuery({ queryKey: ['ta-scripts'], queryFn: api.taScripts, enabled: open })
  const rules = useQuery({ queryKey: ['ta-rules'], queryFn: api.taRules, enabled: open })
  const health = useQuery({ queryKey: ['health'], queryFn: api.health, enabled: open })
  const opened = useQuery({ queryKey: ['agent-attach', 'open-positions'], queryFn: () => api.positions({ status: 'open' }), enabled: open })
  const closed = useQuery({
    queryKey: ['agent-attach', 'closed-positions'], enabled: open,
    queryFn: () => api.positions({ status: 'closed', from: daysBefore(POSITION_DAYS) }),
  })
  const journal = useQuery({ queryKey: ['agent-attach', 'journal'], queryFn: () => api.journal({ from: daysBefore(SIGNAL_DAYS) }), enabled: open })
  const days = useQuery({ queryKey: ['agent-attach', 'days'], queryFn: () => api.performance({ from: daysBefore(POSITION_DAYS) }), enabled: open })
  // LuxAlgo's indicators for what is typed in its list, once it stopped changing for a moment.
  const luxalgo = !!useAgentState().data?.luxalgo
  const [searched, setSearched] = useState('')
  useEffect(() => {
    const timer = window.setTimeout(() => setSearched(luxQuery.trim()), LUXALGO_WAIT_MS)
    return () => window.clearTimeout(timer)
  }, [luxQuery])
  const asked = open && luxalgo && searched.length >= LUXALGO_FROM_CHARS
  const found = useQuery({
    queryKey: ['agent-attach', 'luxalgo', searched],
    queryFn: () => api.luxalgoSearch({ query: searched, type: 'indicators', limit: 8 }),
    enabled: asked,
    staleTime: 60 * 60 * 1000,
  })

  // Once the picker has gone: a dialog of the browser over it would leave it open behind.
  const chooseFiles = useCallback(() => {
    setOpen(false)
    window.setTimeout(() => chooser?.click(), 0)
  }, [chooser])
  const { items, picked } = useMemo(() => attachItems({
    lists: {
      runs: (runs.data?.items ?? []).filter((run) => !hiddenRuns.has(run.id)),
      scripts: scripts.data?.items, rules: rules.data?.rules,
      positions: [...(opened.data?.items ?? []), ...(closed.data?.items ?? [])],
      journal: journal.data?.items, days: [...(days.data?.daily ?? [])].reverse(), health: health.data,
      luxalgo: asked && searched === luxQuery.trim() ? found.data?.results : undefined,
    },
    attached, pending, room, query, timeZone: timezone, luxalgo,
    onAttach, onDetach, onFile: chooseFiles,
  }), [runs.data, hiddenRuns, scripts.data, rules.data, opened.data, closed.data, journal.data, days.data, health.data,
    asked, searched, luxQuery, found.data, attached, pending, room, query, timezone, luxalgo, onAttach, onDetach, chooseFiles])
  const full = room <= 0 && pending.size === 0 ? fullSaid(MAX_ATTACHED) : null

  // What the picker says it is doing: the lists read at first; in LuxAlgo's list, the wait and LuxAlgo's answer.
  const typedInLuxAlgo = luxQuery.trim().length >= LUXALGO_FROM_CHARS
  const loading = inLuxAlgo
    ? typedInLuxAlgo && (searched !== luxQuery.trim() || found.isFetching)
    : [runs, scripts, rules, opened, closed, journal, days].some((entry) => entry.isFetching && entry.data === undefined)
  const failed = inLuxAlgo && typedInLuxAlgo && found.isError

  return (
    <>
      <Hint content={full ?? 'Attach a run, a script, a rule, a position, a signal, a day, an incident or a file'} wrap={!!full || disabled}>
        <Button type="button" size="sm" variant="ghost" aria-label="Attach" leftIcon={<Paperclip className="size-3.5" />}
          disabled={!!full || disabled} onClick={() => setOpen(true)} />
      </Hint>
      <input ref={setChooser} type="file" multiple accept={ACCEPT} tabIndex={-1} aria-hidden className="sr-only"
        onChange={(event) => {
          const files = Array.from(event.target.files ?? [])
          event.target.value = ''
          if (files.length > 0) onFiles(files)
        }} />
      <SpotlightSearch open={open} onOpenChange={setOpen} items={items} grouped limit={60}
        remember={REMEMBERED.attach}
        closeOnSelect={false} picked={picked} footer={attachFooter(pending.size, room)}
        onQueryChange={(text, scope) => {
          const luxList = scope?.id === LUXALGO_LIST
          setInLuxAlgo(luxList)
          if (luxList) setLuxQuery(text)
          else if (!scope) setQuery(text)
        }}
        filter={!inLuxAlgo}
        loading={loading}
        error={failed ? 'LuxAlgo’s Library could not be searched.' : undefined}
        onRetry={failed ? () => void found.refetch() : undefined}
        label="Attach to the conversation"
        placeholder="A run, a script, a rule, a position's number, a day (YYYY-MM-DD)…" />
    </>
  )
}

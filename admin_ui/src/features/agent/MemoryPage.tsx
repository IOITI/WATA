// The AI agent's memory: the entries it keeps between conversations, written by it and by the operator, who reads,
// changes, locks and forgets them here, pauses the agent's changes, and brings back any version from the history.
// An entry is forgotten with an Undo, as a conversation is removed: nothing is sent before its time is over.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AdvancedDataTable,
  AIResponse,
  AIResponseContent,
  Alert,
  AlertDescription,
  AnimatedTabs,
  Badge,
  Button,
  ButtonLink,
  formatDateTime,
  KpiGrid,
  KpiTile,
  PageHeader,
  QueryState,
  UndoToast,
} from 'ioiti-ui'
import type { ColumnDef } from 'ioiti-ui'
import { RouterLink } from 'ioiti-ui/react-router'
import { Download, History, Lock, LockOpen, Pause, Pencil, Play, Plus, RotateCcw, Trash2 } from 'lucide-react'
import { useCallback, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router'

import { useSession } from '@/features/auth/auth-context'
import { api } from '@/lib/api'
import type { AgentMemoryChange, AgentMemoryEntry, AgentMemoryState } from '@/lib/types'

import { MemoryEntryDialog } from './MemoryEntryDialog'
import {
  ACTION_LABEL,
  actionTone,
  authorSaid,
  changedParts,
  changeSentence,
  conversationOfReader,
  exportedMemory,
  KIND_LABEL,
  KINDS,
  memoryFigures,
  readerSaid,
  restorable,
  versionOf,
} from './memoryModel'

const UNDO_MS = 8000
const TABS = [
  { id: 'entries', label: 'Entries' },
  { id: 'history', label: 'History' },
]
type Tab = 'entries' | 'history'

function parseTab(value: string | null): Tab {
  return value === 'history' ? 'history' : 'entries'
}

function conversationLink(id: string | null | undefined, title?: string | null) {
  if (!id) return null
  return (
    <Link className="text-muted-foreground underline-offset-2 hover:underline" to={`/agent/${encodeURIComponent(id)}`}>
      {title ?? 'its conversation'}
    </Link>
  )
}

function download(filename: string, content: string): void {
  const url = URL.createObjectURL(new Blob([content], { type: 'application/json' }))
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

function EntryDetails({ entry, timezone, onEdit, onForget }: {
  entry: AgentMemoryEntry
  timezone: string
  onEdit: () => void
  onForget: () => void
}) {
  const queryClient = useQueryClient()
  const lock = useMutation({
    mutationFn: (locked: boolean) => api.lockAgentMemory(entry.name, locked),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['agent-memory'] }),
  })
  const reader = readerSaid(entry.read_by)
  const readIn = conversationOfReader(entry.read_by)

  return (
    <div className="space-y-3">
      <AIResponse content={entry.body} className="p-3 shadow-none sm:p-4">
        <AIResponseContent />
      </AIResponse>
      <p className="text-xs text-muted-foreground">
        {authorSaid(entry.created_by)} wrote it on {formatDateTime(entry.created_at, timezone, false)}
        {entry.created_in && <> in {conversationLink(entry.created_in)}</>}
        {entry.revision > 1 && (
          <>; {authorSaid(entry.updated_by).toLowerCase()} changed it last on {formatDateTime(entry.updated_at, timezone, false)}
            {entry.updated_in && <> in {conversationLink(entry.updated_in)}</>}</>
        )}
        . Revision {entry.revision}.
        {reader && entry.read_at && (
          <> Read last by {reader === 'The AI agent' ? 'the AI agent' : reader}{readIn && <> in {conversationLink(readIn)}</>} on{' '}
            {formatDateTime(entry.read_at, timezone, false)}.</>
        )}
      </p>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="outline" leftIcon={<Pencil className="size-3.5" />} onClick={onEdit}>
          Change
        </Button>
        <Button size="sm" variant="outline" isLoading={lock.isPending}
          leftIcon={entry.locked ? <LockOpen className="size-3.5" /> : <Lock className="size-3.5" />}
          onClick={() => lock.mutate(!entry.locked)}>
          {entry.locked ? 'Let the agent change it' : 'Lock: the agent only reads it'}
        </Button>
        <ButtonLink to={`?tab=history&find=${encodeURIComponent(entry.name)}`} size="sm" variant="outline"
          leftIcon={<History className="size-3.5" />}>
          Its history
        </ButtonLink>
        <Button size="sm" variant="ghost" leftIcon={<Trash2 className="size-3.5" />} onClick={onForget}>
          Forget it
        </Button>
      </div>
      {lock.error && (
        <Alert tone="danger" role="alert"><AlertDescription>{lock.error.message}</AlertDescription></Alert>
      )}
    </div>
  )
}

function entryColumns(timezone: string): ColumnDef<AgentMemoryEntry>[] {
  return [
    {
      id: 'name',
      header: 'Entry',
      accessorFn: (row) => `${row.name} ${row.description}`,
      sortable: true,
      cell: ({ row }) => (
        <div className="min-w-[14rem] max-w-[32rem] whitespace-normal">
          <span className="break-all font-mono text-sm font-medium">{row.name}</span>
          {row.locked && (
            <>
              <Lock aria-hidden className="ml-1.5 inline size-3.5 align-[-2px] text-muted-foreground" />
              <span className="sr-only">, locked: the agent only reads it</span>
            </>
          )}
          <span className="block text-sm text-muted-foreground">{row.description}</span>
        </div>
      ),
    },
    {
      id: 'kind',
      header: 'Kind',
      accessorFn: (row) => KIND_LABEL[row.kind],
      filterable: true,
      filterOptions: KINDS.map((kind) => ({ value: KIND_LABEL[kind], label: KIND_LABEL[kind] })),
      cell: ({ row }) => <Badge variant="soft">{KIND_LABEL[row.kind]}</Badge>,
    },
    {
      id: 'by',
      header: 'Written by',
      accessorFn: (row) => authorSaid(row.created_by),
      filterable: true,
      filterOptions: [{ value: 'You', label: 'You' }, { value: 'The AI agent', label: 'The AI agent' }],
    },
    {
      id: 'locked',
      header: 'Lock',
      accessorFn: (row) => (row.locked ? 'Locked' : 'Open'),
      filterable: true,
      filterOptions: [{ value: 'Locked', label: 'Locked' }, { value: 'Open', label: 'Open to the agent' }],
    },
    {
      id: 'updated',
      header: 'Changed',
      accessorFn: (row) => row.updated_at,
      sortable: true,
      cell: ({ row }) => formatDateTime(row.updated_at, timezone, false),
    },
    {
      id: 'read',
      header: 'Read last',
      accessorFn: (row) => row.read_at ?? '',
      sortable: true,
      cell: ({ row }) => (row.read_at ? formatDateTime(row.read_at, timezone, false) : 'Not yet'),
    },
  ]
}

function ChangeDetails({ line, onRestore, restoring }: {
  line: AgentMemoryChange
  onRestore: () => void
  restoring: boolean
}) {
  const version = versionOf(line)
  const parts = changedParts(line)
  return (
    <div className="space-y-3 text-sm">
      {parts.length > 0 && <p className="text-muted-foreground">It changed its {parts.join(', ')}.</p>}
      {line.before && line.after && line.before.body !== line.after.body && (
        <div className="grid gap-3 md:grid-cols-2">
          <div>
            <p className="mb-1 text-xs font-medium text-muted-foreground">Before</p>
            <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-lg border border-border p-3 text-xs">{line.before.body}</pre>
          </div>
          <div>
            <p className="mb-1 text-xs font-medium text-muted-foreground">After</p>
            <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-lg border border-border p-3 text-xs">{line.after.body}</pre>
          </div>
        </div>
      )}
      {version && !(line.before && line.after && line.before.body !== line.after.body) && (
        <div>
          <p className="mb-1 text-xs font-medium text-muted-foreground">
            {line.after ? 'As it became' : 'As it was'}: {KIND_LABEL[version.kind]}, “{version.description}”{version.locked ? ', locked' : ''}
          </p>
          <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-lg border border-border p-3 text-xs">{version.body}</pre>
        </div>
      )}
      {restorable(line) && (
        <Button size="sm" variant="outline" leftIcon={<RotateCcw className="size-3.5" />} isLoading={restoring} onClick={onRestore}>
          {line.action === 'forgotten' ? 'Bring it back' : 'Bring back this version'}
        </Button>
      )}
    </div>
  )
}

function historyColumns(timezone: string): ColumnDef<AgentMemoryChange>[] {
  return [
    {
      id: 'at',
      header: 'When',
      accessorFn: (row) => row.at,
      sortable: true,
      cell: ({ row }) => formatDateTime(row.at, timezone, false),
    },
    {
      id: 'action',
      header: 'What',
      accessorFn: (row) => ACTION_LABEL[row.action],
      filterable: true,
      filterOptions: Object.values(ACTION_LABEL).map((label) => ({ value: label, label })),
      cell: ({ row }) => <Badge variant="soft" tone={actionTone(row.action)}>{ACTION_LABEL[row.action]}</Badge>,
    },
    {
      id: 'change',
      header: 'Change',
      accessorFn: (row) => `${changeSentence(row)} ${row.name ?? ''} ${row.from ?? ''}`,
      cell: ({ row }) => (
        <div className="min-w-0 max-w-[32rem] whitespace-normal">
          {changeSentence(row)}
          {row.conversation && <span className="block text-xs">in {conversationLink(row.conversation, row.title_of)}</span>}
        </div>
      ),
    },
    {
      id: 'by',
      header: 'Who',
      accessorFn: (row) => authorSaid(row.by),
      filterable: true,
      filterOptions: [{ value: 'You', label: 'You' }, { value: 'The AI agent', label: 'The AI agent' }],
    },
  ]
}

function Notices({ data, timezone }: { data: AgentMemoryState; timezone: string }) {
  return (
    <>
      {!data.agent && (
        <Alert tone="neutral">
          <AlertDescription>
            The AI agent is off: its memory is kept, and Claude Code reads it. Switch the agent on in{' '}
            <Link className="underline underline-offset-2" to="/config/agent">Configuration › AI agent</Link>.
          </AlertDescription>
        </Alert>
      )}
      {data.agent && !data.used && (
        <Alert tone="neutral">
          <AlertDescription>
            The agent does not use its memory (<Link className="underline underline-offset-2" to="/config/agent">Configuration ›
            AI agent › Memory</Link>): its entries are kept, and Claude Code still reads them.
          </AlertDescription>
        </Alert>
      )}
      {data.pause && (
        <Alert tone="warning">
          <AlertDescription>
            The agent's changes are paused since {formatDateTime(data.pause.since, timezone, false)}: it reads its memory and
            changes nothing until you resume them. You still can.
          </AlertDescription>
        </Alert>
      )}
    </>
  )
}

export function MemoryPage() {
  const { timezone } = useSession()
  const queryClient = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()
  const tab = parseTab(searchParams.get('tab'))
  const find = searchParams.get('find') ?? ''
  const memory = useQuery({ queryKey: ['agent-memory'], queryFn: api.agentMemory })
  const history = useQuery({
    queryKey: ['agent-memory', 'history'],
    queryFn: () => api.agentMemoryHistory(),
    enabled: tab === 'history',
  })
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['agent-memory'] })
  const pause = useMutation({ mutationFn: (paused: boolean) => api.pauseAgentMemory(paused), onSuccess: refresh })
  const restore = useMutation({ mutationFn: (seq: number) => api.restoreAgentMemory(seq), onSuccess: refresh })
  // The dialog: a new entry (null) or one changed; absent while closed.
  const [editing, setEditing] = useState<AgentMemoryEntry | null | undefined>(undefined)
  // Hidden at once, forgotten once the Undo's time is over: nothing is sent before, as for conversations.
  const [hidden, setHidden] = useState<string | null>(null)
  const [shown, setShown] = useState<AgentMemoryEntry | null>(null)
  const forget = useMutation({
    mutationFn: (entry: AgentMemoryEntry) => api.forgetAgentMemory(entry.name, entry.revision),
    // Read again before it is shown: gone once forgotten, back with the error when it changed meanwhile.
    onSettled: async (_, __, entry) => {
      await refresh()
      setHidden((current) => (current === entry.name ? null : current))
    },
  })
  const forgetNow = forget.mutate
  const startForgetting = useCallback((entry: AgentMemoryEntry) => {
    if (shown && hidden === shown.name && shown.name !== entry.name) forgetNow(shown)
    setHidden(entry.name)
    setShown(entry)
  }, [hidden, shown, forgetNow])
  const data = memory.data
  const rows = useMemo(() => (data?.entries ?? []).filter((entry) => entry.name !== hidden), [data, hidden])
  const figures = memoryFigures(rows)
  const tabs = TABS.map((entry) => ({ ...entry, href: `?tab=${entry.id}` }))
  const columns = useMemo(() => entryColumns(timezone), [timezone])
  const changeColumns = useMemo(() => historyColumns(timezone), [timezone])

  return (
    <div className="space-y-6">
      <PageHeader
        title="Memory"
        description="What the AI agent keeps between conversations: its list is shared at each one's start, and it reads, saves and forgets entries as it works. Yours start locked."
        actions={
          data && (
            <>
              <Button size="sm" leftIcon={<Plus className="size-3.5" />} onClick={() => setEditing(null)}
                disabled={figures.entries >= data.limits.entries}>
                New entry
              </Button>
              <Button size="sm" variant="outline" isLoading={pause.isPending}
                leftIcon={data.pause ? <Play className="size-3.5" /> : <Pause className="size-3.5" />}
                onClick={() => pause.mutate(!data.pause)}>
                {data.pause ? "Resume its changes" : 'Pause its changes'}
              </Button>
              <Button size="sm" variant="ghost" leftIcon={<Download className="size-3.5" />}
                onClick={() => download(`wata-agent-memory-${new Date().toISOString().slice(0, 10)}.json`,
                  exportedMemory(data.entries, data.pause, new Date()))}>
                Download
              </Button>
            </>
          )
        }
      />
      <QueryState isPending={memory.isPending} error={memory.error} hasData={!!data}>
        {data && (
          <div className="space-y-6">
            <Notices data={data} timezone={timezone} />
            {(pause.error || restore.error || forget.error) && (
              <Alert tone="danger" role="alert">
                <AlertDescription>
                  {forget.error ? `Not forgotten: ${forget.error.message}` : (pause.error ?? restore.error)?.message}
                </AlertDescription>
              </Alert>
            )}
            <KpiGrid columns={4}>
              <KpiTile label="Entries" value={figures.entries} hint={`of ${data.limits.entries} at most`} />
              <KpiTile label="Written by" value={`${figures.operator} · ${figures.agent}`} hint="you · the agent" />
              <KpiTile label="Locked" value={figures.locked} hint="the agent only reads them" />
              <KpiTile label="Last change" value={figures.last ? formatDateTime(figures.last, timezone, false) : 'None yet'} />
            </KpiGrid>
            <AnimatedTabs
              tabs={tabs}
              activeTab={tab}
              onChange={(next) => setSearchParams({ tab: parseTab(next) })}
              linkComponent={RouterLink}
              renderContent={false}
            />
            {tab === 'entries' && (
              <AdvancedDataTable<AgentMemoryEntry>
                key={`entries:${find}`}
                title="Entries"
                searchPlaceholder="Search an entry…"
                emptyTitle="Nothing kept yet"
                emptyDescription="The agent saves what a later conversation needs as it works; you can write entries too."
                data={rows}
                columns={columns}
                getRowId={(row) => row.name}
                defaultPageSize={20}
                selectable={false}
                initialSearch={find || undefined}
                initialExpandedRowIds={find ? [find] : undefined}
                expandOnRowClick
                singleExpand
                rowLabel={(row) => row.name}
                renderSubComponent={(row) => (
                  <EntryDetails entry={row} timezone={timezone} onEdit={() => setEditing(row)} onForget={() => startForgetting(row)} />
                )}
              />
            )}
            {tab === 'history' && (
              <QueryState isPending={history.isPending} error={history.error} hasData={!!history.data}>
                <AdvancedDataTable<AgentMemoryChange>
                  key={`history:${find}`}
                  title="History"
                  searchPlaceholder="Search a change…"
                  emptyTitle="No change yet"
                  emptyDescription="Each entry saved, changed, locked or forgotten, by you or by the agent, is kept here."
                  data={history.data?.items ?? []}
                  columns={changeColumns}
                  getRowId={(row) => String(row.seq)}
                  defaultPageSize={20}
                  selectable={false}
                  initialSearch={find || undefined}
                  expandOnRowClick
                  singleExpand
                  rowLabel={(row) => changeSentence(row)}
                  renderSubComponent={(row) => (
                    <ChangeDetails line={row} restoring={restore.isPending && restore.variables === row.seq}
                      onRestore={() => restore.mutate(row.seq)} />
                  )}
                />
              </QueryState>
            )}
          </div>
        )}
      </QueryState>
      {data && editing !== undefined && (
        <MemoryEntryDialog
          key={editing?.name ?? 'new'}
          entry={editing}
          limits={data.limits}
          open
          onClose={() => setEditing(undefined)}
          onSaved={() => setEditing(undefined)}
        />
      )}
      {shown && (
        <UndoToast
          key={shown.name}
          open
          title="Entry forgotten"
          description={shown.name}
          restoredMessage="Kept: nothing was forgotten"
          duration={UNDO_MS}
          onUndo={() => setHidden(null)}
          // The countdown ran out, or the toast was closed; also called once an Undo has shown its message.
          onDismiss={() => {
            if (hidden === shown.name) forgetNow(shown)
            setShown(null)
          }}
        />
      )}
    </div>
  )
}

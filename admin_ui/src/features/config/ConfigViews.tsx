import { Search } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { Link, useLocation, useParams } from 'react-router'

import { NotFoundState } from '@/components/NotFoundState'
import { PageHeader, Panel } from '@/components/Panel'
import { RouterLink } from '@/components/RouterLink'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { AnimatedTabs } from '@/components/ui/animated-tabs'
import { Badge } from '@/components/ui/badge'
import { Input, Textarea } from '@/components/ui/form'
import { useSession } from '@/features/auth/auth-context'
import { formatDateTime } from '@/lib/format'
import type { CatalogParam, JsonValue } from '@/lib/types'
import { cn } from '@/lib/utils'

import { ApplyCard } from './ApplyCard'
import { BackupsPanel } from './BackupsPanel'
import { groupCounts, hrefOf, lookup, searchParams, segmentsOf, shownValue } from './catalog'
import { applyEditable, parseJsonObject, pickEditable } from './configModel'
import { CredentialsPanel } from './CredentialsPanel'
import { useConfigEditor } from './editor-context'
import { ParamRow, RuleBlock } from './ParamRow'

const VIEWS = [
  { id: 'parameters', label: 'Parameters', href: '/config' },
  { id: 'json', label: 'JSON', href: '/config/json' },
  { id: 'file', label: 'Whole file', href: '/config/file' },
  { id: 'versions', label: 'Previous versions', href: '/config/versions' },
]

function prettyJson(value: JsonValue): string {
  return JSON.stringify(value, null, 2)
}

/** What every view of the page shows above itself: its title, the last save, the views. */
function ConfigFrame({ title, description, view, children }: { title: string; description: ReactNode; view: string; children: ReactNode }) {
  const { catalog, doc, lastSaved, dismissSaved, disabled } = useConfigEditor()
  const { timezone } = useSession()
  return (
    <>
      <PageHeader title={title} description={description} />
      {lastSaved && <ApplyCard catalog={catalog} saved={lastSaved} onDismiss={dismissSaved} />}
      {disabled && (
        <Alert tone="neutral">
          <AlertDescription>
            Editing is switched off in the configuration (<code>admin.config_edit_enabled</code>). The file is shown
            read-only.
          </AlertDescription>
        </Alert>
      )}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <AnimatedTabs tabs={VIEWS} activeTab={view} renderContent={false} linkComponent={RouterLink} />
        <p className="text-xs text-muted-foreground">File last written {formatDateTime(doc.modified_at, timezone)}</p>
      </div>
      {children}
    </>
  )
}

function SearchResults({ found }: { found: CatalogParam[] }) {
  const { catalog } = useConfigEditor()
  const groups = new Map(catalog.groups.map((group) => [group.id, group.title]))
  if (found.length === 0) return <p className="text-sm text-muted-foreground">No parameter says that.</p>
  return (
    <ul className="divide-y divide-border/60 rounded-xl border border-border/70">
      {found.slice(0, 50).map((param) => (
        <li key={param.path}>
          <Link to={hrefOf(param)} className="block px-4 py-3 hover:bg-surface-hover focus-ring">
            <span className="flex flex-wrap items-center gap-2 text-sm">
              {param.label}
              <span className="text-xs text-muted-foreground">{groups.get(param.group)}</span>
            </span>
            <span className="block text-xs text-text-secondary">{param.help}</span>
            <code className="block break-all text-[11px] text-muted-foreground">{param.path}</code>
          </Link>
        </li>
      ))}
    </ul>
  )
}

/** The knobs that are not in config.json, so that this page leads to all of them. */
const ELSEWHERE = [
  { label: 'Pause of new entries', where: 'The dashboard', href: '/', what: 'Kept in the database: it survives restarts.' },
  { label: "Claude's pause", where: 'System › Claude', href: '/system', what: 'Every MCP call refused until resumed.' },
  { label: 'Research export', where: 'Backtests', href: '/backtests', what: 'Each finished day written for the lab.' },
  { label: "Dukascopy's keeper", where: 'Data › Dukascopy', href: '/data/providers/dukascopy', what: 'Prices kept up to date while the market is closed.' },
  { label: 'Signal rules', where: 'Technical Analysis › Signal rules', href: '/ta/signals', what: 'Live or shadow, their scripts, a rule’s own days of history.' },
]

/** The page's start: a search over every parameter, its groups, and what the file holds that nothing reads. */
export function ConfigOverview() {
  const { catalog, draft, unsaved, doc } = useConfigEditor()
  const [query, setQuery] = useState('')
  const found = useMemo(() => searchParams(catalog, query), [catalog, query])
  const known = catalog.params.length

  return (
    <ConfigFrame
      title="Configuration"
      view="parameters"
      description={`Every parameter of config.json (${known}), with what each changes and which services read it.`}
    >
      <div className="relative">
        <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
        <Input
          aria-label="Search the parameters"
          placeholder="Search: a name, a word of what it does, a key…"
          className="pl-9"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      </div>
      {query.trim() ? (
        <SearchResults found={found} />
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {catalog.groups.map((group) => {
              const params = catalog.params.filter((param) => param.group === group.id)
              const counts = groupCounts(params, draft, unsaved)
              return (
                <Link
                  key={group.id}
                  to={`/config/${group.id}`}
                  className="rounded-2xl border border-border/70 bg-surface/50 p-4 transition-colors hover:bg-surface-hover focus-ring dark:bg-surface/25"
                >
                  <p className="text-sm font-semibold">{group.title}</p>
                  <p className="mt-1 text-xs text-muted-foreground">{group.summary}</p>
                  <div className="mt-3 flex flex-wrap gap-1.5">
                    <Badge variant="outline">{counts.total} parameters</Badge>
                    {counts.unsaved > 0 && <Badge tone="warning">{counts.unsaved} unsaved</Badge>}
                    {counts.changedFromDefault > 0 && <Badge>{counts.changedFromDefault} not the default</Badge>}
                    {counts.serverOnly > 0 && <Badge variant="outline">{counts.serverOnly} set on the server</Badge>}
                    {counts.unused > 0 && <Badge variant="outline">{counts.unused} read by nothing</Badge>}
                  </div>
                </Link>
              )
            })}
          </div>
          {doc.unknown.length > 0 && (
            <Panel
              title="Keys WATA does not know"
              description="In the file but read by no service: a typo, or a leftover of an earlier WATA. Nothing uses them."
            >
              <ul className="space-y-2 text-xs">
                {doc.unknown.map((entry) => {
                  const nearest = entry.nearest ? lookup(catalog, entry.nearest) : undefined
                  return (
                    <li key={entry.path}>
                      <code className="break-all">{entry.path}</code>
                      {nearest && (
                        <span className="text-muted-foreground">
                          {' '}
                          — perhaps{' '}
                          <Link to={hrefOf(nearest)} className="text-foreground underline underline-offset-2">
                            {nearest.label}
                          </Link>
                          ?
                        </span>
                      )}
                    </li>
                  )
                })}
              </ul>
            </Panel>
          )}
          <Panel title="Settings kept elsewhere" description="What changes WATA's work without being in config.json.">
            <ul className="grid gap-3 sm:grid-cols-2">
              {ELSEWHERE.map((entry) => (
                <li key={entry.label} className="text-xs">
                  <Link to={entry.href} className="font-medium text-foreground underline-offset-2 hover:underline">
                    {entry.label}
                  </Link>{' '}
                  <span className="text-muted-foreground">({entry.where})</span>
                  <p className="text-text-secondary">{entry.what}</p>
                </li>
              ))}
            </ul>
          </Panel>
        </>
      )}
    </ConfigFrame>
  )
}

/** Scrolls to the parameter the address names (#p-…) and marks it a moment. */
function useScrollToParameter(): void {
  const { hash } = useLocation()
  useEffect(() => {
    if (!hash) return
    const element = document.getElementById(decodeURIComponent(hash.slice(1)))
    if (!element) return
    element.scrollIntoView?.({ block: 'center' })
    element.classList.add('ring-2', 'ring-warning/60')
    const timer = window.setTimeout(() => element.classList.remove('ring-2', 'ring-warning/60'), 2500)
    return () => window.clearTimeout(timer)
  }, [hash])
}

/** One group of parameters, beside the list of groups. */
export function ConfigGroupPage() {
  const { group: groupId } = useParams()
  const { catalog, unsaved, formVersion } = useConfigEditor()
  const group = catalog.groups.find((candidate) => candidate.id === groupId)
  useScrollToParameter()

  if (!group) {
    return (
      <NotFoundState
        title="No such group of parameters"
        description="The configuration has no group of that name."
        suggestions={catalog.groups.slice(0, 3).map((candidate) => ({ label: candidate.title, href: `/config/${candidate.id}` }))}
      />
    )
  }
  const params = catalog.params.filter((param) => param.group === group.id)

  return (
    <ConfigFrame title={group.title} view="parameters" description={group.summary}>
      <div className="grid grid-cols-[minmax(0,1fr)] gap-6 lg:grid-cols-[14rem_minmax(0,1fr)]">
        <nav aria-label="Groups of parameters" className="min-w-0 lg:sticky lg:top-4 lg:self-start">
          <ul className="flex gap-1 overflow-x-auto pb-1 lg:flex-col lg:overflow-visible">
            {catalog.groups.map((candidate) => {
              const waiting = catalog.params.filter((param) => param.group === candidate.id && unsaved.has(param.path)).length
              return (
                <li key={candidate.id} className="shrink-0">
                  <Link
                    to={`/config/${candidate.id}`}
                    aria-current={candidate.id === group.id ? 'page' : undefined}
                    className={cn(
                      'flex items-center justify-between gap-2 rounded-lg px-3 py-1.5 text-sm hover:bg-surface-hover focus-ring',
                      candidate.id === group.id ? 'bg-surface-hover font-medium text-foreground' : 'text-text-secondary',
                    )}
                  >
                    <span className="whitespace-nowrap">{candidate.title}</span>
                    {waiting > 0 && <Badge tone="warning">{waiting}</Badge>}
                  </Link>
                </li>
              )
            })}
          </ul>
        </nav>
        <div key={formVersion} className="min-w-0 space-y-3">
          {group.id === 'credentials' && <CredentialsPanel />}
          {segmentsOf(params).map((segment) =>
            segment.rule ? (
              <RuleBlock key={segment.rule} ruleType={segment.rule} params={segment.params} />
            ) : (
              segment.params.map((param) => <ParamRow key={param.path} param={param} />)
            ),
          )}
        </div>
      </div>
    </ConfigFrame>
  )
}

/** The editable sections as JSON: what the parameters do not show (a key nothing knows) can be edited here. */
export function ConfigJsonView() {
  const { doc, draft, replaceDraft, disabled } = useConfigEditor()
  const [text, setText] = useState(() => prettyJson(pickEditable(draft, doc.editable_paths)))
  const [error, setError] = useState<string | null>(null)

  return (
    <ConfigFrame title="Configuration as JSON" view="json" description="The sections this page may change, as JSON.">
      <Panel
        title="Editable sections"
        description={`Only ${doc.editable_paths.join(', ')} can be changed here. Credentials and infrastructure settings are edited on the server.`}
      >
        {doc.protected_paths.length > 0 && (
          <p className="mb-3 text-xs text-muted-foreground">
            Inside these sections, where the services connect and listen is changed on the server as well. A save that
            changes one of these is refused:{' '}
            {doc.protected_paths.map((path, index) => (
              <span key={path}>
                {index > 0 && ', '}
                <code>{path}</code>
              </span>
            ))}
            .
          </p>
        )}
        <Textarea
          aria-label="Editable configuration as JSON"
          className="min-h-[28rem] font-mono"
          spellCheck={false}
          value={text}
          disabled={disabled}
          error={!!error}
          onChange={(event) => {
            const next = event.target.value
            setText(next)
            const parsed = parseJsonObject(next)
            if (parsed.ok) {
              setError(null)
              replaceDraft(applyEditable(doc.config, parsed.value, doc.editable_paths))
            } else {
              setError(parsed.error)
            }
          }}
        />
        {error && <p className="mt-2 text-xs text-negative">Not valid JSON yet: {error}. The parameters keep what was last valid.</p>}
      </Panel>
    </ConfigFrame>
  )
}

export function ConfigFileView() {
  const { doc } = useConfigEditor()
  return (
    <ConfigFrame title="The whole file" view="file" description="config.json as it is on the server, secrets masked.">
      <Panel title="config.json" description="Unsaved changes are not shown here.">
        <pre className="max-h-[36rem] overflow-auto rounded-lg border border-border bg-surface-raised/40 p-4 text-xs leading-relaxed">
          {prettyJson(doc.config)}
        </pre>
      </Panel>
    </ConfigFrame>
  )
}

/** The versions kept beside the file, and what each save changed, key by key. */
export function ConfigVersionsView() {
  const { doc, history, catalog } = useConfigEditor()
  const { timezone } = useSession()
  return (
    <ConfigFrame title="Previous versions" view="versions" description="Each save keeps the file it replaces, and writes down what it changed.">
      <BackupsPanel configDoc={doc} timeZone={timezone} />
      <Panel title="What each save changed" description="Newest first: the keys of each save, their values masked as the page shows them.">
        {history === undefined ? (
          <p className="text-xs text-muted-foreground">Reading…</p>
        ) : history.length === 0 ? (
          <p className="text-xs text-muted-foreground">No save written down yet.</p>
        ) : (
          <ol className="space-y-3">
            {history.map((entry) => (
              <li key={`${entry.at}-${entry.revision_after}`} className="rounded-lg border border-border/70 p-3 text-xs">
                <p className="font-medium text-foreground">
                  {formatDateTime(entry.at, timezone)} · {entry.action}
                  {entry.by && <span className="font-normal text-muted-foreground"> · from {entry.by}</span>}
                </p>
                <ul className="mt-1 space-y-0.5">
                  {entry.changes.map((change) => {
                    const param = lookup(catalog, change.path)
                    return (
                      <li key={change.path} className="break-all text-text-secondary">
                        {param ? (
                          <Link to={hrefOf(param)} className="text-foreground underline-offset-2 hover:underline">
                            {param.label}
                          </Link>
                        ) : (
                          <code>{change.path}</code>
                        )}
                        : {change.added ? 'set to ' : ''}
                        {change.added ? shownValue(change.after) : change.removed ? `taken out (was ${shownValue(change.before)})` : `${shownValue(change.before)} → ${shownValue(change.after)}`}
                      </li>
                    )
                  })}
                  {entry.changes.length === 0 && <li className="text-muted-foreground">Nothing changed.</li>}
                </ul>
              </li>
            ))}
          </ol>
        )}
      </Panel>
    </ConfigFrame>
  )
}


// Technical Analysis › LuxAlgo Library: LuxAlgo's trading concepts and indicators with their Pine source, read
// through the admin (src/admin/routers/luxalgo.py), its families a page at a time, a search over concepts and
// indicators, the admin's sign-in to a LuxAlgo account for whole sources, what WATA knows of each indicator (its
// cards' badges) and whether it runs on WATA (the checks, and the view of those checked).

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  AlertDescription,
  AnimatedTabs,
  Badge,
  Button,
  ButtonLink,
  EmptyText,
  formatDateTime,
  Hint,
  Input,
  PageHeader,
  Panel,
  QueryState,
  Select,
  useToast,
} from 'ioiti-ui'
import { RouterLink } from 'ioiti-ui/react-router'
import { Search } from 'lucide-react'
import { useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useSearchParams } from 'react-router'

import { useSession } from '@/features/auth/auth-context'
import { api } from '@/lib/api'
import type { LuxAlgoCompat, LuxAlgoFamily, LuxAlgoKnownShort, LuxAlgoSearchResult, LuxAlgoStatus } from '@/lib/types'

import { IndicatorCard } from './IndicatorCard'
import { cardBadges, checkedOf, COMPAT_LABEL, COMPAT_TONE, compatSaid, conceptPath, indicatorPath, pageCount, returnSaid } from './luxalgoModel'
import { SignInPanel } from './SignInPanel'
import { familyName, useLuxAlgoChecks, useLuxAlgoFamilies, useLuxAlgoStatus } from './useLuxAlgo'

const PAGE_SIZE = 24
const VIEWS = [
  { id: 'library', label: 'Library', href: '?view=library' },
  { id: 'checked', label: 'Checked by WATA', href: '?view=checked' },
]
const STATES = Object.keys(COMPAT_LABEL) as LuxAlgoCompat['state'][]

/** What WATA knows of each indicator it touched, by slug, its newest check included: the cards' badges. */
function useKnown() {
  const known = useQuery({ queryKey: ['luxalgo', 'known'], queryFn: api.luxalgoKnown })
  const checks = useLuxAlgoChecks()
  return useMemo(() => {
    const bySlug = new Map((known.data?.items ?? []).map((entry): [string, LuxAlgoKnownShort] => [entry.slug, entry]))
    for (const compat of checks.data?.items ?? []) {
      if (!compat.slug) continue
      const entry = bySlug.get(compat.slug)
      bySlug.set(compat.slug, entry ? { ...entry, compat } : { slug: compat.slug, scripts: [], verdict: null, compat, runs: 0, rules: [] })
    }
    return bySlug
  }, [known.data, checks.data])
}

/** "Check this page": its indicators asked to be checked for running on WATA, each once. */
function CheckPage({ slugs }: { slugs: string[] }) {
  const toast = useToast()
  const queryClient = useQueryClient()
  const checks = useLuxAlgoChecks()
  const available = !!checks.data?.checks.available
  const ask = useMutation({
    mutationFn: () => api.checkLuxalgo(slugs),
    onSuccess: (said) => {
      void queryClient.invalidateQueries({ queryKey: ['luxalgo', 'compat'] })
      toast({
        title: said.added ? `${said.added} indicator${said.added === 1 ? '' : 's'} to check` : 'Already waiting for their check',
        description: 'One at a time, while no Pine study runs: each card says what came of it, and "Checked by WATA" lists them.',
      })
    },
  })
  const button = (
    <Button variant="outline" size="sm" disabled={!available || slugs.length === 0} isLoading={ask.isPending} onClick={() => ask.mutate()}>
      Check this page
    </Button>
  )
  return available ? button : <Hint content="The checks need the Pine studies, which this admin does not run" wrap>{button}</Hint>
}

function Catalog({ families, family, concept, page, onPage, known }: {
  families?: LuxAlgoFamily[]
  family: string | null
  /** A concept's indicators (its page's "Its indicators"). */
  concept: string | null
  page: number
  onPage: (page: number) => void
  known: Map<string, LuxAlgoKnownShort>
}) {
  const listed = useQuery({
    queryKey: ['luxalgo', 'indicators', family, concept, page],
    queryFn: () => api.luxalgoIndicators({ family, concept, page, page_size: PAGE_SIZE }),
  })
  const pages = listed.data ? pageCount(listed.data.total, PAGE_SIZE) : 1
  const title = concept
    ? `Indicators of ${concept}`
    : family
      ? `${familyName(families, family)} indicators`
      : 'Indicators'
  return (
    <Panel
      title={title}
      description={listed.data ? `${listed.data.total} in LuxAlgo's Library, the newest first.` : undefined}
      action={
        <div className="flex flex-wrap items-center gap-3">
          {concept && (
            <Link to={conceptPath(concept)} className="text-sm underline underline-offset-4">
              The concept
            </Link>
          )}
          <CheckPage slugs={(listed.data?.indicators ?? []).map((indicator) => indicator.slug)} />
        </div>
      }
      dense
    >
      <QueryState isPending={listed.isPending} error={listed.error} hasData={!!listed.data}>
        {listed.data && listed.data.indicators.length === 0 && <EmptyText>No indicator here.</EmptyText>}
        <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {listed.data?.indicators.map((indicator) => (
            <IndicatorCard key={indicator.slug} indicator={indicator} families={families} badges={cardBadges(known.get(indicator.slug))} />
          ))}
        </ul>
        {pages > 1 && (
          <nav aria-label="Pages of the indicators" className="mt-4 flex items-center justify-between gap-3 text-sm">
            <Button variant="outline" size="sm" disabled={page === 0} onClick={() => onPage(page - 1)}>
              Newer
            </Button>
            <span className="text-muted-foreground">
              Page {page + 1} of {pages}
            </span>
            <Button variant="outline" size="sm" disabled={page + 1 >= pages} onClick={() => onPage(page + 1)}>
              Older
            </Button>
          </nav>
        )}
      </QueryState>
    </Panel>
  )
}

function SearchResults({ query, family, families, known }: {
  query: string
  family: string | null
  families?: LuxAlgoFamily[]
  known: Map<string, LuxAlgoKnownShort>
}) {
  const found = useQuery({
    queryKey: ['luxalgo', 'search', query, family],
    queryFn: () => api.luxalgoSearch({ query, family, limit: 40 }),
  })
  const results = found.data?.results ?? []
  const group = (kind: LuxAlgoSearchResult['kind']) => results.filter((result) => result.kind === kind)
  return (
    <Panel title={`Found for “${query}”`} description="Concepts explain an idea; indicators carry a Pine script." dense>
      <QueryState isPending={found.isPending} error={found.error} hasData={!!found.data}>
        {results.length === 0 && <EmptyText>Nothing in LuxAlgo&apos;s Library: try other words.</EmptyText>}
        {group('concept').length > 0 && (
          <section className="mb-4">
            <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">Concepts</h3>
            <ul className="flex flex-wrap gap-2">
              {group('concept').map((concept) => (
                <li key={concept.slug}>
                  <Link to={conceptPath(concept.slug)} className="rounded-md border border-border px-2 py-1 text-sm hover:bg-surface-hover">
                    {concept.name}
                  </Link>
                </li>
              ))}
            </ul>
          </section>
        )}
        {group('indicator').length > 0 && (
          <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {group('indicator').map((indicator) => (
              <IndicatorCard key={indicator.slug} indicator={{ family: '', ...indicator }} families={families}
                badges={cardBadges(known.get(indicator.slug))} />
            ))}
          </ul>
        )}
      </QueryState>
    </Panel>
  )
}

/** The indicators WATA checked, newest first, by what came of it. */
function Checked() {
  const { timezone } = useSession()
  const [state, setState] = useState<LuxAlgoCompat['state'] | ''>('')
  const listed = useLuxAlgoChecks()
  const items = checkedOf(listed.data?.items ?? [], state || null)
  const checks = listed.data?.checks
  return (
    <Panel
      title="Checked by WATA"
      description={checks?.available === false
        ? 'The checks need the Pine studies, which this admin does not run.'
        : `Each indicator WATA read whole, checked as a script is: whether it compiles in PineTS and what alerts it declares.${
          checks?.queued?.length ? ` ${checks.queued.length} waiting.` : ''}`}
      action={
        <label className="flex w-48 flex-col gap-1 text-xs">
          <span className="text-text-secondary">Showing</span>
          <Select value={state} onChange={(event) => setState(event.target.value as LuxAlgoCompat['state'] | '')}
            options={[{ value: '', label: 'Every one' }, ...STATES.map((value) => ({ value, label: COMPAT_LABEL[value] }))]} />
        </label>
      }
      dense
    >
      <QueryState isPending={listed.isPending} error={listed.error} hasData={!!listed.data}>
        {items.length === 0 && <EmptyText>None yet: an indicator is checked once WATA reads it whole, or when asked.</EmptyText>}
        <ul className="flex flex-col divide-y divide-border">
          {items.map((item) => (
            <li key={item.slug} className="flex flex-col gap-1 py-3 sm:flex-row sm:items-start sm:gap-4">
              <div className="flex min-w-48 items-center gap-2">
                <Badge tone={COMPAT_TONE[item.state]} dot>{COMPAT_LABEL[item.state]}</Badge>
              </div>
              <div className="min-w-0 flex-1 text-sm">
                {item.slug && (
                  <Link to={indicatorPath(item.slug)} className="font-medium underline-offset-4 hover:underline">
                    {item.name ?? item.slug}
                  </Link>
                )}
                <p className="text-text-secondary">{compatSaid(item)}</p>
                <p className="text-xs text-muted-foreground">Checked {formatDateTime(item.checked_at, timezone)}</p>
              </div>
            </li>
          ))}
        </ul>
      </QueryState>
    </Panel>
  )
}

function Library({ status }: { status: LuxAlgoStatus }) {
  const [params, setParams] = useSearchParams()
  const families = useLuxAlgoFamilies(status.enabled)
  const known = useKnown()
  const query = params.get('q') ?? ''
  const family = params.get('family')
  const concept = params.get('concept')
  const page = Math.max(0, Number(params.get('page')) || 0)
  const [typed, setTyped] = useState(query)
  const change = (changes: Record<string, string | null>) => {
    const next = new URLSearchParams(params)
    for (const [key, value] of Object.entries(changes)) {
      if (value) next.set(key, value)
      else next.delete(key)
    }
    setParams(next)
  }
  const search = (event: FormEvent) => {
    event.preventDefault()
    change({ q: typed.trim() || null, concept: null, page: null })
  }
  return (
    <>
      <form role="search" onSubmit={search} className="flex flex-wrap items-end gap-3">
        <label className="flex min-w-56 flex-1 flex-col gap-1 text-xs">
          <span className="text-text-secondary">Concepts and indicators</span>
          <Input value={typed} onChange={(event) => setTyped(event.target.value)} placeholder="Supertrend, order blocks, VWAP…" />
        </label>
        <label className="flex w-56 flex-col gap-1 text-xs">
          <span className="text-text-secondary">Family</span>
          <Select
            value={family ?? ''}
            onChange={(event) => change({ family: event.target.value || null, concept: null, page: null })}
            options={[{ value: '', label: 'Every family' }, ...(families.data?.families ?? []).map((entry) => ({ value: entry.key, label: entry.name }))]}
          />
        </label>
        <Button type="submit" variant="outline" leftIcon={<Search className="size-4" aria-hidden />}>
          Search
        </Button>
        {(query || concept) && (
          <Button
            variant="ghost"
            onClick={() => {
              setTyped('')
              change({ q: null, concept: null, page: null })
            }}
          >
            Every indicator
          </Button>
        )}
      </form>
      {query ? (
        <SearchResults query={query} family={family} families={families.data?.families} known={known} />
      ) : (
        <Catalog
          families={families.data?.families}
          family={family}
          concept={concept}
          page={page}
          onPage={(next) => change({ page: next ? String(next) : null })}
          known={known}
        />
      )}
    </>
  )
}

export function LuxAlgoPage() {
  const { config_edit_enabled: editable } = useSession()
  const [params, setParams] = useSearchParams()
  const status = useLuxAlgoStatus()
  const returned = returnSaid(params)
  const view = params.get('view') === 'checked' ? 'checked' : 'library'
  const dismiss = () => {
    const next = new URLSearchParams(params)
    next.delete('luxalgo')
    next.delete('why')
    setParams(next, { replace: true })
  }
  return (
    <div className="space-y-6">
      <PageHeader
        title="LuxAlgo Library"
        description="LuxAlgo's trading concepts and indicators with their Pine source, read through the admin. An indicator taken becomes one of your scripts, its header and licence kept."
      />
      {returned && (
        <Alert tone={returned.tone} dismissible onDismiss={dismiss}>
          <AlertDescription>{returned.text}</AlertDescription>
        </Alert>
      )}
      <QueryState isPending={status.isPending} error={status.error} hasData={!!status.data}>
        {status.data && !status.data.enabled && (
          <Panel title="LuxAlgo's tools are switched off">
            <p className="text-sm text-text-secondary">
              Switch <code>admin.luxalgo_enabled</code> on (Configuration, Technical Analysis, LuxAlgo): this page and the AI agent then read
              LuxAlgo&apos;s Library, market trackers and prop-firm tools through the admin. It applies at the admin&apos;s next start.
            </p>
            <div className="mt-3">
              <ButtonLink to="/config" variant="outline" size="sm">
                Configuration
              </ButtonLink>
            </div>
          </Panel>
        )}
        {status.data?.enabled && (
          <div className="space-y-6">
            <SignInPanel status={status.data} editable={editable} />
            <AnimatedTabs
              tabs={VIEWS}
              activeTab={view}
              onChange={(next) => setParams(next === 'checked' ? { view: 'checked' } : {})}
              linkComponent={RouterLink}
              renderContent={false}
            />
            {view === 'checked' ? <Checked /> : <Library status={status.data} />}
          </div>
        )}
      </QueryState>
    </div>
  )
}

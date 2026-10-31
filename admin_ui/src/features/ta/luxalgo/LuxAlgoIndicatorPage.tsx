// An indicator of LuxAlgo's Library, chart first: the indicator run on a chart of WATA's bars from its whole source
// (nothing taken, nothing saved: IndicatorPreviewChart.tsx), or LuxAlgo's picture of it while its source comes cut;
// then how it works, its Pine source in colour and what WATA knows of it, beside the facts at a glance; and its
// taking as one of the operator's scripts, whole, its origin kept.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  AlertDescription,
  AnimatedTabs,
  Badge,
  Button,
  CopyButton,
  DescriptionList,
  FormControl,
  FormDescription,
  FormItem,
  FormLabel,
  formatDay,
  Hint,
  Input,
  PageHeader,
  Panel,
  QueryState,
  Select,
  SettleModal,
  useToast,
} from 'ioiti-ui'
import { RouterLink } from 'ioiti-ui/react-router'
import { ExternalLink, FileDown } from 'lucide-react'
import { lazy, Suspense, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router'

import { NotFoundState } from '@/components/NotFoundState'
import { AgentSheet } from '@/features/agent/AgentSheet'
import { AgentVerdict } from '@/features/agent/AgentVerdict'
import { AskAgentLink } from '@/features/agent/AskAgentLink'
import { useSession } from '@/features/auth/auth-context'
import { PREVIEW_TIMEFRAME, PREVIEW_TIMEFRAMES, previewAssets, previewSymbol } from '@/features/ta/chart/previewModel'
import { api, isNotFound } from '@/lib/api'
import type { LuxAlgoCompat, LuxAlgoFamily, LuxAlgoIndicator, LuxAlgoSource } from '@/lib/types'

import { InWataPanel } from './InWataPanel'
import { LuxAlgoPicture } from './LuxAlgoPicture'
import { LuxAlgoWriteUp } from './LuxAlgoWriteUp'
import {
  canTake,
  COMPAT_LABEL,
  COMPAT_TONE,
  conceptPath,
  licenceTerms,
  LUXALGO_PATH,
  sourceLinesSaid,
  sourceSaid,
} from './luxalgoModel'
import { familyName, useIndicatorCheck, useLuxAlgoFamilies, useLuxAlgoStatus } from './useLuxAlgo'

// Vela and CodeMirror only once their parts of the page are shown.
const IndicatorPreviewChart = lazy(() =>
  import('@/features/ta/chart/IndicatorPreviewChart').then((module) => ({ default: module.IndicatorPreviewChart })),
)
const PineEditor = lazy(() => import('@/features/ta/editor/PineEditor').then((module) => ({ default: module.PineEditor })))

const OUT = 'inline-flex items-center gap-1 text-sm text-foreground underline underline-offset-4'
const TABS = [
  { id: 'about', label: 'How it works' },
  { id: 'source', label: 'Pine source' },
  { id: 'wata', label: 'In WATA' },
] as const
type Tab = (typeof TABS)[number]['id']
const tabOf = (value: string | null): Tab => (value === 'source' || value === 'wata' ? value : 'about')
const nothing = () => undefined

function TakeDialog({ indicator, source, onClose }: { indicator: LuxAlgoIndicator; source: LuxAlgoSource; onClose: () => void }) {
  const navigate = useNavigate()
  const toast = useToast()
  const queryClient = useQueryClient()
  const [name, setName] = useState(indicator.name)
  const terms = licenceTerms(source.licence)
  const take = useMutation({
    mutationFn: () => api.takeLuxalgoIndicator(indicator.slug, name.trim()),
    onSuccess: (made) => {
      void queryClient.invalidateQueries({ queryKey: ['ta', 'scripts'] })
      void queryClient.invalidateQueries({ queryKey: ['luxalgo', 'indicator', indicator.slug] })
      toast({ title: 'Taken as your script', description: `${made.name}, version ${made.version}` })
      void navigate(`/ta/indicators/${made.id}`)
    },
  })
  return (
    <SettleModal
      open
      onClose={onClose}
      title={`Take ${indicator.name} as your script`}
      description="Its whole source becomes one of your scripts, its header kept: test it, map its alerts and change it as any other."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button disabled={!name.trim()} isLoading={take.isPending} onClick={() => take.mutate()}>
            Take it
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <FormItem>
          <FormLabel htmlFor="luxalgo-name">Name of your script</FormLabel>
          <FormControl>
            <Input id="luxalgo-name" maxLength={80} value={name} onChange={(event) => setName(event.target.value)} />
          </FormControl>
          <FormDescription>A name of yours not taken yet.</FormDescription>
        </FormItem>
        <Alert tone="neutral">
          <AlertDescription>
            LuxAlgo publishes it under {source.licence ?? 'its own licence'}
            {terms ? `: ${terms}` : ''}. Its terms are yours to check before trading it; its page on LuxAlgo&apos;s site says them.
          </AlertDescription>
        </Alert>
        {take.isError && (
          <Alert tone="danger" role="alert">
            <AlertDescription>{take.error.message}</AlertDescription>
          </Alert>
        )}
      </div>
    </SettleModal>
  )
}

/** The indicator on a chart of WATA's bars, run from its whole source; LuxAlgo's picture of it otherwise. */
function OnAChart({ indicator, source, signedIn }: {
  indicator: LuxAlgoIndicator
  source: { data?: LuxAlgoSource; isPending: boolean; error: Error | null }
  signedIn: boolean
}) {
  const { timezone } = useSession()
  const assetsQuery = useQuery({ queryKey: ['data-assets', 'all'], queryFn: () => api.dataAssets('all'), staleTime: 60_000 })
  const [chosen, setChosen] = useState<string | null>(null)
  const [timeframe, setTimeframe] = useState(PREVIEW_TIMEFRAME)
  const listed = assetsQuery.data?.items ?? []
  const assets = previewAssets(listed)
  const symbol = previewSymbol(listed, chosen)
  const whole = canTake(source.data)
  const picture = (
    <LuxAlgoPicture
      url={indicator.image_url}
      name={indicator.name}
      family={indicator.family}
      className="mx-auto max-w-3xl rounded-xl border border-border"
    />
  )
  return (
    <Panel
      title="On a chart"
      description={whole
        ? "Run by WATA from LuxAlgo's source on WATA's own bars, with its own inputs: nothing taken, nothing saved."
        : undefined}
    >
      <QueryState isPending={source.isPending} error={null} hasData>
        {whole && source.data?.source ? (
          <div className="space-y-3">
            {assets.length > 0 && (
              <div className="flex flex-wrap items-end gap-3">
                <label className="flex min-w-0 flex-1 flex-col gap-1 text-xs sm:w-56 sm:flex-none">
                  <span className="text-text-secondary">Asset</span>
                  <Select value={symbol} onChange={(event) => setChosen(event.target.value)}
                    options={assets.map((asset) => ({ value: asset.symbol, label: asset.label }))} />
                </label>
                <label className="flex w-32 flex-col gap-1 text-xs sm:w-36">
                  <span className="text-text-secondary">Bars</span>
                  <Select value={timeframe} onChange={(event) => setTimeframe(event.target.value)} options={PREVIEW_TIMEFRAMES} />
                </label>
              </div>
            )}
            {/* Built once the assets are known: the asset it opens on is the Charts page's. */}
            {!assetsQuery.isPending && (
              <Suspense fallback={<div className="h-72 rounded-xl border border-border sm:h-[28rem]" />}>
                <IndicatorPreviewChart source={source.data.source} name={indicator.name} symbol={symbol} timeframe={timeframe}
                  timeZone={timezone} />
              </Suspense>
            )}
          </div>
        ) : (
          <div className="space-y-3">
            {picture}
            <p className="text-center text-sm text-text-secondary">
              LuxAlgo&apos;s picture of it.{' '}
              {source.error
                ? `Its source could not be read: ${source.error.message}`
                : !source.data?.available || !source.data.source
                  ? 'LuxAlgo publishes no source of it to run.'
                  : signedIn
                    ? 'LuxAlgo gives only a part of its source: it runs whole or not at all.'
                    : (
                      <>
                        Sign in to LuxAlgo to run it on WATA&apos;s bars:{' '}
                        <Link to={LUXALGO_PATH} className="underline underline-offset-4">on the LuxAlgo Library page</Link>.
                      </>
                    )}
            </p>
          </div>
        )}
      </QueryState>
    </Panel>
  )
}

/** Its Pine source, coloured as the editor colours it, read only. */
function SourceTab({ indicator, source, signedIn }: { indicator: LuxAlgoIndicator; source: LuxAlgoSource | undefined; signedIn: boolean }) {
  const cut = source ? sourceSaid(source) : null
  return (
    <div className="space-y-3">
      {source?.licence && (
        <p className="text-sm text-text-secondary">
          Under {source.licence}{source.owner ? `, © ${source.owner}` : ''}, as its header says.
        </p>
      )}
      {cut && (
        <Alert tone="neutral">
          <AlertDescription>
            {cut}{' '}
            {source?.available && !signedIn && (
              <Link to={LUXALGO_PATH} className="underline underline-offset-4">
                Sign in on the LuxAlgo Library page
              </Link>
            )}
          </AlertDescription>
        </Alert>
      )}
      {source?.source && (
        <>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-sm font-medium">
              {source.complete ? `${indicator.name}, whole` : `${indicator.name}, its first lines`}
            </span>
            <CopyButton value={source.source} label="Copy the code" showLabel size="sm" />
          </div>
          <Suspense fallback={<div className="h-[24rem] rounded-xl border border-border sm:h-[32rem]" />}>
            <PineEditor value={source.source} onChange={nothing} diagnostics={[]} label={`Pine source of ${indicator.name}`}
              readOnly className="h-[24rem] sm:h-[32rem]" />
          </Suspense>
        </>
      )}
    </div>
  )
}

/** What there is to know of it at a glance, and where it lives outside WATA. */
function AtAGlance({ indicator, source, compat, families, picture }: {
  indicator: LuxAlgoIndicator
  source: LuxAlgoSource | undefined
  compat: LuxAlgoCompat | null
  families?: LuxAlgoFamily[]
  /** Its picture, unless the chart's panel shows it already. */
  picture: boolean
}) {
  const terms = licenceTerms(source?.licence)
  return (
    <Panel title="At a glance">
      <div className="space-y-4">
        {picture && (
          <LuxAlgoPicture url={indicator.image_url} name={indicator.name} family={indicator.family}
            className="rounded-lg border border-border" />
        )}
        <DescriptionList
          items={[
            { term: 'Family', details: familyName(families, indicator.family) },
            ...(indicator.date_displayed ? [{ term: 'Published', details: formatDay(indicator.date_displayed) }] : []),
            { term: 'By', details: indicator.author ?? 'LuxAlgo' },
            { term: 'Licence', details: source?.licence ? `${source.licence}${source.owner ? `, © ${source.owner}` : ''}` : 'Not read yet' },
            { term: 'Source', details: sourceLinesSaid(source) },
            { term: 'On WATA', details: compat ? COMPAT_LABEL[compat.state] : 'Not checked yet' },
          ]}
        />
        {terms && <p className="text-xs text-muted-foreground">Its licence: {terms}. Its terms are yours to check before trading it.</p>}
        {!!indicator.concepts?.length && (
          <div className="flex flex-wrap gap-2">
            {indicator.concepts.map((concept) => (
              <Link key={concept.slug} to={conceptPath(concept.slug)}>
                <Badge variant="outline">{concept.name}</Badge>
              </Link>
            ))}
          </div>
        )}
        <div className="flex flex-col gap-2">
          {source?.chart_url && (
            <a href={source.chart_url} target="_blank" rel="noopener noreferrer" className={OUT}>
              Open on Vela <ExternalLink className="size-3.5" aria-hidden />
            </a>
          )}
          {indicator.url && (
            <a href={indicator.url} target="_blank" rel="noopener noreferrer" className={OUT}>
              On LuxAlgo&apos;s site <ExternalLink className="size-3.5" aria-hidden />
            </a>
          )}
        </div>
      </div>
    </Panel>
  )
}

export function LuxAlgoIndicatorPage() {
  const { slug = '' } = useParams()
  const [params, setParams] = useSearchParams()
  const tab = tabOf(params.get('tab'))
  const status = useLuxAlgoStatus()
  const enabled = !!status.data?.enabled
  const signedIn = !!status.data?.signed_in
  const families = useLuxAlgoFamilies(enabled)
  const indicator = useQuery({ queryKey: ['luxalgo', 'indicator', slug], queryFn: () => api.luxalgoIndicator(slug), enabled })
  const queryClient = useQueryClient()
  const source = useQuery({
    // Read again once signed in: signed out, it was cut.
    queryKey: ['luxalgo', 'source', slug, signedIn],
    queryFn: async () => {
      const found = await api.luxalgoSource(slug)
      // A source read whole is queued for its check: the checks are read again, and followed while it waits.
      if (found.complete) void queryClient.invalidateQueries({ queryKey: ['luxalgo', 'compat'] })
      return found
    },
    enabled: enabled && !!indicator.data,
  })
  const compat = useIndicatorCheck(slug, indicator.data?.known?.compat)
  const [taking, setTaking] = useState(false)

  if (isNotFound(indicator.error)) {
    return (
      <NotFoundState
        title="No such indicator"
        description="LuxAlgo's Library has no indicator at this address."
        suggestions={[{ label: 'LuxAlgo Library', href: LUXALGO_PATH, description: "LuxAlgo's indicators and concepts" }]}
      />
    )
  }
  const found = indicator.data
  const cut = source.data ? sourceSaid(source.data) : null
  const takeable = canTake(source.data)
  const takeButton = (
    <Button leftIcon={<FileDown className="size-4" aria-hidden />} disabled={!takeable} onClick={() => setTaking(true)}>
      Take as my script
    </Button>
  )
  // A tab's address keeps the rest of the page's (the AI agent's sheet).
  const tabHref = (id: Tab) => {
    const next = new URLSearchParams(params)
    next.set('tab', id)
    return `?${next.toString()}`
  }
  const tabs = TABS.map((entry) => ({ ...entry, href: tabHref(entry.id) }))
  return (
    <div className="space-y-6">
      <PageHeader
        title={found?.name ?? 'Indicator'}
        description={found
          ? [familyName(families.data?.families, found.family), found.date_displayed && formatDay(found.date_displayed), `by ${found.author ?? 'LuxAlgo'}`]
            .filter(Boolean).join(' · ')
          : undefined}
        actions={found && (
          <>
            <AskAgentLink subject={{ kind: 'luxalgo', id: slug }} label="Ask the AI agent about it" />
            {takeable ? takeButton : <Hint content={cut ?? 'Its source is being read'} wrap>{takeButton}</Hint>}
          </>
        )}
      />
      {found && (
        <div className="flex flex-wrap items-center gap-2">
          {source.data?.licence && <Badge variant="outline">{source.data.licence}</Badge>}
          <Badge tone={compat ? COMPAT_TONE[compat.state] : 'neutral'} dot>
            {compat ? COMPAT_LABEL[compat.state] : 'Not checked on WATA'}
          </Badge>
          {!!found.known?.scripts.length && <Badge tone="neutral">Taken</Badge>}
        </div>
      )}
      {found && <AgentVerdict about={`luxalgo:${slug}`} />}
      <QueryState isPending={status.isPending || (enabled && indicator.isPending)} error={status.error ?? indicator.error} hasData={!!found || !enabled}>
        {!enabled && (
          <Alert tone="neutral">
            <AlertDescription>
              LuxAlgo&apos;s tools are switched off (<code>admin.luxalgo_enabled</code>).
            </AlertDescription>
          </Alert>
        )}
        {found && (
          <div className="space-y-6">
            <OnAChart indicator={found} source={source} signedIn={signedIn} />
            <div className="grid gap-6 lg:grid-cols-3">
              <div className="min-w-0 space-y-4 lg:col-span-2">
                <AnimatedTabs
                  tabs={tabs}
                  activeTab={tab}
                  onChange={(next) => setParams((before) => {
                    const after = new URLSearchParams(before)
                    after.set('tab', next)
                    return after
                  })}
                  linkComponent={RouterLink}
                  renderContent={false}
                />
                {tab === 'about' && (
                  found.body_markdown ? (
                    <Panel>
                      <LuxAlgoWriteUp markdown={found.body_markdown} />
                    </Panel>
                  ) : (
                    <p className="text-sm text-muted-foreground">LuxAlgo says nothing more of it.</p>
                  )
                )}
                {tab === 'source' && (
                  <Panel>
                    <QueryState isPending={source.isPending} error={source.error} hasData={!!source.data}>
                      <SourceTab indicator={found} source={source.data} signedIn={signedIn} />
                    </QueryState>
                  </Panel>
                )}
                {tab === 'wata' && found.known && <InWataPanel slug={slug} known={found.known} />}
              </div>
              <AtAGlance indicator={found} source={source.data} compat={compat} families={families.data?.families}
                picture={takeable} />
            </div>
          </div>
        )}
      </QueryState>
      {taking && found && source.data && <TakeDialog indicator={found} source={source.data} onClose={() => setTaking(false)} />}
      {found && <AgentSheet subject={{ kind: 'luxalgo', id: slug, label: `LuxAlgo's ${found.name}` }} />}
    </div>
  )
}

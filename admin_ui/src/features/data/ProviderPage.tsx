import { useQuery } from '@tanstack/react-query'
import { Plus, Settings2 } from 'lucide-react'
import { useState } from 'react'
import type { ReactNode } from 'react'
import { Link, useParams } from 'react-router'

import { KpiTile } from '@/components/KpiTile'
import { NotFoundState } from '@/components/NotFoundState'
import { PageHeader, Panel } from '@/components/Panel'
import { QueryState, ServiceStatusBadge } from '@/components/status'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useSession } from '@/features/auth/auth-context'
import { HistoryPanel } from '@/features/data/HistoryPanel'
import { GapsPanel } from '@/features/prices/GapsPanel'
import { RestartAction } from '@/features/system/RestartViews'
import { api } from '@/lib/api'
import { formatBytes, formatDateTime, formatDay, formatNumber } from '@/lib/format'
import type { CapitalProviderDetails, DataProvider, DukascopyProviderDetails, SaxoProviderDetails } from '@/lib/types'

import { assetHref, assetsLink, PROVIDERS_PATH } from './assets'
import { CapitalSettingsDialog } from './CapitalSettingsDialog'
import type { CapitalDialogStart } from './CapitalSettingsDialog'
import { HistorySettingsDialog } from './HistorySettingsDialog'
import { KIND_LABELS, PROVIDER_NAMES } from './providers'
import { RecorderSettingsDialog } from './RecorderSettingsDialog'
import type { RecorderDialogStart } from './RecorderSettingsDialog'

const REFRESH_MS = 30_000

function isoOf(epoch: number | null): string | null {
  return epoch === null ? null : new Date(epoch * 1000).toISOString()
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid gap-1 border-t border-border/50 py-2.5 sm:grid-cols-[14rem_minmax(0,1fr)] sm:gap-4">
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words text-sm tabular-nums">{children}</dd>
    </div>
  )
}

/** What every provider says of itself first: its state, its assets, the span of its data. */
function Overview({ provider, timeZone }: { provider: DataProvider; timeZone: string }) {
  return (
    <div className="grid gap-4 sm:grid-cols-3">
      <KpiTile label="Status" value={<ServiceStatusBadge status={provider.status} />} hint={provider.detail} />
      <KpiTile
        label="Assets"
        value={provider.assets === null ? '—' : formatNumber(provider.assets)}
        hint={
          <Link to={assetsLink({ provider: provider.id })} className="underline decoration-border underline-offset-2 hover:text-foreground">
            View them on the Assets View
          </Link>
        }
      />
      <KpiTile
        label="Data"
        value={provider.last_t === null ? '—' : formatDateTime(isoOf(provider.last_t), timeZone, false)}
        hint={
          provider.first_day === null ? 'Nothing kept yet' : `The last of it; the first from ${formatDay(provider.first_day)}`
        }
      />
    </div>
  )
}

function SaxoProvider({ provider }: { provider: SaxoProviderDetails }) {
  const { timezone } = useSession()
  const [editing, setEditing] = useState<RecorderDialogStart | null>(null)
  const { settings } = provider
  // Switched on in the file, and not writing: the server has to start it.
  const notRunning = provider.enabled && provider.status === 'down'

  return (
    <>
      <PageHeader
        title={provider.name}
        description={`${KIND_LABELS[provider.kind]}. ${provider.description}`}
        actions={
          <>
            <Button variant="outline" size="sm" leftIcon={<Plus className="size-3.5" />} onClick={() => setEditing('add-instrument')}>
              Add instrument
            </Button>
            <Button size="sm" leftIcon={<Settings2 className="size-3.5" />} onClick={() => setEditing('edit')}>
              Edit configuration
            </Button>
          </>
        }
      />
      <Overview provider={provider} timeZone={timezone} />
      {provider.restart_required && (
        <Alert tone="warning">
          <AlertDescription>
            <div className="space-y-2">
              <p>The recorder runs without settings saved since it started: it applies them at its next start.</p>
              <RestartAction ids={['price_recorder']} />
            </div>
          </AlertDescription>
        </Alert>
      )}
      {notRunning && (
        <Alert tone="danger" role="alert">
          <AlertDescription>
            Switched on in the configuration, and not recording. On the server, <code>recorder</code> must be in{' '}
            <code>COMPOSE_PROFILES</code> (then <code>watastop</code>, <code>watastart</code>), or the recorder restarted,
            from the <Link className="underline underline-offset-2" to="/system">System</Link> page&apos;s containers.
          </AlertDescription>
        </Alert>
      )}

      <GapsPanel gaps={provider.gaps_today} timeZone={timezone} />

      <div className="grid gap-6 xl:grid-cols-2">
        <Panel
          title="Settings"
          description="trade.config.price_recorder, as the file holds it now."
          action={
            <Button variant="ghost" size="sm" onClick={() => setEditing('edit')}>
              Edit
            </Button>
          }
        >
          <dl>
            <Row label="Recording">{settings.enabled ? 'Switched on' : 'Switched off'}</Row>
            <Row label="Saxo's volume">{settings.record_volume ? 'Recorded' : 'Not recorded'}</Row>
            <Row label="Instruments at most">{formatNumber(settings.max_instruments)}</Row>
            <Row label="Candidates looked for every">{formatNumber(settings.refresh_interval_seconds)} s</Row>
            <Row label="Quotes at most every">{formatNumber(settings.refresh_rate_ms)} ms</Row>
            <Row label="Kept">
              {settings.retention_months === 0
                ? 'For good'
                : `${settings.retention_months} month${settings.retention_months === 1 ? '' : 's'}`}
            </Row>
            <Row label="Said to have stopped after">{formatNumber(settings.alert_after_seconds)} s</Row>
          </dl>
        </Panel>
        <Panel
          title="Recorded besides the turbos"
          description="Instruments of the configuration: an index Saxo quotes for the account, for example."
          action={
            <Button variant="ghost" size="sm" onClick={() => setEditing('add-instrument')}>
              Add
            </Button>
          }
        >
          {settings.instruments.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              None: the recorder records the turbos the trader could buy, and finds the index of each indice itself.
            </p>
          ) : (
            <ul className="divide-y divide-border/60 text-sm">
              {settings.instruments.map((instrument) => (
                <li key={`${instrument.label}-${instrument.uic}`} className="flex justify-between gap-3 py-2">
                  <span className="font-medium">{instrument.label}</span>
                  <span className="tabular-nums text-muted-foreground">
                    UIC {instrument.uic} · {instrument.asset_type}
                  </span>
                </li>
              ))}
            </ul>
          )}
          <p className="mt-3 text-xs text-muted-foreground">
            On the server, <code>wataprices probe --uic &lt;n&gt;</code> says whether Saxo quotes an instrument for the
            account, and prints the entry to add.
          </p>
        </Panel>
      </div>

      <RecorderSettingsDialog open={editing !== null} start={editing ?? 'edit'} onClose={() => setEditing(null)} />
    </>
  )
}

function DukascopyProvider({ provider }: { provider: DukascopyProviderDetails }) {
  const { timezone } = useSession()
  const [editing, setEditing] = useState(false)
  const storage = provider.storage

  return (
    <>
      <PageHeader
        title={provider.name}
        description={`${KIND_LABELS[provider.kind]}. ${provider.description}`}
        actions={
          <Button size="sm" leftIcon={<Settings2 className="size-3.5" />} onClick={() => setEditing(true)}>
            Edit configuration
          </Button>
        }
      />
      <Overview provider={provider} timeZone={timezone} />
      {!provider.enabled && (
        <Alert tone="neutral">
          <AlertDescription>
            Downloads are switched off in the configuration (<code>admin.backtests_enabled</code>): what was fetched is
            shown, nothing more can be fetched.
          </AlertDescription>
        </Alert>
      )}

      <Panel
        title="Instruments"
        description={`From ${provider.source}, in Dukascopy's own files. ${
          storage ? `The prices take ${formatBytes(storage.bars_bytes + storage.fetched_bytes)} of disk.` : ''
        }`}
        action={
          <Button variant="ghost" size="sm" onClick={() => setEditing(true)}>
            Add or remove
          </Button>
        }
      >
        <ul className="divide-y divide-border/60 text-sm">
          {provider.instruments.map((instrument) => (
            <li key={instrument.label} className="flex flex-wrap items-center justify-between gap-3 py-2.5">
              <div className="min-w-0">
                <p className="font-medium">
                  {instrument.name} <span className="font-normal text-muted-foreground">· {instrument.symbol}</span>
                </p>
                <p className="text-xs text-muted-foreground">
                  {instrument.days === 0
                    ? 'Nothing fetched yet'
                    : `${formatNumber(instrument.days)} day${instrument.days === 1 ? '' : 's'}, from ${formatDay(instrument.first_day)} to ${formatDay(instrument.last_day)}, ${formatBytes(instrument.bytes)}`}
                </p>
              </div>
              <div className="flex items-center gap-3">
                {instrument.built_in && <Badge>Kept up to date</Badge>}
                {instrument.days > 0 && (
                  <Link
                    to={assetHref({ ref: `dukascopy/${instrument.label}` })}
                    className="text-xs text-muted-foreground underline decoration-border underline-offset-2 hover:text-foreground"
                  >
                    View asset
                  </Link>
                )}
              </div>
            </li>
          ))}
        </ul>
      </Panel>

      {provider.enabled &&
        provider.instruments.map((instrument) =>
          instrument.built_in ? (
            <HistoryPanel key={instrument.label} title={`${instrument.name}: days fetched`} name={instrument.name} />
          ) : (
            <HistoryPanel
              key={instrument.label}
              label={instrument.label}
              title={`${instrument.name}: days fetched`}
              name={instrument.name}
            />
          ),
        )}

      <HistorySettingsDialog open={editing} onClose={() => setEditing(false)} />
    </>
  )
}

function milliseconds(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : `${formatNumber(Math.round(value))} ms`
}

/** How to give the feed its login and start it: the login sealed for the feed, the profile on the server. */
function SettingUp() {
  return (
    <Panel title="Setting it up" description="Done once: the admin seals the key for the feed and cannot read it back.">
      <ol className="list-inside list-decimal space-y-2 text-sm text-muted-foreground">
        <li>
          In Capital.com, <em>Settings › API integrations</em> (two-factor authentication must be on): make a key and
          choose its password. The demo account is enough.
        </li>
        <li>
          In{' '}
          <Link className="underline underline-offset-2" to="/config/credentials">
            Configuration, Credentials
          </Link>
          , Capital.com login: the key, the account&rsquo;s email and the key&rsquo;s password.
        </li>
        <li>
          On the server, add <code>capital</code> to <code>COMPOSE_PROFILES</code> in <code>/app/wata/.env</code>, switch
          the feed on here, then <code>watastop</code> and <code>watastart</code>.
        </li>
        <li>
          To see what Capital.com gives the key: <code>docker exec capital_feed1 python -m src.capital_feed.probe --epic US100</code>{' '}
          (its quotes, how late they are, and what its volume counts).
        </li>
      </ol>
    </Panel>
  )
}

function CapitalProvider({ provider }: { provider: CapitalProviderDetails }) {
  const { timezone } = useSession()
  const [editing, setEditing] = useState<CapitalDialogStart | null>(null)
  const { settings, feed } = provider
  const figures = feed?.figures ?? {}
  const delays = figures.delay_ms
  const stream = figures.stream
  const live = figures.live
  const minutes = figures.minutes
  const followed = new Map((figures.instruments ?? []).map((instrument) => [instrument.epic, instrument]))
  // Switched on in the file, and not writing: the server has to start it.
  const notRunning = provider.enabled && provider.status === 'down'
  const needsLogin = feed?.state === 'no_credentials' || feed?.state === 'refused'

  return (
    <>
      <PageHeader
        title={provider.name}
        description={`${KIND_LABELS[provider.kind]}. ${provider.description}`}
        actions={
          <Button size="sm" leftIcon={<Settings2 className="size-3.5" />} onClick={() => setEditing('edit')}>
            Edit configuration
          </Button>
        }
      />
      <Overview provider={provider} timeZone={timezone} />
      {provider.restart_required && (
        <Alert tone="warning">
          <AlertDescription>
            <div className="space-y-2">
              <p>The feed runs without settings saved since it started: it applies them at its next start.</p>
              <RestartAction ids={['capital_feed']} />
            </div>
          </AlertDescription>
        </Alert>
      )}
      {notRunning && (
        <Alert tone="danger" role="alert">
          <AlertDescription>
            {needsLogin ? (
              <>
                {provider.detail}. Set it in{' '}
                <Link className="underline underline-offset-2" to="/config/credentials">
                  Configuration, Credentials
                </Link>
                , then restart the feed from the{' '}
                <Link className="underline underline-offset-2" to="/system">
                  System
                </Link>{' '}
                page&apos;s containers.
              </>
            ) : (
              <>
                Switched on in the configuration, and not recording. On the server, <code>capital</code> must be in{' '}
                <code>COMPOSE_PROFILES</code> (then <code>watastop</code>, <code>watastart</code>), or the feed
                restarted, from the <Link className="underline underline-offset-2" to="/system">System</Link> page&apos;s
                containers.
              </>
            )}
          </AlertDescription>
        </Alert>
      )}

      {feed && (
        <Panel
          title="The stream now"
          description={`As the feed wrote it ${formatDateTime(feed.updated_at, timezone)}, over the last minute. The delay is Capital.com's time of a quote to the feed's receipt.`}
        >
          <dl>
            <Row label="Account">{feed.environment === 'live' ? 'Live' : feed.environment === 'demo' ? 'Demo' : feed.environment ?? '—'}</Row>
            <Row label="Quotes">
              {figures.quotes_per_minute === undefined ? '—' : `${formatNumber(figures.quotes_per_minute)} in the last minute`}
            </Row>
            <Row label="Delay">
              {delays && delays.p50 !== null
                ? `Median ${milliseconds(delays.p50)}, 95 % under ${milliseconds(delays.p95)}, worst ${milliseconds(delays.max)}`
                : '—'}
            </Row>
            <Row label="Connection">
              {stream
                ? stream.connected
                  ? `Open since ${formatDateTime(stream.since, timezone)} (${formatNumber(stream.connections)} since the feed started)`
                  : `Connecting${stream.last_error ? `: ${stream.last_error}` : ''}`
                : '—'}
            </Row>
            <Row label="To the charts">
              {live
                ? live.failing
                  ? `Not sent: ${live.last_error ?? 'RabbitMQ unavailable'}`
                  : `${formatNumber(live.sent)} sent through RabbitMQ${live.dropped ? `, ${formatNumber(live.dropped)} dropped` : ''}`
                : '—'}
            </Row>
            <Row label="Capital.com's minutes">
              {minutes
                ? !minutes.enabled
                  ? 'Not fetched'
                  : minutes.error
                    ? `Not fetched: ${minutes.error}`
                    : `Last fetched ${formatDateTime(minutes.last, timezone)}`
                : '—'}
            </Row>
            <Row label="Last bar written">{formatDateTime(figures.last_bar_at ?? null, timezone)}</Row>
            {(figures.pending_buckets ?? 0) > 0 && (
              <Row label="Waiting for the database">{formatNumber(figures.pending_buckets ?? 0)} ten seconds</Row>
            )}
            <Row label="Feed">
              v{feed.version ?? '?'}, started {formatDateTime(feed.started_at, timezone)}
            </Row>
          </dl>
        </Panel>
      )}

      <GapsPanel gaps={provider.gaps_today} timeZone={timezone} hours="the markets’ opening hours" />

      <div className="grid gap-6 xl:grid-cols-2">
        <Panel
          title="Instruments"
          description="Capital.com's epic, and the label WATA gives it: the indice of the rules, the folder of its day files."
          action={
            <Button variant="ghost" size="sm" onClick={() => setEditing('edit')}>
              Change
            </Button>
          }
        >
          <ul className="divide-y divide-border/60 text-sm">
            {settings.instruments.map((instrument) => {
              const state = followed.get(instrument.epic)
              return (
                <li key={instrument.epic} className="flex flex-wrap items-center justify-between gap-3 py-2.5">
                  <div className="min-w-0">
                    <p className="font-medium">
                      {state?.name ?? instrument.epic}{' '}
                      <span className="font-normal text-muted-foreground">
                        · {instrument.epic} as {instrument.label}
                      </span>
                    </p>
                    <p className="text-xs text-muted-foreground">
                      {state
                        ? [
                            state.market === 'open'
                              ? 'Market open'
                              : state.market === 'closed'
                                ? `Market closed${state.next_opening ? `, opens ${formatDateTime(state.next_opening, timezone)}` : ''}`
                                : 'Market not known yet',
                            state.subscription && state.subscription !== 'PROCESSED'
                              ? `Capital.com refused it: ${state.subscription}`
                              : null,
                            state.last_quote_at ? `last quote ${formatDateTime(state.last_quote_at, timezone)}` : null,
                          ]
                            .filter(Boolean)
                            .join(' · ')
                        : 'Not followed by the feed running now'}
                    </p>
                  </div>
                  <Link
                    to={assetHref({ ref: `capital/${instrument.epic}` })}
                    className="text-xs text-muted-foreground underline decoration-border underline-offset-2 hover:text-foreground"
                  >
                    View asset
                  </Link>
                </li>
              )
            })}
          </ul>
        </Panel>
        <Panel
          title="Settings"
          description="trade.config.capital_feed, as the file holds it now."
          action={
            <Button variant="ghost" size="sm" onClick={() => setEditing('edit')}>
              Edit
            </Button>
          }
        >
          <dl>
            <Row label="Recording">{settings.enabled ? 'Switched on' : 'Switched off'}</Row>
            <Row label="Account">{settings.environment === 'live' ? 'Live' : 'Demo'}</Row>
            <Row label="Capital.com's volume">{settings.minute_volume ? 'Fetched for each minute' : 'Not fetched'}</Row>
            <Row label="Day files">{settings.day_files ? 'Written for each day over' : 'Not written'}</Row>
            <Row label="Kept in the database">
              {settings.retention_months === 0
                ? 'For good'
                : `${settings.retention_months} month${settings.retention_months === 1 ? '' : 's'}`}
            </Row>
            <Row label="Said to have stopped after">{formatNumber(settings.alert_after_seconds)} s</Row>
          </dl>
        </Panel>
      </div>

      <Panel
        title="Day files for backtests"
        description="Each day of UTC, once over, as a file of ten-second bars in the format of the Dukascopy days: a backtest can run on it."
      >
        {provider.day_files.every((files) => files.days === 0) ? (
          <p className="text-sm text-muted-foreground">None yet: a day is written once it is over.</p>
        ) : (
          <ul className="divide-y divide-border/60 text-sm">
            {provider.day_files.map((files) => (
              <li key={files.label} className="flex flex-wrap items-center justify-between gap-3 py-2">
                <span className="font-medium">{files.label}</span>
                <span className="tabular-nums text-muted-foreground">
                  {files.days === 0
                    ? 'None yet'
                    : `${formatNumber(files.days)} day${files.days === 1 ? '' : 's'}, ${formatDay(files.first_day)} to ${formatDay(files.last_day)}, ${formatBytes(files.bytes)}`}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      <SettingUp />

      <CapitalSettingsDialog open={editing !== null} start={editing ?? 'edit'} onClose={() => setEditing(null)} />
    </>
  )
}

/** One provider: what it is doing, how it is set, and what it holds. */
export function ProviderPage() {
  const { providerId = '' } = useParams()
  const saxoQuery = useQuery({
    queryKey: ['data-provider', 'saxo'],
    queryFn: api.saxoProvider,
    enabled: providerId === 'saxo',
    refetchInterval: REFRESH_MS,
  })
  const dukascopyQuery = useQuery({
    queryKey: ['data-provider', 'dukascopy'],
    queryFn: api.dukascopyProvider,
    enabled: providerId === 'dukascopy',
    refetchInterval: REFRESH_MS,
  })
  const capitalQuery = useQuery({
    queryKey: ['data-provider', 'capital'],
    queryFn: api.capitalProvider,
    enabled: providerId === 'capital',
    // The feed writes what it does every ten seconds.
    refetchInterval: 10_000,
  })

  if (providerId === 'saxo') {
    return (
      <QueryState isPending={saxoQuery.isPending} error={saxoQuery.error} hasData={!!saxoQuery.data}>
        {saxoQuery.data && <SaxoProvider provider={saxoQuery.data} />}
      </QueryState>
    )
  }
  if (providerId === 'dukascopy') {
    return (
      <QueryState isPending={dukascopyQuery.isPending} error={dukascopyQuery.error} hasData={!!dukascopyQuery.data}>
        {dukascopyQuery.data && <DukascopyProvider provider={dukascopyQuery.data} />}
      </QueryState>
    )
  }
  if (providerId === 'capital') {
    return (
      <QueryState isPending={capitalQuery.isPending} error={capitalQuery.error} hasData={!!capitalQuery.data}>
        {capitalQuery.data && <CapitalProvider provider={capitalQuery.data} />}
      </QueryState>
    )
  }
  // Its own names only: an address like /data/providers/constructor names nothing.
  const name = Object.hasOwn(PROVIDER_NAMES, providerId) ? PROVIDER_NAMES[providerId] : undefined
  if (!name) {
    return (
      <NotFoundState
        title="No such provider"
        description="WATA has no code for a provider of this name."
        suggestions={[{ label: 'Providers', href: PROVIDERS_PATH, description: 'The providers WATA has code for' }]}
      />
    )
  }
  return (
    <>
      <PageHeader title={name} />
      <Panel title={`${name} is not integrated yet`}>
        <p className="text-sm text-muted-foreground">
          The providers WATA has code for are on the{' '}
          <Link to={PROVIDERS_PATH} className="text-foreground underline underline-offset-2">
            Providers
          </Link>{' '}
          page.
        </p>
      </Panel>
    </>
  )
}

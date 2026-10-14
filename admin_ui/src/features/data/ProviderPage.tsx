import { useQuery } from '@tanstack/react-query'
import { Plus, Settings2 } from 'lucide-react'
import { useState } from 'react'
import type { ReactNode } from 'react'
import { Link, useParams } from 'react-router'

import { KpiTile } from '@/components/KpiTile'
import { PageHeader, Panel } from '@/components/Panel'
import { QueryState, ServiceStatusBadge } from '@/components/status'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useSession } from '@/features/auth/auth-context'
import { formatBytes } from '@/features/backtests/model'
import { PricesPanel } from '@/features/backtests/PricesPanel'
import { GapsPanel } from '@/features/prices/GapsPanel'
import { api } from '@/lib/api'
import { formatDateTime, formatDay, formatNumber } from '@/lib/format'
import type { DataProvider, DukascopyProviderDetails, SaxoProviderDetails } from '@/lib/types'

import { assetHref, assetsLink, PROVIDERS_PATH } from './assets'
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
            The configuration changed since the services started: the recorder applies its settings at its next start.
          </AlertDescription>
        </Alert>
      )}
      {notRunning && (
        <Alert tone="danger" role="alert">
          <AlertDescription>
            Switched on in the configuration, and not recording. On the server, <code>recorder</code> must be in{' '}
            <code>COMPOSE_PROFILES</code> (then <code>watastop</code>, <code>watastart</code>), or the recorder restarted:{' '}
            <code>docker restart price_recorder1</code>.
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
          storage ? `The prices take ${formatBytes(storage.prices.bars_bytes + storage.prices.fetched_bytes)} of disk.` : ''
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
                {instrument.built_in && <Badge>What backtests run on</Badge>}
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
            <PricesPanel key={instrument.label} title={`${instrument.name}: days fetched`} name={instrument.name} />
          ) : (
            <PricesPanel
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
  const name = PROVIDER_NAMES[providerId]
  return (
    <>
      <PageHeader title={name ?? 'No such provider'} />
      <Panel title={name ? `${name} is not integrated yet` : 'No such provider'}>
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

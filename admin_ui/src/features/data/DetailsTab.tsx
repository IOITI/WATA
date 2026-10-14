import { Link } from 'react-router'
import type { ReactNode } from 'react'

import { Panel } from '@/components/Panel'
import { formatBytes } from '@/features/backtests/model'
import { instrumentLabel } from '@/features/prices/chartData'
import { formatDateTime, formatDay, formatNumber } from '@/lib/format'
import { actionLabel } from '@/lib/labels'
import type { AssetDetails, HistoryRecording, SaxoRecording } from '@/lib/types'

import { assetHref, assetType, PROVIDER_LABELS, STATUS_LABELS, TYPE_LABELS } from './assets'

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid gap-1 border-t border-border/50 py-2.5 sm:grid-cols-[12rem_minmax(0,1fr)] sm:gap-4">
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words text-sm tabular-nums">{children ?? '—'}</dd>
    </div>
  )
}

function isoOf(epoch: number | null): string | null {
  return epoch === null ? null : new Date(epoch * 1000).toISOString()
}

/** Everything WATA keeps about the asset, and how its prices came to be kept. */
export function DetailsTab({ details, timeZone }: { details: AssetDetails; timeZone: string }) {
  const { asset } = details
  const recorded = asset.provider === 'saxo'
  const recording = details.recording

  return (
    <div className="grid gap-6 xl:grid-cols-2">
      <Panel title="The asset">
        <dl>
          <Row label="Name">{instrumentLabel(asset)}</Row>
          <Row label="Provider">{PROVIDER_LABELS[asset.provider]}</Row>
          <Row label="Type">{TYPE_LABELS[assetType(asset)]}</Row>
          <Row label="Description">{asset.description}</Row>
          <Row label="Symbol">{asset.symbol}</Row>
          {recorded && <Row label="Saxo UIC">{asset.uic}</Row>}
          <Row label="Asset type">{asset.asset_type}</Row>
          <Row label="Currency">{asset.currency}</Row>
          <Row label="Indice">{asset.indice}</Row>
          {asset.role === 'turbo' && (
            <>
              <Row label="Direction">{actionLabel(asset.direction)}</Row>
              <Row label="Knock-out level">
                {asset.barrier === null ? null : new Intl.NumberFormat('en-GB').format(asset.barrier)}
              </Row>
              <Row label="Issuer">{asset.issuer}</Row>
              <Row label="Underlying">
                {details.index ? (
                  <Link to={assetHref(details.index)} className="underline decoration-border underline-offset-2 hover:text-foreground">
                    {instrumentLabel(details.index)}
                  </Link>
                ) : asset.underlying_uic ? (
                  `UIC ${asset.underlying_uic}, not recorded on this day`
                ) : null}
              </Row>
            </>
          )}
          <Row label="Status">{STATUS_LABELS[asset.status]}</Row>
          <Row label="Address">
            <code className="text-xs">{asset.ref}</code>
          </Row>
        </dl>
      </Panel>

      <Panel title={recorded ? 'How it is recorded' : 'How it was fetched'}>
        <dl>
          <Row label="First bar">{formatDateTime(isoOf(asset.first_t), timeZone)}</Row>
          <Row label="Last bar">{formatDateTime(isoOf(asset.last_t), timeZone)}</Row>
          <Row label="Days with data">
            {formatDay(details.first_day)} to {formatDay(details.last_day)}
          </Row>
          {recorded ? <RecordedRows recording={recording as SaxoRecording} timeZone={timeZone} /> : <FetchedRows recording={recording as HistoryRecording} />}
        </dl>
      </Panel>
    </div>
  )
}

function RecordedRows({ recording, timeZone }: { recording: SaxoRecording; timeZone: string }) {
  return (
    <>
      <Row label="First in the recorder’s set">{formatDateTime(recording.first_seen_at, timeZone)}</Row>
      <Row label="Last in it">{formatDateTime(recording.last_seen_at, timeZone)}</Row>
      <Row label="Bars">Ten seconds each, bid and ask, by the price recorder from Saxo’s stream</Row>
      <Row label="Set refreshed">
        {recording.refresh_interval_seconds === null ? null : `Every ${formatNumber(recording.refresh_interval_seconds / 60)} min`}
      </Row>
      <Row label="Volume">
        {recording.record_volume === null ? null : recording.record_volume ? 'Recorded' : 'Not recorded'}
      </Row>
      <Row label="Kept">
        {recording.retention_months === null
          ? null
          : recording.retention_months === 0
            ? 'For good'
            : `For ${recording.retention_months} month${recording.retention_months === 1 ? '' : 's'}: older months are dropped`}
      </Row>
    </>
  )
}

function FetchedRows({ recording }: { recording: HistoryRecording }) {
  return (
    <>
      <Row label="Dukascopy symbol">{recording.symbol}</Row>
      <Row label="Label">{recording.label}</Row>
      <Row label="Days of bars">
        {formatNumber(recording.days)}
        {recording.first_file && recording.last_file
          ? `, from ${formatDay(recording.first_file)} to ${formatDay(recording.last_file)} (days of UTC)`
          : ''}
      </Row>
      <Row label="Files">
        <code className="text-xs">{recording.location}</code>, {formatBytes(recording.bytes)}
      </Row>
      <Row label="Prices">In thousandths in Dukascopy’s files (scale {formatNumber(recording.price_scale)})</Row>
      <Row label="Source">{recording.source}</Row>
      <Row label="Fetched on demand">{recording.fetchable ? 'Yes, from its provider’s page' : 'No longer: removed from the instruments'}</Row>
    </>
  )
}

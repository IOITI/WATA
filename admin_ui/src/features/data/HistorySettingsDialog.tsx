import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  AlertDescription,
  Badge,
  Button,
  formatDateTime,
  formatNumber,
  Input,
  QueryState,
  SettleModal,
  SpringSelect,
  StretchSwitch,
} from 'ioiti-ui'
import { Plus, Trash2 } from 'lucide-react'
import { useState } from 'react'

import { useSession } from '@/features/auth/auth-context'
import { KEEP_DAYS, keepSentence } from '@/features/data/providers'
import { api } from '@/lib/api'
import type { DukascopyProviderDetails, HistoryOffer } from '@/lib/types'

/** What a change here makes out of date: the provider, the list of assets, the days fetched. */
const CHANGED = ['data-provider', 'data-providers', 'data-assets', 'history-coverage']
/**
 * An instrument no longer fetched changes no day: its days are not read again, or the page
 * behind would ask once more for the days of a label that is gone, and be refused.
 */
const REMOVED = ['data-provider', 'data-providers', 'data-assets']

function AddInstrument({ offered, onAdded }: { offered: HistoryOffer[]; onAdded: () => void }) {
  const [symbol, setSymbol] = useState(offered[0]?.symbol ?? '')
  const chosen = offered.find((entry) => entry.symbol === symbol)
  const [label, setLabel] = useState(chosen?.label ?? '')
  const add = useMutation({ mutationFn: () => api.addHistoryInstrument({ symbol, label }), onSuccess: onAdded })

  if (offered.length === 0) {
    return <p className="text-xs text-muted-foreground">Every instrument that can be fetched is already there.</p>
  }
  return (
    <div className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)_auto] sm:items-end">
        <SpringSelect
          label="Instrument"
          options={offered.map((entry) => ({ value: entry.symbol, label: `${entry.name} (${entry.symbol})` }))}
          value={symbol}
          onChange={(next) => {
            setSymbol(next)
            setLabel(offered.find((entry) => entry.symbol === next)?.label ?? '')
          }}
        />
        <label className="space-y-1.5 text-sm">
          <span className="block font-medium">Label</span>
          <Input value={label} onChange={(event) => setLabel(event.target.value)} aria-label="Label" />
        </label>
        <Button size="sm" leftIcon={<Plus className="size-3.5" />} isLoading={add.isPending} onClick={() => add.mutate()}>
          Add
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">
        The label names its folder of prices, and the indice its bars are written for. Only the indices whose prices
        Dukascopy publishes in thousandths, as the downloader reads them, are offered.
      </p>
      {add.error && (
        <Alert tone="danger" role="alert">
          <AlertDescription>{add.error.message}</AlertDescription>
        </Alert>
      )}
    </div>
  )
}

function Settings({ provider }: { provider: DukascopyProviderDetails }) {
  const { timezone } = useSession()
  const queryClient = useQueryClient()
  const refresh = (keys: string[] = CHANGED) =>
    Promise.all(keys.map((key) => queryClient.invalidateQueries({ queryKey: [key] })))
  const keepMutation = useMutation({ mutationFn: api.keepHistory, onSuccess: () => refresh() })
  const removeMutation = useMutation({ mutationFn: api.removeHistoryInstrument, onSuccess: () => refresh(REMOVED) })
  const keep = provider.keep
  const keepDays = keep ? [...new Set([...KEEP_DAYS, keep.days])].sort((a, b) => a - b) : KEEP_DAYS
  const failure = keepMutation.error ?? removeMutation.error

  return (
    <div className="space-y-6">
      {!provider.enabled && (
        <Alert tone="neutral">
          <AlertDescription>
            Downloads are switched off in the configuration (<code>admin.backtests_enabled</code>): nothing can be
            changed here.
          </AlertDescription>
        </Alert>
      )}

      {keep && (
        <section className="space-y-3">
          <h3 className="text-sm font-semibold">Kept up to date</h3>
          <StretchSwitch
            label="Keep the Nasdaq history up to date"
            description={keepSentence(keep, (moment) => formatDateTime(moment, timezone, false))}
            checked={keep.enabled}
            disabled={!provider.enabled || keepMutation.isPending}
            onChange={(enabled) => keepMutation.mutate({ enabled, days: keep.days })}
          />
          <div className="max-w-56">
            <SpringSelect
              label="How far back"
              options={keepDays.map((days) => ({ value: String(days), label: `${days} days` }))}
              value={String(keep.days)}
              disabled={!provider.enabled}
              onChange={(days) => keepMutation.mutate({ enabled: keep.enabled, days: Number(days) })}
            />
          </div>
          <p className="text-xs text-muted-foreground">
            The history backtests read only: the newest missing days first, a few at a time, while the market is closed.
            Another instrument is fetched when asked, from its provider&rsquo;s page.
          </p>
        </section>
      )}

      <section className="space-y-3">
        <h3 className="text-sm font-semibold">Instruments</h3>
        <ul className="divide-y divide-border/60 rounded-xl border border-border/60">
          {provider.instruments.map((instrument) => (
            <li key={instrument.label} className="flex flex-wrap items-center justify-between gap-3 px-3 py-2.5 text-sm">
              <div className="min-w-0">
                <p className="font-medium">
                  {instrument.name} <span className="font-normal text-muted-foreground">· {instrument.symbol}</span>
                </p>
                <p className="text-xs text-muted-foreground">
                  Label {instrument.label} · {formatNumber(instrument.days)} day{instrument.days === 1 ? '' : 's'} fetched
                </p>
              </div>
              {instrument.built_in ? (
                <Badge>Kept up to date</Badge>
              ) : (
                <Button
                  variant="ghost"
                  size="sm"
                  leftIcon={<Trash2 className="size-3.5" />}
                  disabled={!provider.enabled || removeMutation.isPending}
                  aria-label={`Stop fetching ${instrument.name}`}
                  onClick={() => removeMutation.mutate(instrument.label)}
                >
                  Stop fetching
                </Button>
              )}
            </li>
          ))}
        </ul>
        <p className="text-xs text-muted-foreground">
          An instrument no longer fetched keeps its days, and its asset stays on the Assets View.
        </p>
        {provider.enabled && (
          <AddInstrument key={provider.offered.map((entry) => entry.symbol).join()} offered={provider.offered} onAdded={() => refresh()} />
        )}
      </section>

      {failure && (
        <Alert tone="danger" role="alert">
          <AlertDescription>{failure.message}</AlertDescription>
        </Alert>
      )}
    </div>
  )
}

/**
 * The Dukascopy downloads' settings: keeping the Nasdaq history up to date,
 * and the instruments that can be fetched. Nothing here acts on the trading
 * services: changes apply at once, without a review.
 */
export function HistorySettingsDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const providerQuery = useQuery({ queryKey: ['data-provider', 'dukascopy'], queryFn: api.dukascopyProvider, enabled: open })
  return (
    <SettleModal
      open={open}
      onClose={onClose}
      title="Dukascopy settings"
      description="Index history from Dukascopy's public files, for backtests. Another broker's prices than Saxo's."
      size="xl"
      footer={
        <Button variant="ghost" size="sm" onClick={onClose}>
          Close
        </Button>
      }
    >
      <QueryState isPending={providerQuery.isPending} error={providerQuery.error} hasData={!!providerQuery.data}>
        {providerQuery.data && <Settings provider={providerQuery.data} />}
      </QueryState>
    </SettleModal>
  )
}

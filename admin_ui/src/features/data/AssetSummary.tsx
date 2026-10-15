import { keepPreviousData, skipToken, useQuery } from '@tanstack/react-query'
import { ArrowRight } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router'

import { instrumentFacts } from '@/features/prices/availability'
import { dayView } from '@/features/prices/view'
import type { ChartView } from '@/features/prices/view'
import { api } from '@/lib/api'
import type { DataAsset } from '@/lib/types'

import { AssetChart } from './AssetChart'
import { assetHref, capitalFacts, historyFacts, openingDay } from './assets'
import type { AssetRow } from './assets'
import { Facts } from './Facts'

interface AssetSummaryProps {
  asset: AssetRow
  /** Every asset listed: where the index under a turbo is found. */
  assets: readonly DataAsset[]
  timeZone: string
  today: string
}

/**
 * An opened row of the Assets View: what the former list of instruments said
 * of the asset, for its last day with data, and the chart of that day. The
 * rest is on its page.
 */
export function AssetSummary({ asset, assets, timeZone, today }: AssetSummaryProps) {
  // Its own bars and side, not the page's: opening another row starts again from its day.
  const [view, setView] = useState<ChartView>(() => dayView(openingDay(asset, timeZone, today)))
  const recorded = asset.provider === 'saxo'
  // Under a turbo, the index of its indice the recorder kept.
  const index =
    recorded && asset.role === 'turbo'
      ? (assets.find((item) => item.provider === 'saxo' && item.role === 'underlying' && item.indice === asset.indice) ?? null)
      : null
  const streamed = asset.provider === 'capital'
  const live = (recorded || streamed) && asset.status === 'recording' && view.to >= today

  // The day it shows, shared with the page's own reading of today: the same request.
  const availabilityQuery = useQuery({
    queryKey: ['prices', 'availability', view.from, view.to],
    queryFn: recorded ? () => api.priceAvailability({ from: view.from, to: view.to }) : skipToken,
    placeholderData: keepPreviousData,
  })
  // The volume Saxo counted comes with what goes on the chart: the chart asks for it anyway.
  const contextQuery = useQuery({
    queryKey: ['asset-context', asset.ref, view.from, view.to],
    queryFn: () => api.assetContext(asset.ref, { from: view.from, to: view.to }),
    placeholderData: keepPreviousData,
  })
  const availability = availabilityQuery.data
  const facts = recorded
    ? instrumentFacts({ ...asset, volume: contextQuery.data?.volume ?? null }, availability, timeZone)
    : streamed
      ? capitalFacts(asset, timeZone)
      : historyFacts(asset, timeZone)

  return (
    <div className="grid gap-5 lg:grid-cols-[17rem_minmax(0,1fr)]">
      <div className="flex flex-col gap-3">
        <Facts facts={facts} id={`asset-facts-${asset.ref}`} />
        {recorded && availability === undefined && <p className="text-xs text-muted-foreground">Reading what was recorded of it…</p>}
        <Link
          to={assetHref(asset)}
          className="inline-flex w-fit items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs font-medium transition-colors hover:bg-foreground/5"
        >
          Open its page
          <ArrowRight aria-hidden className="size-3.5" />
        </Link>
      </div>
      <AssetChart asset={asset} index={index} view={view} onViewChange={setView} live={live} variant="compact" />
    </div>
  )
}

// Technical Analysis › Charts: WATA's assets on Vela, with the scripts written in
// the Indicators page in Vela's own indicator picker. Live where WATA's data is.

import { useQuery } from '@tanstack/react-query'
import { useCallback, useMemo, useState } from 'react'
import { useLocation, useNavigate } from 'react-router'

import { PageHeader } from '@/components/Panel'
import { QueryState } from '@/components/status'
import { Alert } from '@/components/ui/alert'
import { useSession } from '@/features/auth/auth-context'
import { api } from '@/lib/api'

import { TaChart } from './chart/TaChart'
import { LAYOUT_KEY, layoutFor } from './chart/workspace'
import { assetTicker, chartsPath, chartsView, testableAssets, velaSymbol } from './chartLinks'
import { useLibraryResolver } from './editor/useScriptCheck'
import { parseTicker, tickerText } from './engine/tickers'

/** What a chart opens on without a word from its address or its layout: the live index first. */
const PREFERENCE = ['capital', 'dukascopy', 'saxo'] as const

export function ChartsPage() {
  const { timezone } = useSession()
  const location = useLocation()
  const navigate = useNavigate()
  const resolve = useLibraryResolver()
  const view = chartsView(location.search)
  const [skipped, setSkipped] = useState<{ name: string; reason: string }[]>([])

  const layoutQuery = useQuery({
    queryKey: ['ta-workspace', LAYOUT_KEY],
    queryFn: () => api.taWorkspace(LAYOUT_KEY),
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  })
  const assetsQuery = useQuery({ queryKey: ['data-assets', 'all'], queryFn: () => api.dataAssets('all'), staleTime: 60_000 })

  const fallback = useMemo(() => {
    const assets = testableAssets(assetsQuery.data?.items ?? [])
    for (const provider of PREFERENCE) {
      const asset = assets.find((candidate) => candidate.provider === provider && candidate.role !== 'turbo')
      const ticker = asset ? assetTicker(asset) : null
      if (ticker) return velaSymbol(ticker)
    }
    return 'capital:CAPITAL_US100'
  }, [assetsQuery.data])

  // What the page opened on goes into the saved layout before the chart is built; later
  // addresses are followed by the chart itself.
  const [opened] = useState(() => ({
    symbol: view.ticker ? velaSymbol(view.ticker) : undefined,
    timeframe: view.timeframe ?? undefined,
  }))
  const symbol = view.ticker ? velaSymbol(view.ticker) : null
  const ready = !layoutQuery.isPending && !assetsQuery.isPending

  // The address follows the chart, so that it can be shared, reloaded or bookmarked.
  const onMarket = useCallback(
    (marketSymbol: string, marketTimeframe: string) => {
      const ticker = parseTicker(marketSymbol)
      if (!ticker) return
      navigate(chartsPath({ symbol: tickerText(ticker), tf: marketTimeframe }), { replace: true })
    },
    [navigate],
  )
  const onScriptAdded = useCallback(() => {
    if (!view.script) return
    navigate(chartsPath({ symbol: view.ticker ? tickerText(view.ticker) : undefined, tf: view.timeframe ?? undefined }), {
      replace: true,
    })
  }, [navigate, view.script, view.ticker, view.timeframe])

  return (
    <>
      <PageHeader
        title="Charts"
        description="WATA's prices on Vela, with your Pine scripts in its indicator list. Capital.com moves live, Saxo every ten seconds; Dukascopy is history."
      />
      {skipped.length > 0 && (
        <Alert tone="warning">
          {skipped.length === 1 ? 'A script is' : `${skipped.length} scripts are`} not in the indicator list:{' '}
          {skipped.map((entry) => `${entry.name} (${entry.reason})`).join('; ')}.
        </Alert>
      )}
      <QueryState isPending={!ready} error={layoutQuery.error} hasData={ready}>
        {ready && (
          <TaChart
            layout={layoutFor(layoutQuery.data?.value ?? null, opened)}
            symbol={symbol}
            timeframe={view.timeframe}
            fallbackSymbol={fallback}
            timeZone={timezone}
            resolve={resolve}
            addScript={view.script}
            onScriptAdded={onScriptAdded}
            onMarket={onMarket}
            onSkipped={setSkipped}
            className="h-[calc(100dvh-13rem)]"
          />
        )}
      </QueryState>
    </>
  )
}

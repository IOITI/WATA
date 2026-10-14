import { keepPreviousData, skipToken, useQuery } from '@tanstack/react-query'
import { useMemo } from 'react'
import { Link, useSearchParams } from 'react-router'

import { PageHeader, Panel } from '@/components/Panel'
import { QueryState } from '@/components/status'
import { AdvancedDataTable } from '@/components/ui/advanced-data-table'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { useSession } from '@/features/auth/auth-context'
import { availabilityOf, flagText, isFlagged } from '@/features/prices/availability'
import { todayIn } from '@/features/prices/chartData'
import { api } from '@/lib/api'
import type { AssetSeen } from '@/lib/types'
import { cn } from '@/lib/utils'

import { assetColumns } from './assetColumns'
import { assetRows, dayOf, parseSeen, PROVIDERS_PATH, SEEN, SEEN_LABELS, tableFilters } from './assets'
import type { AssetRow } from './assets'
import { AssetSummary } from './AssetSummary'

/** The list is read again now and then: recorded turbos come and go through the day. */
const REFRESH_MS = 60_000
/** What is missing from each instrument today is read from every bar of the day: less often. */
const AVAILABILITY_REFRESH_MS = 120_000

function SeenFilter({ seen, onChange }: { seen: AssetSeen; onChange: (seen: AssetSeen) => void }) {
  return (
    <div role="group" aria-label="Recorded within" className="flex flex-wrap items-center gap-2">
      {SEEN.map((option) => (
        <Button
          key={option}
          variant={option === seen ? 'primary' : 'outline'}
          size="sm"
          aria-pressed={option === seen}
          onClick={() => onChange(option)}
        >
          {SEEN_LABELS[option]}
        </Button>
      ))}
    </div>
  )
}

function NothingStored({ seen }: { seen: AssetSeen }) {
  return (
    <Panel title={seen === 'all' ? 'No price stored yet' : `No asset recorded within ${SEEN_LABELS[seen].toLowerCase()}`}>
      <div className="max-w-2xl space-y-3 text-sm text-muted-foreground">
        <p>
          Prices are kept by two providers: Saxo&rsquo;s stream, recorded by the price recorder, and the history
          downloaded from Dukascopy for backtests. Neither has stored anything{seen === 'all' ? '' : ' in this window'}.
        </p>
        <p>
          The{' '}
          <Link to={PROVIDERS_PATH} className="text-foreground underline underline-offset-2">
            Providers
          </Link>{' '}
          page says what each is doing, and how to switch it on.
        </p>
      </div>
    </Panel>
  )
}

/**
 * Every asset whose prices WATA keeps: the turbos and their index recorded
 * from Saxo, the history downloaded from Dukascopy. A row opens on a click to
 * what is known of the asset and the chart of its last day; its name leads to
 * its own page. The filters and the search can be given in the address: a
 * provider's "View assets" opens the table on its own.
 */
export function AssetsPage() {
  const { timezone } = useSession()
  const [searchParams, setSearchParams] = useSearchParams()
  const seen = parseSeen(searchParams.get('seen'))
  const today = todayIn(timezone)

  const assetsQuery = useQuery({
    queryKey: ['data-assets', seen],
    queryFn: () => api.dataAssets(seen),
    refetchInterval: REFRESH_MS,
    placeholderData: keepPreviousData,
  })
  const answer = assetsQuery.data
  const rows = useMemo<AssetRow[]>(() => assetRows(answer?.items ?? []), [answer])
  // Only today is read again by itself: what Saxo left without a quote today, as the former list did.
  const quotedToday = rows.some((row) => row.provider === 'saxo' && row.last_t !== null && dayOf(row.last_t, timezone) === today)
  const availabilityQuery = useQuery({
    queryKey: ['prices', 'availability', today, today],
    queryFn: quotedToday ? () => api.priceAvailability({ from: today, to: today }) : skipToken,
    refetchInterval: AVAILABILITY_REFRESH_MS,
    staleTime: AVAILABILITY_REFRESH_MS / 2,
  })
  const flags = useMemo(() => {
    const found = new Map<string, string>()
    for (const row of rows) {
      if (row.last_t === null || dayOf(row.last_t, timezone) !== today) continue
      const item = availabilityOf(availabilityQuery.data, row)
      if (item && isFlagged(item)) found.set(row.ref, flagText(item, timezone, false))
    }
    return found
  }, [rows, availabilityQuery.data, timezone, today])
  const columns = useMemo(() => assetColumns(timezone, flags), [timezone, flags])

  const filters = useMemo(() => tableFilters(searchParams), [searchParams])
  const search = searchParams.get('q') ?? ''
  // The table keeps its filters itself: another link (another provider) starts it again.
  const tableKey = [seen, ...['provider', 'type', 'status', 'q'].map((name) => searchParams.get(name) ?? '')].join('|')
  const title =
    flags.size > 0
      ? `${rows.length} assets · ${flags.size} with missing data today`
      : `${rows.length} asset${rows.length === 1 ? '' : 's'}`

  return (
    <>
      <PageHeader
        title="Assets View"
        description={`Every asset whose prices WATA keeps: the turbos and their index recorded from Saxo, the history downloaded from Dukascopy. Click a row to see its last day; its name opens its page. Times are in ${timezone}.`}
        actions={
          <SeenFilter
            seen={seen}
            onChange={(next) => {
              const params = new URLSearchParams(searchParams)
              if (next === 'all') params.delete('seen')
              else params.set('seen', next)
              setSearchParams(params, { replace: true })
            }}
          />
        }
      />

      {answer?.saxo_error && (
        <Alert tone="warning" role="alert">
          <AlertDescription>{answer.saxo_error}. The Dukascopy history is listed all the same.</AlertDescription>
        </Alert>
      )}
      {answer?.truncated && (
        <Alert tone="neutral">
          <AlertDescription>
            The {answer.max} instruments recorded last are listed, the oldest left out: choose a shorter window above to
            see those of a period.
          </AlertDescription>
        </Alert>
      )}

      <QueryState isPending={assetsQuery.isPending} error={assetsQuery.error} hasData={!!answer}>
        {answer && rows.length === 0 && !answer.saxo_error ? (
          <NothingStored seen={seen} />
        ) : (
          <div className={cn('transition-opacity', assetsQuery.isPlaceholderData && 'opacity-60')}>
            <AdvancedDataTable<AssetRow>
              key={tableKey}
              title={title}
              data={rows}
              columns={columns}
              getRowId={(row) => row.ref}
              rowLabel={(row) => row.name}
              selectable={false}
              expandOnRowClick
              singleExpand
              initialFilters={filters}
              initialSearch={search}
              defaultPageSize={20}
              searchPlaceholder="Name, level, issuer, symbol…"
              emptyTitle="No asset"
              renderSubComponent={(row) => <AssetSummary asset={row} assets={rows} timeZone={timezone} today={today} />}
            />
          </div>
        )}
      </QueryState>
    </>
  )
}

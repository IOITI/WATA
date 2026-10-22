import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Plus } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Link } from 'react-router'

import { ButtonLink } from '@/components/ButtonLink'
import { PageHeader, Panel } from '@/components/Panel'
import { QueryState, ServiceStatusBadge } from '@/components/status'
import { AdvancedDataTable } from '@/components/ui/advanced-data-table'
import type { ColumnDef } from '@/components/ui/advanced-data-table'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { SettleModal } from '@/components/ui/settle-modal'
import { useSession } from '@/features/auth/auth-context'
import { api } from '@/lib/api'
import { formatDateTime, formatDay, formatNumber } from '@/lib/format'
import { isPlainClick } from '@/lib/links'
import type { DataProvider, DataProviders } from '@/lib/types'
import { cn } from '@/lib/utils'

import { assetsLink, PROVIDERS_PATH } from './assets'
import { CapitalSettingsDialog } from './CapitalSettingsDialog'
import type { CapitalDialogStart } from './CapitalSettingsDialog'
import { HistorySettingsDialog } from './HistorySettingsDialog'
import { catalogItems, KIND_LABELS, listedProviders } from './providers'
import type { CatalogItem } from './providers'
import { RecorderSettingsDialog } from './RecorderSettingsDialog'
import type { RecorderDialogStart } from './RecorderSettingsDialog'

/** The state of the providers is read again now and then: the recorder writes every ten seconds. */
const REFRESH_MS = 30_000
// The providers switched on from their settings, in a dialog; the others are set up on their own page.
const SET_UP_IN_A_DIALOG = new Set(['saxo', 'capital'])

type Editing =
  | { provider: 'saxo'; start: RecorderDialogStart }
  | { provider: 'capital'; start: CapitalDialogStart }
  | { provider: 'dukascopy' }
  | null

const linkClass = 'text-xs text-muted-foreground underline decoration-border underline-offset-2 hover:text-foreground'

function isoOf(epoch: number | null): string | null {
  return epoch === null ? null : new Date(epoch * 1000).toISOString()
}

function providerColumns(timeZone: string, edit: (provider: DataProvider) => void): ColumnDef<DataProvider>[] {
  return [
    {
      id: 'name',
      header: 'Provider',
      accessorKey: 'name',
      cell: ({ row }) => (
        <div className="min-w-0">
          <Link
            to={`${PROVIDERS_PATH}/${row.id}`}
            className="font-medium text-foreground underline decoration-border underline-offset-2 hover:decoration-foreground"
          >
            {row.name}
          </Link>
          <p className="line-clamp-2 max-w-[18rem] whitespace-normal text-xs text-muted-foreground" title={row.description}>
            {row.description}
          </p>
        </div>
      ),
    },
    { id: 'kind', header: 'Kind', accessorKey: 'kind', cell: ({ row }) => KIND_LABELS[row.kind] },
    {
      id: 'status',
      header: 'Status',
      accessorKey: 'status',
      cell: ({ row }) => (
        <div className="flex min-w-0 flex-col gap-1">
          <ServiceStatusBadge status={row.status} />
          <span className="max-w-[18rem] whitespace-normal text-xs text-muted-foreground">{row.detail}</span>
        </div>
      ),
    },
    {
      id: 'assets',
      header: 'Assets',
      accessorKey: 'assets',
      align: 'right',
      cell: ({ row }) =>
        row.assets === null ? (
          '—'
        ) : (
          <Link to={assetsLink({ provider: row.id })} className="tabular-nums underline decoration-border underline-offset-2 hover:decoration-foreground">
            {formatNumber(row.assets)}
          </Link>
        ),
    },
    {
      id: 'span',
      header: 'Data',
      accessorKey: 'first_day',
      cell: ({ row }) =>
        row.first_day === null || row.last_day === null ? '—' : `${formatDay(row.first_day)} to ${formatDay(row.last_day)}`,
    },
    {
      id: 'last',
      header: 'Last data',
      accessorKey: 'last_t',
      cell: ({ row }) => formatDateTime(isoOf(row.last_t), timeZone, false),
    },
    {
      id: 'actions',
      header: '',
      align: 'right',
      // One under the other: side by side, they would push the table past the width of a laptop.
      cell: ({ row }) => (
        <div className="flex flex-col items-end gap-1.5 whitespace-nowrap">
          <Link to={`${PROVIDERS_PATH}/${row.id}`} className={linkClass}>
            View details
          </Link>
          <button type="button" className={cn(linkClass, 'cursor-pointer')} onClick={() => edit(row)}>
            Edit configuration
          </button>
          <Link to={assetsLink({ provider: row.id })} className={linkClass}>
            View assets
          </Link>
        </div>
      ),
    },
  ]
}

function CatalogDialog({
  open,
  answer,
  onClose,
  onSetUp,
}: {
  open: boolean
  answer: DataProviders | undefined
  onClose: () => void
  onSetUp: (item: CatalogItem) => void
}) {
  return (
    <SettleModal
      open={open}
      onClose={onClose}
      title="Add a provider"
      description="The providers WATA has code for, and the one being written. Each needs its code: a new one is added to WATA, not here."
      size="lg"
      footer={
        <Button variant="ghost" size="sm" onClick={onClose}>
          Close
        </Button>
      }
    >
      <ul className="space-y-3">
        {catalogItems(answer).map((item) => (
          <li key={item.entry.id} className="rounded-xl border border-border/60 p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="text-sm font-medium">
                  {item.entry.name} <span className="font-normal text-muted-foreground">· {KIND_LABELS[item.entry.kind]}</span>
                </p>
                <p className="mt-1 text-xs text-muted-foreground">{item.entry.description}</p>
              </div>
              {item.state === 'active' && (
                <Badge tone="success" dot>
                  Active
                </Badge>
              )}
              {item.state === 'coming' && <Badge>Coming later</Badge>}
              {item.state === 'available' &&
                (SET_UP_IN_A_DIALOG.has(item.entry.id) ? (
                  <Button size="sm" onClick={() => onSetUp(item)}>
                    Set up
                  </Button>
                ) : (
                  // Set up on its own page: a link, which can be opened in another tab.
                  <ButtonLink
                    to={`${PROVIDERS_PATH}/${item.entry.id}`}
                    size="sm"
                    onClick={(event) => {
                      if (isPlainClick(event)) onClose()
                    }}
                  >
                    Set up
                  </ButtonLink>
                ))}
            </div>
            {item.state === 'available' && item.entry.id === 'saxo' && (
              <p className="mt-2 text-xs text-muted-foreground">
                Switches the recorder on in the configuration. On the server, <code>recorder</code> must then be added to{' '}
                <code>COMPOSE_PROFILES</code>, and the application stopped and started (<code>watastop</code>,{' '}
                <code>watastart</code>).
              </p>
            )}
            {item.state === 'available' && item.entry.id === 'capital' && (
              <p className="mt-2 text-xs text-muted-foreground">
                Switches the feed on in the configuration. On the server, its login goes in <code>.env</code> (three{' '}
                <code>CAPITAL_*</code> lines: a key made in Capital.com&rsquo;s settings, a demo account is enough),{' '}
                <code>capital</code> is added to <code>COMPOSE_PROFILES</code>, and the application is stopped and started.
              </p>
            )}
            {item.state === 'available' && item.entry.id === 'dukascopy' && (
              <p className="mt-2 text-xs text-muted-foreground">
                Nothing to switch on: fetch a first period from its page, or have the Nasdaq history kept up to date.
              </p>
            )}
          </li>
        ))}
      </ul>
    </SettleModal>
  )
}

/**
 * The providers of the prices WATA keeps: what each is doing, how many
 * assets it holds and since when, with its configuration a click away.
 * Those not switched on yet are in "Add provider".
 */
export function ProvidersPage() {
  const { timezone } = useSession()
  const providersQuery = useQuery({
    queryKey: ['data-providers'],
    queryFn: api.dataProviders,
    refetchInterval: REFRESH_MS,
    placeholderData: keepPreviousData,
  })
  const answer = providersQuery.data
  const listed = listedProviders(answer)
  const [catalogOpen, setCatalogOpen] = useState(false)
  const [editing, setEditing] = useState<Editing>(null)
  const columns = useMemo(
    () =>
      providerColumns(timezone, (provider) =>
        setEditing(
          provider.id === 'saxo'
            ? { provider: 'saxo', start: 'edit' }
            : provider.id === 'capital'
              ? { provider: 'capital', start: 'edit' }
              : { provider: 'dukascopy' },
        ),
      ),
    [timezone],
  )

  return (
    <>
      <PageHeader
        title="Providers"
        description={`Where the prices WATA keeps come from: streams recorded live, and history downloaded for backtests. Times are in ${timezone}.`}
        actions={
          <Button size="sm" leftIcon={<Plus className="size-3.5" />} onClick={() => setCatalogOpen(true)}>
            Add provider
          </Button>
        }
      />

      <QueryState isPending={providersQuery.isPending} error={providersQuery.error} hasData={!!answer}>
        {listed.length === 0 ? (
          <Panel title="No provider is switched on yet">
            <div className="max-w-2xl space-y-3 text-sm text-muted-foreground">
              <p>Nothing is recorded from Saxo and no history was downloaded: no price is kept.</p>
              <Button size="sm" leftIcon={<Plus className="size-3.5" />} onClick={() => setCatalogOpen(true)}>
                Add provider
              </Button>
            </div>
          </Panel>
        ) : (
          <AdvancedDataTable<DataProvider>
            title={`${listed.length} provider${listed.length === 1 ? '' : 's'}`}
            data={listed}
            columns={columns}
            getRowId={(row) => row.id}
            selectable={false}
            defaultPageSize={10}
            searchPlaceholder="Search a provider…"
          />
        )}
      </QueryState>

      <CatalogDialog
        open={catalogOpen}
        answer={answer}
        onClose={() => setCatalogOpen(false)}
        onSetUp={(item) => {
          setCatalogOpen(false)
          if (item.entry.id === 'saxo') setEditing({ provider: 'saxo', start: 'enable' })
          else if (item.entry.id === 'capital') setEditing({ provider: 'capital', start: 'enable' })
        }}
      />
      <RecorderSettingsDialog
        open={editing?.provider === 'saxo'}
        start={editing?.provider === 'saxo' ? editing.start : 'edit'}
        onClose={() => setEditing(null)}
      />
      <HistorySettingsDialog open={editing?.provider === 'dukascopy'} onClose={() => setEditing(null)} />
      <CapitalSettingsDialog
        open={editing?.provider === 'capital'}
        start={editing?.provider === 'capital' ? editing.start : 'edit'}
        onClose={() => setEditing(null)}
      />
    </>
  )
}

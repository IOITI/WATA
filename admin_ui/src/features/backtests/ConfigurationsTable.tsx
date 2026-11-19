// The table of configurations, the Backtests page's first view: the runs of the same things but their days
// (configurations.ts), each with what it runs and its latest result, its verdict from the list itself (verdict.ts),
// whether it is made again each evening; opened, its newest runs. A basket compares their latest results or removes
// their runs.

import { AdvancedDataTable, Badge, ButtonLink, formatDateTime, formatDay, formatPercent, formatSpan, Hint, SignedValue } from 'ioiti-ui'
import type { ColumnDef } from 'ioiti-ui'
import { RouterLink } from 'ioiti-ui/react-router'
import { Columns3, Moon, Trash2 } from 'lucide-react'
import { useCallback, useMemo, useState } from 'react'
import { Link } from 'react-router'

import { MAX_COMPARED } from '@/components/charts/compare'
import { ClaudeBadge } from '@/components/status'
import { useSession } from '@/features/auth/auth-context'
import type { BacktestConfiguration, BacktestConfigurationRun } from '@/lib/types'

import { configurationHref, daysBetween, DERIVED_LABEL, distinctSaid, keptSentence } from './configurations'
import { sourceLabel } from './model'
import { RunStateBadge } from './parts'
import { useRemoveRuns } from './pending-removals-context'
import { VerdictCell } from './RunsTable'
import { VERDICT_RANK } from './verdict'
import type { Judged } from './verdict'

/** A configuration with runs it may remove, or a latest result to compare, has a box. */
const selectable = (item: BacktestConfiguration) => item.removable.length > 0 || item.latest !== null
const nameOf = (item: BacktestConfiguration) => item.name

/** Its badges: made again each evening, a sweep, Claude's, made with older code. */
export function ConfigurationBadges({ item }: { item: BacktestConfiguration }) {
  return (
    <>
      {item.nightly?.on && (
        <Hint content="Made again after each trading day, its period stretched to the day just over">
          <Badge tone="neutral" className="ml-2 align-middle">
            <Moon className="size-3" aria-hidden /> Each evening
          </Badge>
        </Hint>
      )}
      {item.kind === 'sweep' && (
        <Badge tone="neutral" className="ml-2 align-middle">
          Sweep
        </Badge>
      )}
      {item.made_by.includes('claude') && <ClaudeBadge className="ml-2" />}
    </>
  )
}

function columnsOf(
  timezone: string,
  judged: ReadonlyMap<string, Judged>,
  tagHref: (tag: string) => string,
): ColumnDef<BacktestConfiguration>[] {
  const headline = (item: BacktestConfiguration) => item.latest?.headline ?? null
  return [
    {
      id: 'configuration',
      header: 'Configuration',
      accessorFn: (item) => [item.name, sourceLabel(item), ...item.tags, distinctSaid(item.distinct)].join(' '),
      sortable: true,
      cell: ({ row }) => (
        <div className="max-w-96 whitespace-normal [td_&]:min-w-56">
          <Link to={configurationHref(row.id)} className="font-medium underline-offset-4 hover:underline">
            {row.name}
          </Link>
          <ConfigurationBadges item={row} />
          <p className="text-xs text-muted-foreground">
            {sourceLabel(row)}
            {row.distinct.length > 0 && <span> · {distinctSaid(row.distinct)}</span>}
          </p>
          <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
            {row.latest ? (
              <span>
                {formatDay(row.latest.from)} to {formatDay(row.latest.to)}
              </span>
            ) : (
              <span>No finished run yet</span>
            )}
            {row.tags.map((tag) => (
              <Link key={tag} to={tagHref(tag)} aria-label={`Only the configurations tagged ${tag}`} className="rounded-full">
                <Badge tone="neutral">{tag}</Badge>
              </Link>
            ))}
            {row.latest?.stale && (
              <Hint content="Its latest run was made by other code than this admin's: it could come out otherwise today" wrap>
                <span className="text-warning">made with other code</span>
              </Hint>
            )}
          </p>
        </div>
      ),
    },
    {
      id: 'runs',
      header: 'Runs',
      accessorFn: (item) => item.count,
      sortable: true,
      align: 'right',
      cell: ({ row }) => row.count,
    },
    {
      id: 'verdict',
      header: 'Verdict',
      accessorFn: (item) => {
        const found = judged.get(item.id)
        return found ? VERDICT_RANK[found.verdict.tone] : null
      },
      sortable: true,
      cell: ({ row }) => (row.latest ? <VerdictCell run={row.latest} judged={judged.get(row.id) ?? null} judges={null} /> : '-'),
    },
    {
      id: 'days',
      header: 'Days',
      accessorFn: (item) => headline(item)?.days_tested ?? null,
      sortable: true,
      align: 'right',
      cell: ({ row }) => headline(row)?.days_tested ?? '-',
    },
    {
      id: 'trades',
      header: 'Trades',
      accessorFn: (item) => headline(item)?.trades ?? null,
      sortable: true,
      align: 'right',
      cell: ({ row }) =>
        row.kind === 'sweep' ? (
          <span className="text-muted-foreground">{headline(row)?.cells ?? '-'} combinations</span>
        ) : (
          (headline(row)?.trades ?? '-')
        ),
    },
    {
      id: 'result',
      header: 'All days',
      accessorFn: (item) => headline(item)?.total_percent ?? null,
      sortable: true,
      align: 'right',
      cell: ({ row }) => <SignedValue value={headline(row)?.total_percent} kind="percent" />,
    },
    {
      id: 'money',
      header: 'In money',
      accessorFn: (item) => headline(item)?.profit_loss ?? null,
      sortable: true,
      align: 'right',
      cell: ({ row }) => <SignedValue value={headline(row)?.profit_loss} kind="money" />,
    },
    {
      id: 'beaten',
      header: 'Beats random',
      accessorFn: (item) => headline(item)?.beaten ?? null,
      sortable: true,
      align: 'right',
      cell: ({ row }) => formatPercent(headline(row)?.beaten, false, 0),
    },
    {
      id: 'updated',
      header: 'Last run',
      accessorFn: (item) => item.updated_at ?? '',
      sortable: true,
      cell: ({ row }) => formatDateTime(row.updated_at, timezone, false),
    },
  ]
}

/** One run of a configuration, in its opened row or on its page: when, over which days, what it found. */
export function ConfigurationRunLine({ run, timezone }: { run: BacktestConfigurationRun; timezone: string }) {
  const kept = keptSentence(run)
  return (
    <li className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-sm">
      <Link to={`/backtests/${run.id}`} className="font-medium underline-offset-2 hover:underline">
        {formatDateTime(run.created_at, timezone, false)}
      </Link>
      <span className="text-muted-foreground">
        {formatDay(run.from, false)} to {formatDay(run.to, false)} · {daysBetween(run.from, run.to)} days
      </span>
      {run.state === 'done' ? (
        run.total_percent !== null && (
          <span>
            <SignedValue value={run.total_percent} kind="percent" /> in {run.trades ?? 0} trade{run.trades === 1 ? '' : 's'}
          </span>
        )
      ) : (
        <RunStateBadge state={run.state} />
      )}
      {run.nightly && (
        <Badge tone="neutral">
          <Moon className="size-3" aria-hidden /> Nightly
        </Badge>
      )}
      {run.derived && <Badge tone="neutral">{DERIVED_LABEL[run.derived]}</Badge>}
      {kept && <span className="text-xs text-muted-foreground">{kept}</span>}
      {run.remade_by && <span className="text-xs text-muted-foreground">made again on today’s code since</span>}
      {run.stale && !run.remade_by && <span className="text-xs text-warning">made with other code</span>}
      {run.state === 'done' && run.seconds !== null && (
        <span className="text-xs text-muted-foreground">took {formatSpan(run.seconds)}</span>
      )}
    </li>
  )
}

/** A configuration opened in its row: its newest runs, and its page. */
function OpenedConfiguration({ item, timezone }: { item: BacktestConfiguration; timezone: string }) {
  return (
    <div className="flex flex-col gap-3 py-1">
      <ul className="flex flex-col gap-1.5">
        {item.runs.map((run) => (
          <ConfigurationRunLine key={run.id} run={run} timezone={timezone} />
        ))}
      </ul>
      <div className="flex flex-wrap items-center gap-3">
        <ButtonLink to={configurationHref(item.id)} size="sm" variant="outline">
          Open the configuration
        </ButtonLink>
        {item.count > item.runs.length && (
          <span className="text-xs text-muted-foreground">
            The newest {item.runs.length} of its {item.count} runs: its page lists every one.
          </span>
        )}
      </div>
    </div>
  )
}

export function ConfigurationsTable({
  rows,
  judged,
  tableKey,
  tagHref,
  emptyTitle,
  emptyDescription,
}: {
  rows: readonly BacktestConfiguration[]
  judged: ReadonlyMap<string, Judged>
  /** What is shown: a change starts the table over, its pages, sort and search being of another list. */
  tableKey: string
  tagHref: (tag: string) => string
  emptyTitle: string
  emptyDescription: string
}) {
  const { timezone } = useSession()
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set())
  const removeRuns = useRemoveRuns()
  const columns = useMemo(() => columnsOf(timezone, judged, tagHref), [timezone, judged, tagHref])
  const chosen = useMemo(() => rows.filter((item) => selected.has(item.id) && selectable(item)), [rows, selected])
  const chosenIds = useMemo(() => new Set(chosen.map((item) => item.id)), [chosen])
  // Their latest results, finished runs of one combination alone.
  const compared = chosen.flatMap((item) => (item.latest && item.latest.kind === 'run' ? [item.latest] : []))
  const canCompare = compared.length >= 2 && compared.length <= MAX_COMPARED && compared.length === chosen.length
  const removable = chosen.flatMap((item) => item.removable.map((id) => ({ id, name: item.name })))

  const remove = useCallback(() => {
    removeRuns(removable)
    setSelected(new Set())
  }, [removeRuns, removable])

  return (
    <AdvancedDataTable<BacktestConfiguration>
      key={tableKey}
      title="Configurations"
      searchPlaceholder="Search…"
      emptyTitle={emptyTitle}
      emptyDescription={emptyDescription}
      data={[...rows]}
      columns={columns}
      getRowId={(item) => item.id}
      defaultPageSize={10}
      rowLabel={nameOf}
      expandOnRowClick
      singleExpand
      renderSubComponent={(item) => <OpenedConfiguration item={item} timezone={timezone} />}
      isRowSelectable={selectable}
      selectedRowIds={chosenIds}
      onSelectionChange={setSelected}
      selectionBasket={{
        noun: ['configuration', 'configurations'],
        linkComponent: RouterLink,
        actions: [
          {
            id: 'compare',
            label: canCompare ? `Compare ${compared.length}` : 'Compare',
            icon: <Columns3 className="size-3.5" />,
            href: canCompare ? `/backtests/compare?runs=${compared.map((run) => run.id).join(',')}` : undefined,
            disabled: !canCompare,
            title: canCompare ? undefined : `The latest results of two to ${MAX_COMPARED} configurations of runs can be compared`,
          },
          {
            id: 'remove',
            label: removable.length > 0 ? `Remove ${removable.length} run${removable.length === 1 ? '' : 's'}` : 'Remove',
            icon: <Trash2 className="size-3.5" />,
            variant: 'danger',
            disabled: removable.length === 0,
            title: removable.length === 0 ? 'Their runs wait, run, or are a reference: none can be removed' : undefined,
            onClick: remove,
          },
        ],
      }}
    />
  )
}

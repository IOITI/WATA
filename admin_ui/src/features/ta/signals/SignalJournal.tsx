// The signals the TA engine raised: each rule's alerts that became signals, what
// became of them (journaled in shadow, sent, refused), and, once sent, what the
// trader made of them.

import { useQuery } from '@tanstack/react-query'
import { AdvancedDataTable, Badge, DateRangeFilter, formatDateTime, formatNumber, lastDays, Panel, QueryState, rangeParams, Select } from 'ioiti-ui'
import type { BadgeTone, ColumnDef, DateRange } from 'ioiti-ui'
import { useMemo, useState } from 'react'

import { ActionBadge } from '@/components/status'
import { api } from '@/lib/api'
import type { TaRule, TaSignal } from '@/lib/types'

const OUTCOMES: Record<TaSignal['outcome'], { label: string; tone: BadgeTone }> = {
  shadow: { label: 'Shadow: not sent', tone: 'neutral' },
  sending: { label: 'Being sent', tone: 'warning' },
  sent: { label: 'Sent', tone: 'success' },
  refused: { label: 'Not sent', tone: 'warning' },
  failed: { label: 'Failed', tone: 'danger' },
}

const TRADER: Record<string, { label: string; tone: BadgeTone }> = {
  executed: { label: 'Executed', tone: 'success' },
  closed: { label: 'Positions closed', tone: 'success' },
  refused: { label: 'Refused', tone: 'warning' },
  failed: { label: 'Failed', tone: 'danger' },
  ignored: { label: 'Nothing to do', tone: 'neutral' },
}

export function SignalJournal({ rules, timeZone }: { rules: TaRule[]; timeZone: string }) {
  const [range, setRange] = useState<DateRange>(() => lastDays(7))
  const [ruleId, setRuleId] = useState('')
  const params = { ...rangeParams(range), rule: ruleId || undefined }
  const signalsQuery = useQuery({
    queryKey: ['ta-signals', params],
    queryFn: () => api.taSignals({ from: params.from ?? undefined, to: params.to ?? undefined, rule: params.rule }),
    refetchInterval: 15_000,
  })

  const columns = useMemo<ColumnDef<TaSignal>[]>(
    () => [
      {
        id: 'bar',
        header: 'Bar',
        accessorKey: 'bar_time',
        sortable: true,
        cell: ({ row }) => (
          <div className="whitespace-nowrap">
            {formatDateTime(row.bar_time, timeZone)}
            <p className="text-xs text-muted-foreground">closed {formatDateTime(row.bar_closed_at, timeZone, false)}</p>
          </div>
        ),
      },
      {
        id: 'rule',
        header: 'Rule',
        accessorFn: (row) => `${row.rule_name} ${row.asset} ${row.bars}`,
        sortable: true,
        cell: ({ row }) => (
          <div className="min-w-0 max-w-64 whitespace-normal">
            <span className="font-medium">{row.rule_name}</span>
            <p className="text-xs text-muted-foreground">
              {row.asset} · {row.side} · {row.bars} · script v{row.script_version}
            </p>
          </div>
        ),
      },
      {
        id: 'alert',
        header: 'Alert',
        accessorFn: (row) => `${row.alert_title ?? ''} ${row.alert_message ?? ''}`,
        cell: ({ row }) => (
          <div className="min-w-0 max-w-72 whitespace-normal">
            {row.alert_title ?? <span className="text-muted-foreground">alert()</span>}
            {row.alert_message && <p className="truncate font-mono text-xs text-muted-foreground" title={row.alert_message}>{row.alert_message}</p>}
          </div>
        ),
      },
      {
        id: 'action',
        header: 'Signal',
        accessorFn: (row) => row.action ?? '',
        sortable: true,
        cell: ({ row }) => (
          <div>
            {row.action ? <ActionBadge action={row.action} /> : <span className="text-xs text-muted-foreground">none</span>}
            {row.confidence !== null && <p className="text-xs text-muted-foreground">confidence {formatNumber(row.confidence, 2)}</p>}
          </div>
        ),
      },
      {
        id: 'outcome',
        header: 'Outcome',
        accessorKey: 'outcome',
        sortable: true,
        filterable: true,
        filterOptions: Object.entries(OUTCOMES).map(([value, { label }]) => ({ value, label })),
        cell: ({ row }) => {
          const outcome = OUTCOMES[row.outcome] ?? { label: row.outcome, tone: 'neutral' as const }
          return (
            <div className="min-w-0 max-w-72 whitespace-normal">
              <Badge tone={outcome.tone}>{outcome.label}</Badge>
              {row.detail && <p className="text-xs text-muted-foreground">{row.detail}</p>}
            </div>
          )
        },
      },
      {
        id: 'trader',
        header: 'Trader',
        accessorFn: (row) => row.trader?.outcome ?? '',
        cell: ({ row }) => {
          if (!row.trader) return <span className="text-xs text-muted-foreground">{row.outcome === 'sent' ? 'Not decided yet' : '—'}</span>
          const decision = TRADER[row.trader.outcome] ?? { label: row.trader.outcome, tone: 'neutral' as const }
          return (
            <div className="min-w-0 max-w-64 whitespace-normal">
              <Badge tone={decision.tone}>{decision.label}</Badge>
              {row.trader.reason && <p className="text-xs text-muted-foreground">{row.trader.reason}</p>}
            </div>
          )
        },
      },
    ],
    [timeZone],
  )

  return (
    <Panel
      title="Signals"
      description="Each alert of a rule that became a signal: journaled in shadow, sent to the trader when the rule is live."
    >
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <DateRangeFilter value={range} onChange={setRange} />
        <div className="w-56">
          <Select
            aria-label="Rule"
            value={ruleId}
            onChange={(event) => setRuleId(event.target.value)}
            options={[{ value: '', label: 'Every rule' }, ...rules.map((rule) => ({ value: rule.id, label: rule.name }))]}
          />
        </div>
      </div>
      <QueryState isPending={signalsQuery.isPending} error={signalsQuery.error} hasData={!!signalsQuery.data}>
        <AdvancedDataTable<TaSignal>
          searchPlaceholder="Search…"
          emptyTitle="No signal in this period"
          emptyDescription="The engine journals one each time an alert of a rule becomes a signal."
          data={signalsQuery.data?.items ?? []}
          columns={columns}
          getRowId={(row) => String(row.id)}
          defaultPageSize={20}
          // Records: nothing to do with several at once.
          selectable={false}
        />
      </QueryState>
    </Panel>
  )
}

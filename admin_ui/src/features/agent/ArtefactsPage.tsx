// What the AI agent's conversations made: the backtests it started, the scripts it saved, its shadow rules, the
// experiments it opened, what it proposed and whether it was applied. Each leads to its page and to its
// conversation.

import { useQuery } from '@tanstack/react-query'
import { AdvancedDataTable, Badge, formatDateTime, PageHeader, QueryState } from 'ioiti-ui'
import type { ColumnDef } from 'ioiti-ui'
import { useMemo } from 'react'
import { Link } from 'react-router'

import { useSession } from '@/features/auth/auth-context'
import { rulePath } from '@/features/ta/signals/ruleModel'
import { api } from '@/lib/api'
import type { AgentArtefact } from '@/lib/types'

import { MEMORY_DONE, MEMORY_PATH, memoryLink } from './memoryModel'

const KIND_LABEL: Record<AgentArtefact['kind'], string> = {
  run: 'Backtest',
  script: "Claude's script",
  rule: "Claude's shadow rule",
  experiment: 'Experiment',
  proposal: 'Proposal',
  applied: 'Applied',
  dismissed: 'Dismissed',
  memory: 'Memory',
}

function what(row: AgentArtefact): { text: string; to: string | null } {
  switch (row.kind) {
    case 'run':
      return { text: `Run ${row.id}`, to: `/backtests/${encodeURIComponent(row.id)}` }
    case 'script':
      return { text: `${row.name ?? row.id} v${row.version ?? '?'}`, to: `/ta/indicators/${encodeURIComponent(row.id)}` }
    case 'rule':
      return { text: `${row.name ?? row.id}${row.mode ? ` (${row.mode})` : ''}`, to: rulePath(row.id) }
    case 'experiment':
      return { text: `Experiment ${row.id}`, to: '/backtests/experiments' }
    case 'proposal':
      return { text: row.title ?? row.id, to: `/agent/${encodeURIComponent(row.conversation)}#proposal-${row.id}` }
    case 'memory':
      return {
        text: `${row.action ? `${MEMORY_DONE[row.action][0].toUpperCase()}${MEMORY_DONE[row.action].slice(1)}` : 'Kept'} ${row.name ?? row.id}`,
        to: row.action === 'forgotten' ? `${MEMORY_PATH}?tab=history&find=${encodeURIComponent(row.id)}` : memoryLink(row.id),
      }
    default: {
      const result = row.result
      if (result?.script_id && result.name) return { text: `A new script: ${result.name}`, to: `/ta/indicators/${encodeURIComponent(result.script_id)}` }
      if (result?.script_id) return { text: `A new version: v${result.version}`, to: `/ta/indicators/${encodeURIComponent(result.script_id)}` }
      if (result?.revision) return { text: 'Settings saved to config.json', to: '/config/versions' }
      if (result?.rule_id) return { text: `A rule changed: it runs v${result.script_version}`, to: rulePath(result.rule_id) }
      return { text: `Proposal ${row.id}`, to: `/agent/${encodeURIComponent(row.conversation)}#proposal-${row.id}` }
    }
  }
}

export function ArtefactsPage() {
  const { timezone } = useSession()
  const query = useQuery({ queryKey: ['agent-artefacts'], queryFn: api.agentArtefacts })
  const columns = useMemo<ColumnDef<AgentArtefact>[]>(() => [
    {
      id: 'at', header: 'When', accessorFn: (row) => row.at, sortable: true,
      cell: ({ row }) => formatDateTime(row.at, timezone, false),
    },
    {
      id: 'kind', header: 'What', accessorFn: (row) => KIND_LABEL[row.kind], filterable: true,
      filterOptions: Object.values(KIND_LABEL).map((label) => ({ value: label, label })),
      cell: ({ row }) => <Badge variant="soft" tone={row.kind === 'applied' ? 'success' : 'neutral'}>{KIND_LABEL[row.kind]}</Badge>,
    },
    {
      id: 'item', header: 'Item', accessorFn: (row) => what(row).text,
      cell: ({ row }) => {
        const item = what(row)
        return item.to ? <Link className="underline-offset-2 hover:underline" to={item.to}>{item.text}</Link> : item.text
      },
    },
    {
      id: 'conversation', header: 'Conversation', accessorFn: (row) => row.title_of ?? row.conversation,
      cell: ({ row }) => (
        <Link className="text-muted-foreground underline-offset-2 hover:underline" to={`/agent/${encodeURIComponent(row.conversation)}`}>
          {row.title_of ?? row.conversation}
        </Link>
      ),
    },
  ], [timezone])

  return (
    <div className="space-y-6">
      <PageHeader title="Artefacts" description="What the AI agent made: its backtests, its scripts, its experiments, what it proposed and what it kept in its memory." />
      <QueryState isPending={query.isPending} error={query.error} hasData={!!query.data}>
        <AdvancedDataTable<AgentArtefact>
          title="Artefacts"
          searchPlaceholder="Search…"
          emptyTitle="Nothing made yet"
          emptyDescription="Runs, scripts and proposals appear here as the agent makes them."
          data={query.data?.items ?? []}
          columns={columns}
          getRowId={(row) => `${row.kind}-${row.id}-${row.at}`}
          defaultPageSize={25}
          selectable={false}
        />
      </QueryState>
    </div>
  )
}

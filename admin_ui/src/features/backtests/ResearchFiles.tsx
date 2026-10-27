import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Select } from 'ioiti-ui'

import { api } from '@/lib/api'

import { researchSentence } from './model'

const KEEP_CHOICES = [7, 14, 30, 60, 90, 180, 365]

export function ResearchFiles() {
  const queryClient = useQueryClient()
  const statusQuery = useQuery({
    queryKey: ['research-files'],
    queryFn: api.researchFiles,
    refetchInterval: (query) => (query.state.data?.state === 'exporting' || query.state.data?.state === 'due' ? 10_000 : 120_000),
  })
  const change = useMutation({
    mutationFn: api.setResearchFiles,
    onSuccess: (status) => queryClient.setQueryData(['research-files'], status),
  })
  const status = statusQuery.data
  if (!status) return null
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-xs text-muted-foreground">
      <span>{researchSentence(status)}</span>
      {status.enabled && (
        <label className="flex items-center gap-2">
          <span>Days kept on the server</span>
          <Select
            aria-label="Days of research files kept on the server"
            value={String(status.keep_days)}
            onChange={(event) => change.mutate({ keep_days: Number(event.target.value) })}
            options={[...new Set([...KEEP_CHOICES, status.keep_days])]
              .sort((first, second) => first - second)
              .map((days) => ({ value: String(days), label: `${days}` }))}
          />
        </label>
      )}
      <Button variant="ghost" size="sm" isLoading={change.isPending} onClick={() => change.mutate({ enabled: !status.enabled })}>
        {status.enabled ? 'Stop writing them' : 'Write them'}
      </Button>
      {(status.error || change.error) && (
        <span role="alert" className="text-negative">
          {change.error?.message ?? status.error}
        </span>
      )}
    </div>
  )
}

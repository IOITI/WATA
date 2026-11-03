// The runs among the favourites, put there or taken out: one from its page or its row, several from the
// list's selection. Shown at once wherever the runs are listed or read, then read again from the server.

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useToast } from 'ioiti-ui'

import { api } from '@/lib/api'
import type { BacktestRun, BacktestRunListed } from '@/lib/types'

export function useSetFavourites() {
  const queryClient = useQueryClient()
  const toast = useToast()
  return useMutation({
    mutationFn: ({ ids, favourite }: { ids: string[]; favourite: boolean }) => api.favouriteBacktests(ids, favourite),
    onMutate: ({ ids, favourite }) => {
      const chosen = new Set(ids)
      // Every list of runs: the Backtests page's, and a script's.
      queryClient.setQueriesData<{ items: BacktestRunListed[] }>({ queryKey: ['backtests'] }, (data) =>
        data && Array.isArray(data.items)
          ? { ...data, items: data.items.map((run) => (chosen.has(run.id) ? { ...run, favourite } : run)) }
          : data,
      )
      for (const id of ids) queryClient.setQueryData<BacktestRun>(['backtest', id], (run) => (run ? { ...run, favourite } : run))
    },
    onSuccess: (answer) => {
      if (answer.kept.length > 0) {
        toast({
          title: `${answer.kept.length} run${answer.kept.length === 1 ? ' was' : 's were'} left as ${answer.kept.length === 1 ? 'it was' : 'they were'}`,
          description: answer.kept[0].reason,
          variant: 'error',
        })
      }
    },
    onError: (error) =>
      toast({ title: 'The favourites were not changed', description: error instanceof Error ? error.message : String(error), variant: 'error' }),
    onSettled: (_answer, _error, { ids }) => {
      void queryClient.invalidateQueries({ queryKey: ['backtests'] })
      for (const id of ids) void queryClient.invalidateQueries({ queryKey: ['backtest', id] })
    },
  })
}

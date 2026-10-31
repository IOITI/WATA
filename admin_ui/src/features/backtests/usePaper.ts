// The paper trading of every rule (GET /api/backtests/paper): Backtests › Paper trading's, and a rule's own page's.

import { useQuery } from '@tanstack/react-query'

import { api } from '@/lib/api'

import { anyUnderWay } from './paperModel'

/** The paper trading of every rule, read again every few seconds while a report is being made. */
export function usePaper(enabled: boolean) {
  return useQuery({
    queryKey: ['backtest-paper'],
    queryFn: api.backtestPaper,
    enabled,
    refetchInterval: (query) => (anyUnderWay(query.state.data) ? 5_000 : false),
  })
}

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Ruler } from 'lucide-react'

import { Panel } from '@/components/Panel'
import { QueryState } from '@/components/status'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { useSession } from '@/features/auth/auth-context'
import { api } from '@/lib/api'
import { formatDateTime } from '@/lib/format'

import { calibrationSentences } from './model'

/**
 * The turbo the backtests are made with: assumed until it is measured on
 * WATA's closed trades, which this panel asks for and shows.
 */
export function TurboPanel() {
  const { timezone } = useSession()
  const queryClient = useQueryClient()
  const calibrationQuery = useQuery({
    queryKey: ['backtest-calibration'],
    queryFn: api.backtestCalibration,
    refetchInterval: (query) => (query.state.data?.status.state === 'running' ? 3_000 : false),
  })
  const calibration = calibrationQuery.data
  const measuring = calibration?.status.state === 'running'

  const measureMutation = useMutation({
    mutationFn: api.measureTurbo,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['backtest-calibration'] }),
  })

  const turbo = calibration?.turbo
  const result = calibration?.result ?? null
  const leverages = turbo?.leverages.join(', ')

  return (
    <Panel
      title="Turbo"
      description={
        turbo
          ? turbo.measured
            ? `Backtests are made with the turbo measured on WATA's closed trades: paid ${turbo.entry_price}, 1.00 for ${leverages} index points, spread ${turbo.spread}.`
            : `Backtests assume a turbo paid ${turbo.entry_price} that moves by 1.00 for ${leverages} index points, spread ${turbo.spread}. These are guesses until it is measured on WATA's closed trades.`
          : undefined
      }
    >
      <QueryState isPending={calibrationQuery.isPending} error={calibrationQuery.error} hasData={!!calibration}>
        {calibration && (
          <div className="flex flex-col gap-4">
            {result && (
              <div className="flex flex-col gap-1.5 text-sm leading-relaxed">
                {calibrationSentences(result).map((sentence) => (
                  <p key={sentence}>{sentence}</p>
                ))}
                {result.measured_at && (
                  <p className="text-xs text-muted-foreground">Measured {formatDateTime(result.measured_at, timezone, false)}.</p>
                )}
              </div>
            )}

            {calibration.status.state === 'failed' && (
              <Alert tone="danger" role="alert">
                <AlertTitle>The measure failed</AlertTitle>
                <AlertDescription>
                  <pre className="mt-1 whitespace-pre-wrap font-mono text-xs">
                    {calibration.lines.join('\n') || calibration.status.error}
                  </pre>
                </AlertDescription>
              </Alert>
            )}
            {calibration.status.state === 'interrupted' && (
              <Alert tone="warning">
                <AlertDescription>The last measure was interrupted when the admin restarted. Ask for it again.</AlertDescription>
              </Alert>
            )}
            {measureMutation.error && (
              <Alert tone="danger" role="alert">
                <AlertDescription>{measureMutation.error.message}</AlertDescription>
              </Alert>
            )}

            <div className="flex flex-wrap items-center gap-3">
              <Button
                size="sm"
                variant={result?.fitted ? 'outline' : 'primary'}
                leftIcon={<Ruler className="size-3.5" />}
                isLoading={measuring || measureMutation.isPending}
                disabled={measuring}
                onClick={() => measureMutation.mutate()}
              >
                {measuring
                  ? `Measuring on ${calibration.status.trades ?? ''} trades`
                  : result
                    ? 'Measure again'
                    : 'Measure on real trades'}
              </Button>
              <p className="text-xs text-muted-foreground">
                Reads every closed trade of WATA and the index at its open and its close: the days WATA traded need
                their prices, see above. Nothing is changed in the trading services.
              </p>
            </div>
          </div>
        )}
      </QueryState>
    </Panel>
  )
}

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, AlertDescription, Button, DateRangePicker, formatNumber, Panel, Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow, toIsoDay } from 'ioiti-ui'
import type { DateRange } from 'ioiti-ui'
import { Ruler } from 'lucide-react'
import { useState } from 'react'

import { useSession } from '@/features/auth/auth-context'
import { api } from '@/lib/api'

import { localDay } from './model'
import { defaultMeasurePeriod, measureSentence, measureState, underlyingOfRef } from './turboModel'

/**
 * The turbos made up from the index for long histories: measured on the days
 * the price recorder kept turbos, by a command of their own, again when asked.
 */
export function TurboModelPanel() {
  const { timezone } = useSession()
  const queryClient = useQueryClient()
  const [period, setPeriod] = useState(() => defaultMeasurePeriod(localDay(new Date().toISOString(), timezone)))
  const modelQuery = useQuery({
    queryKey: ['turbo-model'],
    queryFn: api.turboModel,
    refetchInterval: (query) => (query.state.data?.state === 'running' ? 3_000 : false),
  })
  const measure = useMutation({
    mutationFn: () => api.measureTurbos(period),
    onSuccess: async (model) => {
      queryClient.setQueryData(['turbo-model'], model)
      await queryClient.invalidateQueries({ queryKey: ['backtest-options'] })
    },
  })
  const model = modelQuery.data
  if (!model) return null
  const state = measureState(model)
  const range: DateRange = { from: new Date(`${period.from}T12:00:00`), to: period.to ? new Date(`${period.to}T12:00:00`) : null }
  return (
    <Panel
      title="Turbos made up from the index"
      description={`For years of days: turbos priced from ${underlyingOfRef(model.underlying)} as the recorded ones were for the same distance to their level, knocked out when it crosses it. Measured on the days the price recorder kept turbos.`}
    >
      <div className="flex flex-col gap-4 text-sm">
        <p>{measureSentence(model.measure)}</p>
        {model.measure && (
          <Table density="dense" aria-label="The measure of each direction">
            <TableHead>
              <TableRow>
                <TableHeaderCell scope="col">
                  Direction
                </TableHeaderCell>
                <TableHeaderCell numeric scope="col">
                  € a point
                </TableHeaderCell>
                <TableHeaderCell numeric scope="col">
                  Level beyond the knock-out
                </TableHeaderCell>
                <TableHeaderCell numeric scope="col">
                  Levels every
                </TableHeaderCell>
                <TableHeaderCell numeric scope="col">
                  Turbos measured
                </TableHeaderCell>
                <TableHeaderCell numeric scope="col">
                  R²
                </TableHeaderCell>
                <TableHeaderCell numeric scope="col">
                  Median error
                </TableHeaderCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {(['long', 'short'] as const).map((direction) => {
                const measured = model.measure!.directions[direction]
                return (
                  <TableRow key={direction} className="border-border/50">
                    <TableHeaderCell scope="row" className="capitalize">
                      {direction}
                    </TableHeaderCell>
                    <TableCell numeric>{formatNumber(measured.factor, 5)}</TableCell>
                    <TableCell numeric>{formatNumber(measured.offset, 1)} points</TableCell>
                    <TableCell numeric>{formatNumber(measured.spacing, 0)} points</TableCell>
                    <TableCell numeric>{formatNumber(measured.turbos)}</TableCell>
                    <TableCell numeric>{measured.r2 === null ? '-' : formatNumber(measured.r2, 3)}</TableCell>
                    <TableCell numeric className="whitespace-nowrap">
                      {measured.error_eur === null ? '-' : `${formatNumber(measured.error_eur, 3)} €`}
                      {measured.error_percent === null ? '' : ` (${formatNumber(measured.error_percent, 1)} %)`}
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        )}
        {state && (
          <Alert tone={model.state === 'failed' ? 'danger' : 'neutral'}>
            <AlertDescription>{state}</AlertDescription>
          </Alert>
        )}
        <div className="flex flex-wrap items-center gap-3">
          {/* Its end empty until it is picked: given back with one, the calendar would start a range again. */}
          <DateRangePicker
            value={range}
            onChange={(chosen) => {
              if (chosen.from) setPeriod({ from: toIsoDay(chosen.from), to: chosen.to ? toIsoDay(chosen.to) : '' })
            }}
            placeholder="Recorded days"
            align="start"
            locale="en-GB"
            weekStartsOn={1}
          />
          <Button
            size="sm"
            leftIcon={<Ruler className="size-3.5" />}
            pending={measure.isPending}
            disabled={model.state === 'running' || !period.to}
            onClick={() => measure.mutate()}
          >
            {model.measure ? 'Measure them again' : 'Measure them'}
          </Button>
          <span className="text-xs text-muted-foreground">{model.max_days} days at most, one measure at a time.</span>
        </div>
        {measure.error && (
          <p role="alert" className="text-sm text-negative">
            {measure.error.message}
          </p>
        )}
      </div>
    </Panel>
  )
}

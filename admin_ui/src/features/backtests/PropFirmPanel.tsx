import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, AlertDescription, Badge, Button, Hint, Input, Panel, QueryState, Select } from 'ioiti-ui'
import { Scale } from 'lucide-react'
import { useState } from 'react'

import { useSession } from '@/features/auth/auth-context'
import { api } from '@/lib/api'
import type { BacktestRun, PropFirmAsked, PropFirmKept, PropFirmState } from '@/lib/types'

import { ACCOUNT_SCOPES, answerSaid, askedOf, askedSaid, barSaid, challengeRows, figuresSaid, PRODUCT_TYPES, RISK_PERCENT, sentSaid } from './propFirms'
import type { AccountScope } from './propFirms'

/** LuxAlgo's answer: each challenge screened, with the spread of its estimates. */
function Screened({ kept }: { kept: PropFirmKept }) {
  const { timezone } = useSession()
  const { answer } = kept
  const rows = challengeRows(answer)
  return (
    <div className="flex flex-col gap-3 text-sm">
      <p className="text-text-secondary">
        {askedSaid(kept, timezone)} {barSaid(kept)}
      </p>
      {answer.text && !rows.length ? (
        <pre className="max-h-72 overflow-auto whitespace-pre-wrap font-mono text-xs text-text-secondary">{answer.text}</pre>
      ) : (
        <>
          <p>{answerSaid(answer)}</p>
          {rows.length > 0 && (
            <div className="relative overflow-x-auto">
              <table className="w-full whitespace-nowrap text-sm">
                <thead>
                  <tr className="text-xs text-muted-foreground">
                    <th scope="col" className="p-2 text-left font-medium">Challenge</th>
                    <th scope="col" className="p-2 text-right font-medium">Account</th>
                    <th scope="col" className="p-2 text-right font-medium">Passed per attempt</th>
                    <th scope="col" className="p-2 text-right font-medium">Funded</th>
                    <th scope="col" className="p-2 text-right font-medium">Attempts</th>
                    <th scope="col" className="p-2 text-right font-medium">Expected cost</th>
                    <th scope="col" className="p-2 text-right font-medium">Expected value</th>
                    <th scope="col" className="p-2 text-left font-medium">LuxAlgo&apos;s flags</th>
                  </tr>
                </thead>
                <tbody className="tabular-nums">
                  {rows.map((row) => (
                    <tr key={row.key} className="border-t border-border">
                      <td className="p-2">
                        <span className="flex items-center gap-2">
                          {row.name}
                          {row.meetsBar ? <Badge tone="success" dot>Meets the bar</Badge> : <Badge tone="neutral">Under the bar</Badge>}
                        </span>
                      </td>
                      <td className="p-2 text-right">{row.size}</td>
                      <td className="p-2 text-right">
                        {row.pass}
                        {row.range && <span className="block text-xs text-muted-foreground">95 %: {row.range}</span>}
                      </td>
                      <td className="p-2 text-right">{row.funded}</td>
                      <td className="p-2 text-right">{row.attempts}</td>
                      <td className="p-2 text-right">{row.cost}</td>
                      <td className={`p-2 text-right ${row.evSign > 0 ? 'text-positive' : row.evSign < 0 ? 'text-negative' : ''}`}>{row.ev}</td>
                      <td className="p-2 text-xs text-text-secondary">{row.flags.join(', ') || '-'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
      {(answer.note || answer.disclaimer) && (
        <p className="text-xs text-muted-foreground">
          LuxAlgo: {[answer.note, answer.disclaimer].filter(Boolean).join(' ')}
        </p>
      )}
    </div>
  )
}

/**
 * Whether a funded-account challenge would pass with this run's trades: LuxAlgo's simulator draws many paths from
 * them (keeping their streaks) and screens the challenges it knows against a bar. Only the run's figures leave.
 */
export function PropFirmPanel({ run }: { run: BacktestRun }) {
  const queryClient = useQueryClient()
  const key = ['backtest-propfirms', run.id]
  const state = useQuery({ queryKey: key, queryFn: () => api.propFirms(run.id) })
  const [risk, setRisk] = useState(String(RISK_PERCENT.default))
  const [product, setProduct] = useState<PropFirmAsked['product_type']>('futures')
  const [scope, setScope] = useState<AccountScope>('medium')
  const asked = askedOf(risk, product, scope)
  const check = useMutation({
    mutationFn: (body: PropFirmAsked) => api.checkPropFirms(run.id, body),
    onSuccess: (kept) => queryClient.setQueryData<PropFirmState>(key, (current) => (current ? { ...current, kept } : current)),
  })
  const figures = state.data?.figures
  const kept = state.data?.kept
  const button = (
    <Button size="sm" variant="outline" leftIcon={<Scale className="size-3.5" aria-hidden />} isLoading={check.isPending}
      disabled={!figures || typeof asked === 'string'} onClick={() => typeof asked !== 'string' && check.mutate(asked)}>
      {kept ? 'Check again' : 'Check with LuxAlgo'}
    </Button>
  )

  return (
    <Panel
      title="Funded challenges (LuxAlgo)"
      description="Would a funded-account challenge pass with these trades? LuxAlgo's simulator draws many paths from the run's results, keeping their streaks, and screens the challenges it knows against a pass per attempt of one in two. A screen of distributions, not a ranking."
    >
      <QueryState isPending={state.isPending} error={state.error} hasData={!!state.data}>
        {state.data?.refused ? (
          <p className="text-sm text-text-secondary">Not checkable: {state.data.refused}.</p>
        ) : figures && (
          <div className="flex flex-col gap-4">
            <p className="text-sm">{figuresSaid(figures)}</p>
            {figures.notes.length > 0 && (
              <ul className="list-disc pl-5 text-sm text-text-secondary">
                {figures.notes.map((note) => <li key={note}>{note}.</li>)}
              </ul>
            )}
            <div className="flex flex-wrap items-end gap-3">
              <label className="flex w-40 flex-col gap-1 text-xs">
                <span className="text-text-secondary">Risk a trade (% of the balance)</span>
                <Input type="number" inputMode="decimal" min={RISK_PERCENT.min} max={RISK_PERCENT.max} step={0.1} value={risk}
                  onChange={(event) => setRisk(event.target.value)} />
              </label>
              <label className="flex w-36 flex-col gap-1 text-xs">
                <span className="text-text-secondary">Challenges on</span>
                <Select value={product} onChange={(event) => setProduct(event.target.value as PropFirmAsked['product_type'])} options={PRODUCT_TYPES} />
              </label>
              <label className="flex w-48 flex-col gap-1 text-xs">
                <span className="text-text-secondary">Accounts</span>
                <Select value={scope} onChange={(event) => setScope(event.target.value as AccountScope)}
                  options={ACCOUNT_SCOPES.map((entry) => ({ value: entry.value, label: entry.label }))} />
              </label>
              {typeof asked === 'string' ? <Hint content={asked} wrap>{button}</Hint> : button}
            </div>
            <p className="text-xs text-muted-foreground">
              {sentSaid(figures)}
              {check.isPending && ' LuxAlgo screens each challenge with thousands of paths: up to a minute for 40 of them.'}
            </p>
            {check.error && (
              <Alert tone="danger" role="alert">
                <AlertDescription>{check.error.message}</AlertDescription>
              </Alert>
            )}
            {kept && <Screened kept={kept} />}
          </div>
        )}
      </QueryState>
    </Panel>
  )
}

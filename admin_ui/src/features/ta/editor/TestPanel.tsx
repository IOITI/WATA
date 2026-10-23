// The editor's test: the script run on a day of one of WATA's assets, as the chart
// and the signal engine would run it. What it found is drawn under the editor
// (TestResult). Its bars, and the script as it ran (its libraries inlined), can be
// downloaded for pinets-cli or an agent: `pinets run <script> --data <bars>`.

import { useMutation, useQuery } from '@tanstack/react-query'
import { ChartSpline, Download, Play } from 'lucide-react'
import { useId, useMemo, useState } from 'react'
import { Link } from 'react-router'
import type { IPineInput } from 'pinets'

import { Panel } from '@/components/Panel'
import { Button } from '@/components/ui/button'
import { Combobox } from '@/components/ui/combobox'
import { Checkbox, Input, Select } from '@/components/ui/form'
import { useSession } from '@/features/auth/auth-context'
import { assetKeywords } from '@/features/data/assets'
import { api } from '@/lib/api'
import type { DataAsset } from '@/lib/types'

import { assetTicker, chartsPath, testableAssets } from '../chartLinks'
import { toCandle, wataSeriesFetcher } from '../data/seriesFetcher'
import { addDays, dayIn, WataBars } from '../data/wataBars'
import type { PreparedScript } from '../engine/prepare'
import type { PineWorkerClient, RunResponse } from '../engine/pineWorkerClient'
import { tickerText } from '../engine/tickers'
import type { PriceSide, WataTicker } from '../engine/tickers'
import { DEFAULT_TIMEFRAME, TIMEFRAMES, timeframeOf } from '../engine/timeframes'
import type { Candle, RunSummary } from '../engine/workerProtocol'

interface TestPanelProps {
  prepared: PreparedScript | null
  inputs: IPineInput[]
  worker: PineWorkerClient | null
  scriptId: string | null
  scriptName: string
  /** What a test that ran found, for the page to show; null when the last one failed. */
  onResult?: (result: TestDone | null) => void
}

type BarsKind = 'time' | 'range'

interface TestRun {
  day: string
  ticker: WataTicker
  timeframe: string
  candles: Candle[]
  response: RunResponse
}

/** A test that ran: its day, its bars, what the script plotted and raised. */
export type TestDone = Omit<TestRun, 'response'> & { response: RunSummary }

function assetLabel(asset: DataAsset): string {
  const provider = { capital: 'Capital.com', dukascopy: 'Dukascopy', saxo: 'Saxo' }[asset.provider]
  return `${asset.description ?? asset.symbol ?? asset.key} · ${provider}`
}

/** A value written in an input's field, as the input's type wants it. */
function inputValue(input: IPineInput, text: string | boolean): unknown {
  if (input.type === 'bool') return Boolean(text)
  if (input.type === 'int' || input.type === 'float' || input.type === 'price') {
    const number = Number(text)
    return Number.isFinite(number) ? number : input.defval
  }
  return text
}

function download(filename: string, content: string, type: string): void {
  const url = URL.createObjectURL(new Blob([content], { type }))
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

function fileName(name: string): string {
  return name.replace(/[^A-Za-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '') || 'script'
}

export function TestPanel({ prepared, inputs, worker, scriptId, scriptName, onResult }: TestPanelProps) {
  const { timezone } = useSession()
  const bars = useMemo(() => new WataBars(), [])
  const assetsQuery = useQuery({ queryKey: ['data-assets', 'all'], queryFn: () => api.dataAssets('all'), staleTime: 60_000 })
  const assets = useMemo(() => testableAssets(assetsQuery.data?.items ?? []), [assetsQuery.data])

  const [chosenRef, setChosenRef] = useState<string | null>(null)
  const assetFieldId = useId()
  const asset = assets.find((candidate) => candidate.ref === chosenRef) ?? assets.find((candidate) => candidate.provider === 'capital') ?? assets[0]
  const [barsKind, setBarsKind] = useState<BarsKind>('time')
  const [timeframe, setTimeframe] = useState(DEFAULT_TIMEFRAME.pine)
  const [rangeTicks, setRangeTicks] = useState(60)
  const [side, setSide] = useState<PriceSide>('bid')
  const [chosenDay, setChosenDay] = useState<string | null>(null)
  // Read once: a day that turns while the page is open changes nothing asked of it.
  const [today] = useState(() => dayIn(Date.now(), timezone))
  const lastDay = asset?.last_t ? dayIn(asset.last_t * 1000, timezone) : today
  const day = chosenDay ?? lastDay
  const [overrides, setOverrides] = useState<Record<string, unknown>>({})

  const ticker: WataTicker | null = asset
    ? { ...assetTicker(asset)!, side, rangeTicks: barsKind === 'range' ? Math.max(1, rangeTicks) : null }
    : null

  const runMutation = useMutation({
    mutationFn: async (): Promise<TestRun> => {
      if (!worker || !prepared || !ticker) throw new Error('The script is not ready to run')
      const length = timeframeOf(timeframe) ?? DEFAULT_TIMEFRAME
      // The day before warms the indicators up; a day of Paris spans at most 14 hours either side of UTC's.
      const window = {
        from: Date.parse(`${addDays(day, -1)}T00:00:00Z`) - 14 * 3_600_000,
        to: Date.parse(`${day}T23:59:59Z`) + 14 * 3_600_000,
      }
      const found = await bars.bars(ticker, length.seconds, window)
      if (found.length === 0) throw new Error(`No bars of ${tickerText(ticker)} on ${day}`)
      const candles = found.map(toCandle)
      // The plots' numbers of the day alone: what the result draws.
      const ofDay = candles.filter((candle) => dayIn(candle.openTime, timezone) === day)
      const response = await worker.run(
        {
          source: prepared.source,
          bars: candles,
          symbol: tickerText(ticker),
          timeframe: length.pine,
          inputs: overrides,
          series: ofDay.length > 0 ? { from: ofDay[0].openTime, to: ofDay[ofDay.length - 1].openTime } : undefined,
        },
        wataSeriesFetcher(bars),
      )
      return { day, ticker, timeframe: length.pine, candles, response }
    },
    onSuccess: (done) => onResult?.(done.response.ok && done.response.op === 'run' ? { ...done, response: done.response } : null),
    onError: () => onResult?.(null),
  })
  const run = runMutation.data
  const response = run?.response

  return (
    <Panel
      title="Test"
      description="Runs the script on a day of WATA's prices, as Charts and the signal engine run it."
      bodyClassName="flex flex-col gap-3 pt-3"
    >
      {/* Every turbo still quoted is offered: hundreds, found by typing ("24150"). A label beside, not around: it would pass an option's click on to the field. */}
      <div className="flex flex-col gap-1 text-xs">
        <label htmlFor={assetFieldId} className="font-medium text-text-secondary">
          Asset
        </label>
        <Combobox
          id={assetFieldId}
          className="w-full"
          value={asset?.ref ?? null}
          onValueChange={(value) => value && setChosenRef(value)}
          options={assets.map((candidate) => ({ value: candidate.ref, label: assetLabel(candidate), keywords: assetKeywords(candidate) }))}
          disabled={assets.length === 0}
          placeholder="No asset"
          searchPlaceholder="A name, a level, a symbol…"
          emptyText="No asset of that name."
        />
      </div>
      <div className="grid grid-cols-2 gap-2">
        <label className="flex flex-col gap-1 text-xs">
          <span className="font-medium text-text-secondary">Bars</span>
          <Select
            value={barsKind === 'range' ? 'range' : timeframe}
            onChange={(event) => {
              if (event.target.value === 'range') setBarsKind('range')
              else {
                setBarsKind('time')
                setTimeframe(event.target.value)
              }
            }}
            options={[
              ...TIMEFRAMES.map((length) => ({ value: length.pine, label: length.label })),
              { value: 'range', label: 'Range bars' },
            ]}
          />
        </label>
        {barsKind === 'range' ? (
          <label className="flex flex-col gap-1 text-xs">
            <span className="font-medium text-text-secondary">Range (R, in price steps)</span>
            <Input
              type="number"
              min={1}
              value={rangeTicks}
              onChange={(event) => setRangeTicks(Number(event.target.value) || 1)}
            />
          </label>
        ) : (
          <label className="flex flex-col gap-1 text-xs">
            <span className="font-medium text-text-secondary">Price</span>
            <Select
              value={side}
              onChange={(event) => setSide(event.target.value as PriceSide)}
              options={[
                { value: 'bid', label: 'Bid' },
                { value: 'ask', label: 'Ask' },
              ]}
            />
          </label>
        )}
      </div>
      <label className="flex flex-col gap-1 text-xs">
        <span className="font-medium text-text-secondary">Day</span>
        <Input type="date" value={day} max={lastDay} onChange={(event) => setChosenDay(event.target.value || null)} />
      </label>

      {inputs.length > 0 && (
        <details className="rounded-lg border border-border px-3 py-2 text-xs">
          <summary className="cursor-pointer font-medium">
            Inputs{Object.keys(overrides).length > 0 ? ` (${Object.keys(overrides).length} changed)` : ''}
          </summary>
          <div className="mt-2 flex max-h-72 flex-col gap-2 overflow-auto">
            {inputs.map((input) => {
              const title = input.title ?? input.name
              const current = title in overrides ? overrides[title] : input.defval
              if (input.type === 'bool') {
                return (
                  <Checkbox
                    key={input.id}
                    label={title}
                    checked={Boolean(current)}
                    onChange={(event) => setOverrides((before) => ({ ...before, [title]: event.target.checked }))}
                  />
                )
              }
              return (
                <label key={input.id} className="flex flex-col gap-1">
                  <span className="text-text-secondary">
                    {title} <span className="text-muted-foreground">({input.type})</span>
                  </span>
                  {input.options && input.options.length > 0 ? (
                    <Select
                      value={String(current)}
                      onChange={(event) =>
                        setOverrides((before) => ({ ...before, [title]: inputValue(input, event.target.value) }))
                      }
                      options={input.options.map((option) => ({ value: String(option), label: String(option) }))}
                    />
                  ) : (
                    <Input
                      value={String(current ?? '')}
                      inputMode={input.type === 'int' || input.type === 'float' ? 'decimal' : undefined}
                      onChange={(event) =>
                        setOverrides((before) => ({ ...before, [title]: inputValue(input, event.target.value) }))
                      }
                    />
                  )}
                </label>
              )
            })}
            {Object.keys(overrides).length > 0 && (
              <Button variant="ghost" size="sm" onClick={() => setOverrides({})}>
                Back to the script's values
              </Button>
            )}
          </div>
        </details>
      )}

      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          leftIcon={<Play className="size-3.5" />}
          isLoading={runMutation.isPending}
          disabled={!prepared || !worker || !ticker}
          onClick={() => runMutation.mutate()}
        >
          Run
        </Button>
        {scriptId && ticker && (
          <Link
            to={chartsPath({ symbol: tickerText(ticker), tf: barsKind === 'range' ? '1' : timeframe, script: scriptId })}
            className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-border px-3 text-xs hover:bg-surface-hover"
          >
            <ChartSpline className="size-3.5" />
            Open in Charts
          </Link>
        )}
        {run && (
          <Button
            variant="outline"
            size="sm"
            leftIcon={<Download className="size-3.5" />}
            onClick={() =>
              download(`${tickerText(run.ticker)}-${run.timeframe}-${run.day}.json`, JSON.stringify(run.candles, null, 1), 'application/json')
            }
          >
            Download data
          </Button>
        )}
        {run && prepared && (
          <Button
            variant="outline"
            size="sm"
            leftIcon={<Download className="size-3.5" />}
            title="The script as it ran, its libraries inlined: what pinets-cli runs on the data"
            onClick={() => download(`${fileName(scriptName)}.prepared.pine`, prepared.source, 'text/plain')}
          >
            Download script
          </Button>
        )}
      </div>
      {!prepared && <p className="text-xs text-muted-foreground">The script runs once it compiles.</p>}

      {runMutation.error && (
        <p role="alert" className="text-sm text-negative">
          {runMutation.error.message}
        </p>
      )}
      {response && !response.ok && (
        <p role="alert" className="whitespace-pre-wrap text-sm text-negative">
          {response.error.message}
        </p>
      )}
      {response?.ok && response.op === 'run' && run && (
        <p className="text-xs text-muted-foreground">
          Ran on {response.bars} bars of {tickerText(run.ticker)} in {response.milliseconds} ms: what it found is under the editor.
        </p>
      )}
    </Panel>
  )
}

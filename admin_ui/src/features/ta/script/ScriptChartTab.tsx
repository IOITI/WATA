// A script's page, its Chart tab: the script drawn on WATA's bars as it is being written, its code beside it. The
// chart follows the page's draft: each one that compiles is drawn in place (ScriptLiveChart.tsx), whichever editor
// it was typed in, this one or the Editor tab's (one draft, one Save). Its bars as the rules running it run them,
// first; "Take as picture" keeps what it shows as the script's picture in the Indicators list.

import { useQuery } from '@tanstack/react-query'
import { Button, Hint, Panel, Select } from 'ioiti-ui'
import { Camera, ImageOff } from 'lucide-react'
import { lazy, Suspense, useMemo, useRef, useState } from 'react'

import type { ChartSnapshot } from '@/features/prices/chartSnapshot'
import { useSession } from '@/features/auth/auth-context'
import { api } from '@/lib/api'
import type { TaScript } from '@/lib/types'

import { PREVIEW_TIMEFRAME, previewAssets, previewMarket, previewSymbol, scriptBars, scriptOpening } from '../chart/previewModel'
import type { PreparedScript } from '../engine/prepare'
import type { Diagnostic } from '../editor/diagnostics'
import { DiagnosticsList } from '../editor/DiagnosticsList'
import { PineEditor } from '../editor/PineEditor'
import type { PineFunction } from '../editor/pineLanguage'
import { chartPicture } from '../pictureFile'
import { useScriptPicture } from '../useScriptDetails'
import { rulesRunning } from './scriptPageModel'

const ScriptLiveChart = lazy(() =>
  import('@/features/ta/chart/ScriptLiveChart').then((module) => ({ default: module.ScriptLiveChart })),
)

const CHART_HEIGHT = 'h-80 sm:h-[28rem]'

export interface ScriptChartTabProps {
  script: TaScript
  /** The page's draft, as typed in either editor. */
  value: string
  onChange: (source: string) => void
  /** The draft with its libraries inlined, once it compiled; null while it does not. */
  prepared: PreparedScript | null
  diagnostics: Diagnostic[]
  checking: boolean
  dirty: boolean
  title: string
  onSave: () => void
  functions?: PineFunction[]
}

/** What the chart shows, in a sentence under it. */
function drawnSaid({ prepared, checking, dirty, version }: { prepared: PreparedScript | null; checking: boolean; dirty: boolean; version: number }): string {
  if (!prepared) return checking ? 'Compiling the draft…' : 'The draft does not compile: the chart keeps the last one that did.'
  if (dirty) return 'Your draft, unsaved: each change is drawn once it compiles, with the script’s own inputs.'
  return `Version ${version}, as saved, with its own inputs.`
}

export function ScriptChartTab({ script, value, onChange, prepared, diagnostics, checking, dirty, title, onSave, functions }: ScriptChartTabProps) {
  const { timezone } = useSession()
  const assetsQuery = useQuery({ queryKey: ['data-assets', 'all'], queryFn: () => api.dataAssets('all'), staleTime: 60_000 })
  const rulesQuery = useQuery({ queryKey: ['ta-rules'], queryFn: api.taRules })
  const listed = useMemo(() => assetsQuery.data?.items ?? [], [assetsQuery.data])
  // Its own rules: a library has no chart.
  const rules = useMemo(
    () => rulesRunning(script.id, rulesQuery.data?.rules ?? []).filter((rule) => rule.script.id === script.id),
    [rulesQuery.data, script.id],
  )
  const assets = previewAssets(listed)
  const offered = useMemo(() => scriptBars(rules), [rules])
  const opening = scriptOpening(listed, rules)
  const [chosenSymbol, setChosenSymbol] = useState<string | null>(null)
  const [chosenBars, setChosenBars] = useState<string | null>(null)
  const symbol = previewSymbol(listed, chosenSymbol ?? opening.symbol)
  const bars = offered.find((entry) => entry.value === (chosenBars ?? opening.bars)) ?? offered.find((entry) => entry.value === PREVIEW_TIMEFRAME)!
  const market = previewMarket(symbol, bars)
  // Built once what it opens on is known: never on a fallback first, then again.
  const known = !assetsQuery.isPending && !rulesQuery.isPending

  const snapshot = useRef<ChartSnapshot | null>(null)
  const picture = useScriptPicture()
  const [refused, setRefused] = useState<string | null>(null)
  const [taking, setTaking] = useState(false)
  const take = async () => {
    setRefused(null)
    const canvas = snapshot.current?.()
    if (!canvas) {
      setRefused('The chart has nothing to show yet.')
      return
    }
    setTaking(true)
    try {
      const blob = await chartPicture(canvas)
      if (!blob) {
        setRefused('The browser could not make a picture of the chart.')
        return
      }
      picture.set.mutate({ id: script.id, picture: blob, name: blob.type === 'image/jpeg' ? 'chart.jpg' : 'chart.png' })
    } finally {
      setTaking(false)
    }
  }

  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <Panel
        title="On a chart"
        description="Drawn by WATA on its own bars, nothing saved."
        action={
          <div className="flex flex-wrap gap-2">
            {script.own_picture && (
              <Hint content="The list shows LuxAlgo's picture of it again, or none">
                <Button variant="ghost" size="sm" leftIcon={<ImageOff className="size-3.5" />} isLoading={picture.drop.isPending}
                  onClick={() => picture.drop.mutate(script.id)}>
                  Remove the picture
                </Button>
              </Hint>
            )}
            <Hint content="What the chart shows now becomes the script's picture in the Indicators list">
              <Button variant="outline" size="sm" leftIcon={<Camera className="size-3.5" />} isLoading={taking || picture.set.isPending}
                disabled={!known} onClick={() => void take()}>
                Take as picture
              </Button>
            </Hint>
          </div>
        }
      >
        <div className="flex flex-col gap-3">
          {assets.length > 0 && (
            <div className="flex flex-wrap items-end gap-3">
              <label className="flex min-w-0 flex-1 flex-col gap-1 text-xs sm:w-56 sm:flex-none">
                <span className="text-text-secondary">Asset</span>
                <Select value={symbol} onChange={(event) => setChosenSymbol(event.target.value)}
                  options={assets.map((asset) => ({ value: asset.symbol, label: asset.label }))} />
              </label>
              <label className="flex min-w-0 flex-1 flex-col gap-1 text-xs sm:w-72 sm:flex-none">
                <span className="text-text-secondary">Bars</span>
                <Select value={bars.value} onChange={(event) => setChosenBars(event.target.value)}
                  options={offered.map((entry) => ({ value: entry.value, label: entry.label }))} />
              </label>
            </div>
          )}
          {known ? (
            <Suspense fallback={<div className={`${CHART_HEIGHT} rounded-xl border border-border`} />}>
              <ScriptLiveChart source={prepared?.source ?? null} name={title} symbol={market.symbol} timeframe={market.timeframe}
                timeZone={timezone} snapshotRef={snapshot} />
            </Suspense>
          ) : (
            <div className={`${CHART_HEIGHT} rounded-xl border border-border`} aria-busy />
          )}
          <p className="text-xs text-muted-foreground" aria-live="polite">
            {drawnSaid({ prepared, checking, dirty, version: script.version })}
          </p>
          {refused && (
            <p role="alert" className="text-sm text-negative">
              {refused}
            </p>
          )}
        </div>
      </Panel>
      <Panel
        title="Its code"
        description="The Editor's draft: a change here is one there, kept by the page's Save."
      >
        <div className="flex flex-col gap-3">
          <PineEditor
            value={value}
            onChange={onChange}
            diagnostics={diagnostics}
            label={`Pine source of ${title}, beside its chart`}
            onSave={onSave}
            functions={functions}
            className={CHART_HEIGHT}
          />
          <DiagnosticsList diagnostics={diagnostics} checking={checking} />
        </div>
      </Panel>
    </div>
  )
}

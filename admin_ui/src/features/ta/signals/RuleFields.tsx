// The fields a signal rule and a backtest share: the script and the version it
// runs, the script's own inputs (read by PineTS in the page's worker), what
// each alert becomes, the price side and the bars.

import type { IPineInput } from 'pinets'
import { useId, useState } from 'react'

import { Alert, AlertDescription } from '@/components/ui/alert'
import { Checkbox, Input, Select } from '@/components/ui/form'
import type { TaAlertAction, TaScriptSummary } from '@/lib/types'
import { cn } from '@/lib/utils'

import { messageAction } from '../engine/outline'
import { readTimeframe, TIMEFRAME_HINT, TIMEFRAMES, unevenNote } from '../engine/timeframes'
import { ACTION_LABEL, ALERT_ACTIONS, inputSections, inputValueOf } from './ruleModel'
import type { InputValue, RuleDraft } from './ruleModel'
import type { ScriptInputs } from './useScriptInputs'

const SOURCES = ['open', 'high', 'low', 'close', 'hl2', 'hlc3', 'ohlc4', 'hlcc4', 'volume']

type Update = (changes: Partial<RuleDraft>) => void

function InputField({
  input,
  value,
  onChange,
  onReset,
}: {
  input: IPineInput
  value: InputValue | undefined
  onChange: (value: InputValue | null, raw: string | boolean) => void
  onReset: () => void
}) {
  const title = input.title ?? input.name ?? input.id
  const shown = value ?? (input.defval as InputValue)
  const changed = value !== undefined
  const hint = (
    <span className="text-muted-foreground">
      {input.type}
      {changed && (
        <>
          {' · '}the script says {String(input.defval)}{' '}
          <button type="button" className="underline underline-offset-2" onClick={onReset}>
            put it back
          </button>
        </>
      )}
    </span>
  )
  if (input.type === 'bool') {
    return (
      <div className="flex flex-col gap-1">
        <Checkbox label={title} checked={Boolean(shown)} onChange={(event) => onChange(event.target.checked, event.target.checked)} />
        <span className="text-xs">{hint}</span>
      </div>
    )
  }
  const options = input.options?.length ? input.options.map(String) : input.type === 'source' ? SOURCES : null
  return (
    <div className="flex flex-col gap-1 text-xs">
      <label className="flex flex-col gap-1">
        <span className="text-text-secondary">{title}</span>
        {options ? (
          <Select
            value={String(shown)}
            onChange={(event) => onChange(inputValueOf(input, event.target.value), event.target.value)}
            options={options.map((option) => ({ value: option, label: option }))}
          />
        ) : (
          <Input
            value={String(shown ?? '')}
            type={input.type === 'int' || input.type === 'float' || input.type === 'price' ? 'number' : 'text'}
            step={input.step ?? (input.type === 'int' ? 1 : 'any')}
            min={input.minval}
            max={input.maxval}
            onChange={(event) => onChange(inputValueOf(input, event.target.value), event.target.value)}
          />
        )}
      </label>
      {hint}
    </div>
  )
}

/** The script and its version. A script chosen again starts with its own inputs and no alert mapped. */
export function ScriptChoice({
  draft,
  update,
  scripts,
  onScriptChange,
}: {
  draft: RuleDraft
  update: Update
  scripts: TaScriptSummary[]
  onScriptChange?: () => void
}) {
  const runnable = scripts.filter((script) => script.kind !== 'library')
  const script = scripts.find((entry) => entry.id === draft.scriptId)
  return (
    <>
      <label className="flex flex-col gap-1">
        <span className="text-xs text-text-secondary">Script</span>
        <Select
          value={draft.scriptId}
          onChange={(event) => {
            const chosen = scripts.find((entry) => entry.id === event.target.value)
            update({ scriptId: event.target.value, version: chosen?.version ?? 1, inputs: {}, inputNames: {}, alerts: {}, confidencePlot: '' })
            onScriptChange?.()
          }}
          options={runnable.map((entry) => ({ value: entry.id, label: entry.name }))}
        />
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-xs text-text-secondary">Version</span>
        <Select
          value={String(draft.version)}
          onChange={(event) => update({ version: Number(event.target.value) })}
          options={[...(script?.versions ?? [])].reverse().map((entry) => ({
            value: String(entry.number),
            label: `v${entry.number}${entry.number === script?.version ? ' (current)' : ''}`,
          }))}
        />
      </label>
    </>
  )
}

/**
 * The price side and the bars the script runs on, a signal rule's or a backtest's: any
 * length WATA makes, from ten seconds to a day, typed when it is none of the menu's, or
 * range bars. The engine runs a rule's script over all its bars at each new one.
 */
export function BarsFields({
  draft,
  update,
  rangeNote = null,
}: {
  draft: RuleDraft
  update: Update
  /** What the range is in the asset's points, said under its field. */
  rangeNote?: string | null
}) {
  const offered = TIMEFRAMES
  const inMenu = offered.some((timeframe) => timeframe.pine === draft.timeframe)
  // Typed rather than chosen: kept while it is typed, even when it reads as one of the menu's ("1" on the way to "15s").
  const [typingChosen, setTyping] = useState(() => !inMenu)
  const typing = typingChosen || (draft.barsType === 'time' && !inMenu)
  const typed = typing ? readTimeframe(draft.timeframe) : null
  const length = typing ? (typed && 'timeframe' in typed ? typed.timeframe : null) : offered.find((timeframe) => timeframe.pine === draft.timeframe)
  const note = draft.barsType === 'time' && length ? unevenNote(length.seconds) : null
  const helpId = useId()
  return (
    <>
      <label className="flex flex-col gap-1">
        <span className="text-xs text-text-secondary">Price</span>
        <Select
          value={draft.side}
          onChange={(event) => update({ side: event.target.value === 'ask' ? 'ask' : 'bid' })}
          options={[
            { value: 'bid', label: 'Bid' },
            { value: 'ask', label: 'Ask' },
          ]}
        />
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-xs text-text-secondary">Bars</span>
        <Select
          value={draft.barsType === 'range' ? 'range' : typing ? 'typed' : draft.timeframe}
          onChange={(event) => {
            const value = event.target.value
            if (value === 'range') {
              update({ barsType: 'range' })
              return
            }
            setTyping(value === 'typed')
            update(value === 'typed' ? { barsType: 'time' } : { barsType: 'time', timeframe: value })
          }}
          options={[
            ...offered.map((timeframe) => ({ value: timeframe.pine, label: timeframe.label })),
            { value: 'typed', label: 'Other length…' },
            { value: 'range', label: 'Range bars' },
          ]}
        />
      </label>
      {draft.barsType === 'range' ? (
        <div className="flex flex-col gap-1">
          <label className="flex flex-col gap-1">
            <span className="text-xs text-text-secondary">Range, in steps of the price</span>
            <Input
              type="number"
              min={1}
              step={1}
              value={draft.ticks}
              aria-describedby={rangeNote ? helpId : undefined}
              onChange={(event) => update({ ticks: event.target.value })}
            />
          </label>
          {rangeNote && (
            <span id={helpId} className="text-xs text-muted-foreground">
              {rangeNote}
            </span>
          )}
        </div>
      ) : typing ? (
        <div className="flex flex-col gap-1">
          <label className="flex flex-col gap-1">
            <span className="text-xs text-text-secondary">Length of the bars</span>
            <Input
              value={draft.timeframe}
              placeholder="45s, 90s, 2m"
              title={TIMEFRAME_HINT}
              autoComplete="off"
              spellCheck={false}
              error={!length}
              aria-invalid={!length}
              aria-describedby={helpId}
              onChange={(event) => update({ timeframe: event.target.value })}
            />
          </label>
          <span id={helpId} className={cn('text-xs', length ? 'text-muted-foreground' : 'text-red-700 dark:text-[#FF7A7A]')}>
            {length ? `Bars of ${length.label}.` : typed && 'error' in typed ? `${typed.error} ${TIMEFRAME_HINT}` : TIMEFRAME_HINT}
          </span>
          {note && <span className="text-xs text-muted-foreground">{note}</span>}
        </div>
      ) : note ? (
        <p className="self-center text-xs text-muted-foreground">{note}</p>
      ) : (
        <div />
      )}
    </>
  )
}

/** The script's inputs in the sections it declares them in, what each alert becomes, and the plot read as a confidence. */
export function ScriptSettings({
  draft,
  update,
  inputs,
  invalidInputs,
  setInvalidInputs,
}: {
  draft: RuleDraft
  update: Update
  inputs: ScriptInputs
  invalidInputs: Record<string, string>
  setInvalidInputs: (change: (current: Record<string, string>) => Record<string, string>) => void
}) {
  const { meta, outline, source } = inputs
  const titles = [
    ...(outline?.alerts.map((alert) => alert.title) ?? []),
    ...Object.keys(draft.alerts).filter((title) => !outline?.alerts.some((alert) => alert.title === title)),
  ]
  return (
    <>
      <section className="space-y-2">
        <h3 className="text-xs font-medium uppercase tracking-wide text-text-secondary">Inputs</h3>
        {inputs.pending && source && <p className="text-xs text-muted-foreground">Reading the script’s inputs…</p>}
        {inputs.error && (
          <Alert tone="danger">
            <AlertDescription>
              This version does not compile: {inputs.error.message}. Fix it on the Indicators page, or choose another version.
            </AlertDescription>
          </Alert>
        )}
        {meta && meta.length === 0 && <p className="text-xs text-muted-foreground">The script has no inputs.</p>}
        {meta && meta.length > 0 && (
          // In the sections the script declares them in: each group in a box named after it.
          <div className="space-y-3">
            {inputSections(meta).map((section, index) => {
              const fields = (
                <div className="grid gap-3 sm:grid-cols-2">
                  {section.inputs.map((input) => (
                    <InputField
                      key={input.id}
                      input={input}
                      value={input.id in invalidInputs ? invalidInputs[input.id] : draft.inputs[input.id]}
                      onChange={(value, raw) => {
                        setInvalidInputs(({ [input.id]: _dropped, ...others }) => (value === null ? { ...others, [input.id]: String(raw) } : others))
                        if (value !== null) update({ inputs: { ...draft.inputs, [input.id]: value } })
                      }}
                      onReset={() => {
                        const { [input.id]: _reset, ...kept } = draft.inputs
                        setInvalidInputs(({ [input.id]: _dropped, ...others }) => others)
                        update({ inputs: kept })
                      }}
                    />
                  ))}
                </div>
              )
              return section.group === null ? (
                <div key={`none-${index}`}>{fields}</div>
              ) : (
                <fieldset key={`group-${section.group}`} className="min-w-0 rounded-lg border border-border/70 px-3 pt-1 pb-3">
                  <legend className="px-1 text-xs font-semibold text-foreground">{section.group}</legend>
                  {fields}
                </fieldset>
              )
            })}
          </div>
        )}
      </section>

      <section className="space-y-2">
        <h3 className="text-xs font-medium uppercase tracking-wide text-text-secondary">What its alerts become</h3>
        {outline && titles.length === 0 && !outline.alertCalls && (
          <p className="text-xs text-muted-foreground">This version raises no alert: alertcondition() and alert() are what become signals.</p>
        )}
        <div className="space-y-2">
          {titles.map((title) => {
            const found = outline?.alerts.find((alert) => alert.title === title)
            const says = messageAction(found?.message ?? null)
            return (
              <label key={title} className="grid items-center gap-2 sm:grid-cols-[1fr_14rem]">
                <span className="min-w-0">
                  <span className="font-medium">{title}</span>
                  <span className="block text-xs text-muted-foreground">
                    {!found ? 'Not in this version: it will never be raised.' : says ? `Its message says ${says}.` : 'alertcondition()'}
                  </span>
                </span>
                <Select
                  aria-label={`What “${title}” becomes`}
                  value={draft.alerts[title] ?? 'ignore'}
                  onChange={(event) => update({ alerts: { ...draft.alerts, [title]: event.target.value as TaAlertAction } })}
                  options={ALERT_ACTIONS}
                />
              </label>
            )
          })}
          {(outline?.alertCalls || draft.alertCalls === 'message') && (
            <label className="grid items-center gap-2 sm:grid-cols-[1fr_14rem]">
              <span>
                <span className="font-medium">alert() calls</span>
                <span className="block text-xs text-muted-foreground">They have no title: all of them, or none.</span>
              </span>
              <Select
                aria-label="What alert() calls become"
                value={draft.alertCalls}
                onChange={(event) => update({ alertCalls: event.target.value === 'message' ? 'message' : 'ignore' })}
                options={[
                  { value: 'ignore', label: ACTION_LABEL.ignore },
                  { value: 'message', label: ACTION_LABEL.message },
                ]}
              />
            </label>
          )}
        </div>
        <p className="text-xs text-muted-foreground">
          A message is read as WATA’s webhook JSON, as TradingView sends it: its action (long, short or close-position), its
          indice (which must be the rule’s) and its confidence. WATA stamps each signal with the close of its bar.
        </p>
        <p className="text-xs text-muted-foreground">
          One bar, one trade: when a bar raises an entry and a close of positions, only the entry is sent. It sells the other
          side before it buys, and a close after it would sell the position it just opened.
        </p>
      </section>

      {outline && outline.plots.length > 0 && (
        <label className="flex max-w-sm flex-col gap-1">
          <span className="text-xs text-text-secondary">Confidence (a plot read at the bar, 0 to 2)</span>
          <Select
            value={draft.confidencePlot}
            onChange={(event) => update({ confidencePlot: event.target.value })}
            options={[{ value: '', label: 'None' }, ...outline.plots.map((plot) => ({ value: plot, label: plot }))]}
          />
        </label>
      )}
    </>
  )
}

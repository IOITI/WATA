// What a run was asked, as its page and its configuration's page say it: its signals, its asset, its days, what it
// was made with, and what it changed of WATA's settings.

import { formatDateTime, formatDay, formatNumber, Panel } from 'ioiti-ui'
import { Link } from 'react-router'

import { barsLabel } from '@/features/ta/signals/ruleModel'
import { RulePeekName } from '@/features/ta/signals/RulePeek'
import type { BacktestRun } from '@/lib/types'

import { assetLabel, settingChanges } from './model'
import { Measure, SettingChanges } from './parts'
import { underlyingLabel } from './turboModel'

/** What a run was asked: its signals, its asset, its days, and what it changed of WATA's settings. */
export function RunFacts({ run, labels }: { run: BacktestRun; labels: Parameters<typeof settingChanges>[2] }) {
  const request = run.request
  const source = request.source
  const engine = run.summary?.made_with.engine
  const turbos = run.summary?.made_with.turbos
  const modelled = turbos && turbos.kind === 'modelled' ? turbos : null
  const execution = run.summary?.made_with.execution
  const drawn = execution && execution.kind === 'measured' ? execution : null
  return (
    <Panel title="What it ran">
      <div className="grid gap-8 lg:grid-cols-2">
        <dl className="flex flex-col gap-1.5">
          <Measure label="Signals">
            {source.kind === 'pine' ? (
              <>
                <Link to={`/ta/indicators/${source.rule.script.id}`} className="underline-offset-4 hover:underline">
                  {source.rule.name}
                </Link>{' '}
                v{source.rule.script.version}
                {source.from_rule && (
                  <>
                    {' '}
                    (the rule <RulePeekName id={source.from_rule.id} name={source.from_rule.name} />)
                  </>
                )}
              </>
            ) : source.kind === 'ta_signals' ? (
              <>
                The {formatNumber(source.signals)} signal{source.signals === 1 ? '' : 's'}{' '}
                <RulePeekName id={source.rule.id} name={source.rule.name} /> raised
                {source.versions && source.versions.length > 0 && (
                  <span className="block text-xs text-muted-foreground">
                    as the engine journaled them, by {source.versions.map((version) => `v${version}`).join(' and ')} of its script
                  </span>
                )}
              </>
            ) : (
              `The ${source.signals} signal${source.signals === 1 ? '' : 's'} WATA received`
            )}
          </Measure>
          <Measure label="Asset">
            {assetLabel(request.chart.asset)} · {barsLabel(request.chart.bars)} · {request.chart.side}
          </Measure>
          <Measure label="Turbos of">{request.indice}</Measure>
          {modelled && (
            <Measure label="Model">
              <span className="text-xs text-warning">
                Turbos made up from {underlyingLabel(modelled.underlying)}, measured on {modelled.days} recorded day{modelled.days === 1 ? '' : 's'}
                {modelled.few_days ? ': few, a rough guess' : ''}. Check it against the recorded days.
              </span>
            </Measure>
          )}
          <Measure label="Period">
            {formatDay(request.from)} to {formatDay(request.to)}
          </Measure>
          {source.kind === 'pine' && source.rule.inputs.length > 0 && (
            <Measure label="Inputs">{source.rule.inputs.map((entry) => `${entry.title ?? entry.id} ${String(entry.value)}`).join(', ')}</Measure>
          )}
          {source.kind === 'pine' && (
            <Measure label="Alerts">{source.rule.alerts.map((entry) => `“${entry.title}” → ${entry.action}`).join('; ') || 'alert() calls'}</Measure>
          )}
          <Measure label="Asked">{formatDateTime(run.status.created_at, 'UTC', false)} UTC</Measure>
          {run.summary && (
            <Measure label="Made with">
              WATA {run.summary.made_with.version}
              {engine ? `, PineTS ${engine.runner.pinets} (${formatNumber(engine.milliseconds / 1000, 1)} s of script)` : ''}
              {drawn && (
                <span className="block text-xs text-muted-foreground">
                  Each order’s time and price drawn from {formatNumber(drawn.orders)} of WATA’s own, from {formatDay(drawn.from)} to{' '}
                  {formatDay(drawn.to)}
                </span>
              )}
              {run.stale && <span className="block text-xs text-warning">Other code than this admin’s: it could come out otherwise today.</span>}
            </Measure>
          )}
        </dl>
        <div className="flex flex-col gap-2">
          <h3 className="text-xs font-medium text-muted-foreground">Changed from WATA’s settings</h3>
          <SettingChanges
            label="Changed from WATA’s settings"
            changes={settingChanges(run.settings.changed, run.settings.live_before, labels)}
            none="Nothing: the live settings of the day it was asked."
          />
        </div>
      </div>
    </Panel>
  )
}

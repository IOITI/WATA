import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useMemo, useState } from 'react'

import { ConfirmLiveAction } from '@/components/ConfirmLiveAction'
import { PageHeader, Panel } from '@/components/Panel'
import { QueryState } from '@/components/status'
import { useToast } from '@/components/toast-context'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { AnimatedTabs } from '@/components/ui/animated-tabs'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/form'
import { SmoothAccordion } from '@/components/ui/smooth-accordion'
import { SpringSelect } from '@/components/ui/spring-select'
import { StretchSwitch } from '@/components/ui/stretch-switch'
import { useSession } from '@/features/auth/auth-context'
import { api, ApiError } from '@/lib/api'
import { formatDateTime } from '@/lib/format'
import type { ConfigDocument, JsonObject, JsonValue } from '@/lib/types'

import { BackupsPanel } from './BackupsPanel'
import { ChangeList } from './ChangeList'
import {
  applyEditable,
  diffConfigs,
  getAt,
  parseJsonObject,
  pickEditable,
  ruleConfigPath,
  setAt,
  toggled,
  withoutRule,
  withRule,
} from './configModel'
import type { ConfigPath } from './configModel'
import { DraftProvider, FieldGrid, MissingRule, NumberField, SwitchField, TextField } from './fields'
import { useDraft } from './draft-context'
import { LeaveGuard } from './LeaveGuard'
import { DateListField, MapField, RowsField } from './listFields'

const TABS = [
  { id: 'settings', label: 'Trading settings' },
  { id: 'json', label: 'JSON' },
  { id: 'file', label: 'Whole file' },
  { id: 'backups', label: 'Previous versions' },
]

const LOG_LEVELS = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'].map((level) => ({ value: level, label: level }))

const POSITION_MANAGEMENT: ConfigPath = ['trade', 'config', 'position_management']
const TURBO_PREFERENCE: ConfigPath = ['trade', 'config', 'turbo_preference']
const BUYING_POWER: ConfigPath = ['trade', 'config', 'buying_power']
const POSITION_SIZING: ConfigPath = ['trade', 'config', 'position_sizing']

const PERIOD_COLUMNS = [
  { key: 'label', label: 'Name', kind: 'text' as const, width: '2fr' },
  { key: 'start_hour', label: 'From (hour)', kind: 'number' as const },
  { key: 'start_minute', label: 'From (min)', kind: 'number' as const },
  { key: 'end_hour', label: 'To (hour)', kind: 'number' as const },
  { key: 'end_minute', label: 'To (min)', kind: 'number' as const },
  { key: 'scale_percent', label: 'Size (%)', kind: 'number' as const },
]

const CONFIDENCE_COLUMNS = [
  { key: 'label', label: 'Name', kind: 'text' as const, width: '2fr' },
  { key: 'min_confidence', label: 'Confidence from', kind: 'number' as const },
  { key: 'max_confidence', label: 'Confidence below', kind: 'number' as const },
  { key: 'scale_percent', label: 'Size (%)', kind: 'number' as const },
]

// No entry around US releases: the trader's macro_news rule (src/trade/macro_calendar.py), with its defaults.
const NEWS_EVENTS = [
  { event: 'FOMC', label: 'FOMC statements' },
  { event: 'CPI', label: 'CPI' },
  { event: 'NFP', label: 'The jobs report (NFP)' },
  { event: 'PCE', label: 'PCE' },
]
const NEWS_RULE = {
  rule_name: 'No entry around US releases',
  rule_type: 'macro_news',
  rule_config: {
    events: NEWS_EVENTS.map((entry) => entry.event),
    minutes_before: 5,
    minutes_after: 15,
    minutes_after_fomc: 45,
    flatten_minutes_before: 0,
  },
}

function prettyJson(value: JsonValue): string {
  return JSON.stringify(value, null, 2)
}

function RuleSection({
  config,
  ruleType,
  children,
}: {
  config: JsonObject
  ruleType: string
  children: (path: ConfigPath) => React.ReactNode
}) {
  const path = ruleConfigPath(config, ruleType)
  return path ? <FieldGrid>{children(path)}</FieldGrid> : <MissingRule ruleType={ruleType} />
}

/** The window around US releases: the rule added with its defaults when switched on, taken away when off. */
function NewsSection({ draft }: { draft: JsonObject }) {
  const { update, disabled } = useDraft()
  const path = ruleConfigPath(draft, 'macro_news')
  const events = path ? getAt(draft, [...path, 'events']) : undefined
  const all = NEWS_EVENTS.map((entry) => entry.event)
  return (
    <div className="space-y-5">
      <StretchSwitch
        label="No entry around US releases"
        description="The trader refuses new entries from shortly before to shortly after each release of the calendar kept with WATA (New York's hours, whatever the clocks). Positions already open are kept, unless the position monitor is to sell them before the release (below)."
        checked={path !== null}
        disabled={disabled}
        onChange={(on) => update(['trade', 'rules'], on ? withRule(draft, NEWS_RULE) : withoutRule(draft, 'macro_news'))}
      />
      {path && (
        <FieldGrid>
          <NumberField label="From before a release" unit="min" path={[...path, 'minutes_before']} hint="0 to 120" />
          <NumberField label="Until after it" unit="min" path={[...path, 'minutes_after']} hint="0 to 120" />
          <NumberField
            label="Until after an FOMC statement"
            unit="min"
            path={[...path, 'minutes_after_fomc']}
            hint="Its press conference starts 30 minutes later"
          />
          <NumberField
            label="Sell what is held, from before a release"
            unit="min"
            path={[...path, 'flatten_minutes_before']}
            hint="0 keeps open positions. Around a release Saxo can stop quoting a turbo for minutes, when no stop can be sold"
          />
          {NEWS_EVENTS.map((entry) => (
            <StretchSwitch
              key={entry.event}
              label={entry.label}
              checked={Array.isArray(events) && events.includes(entry.event)}
              disabled={disabled}
              onChange={(on) => update([...path, 'events'], toggled(all, events, entry.event, on))}
            />
          ))}
        </FieldGrid>
      )}
    </div>
  )
}

function SettingsForm({ draft }: { draft: JsonObject }) {
  const sections = [
    {
      id: 'day',
      title: 'Daily targets',
      subtitle: 'When the day stops taking new trades, and when positions are closed',
      content: (
        <RuleSection config={draft} ruleType="day_trading">
          {(path) => (
            <>
              <NumberField label="Daily profit target" unit="%" path={[...path, 'percent_profit_wanted_per_days']} />
              <NumberField
                label="No new entry above"
                unit="%"
                path={[...path, 'dont_enter_trade_if_day_profit_is_more_than']}
                hint="Day performance above which signals are refused"
              />
              <NumberField
                label="Maximum day loss"
                unit="%"
                path={[...path, 'max_day_loss_percent']}
                hint="Negative. Signals are refused below it"
              />
              <TextField
                label="Close positions at"
                path={[...path, 'close_position_time']}
                placeholder="21:55"
                hint="HH:MM, in the trading timezone. Read by the scheduler"
              />
            </>
          )}
        </RuleSection>
      ),
    },
    {
      id: 'risk',
      title: 'Risk limits',
      subtitle: 'Trades per day and the pause after a loss',
      content: (
        <RuleSection config={draft} ruleType="risk_management">
          {(path) => (
            <>
              <NumberField label="Trades per day" path={[...path, 'max_trades_per_day']} hint="0 for no limit" />
              <NumberField
                label="Cooldown after a loss"
                unit="min"
                path={[...path, 'cooldown_after_loss_minutes']}
                hint="0 to switch it off"
              />
            </>
          )}
        </RuleSection>
      ),
    },
    {
      id: 'news',
      title: 'US releases',
      subtitle: 'No new entry around CPI, the jobs report, PCE and FOMC statements',
      content: <NewsSection draft={draft} />,
    },
    {
      id: 'exits',
      title: 'Position exits',
      subtitle: 'Stop-loss, take-profit and trailing stop applied by the position monitor',
      content: (
        <div className="space-y-5">
          <FieldGrid>
            <NumberField
              label="Stop-loss"
              unit="%"
              path={[...POSITION_MANAGEMENT, 'performance_thresholds', 'stoploss_percent']}
              hint="Negative"
            />
            <NumberField
              label="Take-profit"
              unit="%"
              path={[...POSITION_MANAGEMENT, 'performance_thresholds', 'max_profit_percent']}
            />
          </FieldGrid>
          <SwitchField
            label="Trailing stop"
            hint="Closes when the position falls back from its best performance"
            path={[...POSITION_MANAGEMENT, 'performance_thresholds', 'trailing_stop', 'enabled']}
          />
          <FieldGrid>
            <NumberField
              label="Trailing stop starts at"
              unit="%"
              path={[...POSITION_MANAGEMENT, 'performance_thresholds', 'trailing_stop', 'activation_percent']}
            />
            <NumberField
              label="Trailing stop drawdown"
              unit="%"
              path={[...POSITION_MANAGEMENT, 'performance_thresholds', 'trailing_stop', 'drawdown_percent']}
            />
          </FieldGrid>
          <SwitchField
            label="Broker-side protective stop"
            hint="A stop order placed at Saxo with each position. Not yet tested against Saxo"
            path={[...POSITION_MANAGEMENT, 'protective_stop', 'enabled']}
          />
          <FieldGrid>
            <NumberField
              label="Protective stop"
              unit="%"
              path={[...POSITION_MANAGEMENT, 'protective_stop', 'percent']}
              hint="Must be wider than the stop-loss"
            />
          </FieldGrid>
        </div>
      ),
    },
    {
      id: 'sizing',
      title: 'Instrument and size',
      subtitle: 'Which turbos are bought, and with how much of the account',
      content: (
        <FieldGrid>
          <NumberField label="Turbo price from" path={[...TURBO_PREFERENCE, 'price_range', 'min']} />
          <NumberField label="Turbo price up to" path={[...TURBO_PREFERENCE, 'price_range', 'max']} />
          <NumberField
            label="Account funds to use"
            unit="%"
            path={[...BUYING_POWER, 'max_account_funds_to_use_percentage']}
            hint="1 to 100"
          />
          <NumberField label="Cash kept in reserve" unit="%" path={[...BUYING_POWER, 'reserve_cash_percent']} />
        </FieldGrid>
      ),
    },
    {
      id: 'position-sizing',
      title: 'Position size by time and confidence',
      subtitle: 'Scales the size of a new position down outside the best conditions',
      content: (
        <div className="space-y-5">
          <SwitchField
            label="Scale by time of day"
            hint="The period the signal arrives in sets the share of the usual size"
            path={[...POSITION_SIZING, 'time_of_day_scaling', 'enabled']}
          />
          <RowsField
            label="Periods"
            path={[...POSITION_SIZING, 'time_of_day_scaling', 'periods']}
            columns={PERIOD_COLUMNS}
            newRow={() => ({
              label: 'New period', start_hour: 8, start_minute: 0, end_hour: 22, end_minute: 0, scale_percent: 100,
            })}
            hint="Hours and minutes in the trading timezone."
          />
          <SwitchField
            label="Scale by signal confidence"
            hint="The confidence sent with a signal sets the share of the usual size"
            path={[...POSITION_SIZING, 'confidence_scaling', 'enabled']}
          />
          <FieldGrid>
            <NumberField
              label="Confidence when a signal has none"
              path={[...POSITION_SIZING, 'confidence_scaling', 'default_confidence']}
            />
            <NumberField
              label="Refuse signals below"
              path={[...POSITION_SIZING, 'confidence_scaling', 'min_confidence_threshold']}
              hint="Confidence under which a signal is not traded"
            />
          </FieldGrid>
          <RowsField
            label="Confidence bands"
            path={[...POSITION_SIZING, 'confidence_scaling', 'scaling_rules']}
            columns={CONFIDENCE_COLUMNS}
            newRow={() => ({ label: 'New band', min_confidence: 0, max_confidence: 2, scale_percent: 100 })}
          />
        </div>
      ),
    },
    {
      id: 'indices',
      title: 'Allowed indices',
      subtitle: 'The indices signals may name, and the Saxo identifier of each',
      content: (
        <RuleSection config={draft} ruleType="allowed_indices">
          {(path) => (
            <div className="sm:col-span-2 lg:col-span-3">
              <MapField
                label="Indices"
                path={[...path, 'indice_ids']}
                keyLabel="Name in the signal"
                valueLabel="Saxo UIC of the underlying"
                hint="A signal for any other indice is refused."
              />
            </div>
          )}
        </RuleSection>
      ),
    },
    {
      id: 'hours',
      title: 'Market hours and signals',
      subtitle: 'When signals are accepted and how old they may be',
      content: (
        <div className="space-y-5">
          <RuleSection config={draft} ruleType="market_hours">
            {(path) => (
              <>
                <NumberField label="Trading starts" unit="hour" path={[...path, 'trading_start_hour']} />
                <NumberField label="Trading ends" unit="hour" path={[...path, 'trading_end_hour']} />
                <NumberField label="Risky period from" unit="hour" path={[...path, 'risky_trading_start_hour']} />
                <NumberField label="Risky period from" unit="minute" path={[...path, 'risky_trading_start_minute']} />
              </>
            )}
          </RuleSection>
          <RuleSection config={draft} ruleType="signal_validation">
            {(path) => (
              <NumberField
                label="Maximum signal age"
                unit="s"
                path={[...path, 'max_signal_age_seconds']}
                hint="Whole seconds"
              />
            )}
          </RuleSection>
        </div>
      ),
    },
    {
      id: 'closed',
      title: 'Market closed dates',
      subtitle: 'Days on which no signal is traded',
      content: (
        <RuleSection config={draft} ruleType="market_closed_dates">
          {(path) => (
            <div className="sm:col-span-2 lg:col-span-3">
              <DateListField
                label="Dates"
                path={[...path, 'market_closed_dates']}
                hint="Stored as DD/MM/YYYY. Past dates have no effect and can be removed."
              />
            </div>
          )}
        </RuleSection>
      ),
    },
  ]

  return <SmoothAccordion items={sections} allowMultiple defaultOpen={['day', 'risk']} />
}

function ConfigEditor({ configDoc }: { configDoc: ConfigDocument }) {
  const toast = useToast()
  const queryClient = useQueryClient()
  const { timezone } = useSession()

  const [draft, setDraft] = useState<JsonObject>(configDoc.config)
  const [tab, setTab] = useState('settings')
  // The JSON tab edits text; it only becomes the draft once it parses.
  const [jsonText, setJsonText] = useState('')
  const [jsonError, setJsonError] = useState<string | null>(null)
  // Bumped to remount the fields (which hold what is being typed) when the draft is replaced.
  const [formVersion, setFormVersion] = useState(0)
  const [reviewing, setReviewing] = useState(false)
  // Number fields holding something that is not a number: the draft does not have what they show.
  const [invalidFields, setInvalidFields] = useState<ReadonlySet<string>>(() => new Set())
  const reportInvalid = useCallback((fieldId: string, invalid: boolean) => {
    setInvalidFields((current) => {
      if (current.has(fieldId) === invalid) return current
      const next = new Set(current)
      if (invalid) next.add(fieldId)
      else next.delete(fieldId)
      return next
    })
  }, [])

  const changes = useMemo(() => diffConfigs(configDoc.config, draft), [configDoc.config, draft])
  const disabled = !configDoc.editable
  const invalidCount = invalidFields.size
  const jsonBroken = !!jsonError && tab === 'json'
  // What the form shows cannot be sent as it is.
  const unusable = invalidCount > 0 || jsonBroken
  const unsaved = changes.length > 0 || unusable

  const replaceDraft = (next: JsonObject) => {
    setDraft(next)
    setFormVersion((version) => version + 1)
  }

  const selectTab = (next: string) => {
    if (next === 'json') {
      setJsonText(prettyJson(pickEditable(draft, configDoc.editable_paths)))
      setJsonError(null)
    }
    if (tab === 'json' && next !== 'json') setFormVersion((version) => version + 1)
    setTab(next)
  }

  const validateMutation = useMutation({ mutationFn: () => api.validateConfig(draft) })
  const saveMutation = useMutation({
    mutationFn: () => api.saveConfig(draft, configDoc.revision),
    onSuccess: async (saved) => {
      setReviewing(false)
      toast({
        title: 'Configuration saved',
        description: `Previous version kept as ${saved.backup}.`,
        variant: 'success',
      })
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['config'] }),
        queryClient.invalidateQueries({ queryKey: ['config-backups'] }),
        queryClient.invalidateQueries({ queryKey: ['session'] }),
      ])
    },
  })
  const conflict = saveMutation.error instanceof ApiError && saveMutation.error.status === 409

  return (
    <>
      {disabled && (
        <Alert tone="neutral">
          <AlertDescription>
            Editing is switched off in the configuration (<code>admin.config_edit_enabled</code>). The file is shown
            read-only.
          </AlertDescription>
        </Alert>
      )}

      <div className="flex flex-wrap items-center justify-between gap-3">
        <AnimatedTabs tabs={TABS} activeTab={tab} onChange={selectTab} renderContent={false} />
        <p className="text-xs text-muted-foreground">File last written {formatDateTime(configDoc.modified_at, timezone)}</p>
      </div>

      <DraftProvider
        value={{
          draft,
          disabled,
          reportInvalid,
          update: (path, value) => {
            validateMutation.reset()
            setDraft((current) => setAt(current, path, value) as JsonObject)
          },
        }}
      >
        {tab === 'settings' && (
          <div key={formVersion} className="space-y-4">
            <SettingsForm draft={draft} />
            <Panel title="Logging">
              <SpringSelect
                className="max-w-xs"
                label="Log level"
                options={LOG_LEVELS}
                value={String(getAt(draft, ['logging', 'level']) ?? 'INFO')}
                disabled={disabled}
                onChange={(level) => setDraft((current) => setAt(current, ['logging', 'level'], level) as JsonObject)}
              />
            </Panel>
            <p className="text-xs text-muted-foreground">
              The watchlist manager, streaming, polling and the other settings of <code>trade.config</code> are edited
              in the JSON tab.
            </p>
          </div>
        )}
      </DraftProvider>

      {tab === 'json' && (
        <Panel
          title="Editable sections"
          description={`Only ${configDoc.editable_paths.join(', ')} can be changed here. Credentials and infrastructure settings are edited on the server.`}
        >
          {configDoc.protected_paths.length > 0 && (
            <p className="mb-3 text-xs text-muted-foreground">
              Inside these sections, where the services connect and listen is changed on the server as well. A save
              that changes one of these is refused:{' '}
              {configDoc.protected_paths.map((path, index) => (
                <span key={path}>
                  {index > 0 && ', '}
                  <code>{path}</code>
                </span>
              ))}
              .
            </p>
          )}
          <Textarea
            aria-label="Editable configuration as JSON"
            className="min-h-[28rem] font-mono"
            spellCheck={false}
            value={jsonText}
            disabled={disabled}
            error={!!jsonError}
            onChange={(event) => {
              const text = event.target.value
              setJsonText(text)
              const parsed = parseJsonObject(text)
              if (parsed.ok) {
                setJsonError(null)
                validateMutation.reset()
                setDraft(applyEditable(configDoc.config, parsed.value, configDoc.editable_paths))
              } else {
                setJsonError(parsed.error)
              }
            }}
          />
          {jsonError && <p className="mt-2 text-xs text-negative">Not valid JSON yet: {jsonError}</p>}
        </Panel>
      )}

      {tab === 'file' && (
        <Panel title="config.json" description="As it is on the server, with secrets masked. Unsaved changes are not shown here.">
          <pre className="max-h-[36rem] overflow-auto rounded-lg border border-border bg-surface-raised/40 p-4 text-xs leading-relaxed">
            {prettyJson(configDoc.config)}
          </pre>
        </Panel>
      )}

      {tab === 'backups' && <BackupsPanel configDoc={configDoc} timeZone={timezone} />}

      {validateMutation.isSuccess && (
        <Alert tone="success">
          <AlertDescription>The configuration is valid.</AlertDescription>
        </Alert>
      )}
      {validateMutation.isError && (
        <Alert tone="danger" role="alert">
          <AlertTitle>This configuration is refused</AlertTitle>
          <AlertDescription>{validateMutation.error.message}</AlertDescription>
        </Alert>
      )}

      {!disabled && tab !== 'backups' && (
        <div className="sticky bottom-0 -mx-4 flex flex-wrap items-center justify-between gap-3 border-t border-border bg-background/95 px-4 py-3 backdrop-blur sm:-mx-6 sm:px-6">
          <div className="text-xs text-muted-foreground">
            <p>
              {changes.length === 0
                ? 'No unsaved change'
                : `${changes.length} unsaved change${changes.length === 1 ? '' : 's'}`}
            </p>
            {invalidCount > 0 && (
              <p role="alert" className="mt-0.5 font-medium text-negative">
                {invalidCount === 1 ? '1 field is not a number' : `${invalidCount} fields are not numbers`}: correct{' '}
                {invalidCount === 1 ? 'it' : 'them'} before checking or saving.
              </p>
            )}
          </div>
          <div className="flex flex-wrap gap-2">
            <Button
              variant="ghost"
              size="sm"
              disabled={!unsaved}
              onClick={() => {
                validateMutation.reset()
                replaceDraft(configDoc.config)
                if (tab === 'json') setJsonText(prettyJson(pickEditable(configDoc.config, configDoc.editable_paths)))
                setJsonError(null)
              }}
            >
              Discard
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={unusable}
              isLoading={validateMutation.isPending}
              onClick={() => validateMutation.mutate()}
            >
              Check
            </Button>
            <Button
              size="sm"
              disabled={changes.length === 0 || unusable}
              onClick={() => {
                saveMutation.reset()
                setReviewing(true)
              }}
            >
              Review and save
            </Button>
          </div>
        </div>
      )}

      <LeaveGuard
        dirty={unsaved && !disabled}
        summary={
          changes.length === 0
            ? 'What was typed in this form will be lost.'
            : `${changes.length} unsaved change${changes.length === 1 ? '' : 's'} to the configuration will be lost.`
        }
      />

      <ConfirmLiveAction
        open={reviewing}
        title="Save the configuration"
        confirmLabel="Slide to save"
        doneLabel="Saved"
        warning="Each service applies the file the next time it starts, including when it restarts by itself after a crash."
        pending={saveMutation.isPending}
        error={
          conflict
            ? 'The file changed on the server since this page loaded it. Close this dialog and reload the page: your changes will have to be made again.'
            : saveMutation.error?.message
        }
        onConfirm={() => saveMutation.mutate()}
        onClose={() => setReviewing(false)}
        description={
          <div className="space-y-3">
            <p>
              {changes.length} change{changes.length === 1 ? '' : 's'} to <code>config.json</code>. The current file is
              kept as a backup next to it, and the new one is checked with the services&apos; own validator before it is
              written.
            </p>
            <ChangeList changes={changes} />
          </div>
        }
      />
    </>
  )
}

export function ConfigPage() {
  const { restart_required: restartRequired } = useSession()
  const configQuery = useQuery({
    queryKey: ['config'],
    queryFn: api.config,
    // The editor holds a draft of this file: it must not change under it.
    refetchOnWindowFocus: false,
    staleTime: Infinity,
  })
  const configDoc = configQuery.data

  return (
    <>
      <PageHeader
        title="Configuration"
        description="The trading parameters of config.json. Saving does not restart anything."
      />
      {restartRequired && (
        <Alert tone="warning">
          <AlertTitle>Configuration changed since the services started</AlertTitle>
          <AlertDescription>
            Each service reads the configuration when it starts. Run <code>watastop</code> then <code>watastart</code>{' '}
            on the server to apply it everywhere.
          </AlertDescription>
        </Alert>
      )}
      <QueryState isPending={configQuery.isPending} error={configQuery.error} hasData={!!configDoc}>
        {configDoc && <ConfigEditor key={configDoc.revision} configDoc={configDoc} />}
      </QueryState>
    </>
  )
}

import { AlertTriangle, ChevronDown, Lock, RotateCcw } from 'lucide-react'
import { useId, useState } from 'react'
import { Link } from 'react-router'

import { Hint } from '@/components/Hint'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { StretchSwitch } from '@/components/ui/stretch-switch'
import type { CatalogParam, JsonObject, JsonValue } from '@/lib/types'
import { cn } from '@/lib/utils'

import {
  anchorOf,
  boundsSaid,
  configPathOf,
  differsFromDefault,
  hasRule,
  hrefOf,
  lookup,
  resetTo,
  shownValue,
  valueOf,
  withoutRule,
  withRuleOf,
} from './catalog'
import type { ConfigPath } from './configModel'
import { DEFAULT_TIMEZONE, MARKET_TIMEZONE, serviceNames, TIMEZONE, useConfigEditor } from './editor-context'
import {
  ChoicesField,
  NumberField,
  NumbersField,
  SelectField,
  SwitchField,
  TextField,
  TimeField,
  TimezoneField,
} from './fields'
import { DateListField, MapField, RowsField } from './listFields'
import type { RowColumn } from './listFields'
import { gapBetween, sameMoment, zoneLabel } from './zones'

const RULE_NAME = 'trade.rules[*].rule_name'
const START = 'trade.rules[market_hours].rule_config.trading_start_hour'
const END = 'trade.rules[market_hours].rule_config.trading_end_hour'
const RISKY_HOUR = 'trade.rules[market_hours].rule_config.risky_trading_start_hour'
const RISKY_MINUTE = 'trade.rules[market_hours].rule_config.risky_trading_start_minute'
const CLOSE_TIME = 'trade.rules[day_trading].rule_config.close_position_time'
const TA_RESTART = 'trade.config.ta_engine.restart_time'

/** The timezone the hours of the market are read in: their own, or the trading timezone. */
function marketZone(draft: JsonObject): string {
  const own = valueOf(draft, MARKET_TIMEZONE)
  const trading = valueOf(draft, TIMEZONE)
  return typeof own === 'string' && own ? own : typeof trading === 'string' && trading ? trading : DEFAULT_TIMEZONE
}

function clockOf(value: JsonValue | undefined): [number, number] | null {
  const match = typeof value === 'string' ? /^(\d{1,2}):(\d{2})$/.exec(value) : null
  return match ? [Number(match[1]), Number(match[2])] : null
}

/** The moment a parameter names, if it names one: an hour of the market, the close, the engine's restart. */
function momentOf(param: CatalogParam, draft: JsonObject): { hour: number; minute: number; zone: string } | null {
  const value = valueOf(draft, param.path)
  if (param.path === START || param.path === END) {
    return typeof value === 'number' ? { hour: value, minute: 0, zone: marketZone(draft) } : null
  }
  if (param.path === RISKY_HOUR || param.path === RISKY_MINUTE) {
    const hour = valueOf(draft, RISKY_HOUR)
    const minute = valueOf(draft, RISKY_MINUTE)
    return typeof hour === 'number' && typeof minute === 'number' ? { hour, minute, zone: marketZone(draft) } : null
  }
  if (param.path === CLOSE_TIME || param.path === TA_RESTART) {
    const clock = clockOf(value ?? (param.default as JsonValue | undefined))
    return clock ? { hour: clock[0], minute: clock[1], zone: param.path === TA_RESTART ? 'UTC' : marketZone(draft) } : null
  }
  return null
}

/** An hour of the configuration as it is today elsewhere, and whether the clocks are changed apart. */
export function MomentElsewhere({ hour, minute, zone }: { hour: number; minute: number; zone: string }) {
  const others = ['America/New_York', 'Europe/Paris', 'UTC'].filter((other) => other !== zone)
  const moments = sameMoment(hour, minute, zone, others)
  const paris = zone === 'Europe/Paris' ? null : gapBetween(zone, 'Europe/Paris')
  const clock = `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}`
  return (
    <p className="text-xs text-muted-foreground">
      Today {clock} in {zoneLabel(zone)} is{' '}
      {moments.map((moment, index) => (
        <span key={moment.zone}>
          {index > 0 && (index === moments.length - 1 ? ' and ' : ', ')}
          {moment.clock}
          {moment.dayShift > 0 ? ' the next day' : moment.dayShift < 0 ? ' the day before' : ''} in {zoneLabel(moment.zone)}
        </span>
      ))}
      .
      {paris && paris.usual !== paris.now && (
        <span className="text-warning">
          {' '}
          This week the clocks are changed apart: {zoneLabel(zone)} is {Math.abs(paris.now) / 60} h from Paris, usually{' '}
          {Math.abs(paris.usual) / 60} h.
        </span>
      )}
    </p>
  )
}

function columnsOf(param: CatalogParam): RowColumn[] {
  return param.columns.map((column) => ({
    key: column.key,
    label: column.unit ? `${column.label} (${column.unit})` : column.label,
    kind: column.kind === 'text' ? 'text' : 'number',
    width: column.kind === 'text' ? '2fr' : undefined,
  }))
}

function newRowOf(param: CatalogParam): JsonObject {
  return Object.fromEntries(param.columns.map((column) => [column.key, column.kind === 'text' ? '' : (column.minimum ?? 0)]))
}

/** The field of an editable parameter, by its kind. */
function ParamControl({ param, at }: { param: CatalogParam; at: ConfigPath | null }) {
  const label = param.label
  switch (param.kind) {
    case 'int':
    case 'number':
      return (
        <NumberField
          label={label}
          unit={param.unit ?? undefined}
          path={at}
          optional={param.optional}
          integer={param.kind === 'int'}
          minimum={param.minimum}
          maximum={param.maximum}
          placeholder={param.optional && param.default !== null ? `${String(param.default)} when empty` : param.optional ? 'Off when empty' : undefined}
        />
      )
    case 'bool':
      return <SwitchField label={label} path={at} whenAbsent={param.default === true} />
    case 'enum':
      return <SelectField label={label} path={at} choices={param.choices} />
    case 'time':
      return <TimeField label={label} path={at} optional={param.optional} placeholder={param.default ? String(param.default) : undefined} />
    case 'timezone':
      return <TimezoneField label={label} path={at} unsetLabel={param.optional && param.default === null ? 'The trading timezone' : undefined} />
    case 'dates':
      return at ? <DateListField label={label} path={at} /> : null
    case 'map':
      return at ? <MapField label={label} path={at} keyLabel={param.key_label ?? 'Name'} valueLabel={param.value_label ?? 'Value'} /> : null
    case 'rows':
      return at ? <RowsField label={label} path={at} columns={columnsOf(param)} newRow={() => newRowOf(param)} /> : null
    case 'strings':
      return param.choices.length > 0 ? <ChoicesField label={label} path={at} choices={param.choices} /> : null
    case 'numbers':
      return <NumbersField label={label} path={at} unit={param.unit ?? undefined} />
    case 'text':
      return <TextField label={label} path={at} placeholder={param.default ? String(param.default) : undefined} />
    default:
      return null
  }
}

/** What a parameter the page does not change holds, as the file has it (a secret masked). */
function ReadOnlyValue({ param, value }: { param: CatalogParam; value: JsonValue | undefined }) {
  return (
    <div className="space-y-1">
      <p className="text-xs text-text-secondary">
        {param.label}
        {param.unit && <span className="ml-1 text-muted-foreground">({param.unit})</span>}
      </p>
      <p className="break-all rounded-lg border border-border bg-surface-raised/40 px-3 py-2 font-mono text-xs">
        {value === undefined && param.optional && param.default !== null ? `${shownValue(param.default)} (not in the file)` : shownValue(value)}
      </p>
    </div>
  )
}

const ACCESS_SAID: Record<string, { label: string; tone: 'neutral' | 'warning' }> = {
  server: { label: 'Set on the server', tone: 'neutral' },
  credential: { label: 'Credential', tone: 'warning' },
  unused: { label: 'Read by nothing', tone: 'neutral' },
}

/** Everything the catalog says of a parameter: what it changes, with what, what to watch, and who reads it. */
function ParamDetails({ param, id }: { param: CatalogParam; id: string }) {
  const { catalog } = useConfigEditor()
  const names = serviceNames(catalog)
  const consumers = new Map(catalog.consumers.map((consumer) => [consumer.id, consumer]))
  const bounds = boundsSaid(param)
  return (
    <div id={id} className="mt-3 space-y-3 rounded-lg border border-border/70 bg-surface-raised/30 p-3 text-xs leading-relaxed">
      {param.effects.length > 0 && (
        <div>
          <p className="font-medium text-foreground">What it changes</p>
          <ul className="mt-1 list-disc space-y-1 pl-4 text-text-secondary">
            {param.effects.map((effect) => (
              <li key={effect}>{effect}</li>
            ))}
          </ul>
        </div>
      )}
      {param.readers.length > 0 && (
        <p className="text-text-secondary">
          <span className="font-medium text-foreground">Read at their start by</span>{' '}
          {param.readers.map((reader) => names.get(reader) ?? reader).join(', ')}: a change applies when each starts again.
        </p>
      )}
      {param.consumers.length > 0 && (
        <p className="text-text-secondary">
          <span className="font-medium text-foreground">Also read by</span>{' '}
          {param.consumers
            .map((id) => consumers.get(id))
            .filter((consumer) => consumer !== undefined)
            .map((consumer) => (consumer.live ? consumer.name : `${consumer.name} (as the admin read it at its start)`))
            .join('; ')}
          .
        </p>
      )}
      {param.related.length > 0 && (
        <div>
          <p className="font-medium text-foreground">Works with</p>
          <ul className="mt-1 flex flex-wrap gap-2">
            {param.related.map((path) => {
              const related = lookup(catalog, path)
              return related ? (
                <li key={path}>
                  <Link to={hrefOf(related)} className="rounded-md border border-border px-2 py-0.5 hover:bg-surface-hover focus-ring">
                    {related.label}
                  </Link>
                </li>
              ) : null
            })}
          </ul>
        </div>
      )}
      {param.caveats.length > 0 && (
        <ul className="space-y-1">
          {param.caveats.map((caveat) => (
            <li key={caveat} className="flex gap-1.5 text-text-secondary">
              <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-warning" aria-hidden />
              <span>{caveat}</span>
            </li>
          ))}
        </ul>
      )}
      {param.also_needs.length > 0 && (
        <p className="text-text-secondary">
          <span className="font-medium text-foreground">Also needs</span> {param.also_needs.join(' ')}
        </p>
      )}
      {(param.reason || param.how) && (
        <p className="text-text-secondary">
          {param.reason} {param.how && <span className="text-foreground">{param.how}</span>}
        </p>
      )}
      <p className="text-muted-foreground">
        <code className="break-all">{param.path}</code>
        {param.optional &&
          ` · May be absent: ${param.default === null ? 'off, or nothing, then' : `${shownValue(param.default)} then`}.`}
        {bounds && ` · ${bounds}.`}
      </p>
    </div>
  )
}

/** One parameter: its field (or its value, read-only), what is unsaved or not the default, and all that it changes. */
export function ParamRow({ param }: { param: CatalogParam }) {
  const { draft, doc, unsaved, replaceDraft, disabled, catalog } = useConfigEditor()
  const [open, setOpen] = useState(false)
  const detailsId = useId()
  const at = configPathOf(draft, param.path)
  const value = valueOf(draft, param.path)
  const editable = param.access === 'editable' && !disabled
  const differs = differsFromDefault(param, value)
  const moment = momentOf(param, draft)
  const names = serviceNames(catalog)
  const said = ACCESS_SAID[param.access]

  return (
    <div
      id={anchorOf(param.path)}
      className={cn(
        'scroll-mt-24 rounded-xl border border-border/70 p-4 transition-shadow',
        unsaved.has(param.path) && 'border-warning/50',
      )}
    >
      <div className="flex flex-wrap items-center gap-1.5 empty:hidden [&:not(:empty)]:mb-2">
        {unsaved.has(param.path) && <Badge tone="warning">Unsaved</Badge>}
        {differs && <Badge>Not the default</Badge>}
        {said && (
          <Badge tone={said.tone} variant="outline">
            {param.access !== 'unused' && <Lock className="mr-1 size-3" aria-hidden />}
            {said.label}
          </Badge>
        )}
      </div>
      {param.access === 'editable' ? <ParamControl param={param} at={at} /> : <ReadOnlyValue param={param} value={value} />}
      {at === null && param.access === 'editable' && (
        <p className="mt-1 text-xs text-muted-foreground">Its rule is not in the configuration.</p>
      )}
      {moment && <div className="mt-1.5"><MomentElsewhere {...moment} /></div>}
      <p className="mt-2 text-xs text-text-secondary">{param.help}</p>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        {param.readers.length > 0 && (
          <Hint content="Each reads the file when it starts: a saved change applies at its next start.">
            <span className="text-xs text-muted-foreground" tabIndex={0}>
              Read by {param.readers.map((reader) => names.get(reader) ?? reader).join(', ')}
              {param.risk === 'trading' && param.access === 'editable' && (
                <span className="text-warning"> · changes what is traded</span>
              )}
            </span>
          </Hint>
        )}
        <span className="flex-1" />
        {editable && differs && param.optional && (
          <Button
            variant="ghost"
            size="sm"
            leftIcon={<RotateCcw className="size-3.5" />}
            onClick={() => replaceDraft(resetTo(draft, param))}
          >
            Reset to the default
          </Button>
        )}
        <Button
          variant="ghost"
          size="sm"
          aria-expanded={open}
          aria-controls={detailsId}
          rightIcon={<ChevronDown className={cn('size-3.5 transition-transform', open && 'rotate-180')} />}
          onClick={() => setOpen((current) => !current)}
        >
          What it changes
        </Button>
      </div>
      {open && <ParamDetails param={param} id={detailsId} />}
      {/* What the file held when the page opened, for a parameter changed since. */}
      {unsaved.has(param.path) && (
        <p className="mt-2 text-xs text-muted-foreground">Saved value: {shownValue(valueOf(doc.config, param.path))}</p>
      )}
    </div>
  )
}

/** A rule of the trading rules: its name, an optional rule's switch, then its parameters. */
export function RuleBlock({ ruleType, params }: { ruleType: string; params: CatalogParam[] }) {
  const { draft, catalog, replaceDraft, disabled, unsaved } = useConfigEditor()
  const ruleParam = lookup(catalog, `trade.rules[${ruleType}]`)
  const present = hasRule(draft, ruleType)
  const nameParam = lookup(catalog, RULE_NAME)
  const at = configPathOf(draft, `trade.rules[${ruleType}].rule_name`)
  const keys = params.filter((param) => param.kind !== 'rule')

  return (
    <section className="space-y-3 rounded-2xl border border-border/60 bg-surface/30 p-4">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold">{ruleParam?.label ?? ruleType}</h3>
          <p className="text-xs text-muted-foreground">
            The rule <code>{ruleType}</code> of <code>trade.rules</code>.{ruleParam?.help ? ` ${ruleParam.help}` : ''}
          </p>
        </div>
        {ruleParam?.optional && (
          <StretchSwitch
            label={present ? 'On' : 'Off'}
            checked={present}
            disabled={disabled}
            onChange={(on) => replaceDraft(on ? withRuleOf(draft, ruleParam) : withoutRule(draft, ruleType))}
          />
        )}
        {ruleParam && unsaved.has(ruleParam.path) && <Badge tone="warning">Unsaved</Badge>}
      </header>
      {present ? (
        keys.map((param) => <ParamRow key={param.path} param={param} />)
      ) : ruleParam?.optional ? (
        <p className="text-xs text-muted-foreground">Off: none of its settings applies.</p>
      ) : (
        <p className="text-xs text-negative">
          This rule is missing from the file: the services refuse a configuration without it.
        </p>
      )}
      {present && at && nameParam && (
        <div className="max-w-xs">
          <TextField label="Name of the rule" path={at} hint={nameParam.help} />
        </div>
      )}
    </section>
  )
}


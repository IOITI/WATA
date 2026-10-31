// The AI agent's conversations as the pages show them (src/admin/agent/transcript.py projects them): each
// request's steps as activity (its notes between them), what it is doing while it works, its answer as a
// response, what it rests on, the requests offered to start from, the text streamed folded in as it comes, why
// nothing can be asked, the model and effort a request goes with, and what its context window holds. Pure.

import type { AgentActivityItemData, AIResponseSource, AIResponseStatus, ActivityType } from 'ioiti-ui'

import type {
  AgentAction,
  AgentChart,
  AgentChoice,
  AgentContext,
  AgentContextPart,
  AgentConversation,
  AgentConversationListed,
  AgentEffort,
  AgentModel,
  AgentPending,
  AgentProposal,
  AgentRequest,
  AgentRequestStatus,
  AgentState,
  AgentStatus,
  AgentStep,
  AgentSubject,
  AgentTask,
  AgentVerdict,
  AgentVerdictTone,
  BacktestHeadline,
} from '@/lib/types'

/** A conversation the agent is working on: a request queued, under way, or waiting for its runs. */
export const UNDER_WAY: readonly AgentStatus[] = ['queued', 'running', 'waiting']

export function isUnderWay(status: AgentStatus | AgentRequestStatus | null | undefined): boolean {
  return status === 'queued' || status === 'running' || status === 'waiting'
}

export const STATUS_LABEL: Record<AgentStatus, string> = {
  queued: 'Waiting its turn',
  running: 'Working',
  waiting: 'Waiting for its runs',
  idle: 'Answered',
  failed: 'Failed',
}

export const OUTCOME_LABEL: Record<AgentRequestStatus, string> = {
  queued: 'Waiting its turn',
  running: 'Working',
  waiting: 'Waiting for its runs',
  done: 'Answered',
  stopped: 'Stopped',
  failed: 'Failed',
  declined: 'Declined',
  limit: 'At its limits',
  cut: 'Cut short',
}

/**
 * How a request ended, under its answer: the loop's own sentence when it wrote one ("Stopped by the operator"),
 * which says the outcome already; a failure's or a refusal's is said in its alert instead.
 */
export function outcomeSaid(status: AgentRequestStatus, detail?: string | null): string {
  return detail && status !== 'failed' && status !== 'declined' ? detail : OUTCOME_LABEL[status]
}

/** A conversation's status as a badge's tone: under way, neutral (its words say it). */
export function statusTone(status: AgentStatus, outcome?: AgentRequestStatus | null): 'success' | 'warning' | 'danger' | 'neutral' {
  if (isUnderWay(status)) return 'neutral'
  if (status === 'failed' || outcome === 'failed' || outcome === 'declined') return 'danger'
  if (outcome === 'limit' || outcome === 'cut' || outcome === 'stopped') return 'warning'
  return 'success'
}

export function costText(usd: number | null | undefined): string {
  if (usd === null || usd === undefined) return '—'
  return usd > 0 && usd < 0.01 ? 'under $0.01' : `$${usd.toFixed(2)}`
}

/** Where a subject is shown, with the agent opened beside it on a conversation about it for the lists. */
export function subjectLink(subject: AgentSubject): string {
  switch (subject.kind) {
    case 'run':
      return `/backtests/${encodeURIComponent(subject.id)}`
    case 'script':
      return `/ta/indicators/${encodeURIComponent(subject.id)}`
    case 'position':
      return `/positions?find=${encodeURIComponent(subject.id)}`
    case 'signal':
      return `/signals?find=${encodeURIComponent(subject.id)}`
    case 'day':
      return `/report?day=${encodeURIComponent(subject.id)}`
    case 'briefing':
      return '/'
    case 'rule':
      return '/ta/signals'
    case 'change':
      return '/config'
    case 'draft':
      return '/ta/indicators'
    case 'luxalgo':
      return `/ta/luxalgo/indicators/${encodeURIComponent(subject.id)}`
    default:
      return '/system'
  }
}

/** How many things a listed conversation holds attached besides its subject. */
export function moreAttached(row: Pick<AgentConversationListed, 'attached' | 'subject'>): number {
  return Math.max(0, (row.attached ?? 0) - (row.subject ? 1 : 0))
}

export function subjectSaid(subject: AgentSubject): string {
  switch (subject.kind) {
    case 'run':
      return `Run ${subject.label}`
    case 'script':
      return `Script ${subject.label}${subject.version ? ` v${subject.version}` : ''}`
    case 'position':
      return `Position ${subject.label}`
    case 'signal':
      return `Signal: ${subject.label}`
    case 'day':
      return `The trading day ${subject.id}`
    case 'briefing':
      return `The day ahead, ${subject.id}`
    case 'rule':
      return `Signal rule ${subject.label}`
    case 'change':
    case 'draft':
      return `To review: ${subject.label}`
    case 'luxalgo':
      // Its label names LuxAlgo already: "LuxAlgo's Tri-star".
      return subject.label
    default:
      return `Incident: ${subject.label}`
  }
}

/** What the admin asked by itself, said on Activity beside its conversation. */
export const ASKED_BY: Record<string, string> = {
  debrief: 'Daily debrief', briefing: 'Morning briefing', triage: 'Triage', dossier: 'Go-live dossier',
}

/** Who asked a conversation, said on Activity: the admin by itself (a debrief, a routine...), or nothing. */
export function askedBy(by: string | null | undefined): string | null {
  if (!by) return null
  return by.startsWith('routine:') ? 'Routine' : ASKED_BY[by] ?? null
}

// ── a subject in the address: a list's row asked about (?agent=new&about=position:5001) ──

export const ABOUT_PARAM = 'about'

/** A subject as the address carries it: its kind and id, a signal's day or an incident's moment after an @. */
export function aboutParam(subject: Pick<AgentSubject, 'kind' | 'id' | 'day' | 'at'>): string {
  const extra = subject.kind === 'signal' ? subject.day : subject.kind === 'incident' ? subject.at : null
  return `${subject.kind}:${subject.id}${extra ? `@${extra}` : ''}`
}

const ABOUT_KINDS = ['position', 'signal', 'day', 'incident', 'rule', 'luxalgo'] as const

/** The subject the address names, or null; its label until the admin names it. */
export function subjectOf(about: string | null): AgentSubject | null {
  // A LuxAlgo indicator's slug runs to 121 characters.
  const match = about ? /^(position|signal|day|incident|rule|luxalgo):([^@]{1,121})(?:@(.{1,16}))?$/.exec(about) : null
  if (!match || !(ABOUT_KINDS as readonly string[]).includes(match[1])) return null
  const [, kind, id, extra] = match
  if (kind === 'signal') return extra ? { kind, id, day: extra, label: `the signal of ${extra}` } : null
  if (kind === 'incident') return extra ? { kind, id, at: extra, label: `${id.replace(/_/g, ' ')} at ${extra} UTC` } : null
  return { kind: kind as 'position' | 'day' | 'rule' | 'luxalgo', id, label: id }
}

/** The address that opens the agent on a new conversation about ``subject``, from the page it is on. */
export function askAbout(search: URLSearchParams, subject: Pick<AgentSubject, 'kind' | 'id' | 'day' | 'at'>): string {
  const next = new URLSearchParams(search)
  next.set(AGENT_PARAM, NEW)
  next.set(ABOUT_PARAM, aboutParam(subject))
  return `?${next.toString()}`
}

// ── a tool's step ──

type Input = Record<string, unknown>

const short = (value: unknown) => (typeof value === 'string' ? value : '?')

function period(input: Input): string {
  const start = typeof input.start_day === 'string' ? input.start_day : null
  const end = typeof input.end_day === 'string' ? input.end_day : null
  return start && end && end !== start ? `${start} to ${end}` : start ?? 'the days asked'
}

function runs(ids: unknown): string {
  if (!Array.isArray(ids) || ids.length === 0) return 'its runs'
  return ids.length === 1 ? `run ${short(ids[0])}` : `${ids.length} runs`
}

function entries(names: unknown): string {
  if (!Array.isArray(names) || names.length === 0) return 'entries'
  return names.length <= 2 ? names.map(short).join(' and ') : `${names.length} entries`
}

function changes(input: Input): string {
  const hours = typeof input.hours === 'number' ? `${input.hours} h` : null
  if (typeof input.until === 'string') return `Read what changed in the ${hours ?? 'hours'} before ${input.until}`
  return hours ? `Read what changed in the last ${hours}` : 'Read what changed lately'
}

const DERIVED: Record<string, string> = {
  stress: 'Stress-tested',
  model_check: 'Checked the turbo model on',
  neighbours: 'Tried the neighbours of',
  cell: 'Made a run of a cell of',
}

// Each tool said from what it was given: what the operator reads in the activity.
const TITLES: Record<string, (input: Input) => string> = {
  status: () => "Read the research side's status",
  data_assets: () => 'Listed the assets recorded',
  data_coverage: () => 'Read which days are recorded',
  data_signals: (input) => `Read the signals of ${period(input)}`,
  data_trades: (input) => `Read WATA's trades of ${period(input)}`,
  data_execution: (input) => `Read how WATA's orders were filled, ${period(input)}`,
  pine_list: () => 'Listed the Pine scripts',
  pine_get: (input) => `Read script ${short(input.script_id)}${input.version ? ` v${String(input.version)}` : ''}`,
  pine_check: () => 'Checked a script',
  pine_run: (input) => `Ran a script on ${period(input)}`,
  pine_signals: (input) => `Replayed a script's signals, ${period(input)}`,
  pine_compare: (input) => `Compared a script's signals with TradingView's, ${period(input)}`,
  pine_save: (input) => (typeof input.script_id === 'string' && input.script_id
    ? `Saved a new version of its script ${input.script_id}` : `Saved the script "${short(input.name)}"`),
  backtest_options: () => "Read the backtests' settings",
  backtest_list: () => 'Listed the backtests',
  backtest_result: (input) => `Read run ${short(input.run_id)}${input.part && input.part !== 'summary' ? ` (${String(input.part)})` : ''}`,
  backtest_trades: (input) => `Read the trades of run ${short(input.run_id)}`,
  backtest_days: (input) => `Read the days of run ${short(input.run_id)}`,
  backtest_signals: (input) => `Read the signals of run ${short(input.run_id)}`,
  backtest_compare: (input) => `Compared ${Array.isArray(input.run_ids) ? input.run_ids.length : 'some'} runs`,
  backtest_start: (input) => {
    const request = (input.request ?? {}) as Input
    return `Started a backtest${typeof request.name === 'string' && request.name ? `: ${request.name}` : ''}`
  },
  backtest_derive: (input) => `${DERIVED[String(input.kind)] ?? 'Started a run from'} run ${short(input.run_id)}`,
  backtest_wait: (input) => `Waited for ${runs(input.run_ids)}`,
  turbo_model: () => 'Read the turbo model',
  rule_list: () => 'Listed the signal rules',
  rule_compare: (input) => `Compared rule ${short(input.rule_id)} with TradingView`,
  exp_new: (input) => `Opened the experiment "${short(input.title)}"`,
  exp_get: (input) => `Read experiment ${short(input.experiment_id)}`,
  exp_conclude: (input) => `Concluded the experiment: ${short(input.verdict)}`,
  propose_script_change: (input) => `Proposed a new version: ${short(input.title)}`,
  propose_rule_change: (input) => `Proposed a change to a rule: ${short(input.title)}`,
  propose_settings_change: (input) => `Proposed new settings: ${short(input.title)}`,
  propose_new_script: (input) => `Proposed a new script: ${short(input.name)}`,
  data_position: (input) => `Read position ${short(input.position_id)}`,
  data_orders: (input) => `Read the orders sent to Saxo, ${period(input)}`,
  ops_health: () => "Read the admin's health",
  ops_logs: (input) => `Read the logs of ${short(input.service)} around ${short(input.around)}`,
  ops_trading_now: () => 'Read trading as the dashboard shows it',
  ops_changes: changes,
  ops_engine: () => "Read the TA engine's last minutes",
  ops_release: () => 'Read what waits to be deployed',
  show_chart: (input) => `Drew a chart: ${short(input.title)}`,
  set_verdict: (input) => `Said its verdict on ${typeof input.about === 'string' ? verdictSubject(input.about) : '?'}`,
  propose_action: (input) => `Proposed an action: ${short(input.title)}`,
  propose_new_rule: (input) => `Proposed a new rule: ${short(input.name)}`,
  ask_operator: (input) => `Asked you: ${short(input.question)}`,
  rule_shadow_save: (input) => (typeof input.rule_id === 'string' && input.rule_id
    ? `Changed its shadow rule ${input.rule_id}` : `Saved the shadow rule "${short(input.name)}"`),
  rule_shadow_remove: (input) => `Removed its shadow rule ${short(input.rule_id)}`,
  rule_set_reference: (input) => `Set run ${short(input.run_id)} as its rule's reference`,
  task_done: () => 'Said the task is done',
  memory_list: () => "Read its memory's list",
  memory_read: (input) => `Read ${entries(input.names)} from its memory`,
  memory_save: (input) => `Saved ${short(input.name)} to its memory`,
  memory_forget: (input) => `Forgot ${short(input.name)} from its memory`,
  luxalgo_tools: (input) =>
    typeof input.tool === 'string' && input.tool ? `Read how LuxAlgo's ${input.tool} is called` : "Listed LuxAlgo's tools",
  luxalgo_call: (input) => `Asked LuxAlgo: ${luxalgoAsked(input)}`,
  luxalgo_save_script: (input) => `Took LuxAlgo's ${short(input.slug)} as its script`,
  luxalgo_known: (input) => `Read what WATA knows of ${Array.isArray(input.slugs) && input.slugs.length === 1
    ? `LuxAlgo's ${short(input.slugs[0])}` : `${Array.isArray(input.slugs) ? input.slugs.length : 'some'} LuxAlgo indicators`}`,
}

// A call of LuxAlgo's tools, said from its tool and its main argument.
function luxalgoAsked(input: Input): string {
  const tool = typeof input.tool === 'string' ? input.tool : '?'
  const given = (input.arguments && typeof input.arguments === 'object' ? input.arguments : {}) as Input
  const about = [given.query, given.slug, given.dataset, given.ticker, given.key].find((value) => typeof value === 'string' && value)
  return about ? `${tool} (${short(about)})` : tool
}

const TYPES: Record<string, ActivityType> = {
  backtest_start: 'tool_execution',
  backtest_derive: 'tool_execution',
  backtest_wait: 'tool_execution',
  pine_check: 'code_execution',
  pine_run: 'code_execution',
  pine_signals: 'code_execution',
  pine_compare: 'code_execution',
  pine_save: 'writing',
  exp_new: 'writing',
  exp_conclude: 'writing',
  propose_script_change: 'writing',
  propose_rule_change: 'writing',
  propose_settings_change: 'writing',
  propose_new_script: 'writing',
  propose_new_rule: 'writing',
  propose_action: 'writing',
  show_chart: 'writing',
  set_verdict: 'writing',
  ask_operator: 'writing',
  rule_shadow_save: 'writing',
  rule_shadow_remove: 'writing',
  rule_set_reference: 'writing',
  task_done: 'writing',
  memory_save: 'writing',
  memory_forget: 'writing',
  luxalgo_save_script: 'writing',
  data_position: 'database_query',
  data_orders: 'database_query',
  data_signals: 'database_query',
  data_trades: 'database_query',
  data_execution: 'database_query',
  data_coverage: 'database_query',
}

// Each tool while the agent writes its call: what it is given is not known yet.
const WRITING: Record<string, string> = {
  status: "Reading the research side's status",
  data_assets: 'Listing the assets recorded',
  data_coverage: 'Reading which days are recorded',
  data_signals: 'Reading signals',
  data_trades: "Reading WATA's trades",
  data_execution: "Reading how WATA's orders were filled",
  pine_list: 'Listing the Pine scripts',
  pine_get: 'Reading a script',
  pine_check: 'Checking a script',
  pine_run: 'Running a script',
  pine_signals: "Replaying a script's signals",
  pine_compare: "Comparing a script's signals with TradingView's",
  pine_save: 'Saving a script',
  backtest_options: "Reading the backtests' settings",
  backtest_list: 'Listing the backtests',
  backtest_result: 'Reading a run',
  backtest_trades: "Reading a run's trades",
  backtest_days: "Reading a run's days",
  backtest_signals: "Reading a run's signals",
  backtest_compare: 'Comparing runs',
  backtest_start: 'Starting a backtest',
  backtest_derive: 'Starting a run from a run',
  backtest_wait: 'Waiting for runs',
  turbo_model: 'Reading the turbo model',
  rule_list: 'Listing the signal rules',
  rule_compare: 'Comparing a rule with TradingView',
  exp_new: 'Opening an experiment',
  exp_get: 'Reading an experiment',
  exp_conclude: 'Concluding an experiment',
  propose_script_change: 'Proposing a new version',
  propose_rule_change: 'Proposing a change to a rule',
  propose_settings_change: 'Proposing new settings',
  propose_new_script: 'Proposing a new script',
  data_position: 'Reading a position',
  data_orders: 'Reading the orders sent to Saxo',
  ops_health: "Reading the admin's health",
  ops_logs: 'Reading the logs',
  ops_trading_now: 'Reading trading now',
  ops_changes: 'Reading what changed',
  ops_engine: "Reading the TA engine's last minutes",
  ops_release: 'Reading what waits to be deployed',
  show_chart: 'Drawing a chart',
  set_verdict: 'Saying its verdict',
  propose_action: 'Proposing an action',
  propose_new_rule: 'Proposing a new rule',
  ask_operator: 'Asking you a question',
  rule_shadow_save: 'Saving a shadow rule',
  rule_shadow_remove: 'Removing a shadow rule',
  rule_set_reference: "Setting a rule's reference",
  task_done: 'Ending the task',
  memory_list: "Reading its memory's list",
  memory_read: 'Reading its memory',
  memory_save: 'Saving to its memory',
  memory_forget: 'Forgetting an entry of its memory',
  luxalgo_tools: "Reading LuxAlgo's tools",
  luxalgo_call: 'Asking LuxAlgo',
  luxalgo_save_script: "Taking one of LuxAlgo's indicators",
  luxalgo_known: "Reading what WATA knows of LuxAlgo's indicators",
}

export function stepTitle(tool: string, input: unknown): string {
  const titled = TITLES[tool]
  return titled ? titled((input && typeof input === 'object' ? input : {}) as Input) : tool
}

export function writingTitle(tool: string | null | undefined): string {
  return (tool && WRITING[tool]) ?? `Writing a call to ${tool ?? 'a tool'}`
}

/** A step under way, in its present tense: what it was given, where that says more. */
function doingTitle(tool: string, input: unknown): string {
  if (tool === 'backtest_wait') return `Waiting for ${runs(((input && typeof input === 'object' ? input : {}) as Input).run_ids)}`
  return writingTitle(tool)
}

function seconds(from?: string | null, to?: string | null): number | null {
  if (!from || !to) return null
  const span = (Date.parse(to) - Date.parse(from)) / 1000
  return Number.isFinite(span) && span >= 0 ? span : null
}

// A step quicker than this says no time: every read would say "0.0 s".
const SHOWN_FROM_SECONDS = 0.1

function stepSpan(step: AgentStep): number | null {
  const span = seconds(step.started_at, step.ended_at)
  return span !== null && span >= SHOWN_FROM_SECONDS ? span : null
}

export function durationText(span: number | null): string | undefined {
  if (span === null) return undefined
  if (span < 60) return `${span < 10 ? span.toFixed(1) : Math.round(span)} s`
  const minutes = Math.round(span / 60)
  return minutes < 60 ? `${minutes} min` : `${Math.floor(minutes / 60)} h ${minutes % 60} min`
}

function output(text: string | null | undefined): string | Record<string, unknown> | undefined {
  if (!text) return undefined
  try {
    const parsed: unknown = JSON.parse(text)
    return parsed && typeof parsed === 'object' ? (parsed as Record<string, unknown>) : text
  } catch {
    return text
  }
}

const STEP_STATUS: Record<NonNullable<AgentStep['state']>, AgentActivityItemData['status']> = {
  running: 'running',
  done: 'success',
  error: 'error',
  cancelled: 'cancelled',
}

/**
 * A request's steps as the activity shows them, the step being written (pending) last. Its notes are notes: its
 * words between two stretches of its steps, never steps of the timeline.
 */
export function activityItems(
  request: AgentRequest,
  pending: AgentPending | null,
  time: (iso: string) => string,
): AgentActivityItemData[] {
  const items: AgentActivityItemData[] = request.steps.map((step) => {
    if (step.kind === 'note') {
      return { id: step.id, kind: 'note', type: 'thinking', title: step.text ?? '', status: 'success',
        timestamp: step.at ? time(step.at) : undefined }
    }
    const state = step.state ?? 'running'
    const waiting = state === 'running' && step.tool === 'backtest_wait'
    const firstLine = state === 'error' && step.output ? step.output.split('\n')[0].slice(0, 200) : undefined
    return {
      id: step.id,
      type: TYPES[step.tool ?? ''] ?? 'reading',
      title: stepTitle(step.tool ?? '', step.input),
      description: step.progress ?? firstLine,
      status: STEP_STATUS[state],
      statusLabel: waiting ? 'Waiting' : state === 'running' ? 'Running' : state === 'done' ? 'Done'
        : state === 'error' ? 'Refused' : 'Stopped',
      duration: durationText(stepSpan(step)),
      timestamp: step.started_at ? time(step.started_at) : undefined,
      details: { input: (step.input ?? undefined) as Record<string, unknown> | undefined, output: output(step.output) },
    }
  })
  if (pending && pending.request === request.index) {
    const note = joined(pending.note)
    if (note) items.push({ id: 'pending-note', kind: 'note', type: 'thinking', title: note, status: 'running' })
    if (pending.writing) {
      items.push({ id: 'pending-writing', type: 'writing', title: writingTitle(pending.writing.tool),
        description: pending.writing.chars > 0 ? `${pending.writing.chars.toLocaleString()} characters so far` : undefined,
        status: 'running', statusLabel: 'Writing' })
    }
  }
  return items
}

/** What a request did, in a line (its activity folded): its tool calls, its notes, and the time it took once ended. */
export function activitySummary(request: AgentRequest): string {
  const count = (kind: AgentStep['kind']) => request.steps.filter((step) => step.kind === kind).length
  const tools = count('tool')
  const notes = count('note')
  const took = isUnderWay(request.status) ? undefined : durationText(seconds(request.started_at, request.ended_at))
  return [`${tools} tool call${tools === 1 ? '' : 's'}`, notes > 0 ? `${notes} note${notes === 1 ? '' : 's'}` : null, took]
    .filter(Boolean).join(' · ')
}

/** The blocks of a text streamed, in their order. */
export function joined(blocks: Record<string, string> | undefined): string {
  return Object.keys(blocks ?? {}).sort((a, b) => Number(a) - Number(b)).map((key) => blocks![key]).join('').trim()
}

// ── what it is doing ──

/** What the agent is doing on a request under way, said where its answer will be until it writes it. */
export interface Working {
  /** What it does now. */
  label: string
  /** What it is about, said in turn under it: the step's progress, its latest thought, how much it did. */
  lines: string[]
  /** When it began: the time it has taken. */
  since?: string
}

const THOUGHT_SENTENCES = 3
const LINE_CHARS = 160

/** The last sentences of a text, each cut to a line: a sentence ends where a stop is followed by a space. */
export function lastSentences(text: string, count = THOUGHT_SENTENCES): string[] {
  return text.replace(/\s+/g, ' ').trim().split(/(?<=[.!?])\s+/).filter(Boolean).slice(-count)
    .map((sentence) => (sentence.length > LINE_CHARS ? `${sentence.slice(0, LINE_CHARS - 1).trimEnd()}…` : sentence))
}

/**
 * What a request under way is doing, from its own state: the call being written, else the step running, else
 * thinking; under it the step's progress (a wait's runs one by one) or the call's length so far, the last
 * sentences of its latest thought (the note streamed, else the last one), and what it did so far. Before any of
 * that, the first things a request does. None once it ended.
 */
export function workingOf(request: AgentRequest, pending: AgentPending | null): Working | null {
  if (!isUnderWay(request.status)) return null
  if (request.status === 'queued') return { label: OUTCOME_LABEL.queued, lines: [], since: request.asked_at ?? undefined }
  const mine = pending && pending.request === request.index ? pending : null
  const tools = request.steps.filter((step) => step.kind === 'tool')
  const running = tools.findLast((step) => step.state === 'running')
  let label = 'Thinking'
  let doing: string[] = []
  if (mine?.writing) {
    label = writingTitle(mine.writing.tool)
    if (mine.writing.chars > 0) doing = [`${mine.writing.chars.toLocaleString()} characters of its call written`]
  } else if (running) {
    label = doingTitle(running.tool ?? '', running.input)
    doing = (running.progress ?? '').split('; ').map((part) => part.trim()).filter(Boolean)
  }
  const thought = joined(mine?.note) || (request.steps.findLast((step) => step.kind === 'note')?.text ?? '')
  const started = request.runs.length
  const tally = tools.length > 0
    ? [`${tools.length} tool call${tools.length === 1 ? '' : 's'} so far`, started ? `${started} run${started === 1 ? '' : 's'} started` : null]
      .filter(Boolean).join(' · ')
    : null
  const lines = [...doing, ...lastSentences(thought), ...(tally ? [tally] : [])]
  return {
    label,
    lines: lines.length > 0 ? lines
      : [request.shared || request.attached?.length ? 'Reading the request and what was shared' : 'Reading the request',
          'Planning its first steps'],
    since: request.started_at ?? request.asked_at ?? undefined,
  }
}

/** A request's answer as the response shows it: its text, streamed while it comes, or how it ended. */
export function answerOf(request: AgentRequest, pending: AgentPending | null): { content: string; status: AIResponseStatus } {
  const streamed = pending && pending.request === request.index ? joined(pending.text) : ''
  switch (request.status) {
    case 'queued':
    case 'running':
    case 'waiting':
      return { content: streamed, status: streamed ? 'streaming' : 'generating' }
    case 'failed':
    case 'declined':
      return { content: request.detail ?? '', status: 'error' }
    case 'stopped':
      return { content: request.answer?.text || request.detail || '', status: 'cancelled' }
    case 'limit':
    case 'cut':
      if (!request.answer?.text) return { content: request.detail ?? '', status: 'cancelled' }
      return { content: request.answer.text, status: 'complete' }
    default:
      return { content: request.answer?.text ?? '', status: 'complete' }
  }
}

/**
 * What an answer rests on, as its sources: what was shared, the runs it started, what it proposed. A file attached
 * is named without a link: the sources lead through the router, and a file is not a page (its chip leads to it).
 */
export function sourcesOf(request: AgentRequest, proposals: AgentProposal[]): AIResponseSource[] {
  const sources: AIResponseSource[] = []
  for (const entry of request.attached ?? (request.shared ? [request.shared] : [])) {
    sources.push(entry.kind === 'file'
      ? { id: `file-${entry.id}`, title: entry.name, snippet: 'Attached to the request' }
      : { id: `shared-${entry.kind}-${entry.id}`, title: subjectSaid(entry), url: subjectLink(entry), snippet: 'Shared with the request' })
  }
  for (const run of request.runs) {
    sources.push({ id: `run-${run}`, title: `Run ${run}`, url: `/backtests/${encodeURIComponent(run)}`,
      snippet: 'Started by the agent' })
  }
  for (const proposal of proposals.filter((entry) => request.proposals.includes(entry.id))) {
    sources.push({ id: proposal.id, title: proposal.title, url: `#proposal-${proposal.id}`,
      snippet: PROPOSAL_SAID[proposal.kind] ?? 'A proposal' })
  }
  return sources
}

/** What each kind of proposal is, under the answer that made it. */
export const PROPOSAL_SAID: Record<AgentProposal['kind'], string> = {
  script: 'A new version of a script, proposed',
  rule: 'A change to a rule, proposed',
  settings: 'A change of settings, proposed',
  new_script: 'A new script, proposed',
  new_rule: 'A new signal rule, proposed',
  action: 'An action on live trading, proposed',
}

// ── an action on live trading ──

/** An action in words, as its card names it. */
export function actionSaid(action: AgentAction): string {
  if (action.kind === 'pause_entries') return 'Pause new entries'
  if (action.kind === 'resume_entries') return 'Resume new entries'
  if (action.kind === 'close_position') {
    return `Close position ${action.position_id}${action.instrument_name ? ` (${action.instrument_name})` : ''}`
  }
  if (action.kind === 'restart') {
    const names = (action.services ?? []).map((service) => service.name.toLowerCase())
    return `Restart the ${names.length > 1 ? `${names.slice(0, -1).join(', ')} and ${names.at(-1)}` : names[0] ?? 'services'}`
  }
  return action.kind === 'rule_live' ? `Take ${action.rule?.name} live` : `Take ${action.rule?.name} out of live`
}

/** Where the action is done by hand: the page that holds its own confirmation. */
export function actionPage(action: AgentAction): { to: string; label: string } {
  if (action.kind === 'pause_entries' || action.kind === 'resume_entries') return { to: '/', label: 'the dashboard' }
  if (action.kind === 'close_position') return { to: `/positions?find=${encodeURIComponent(action.position_id ?? '')}`, label: 'the position' }
  if (action.kind === 'restart') return { to: '/system', label: 'System' }
  return { to: '/ta/signals', label: 'Signal rules' }
}

/** Whether an action proposed may still be applied from its card: the situation may have changed since. */
export function actionFresh(proposal: Pick<AgentProposal, 'fresh_until'>, now: number): boolean {
  return !!proposal.fresh_until && now <= Date.parse(proposal.fresh_until)
}

/** The tools a request used, for the response's summary. */
export function toolsOf(request: AgentRequest): { name: string; status: 'success' | 'running' | 'error'; duration?: string }[] {
  return request.steps
    .filter((step) => step.kind === 'tool')
    .map((step) => ({
      name: step.tool ?? '?',
      status: step.state === 'error' ? 'error' : step.state === 'running' ? 'running' : 'success',
      duration: durationText(stepSpan(step)),
    }))
}

// ── what is offered and refused ──

export interface Preset {
  label: string
  prompt: string
}

// What is offered while the agent has LuxAlgo's tools (its state's `luxalgo`).
const SIMILAR_IN_LUXALGO: Preset = {
  label: 'Find similar in LuxAlgo',
  prompt: "Find LuxAlgo indicators like this one: name the concepts it uses, search LuxAlgo's Library and concepts, read what WATA already knows of the closest (luxalgo_known), compare them with it, test the most promising within your limits, and say what you found.",
}
const FUNDED_CHALLENGE: Preset = {
  label: 'Would it pass a funded challenge?',
  prompt: "Would this run's strategy pass a funded-account challenge? Read its trades as R-multiples (backtest_result, part r_series) and send them with luxalgo_call propfirms_validate_strategy: futures, a risk of 0.5 % of the balance per trade, the bar of a pass per attempt of one in two. Say which challenges it passes, what that rests on and LuxAlgo's flags: a screen of distributions, not a ranking.",
}
const LUXALGO_PRESETS: Preset[] = [
  { label: 'Worth testing on WATA?', prompt: "Is this indicator worth testing on WATA? Read it and what WATA already knows of it, take it whole if LuxAlgo gives its source whole, count its signals on the last five recorded days with pine_signals (adding alertcondition() lines in a version of your own if it has none), and say whether it deserves a backtest and why. Start no backtest." },
  { label: 'Test it', prompt: "Test this indicator on WATA: take it whole, add WATA alerts if it has none, open an experiment, backtest it within your limits on the recorded turbos and judge it by the bar. Set your verdict on the indicator, and propose it as a new script of mine if it holds." },
  { label: 'Make it a signal rule', prompt: "Make this indicator a signal rule of mine: take it whole, map its alerts to WATA's actions (adding alertcondition() lines in a version of your own if it needs them), check its signals on recorded days, and propose a new rule in shadow. Never live." },
]

/** Requests to start from, by what the conversation is about; ``luxalgo``: the agent has LuxAlgo's tools. */
export function presetsFor(kind: AgentSubject['kind'] | null, options: { luxalgo?: boolean } = {}): Preset[] {
  const luxalgo = !!options.luxalgo
  if (kind === 'luxalgo') return LUXALGO_PRESETS
  if (kind === 'run') {
    return [
      { label: 'Analyse it', prompt: 'Can this run be believed? Read its verdict checks, the edge of its entries, random entries, stress, regimes and trades. Say what it rests on and what to try next.' },
      { label: 'Find better settings', prompt: 'Look for settings that could do better than this run. Test the most promising with backtests within your limits, on the same days, compare them with this run, and say which holds and why.' },
      { label: 'Stress it', prompt: 'Stress this run and try its neighbours: does its result survive worse fills and settings near its own?' },
      { label: 'Improve its script', prompt: "Read this run's script and look for a change that could do better: save it as your own script, backtest it against this run on the same days, and propose it if it holds." },
      ...(luxalgo ? [FUNDED_CHALLENGE] : []),
    ]
  }
  if (kind === 'script') {
    return [
      { label: 'Review it', prompt: "Review this Pine script for bugs, repainting and WATA's rules for scripts. Propose the fixes as a new version." },
      { label: 'Explain it', prompt: 'Explain what this script does: its inputs, which alerts it raises and when.' },
      { label: 'Improve it', prompt: 'Suggest improvements to this script, backtest the most promising against the current version on the recorded days, and propose it if it holds.' },
      ...(luxalgo ? [SIMILAR_IN_LUXALGO] : []),
    ]
  }
  if (kind === 'position') {
    return [
      { label: 'Why did it end so?', prompt: 'Why did this position end the way it did? Go from the signal to the sale: the decision, the turbo chosen, the fills against the quotes, what sold it.' },
      { label: 'Was the fill fair?', prompt: "Were this position's fills fair? Compare the prices paid with the turbo's recorded quotes when each order left, and the delays with WATA's usual ones." },
      { label: 'What did Saxo refuse?', prompt: 'Did Saxo refuse any order of this position? Say what it answered, how long it lasted and what it cost.' },
    ]
  }
  if (kind === 'signal') {
    return [
      { label: 'Why this outcome?', prompt: 'Why did the trader decide as it did on this signal? Say which rule took or refused it, and what came of it.' },
      { label: 'Was it worth it?', prompt: 'Was this signal worth taking? Look at how the index moved after it, and at the position it opened if any.' },
    ]
  }
  if (kind === 'day') {
    return [
      { label: 'How did it go?', prompt: 'How did this trading day go? Its trades and their results, the signals refused and why, what Saxo refused, and what stands out.' },
      { label: 'What went wrong?', prompt: "What went wrong this day, if anything: losses, refusals, delays, fills far from the quotes? Say what each rests on." },
    ]
  }
  if (kind === 'incident') {
    return [
      { label: 'What went wrong?', prompt: 'What went wrong here? Read the health and the log lines around the moment, say what happened and what I could do. Act on nothing.' },
      { label: 'Is it still going on?', prompt: "Is this still going on? Compare the health now with the log lines around the moment, and say whether trading is affected." },
    ]
  }
  if (kind === 'rule') {
    return [
      { label: 'Is it ready to go live?', prompt: "Write this rule's go-live dossier: its paper days against its reference, its report against the bar, its reference run under worse fills, its signals beside TradingView's, the engine running it and its readiness. Set your verdict on the rule." },
      { label: 'Why did a paper day leave its range?', prompt: "Why did this rule's latest paper day outside its reference's range go so? Read its trades and signals that day, and say whether it is the rule, the market or the fills." },
      { label: 'Improve it', prompt: "Look for a change of this rule's script or inputs that could do better: test it on the recorded days against the rule as it runs, and propose it if it holds." },
      ...(luxalgo ? [SIMILAR_IN_LUXALGO] : []),
    ]
  }
  return [
    { label: 'What to try next', prompt: "Look at WATA's recent backtests and signal rules and say what is most worth trying next, and why." },
  ]
}

/** Why nothing can be asked of the agent now, or null. */
export function askRefusal(state: AgentState | undefined): string | null {
  if (!state) return 'Reading the agent’s state…'
  if (!state.enabled) return 'The AI agent is off: switch it on in Configuration › AI agent, then restart the admin.'
  if (state.broken) return state.broken
  if (state.key === false) return 'No Claude API key is set: set it in Configuration › AI agent.'
  if (state.pause) return 'The agent is paused: resume it from its Activity page.'
  if (state.month && state.month.spent_usd >= state.month.budget_usd) {
    return `This month's budget of ${costText(state.month.budget_usd)} is spent: raise it in Configuration › AI agent.`
  }
  return null
}

/** What "Tell me on Telegram" does, or why it cannot be asked. */
export function tellSaid(state: AgentState | undefined, tell: boolean, task = false): string {
  if (state?.telegram === false) return 'No Telegram bot is set: set it in Configuration › Credentials to be told there.'
  const what = task ? 'its conclusion, once the task ends' : 'the first lines of the answer, once it is written'
  return tell ? `Telegram says ${what}.` : `Say on Telegram ${what}.`
}

/** What a request may use, said under the field. */
export function limitsSaid(state: AgentState | undefined): string | undefined {
  if (!state?.limits) return undefined
  const spent = state.month ? ` · ${costText(state.month.spent_usd)} of ${costText(state.month.budget_usd)} this month` : ''
  return `up to ${costText(state.limits.request_usd)} a request${spent}`
}

// ── the model and the effort of a request ──

export const EFFORTS: readonly AgentEffort[] = ['low', 'medium', 'high', 'xhigh', 'max']

/** What each effort asks of the model: how much it thinks, and how many tools it calls, before it answers. */
export const EFFORT_SAID: Record<AgentEffort, string> = {
  low: 'Quickest and cheapest: little thinking, few tool calls',
  medium: 'For a plain question: some thinking',
  high: 'Thorough: what most analysis needs',
  xhigh: 'Longer searches: more thinking, more tool calls',
  max: 'As much as it takes: the slowest and dearest',
}

/** A model's name without the family's: "Opus 5.5", short enough for a phone's composer. */
export function shortModel(label: string): string {
  return label.replace(/^Claude /, '')
}

/** The model and effort a request goes with: the conversation's last, else the settings'. */
export function choiceOf(state: AgentState | undefined, conversation?: AgentConversation | null): AgentChoice | null {
  const model = conversation?.model ?? state?.model
  const effort = conversation?.effort ?? (state?.effort as AgentEffort | undefined)
  return model && effort ? { model, effort } : null
}

/** The models whose notes a conversation holds: those its requests ran on. */
export function writersOf(conversation: AgentConversation | null | undefined): Set<string> {
  return new Set((conversation?.requests ?? []).map((request) => request.model).filter((model): model is string => !!model))
}

/** Of those models, the ones whose notes ``model`` cannot read, by name. */
export function unreadBy(model: AgentModel | undefined, writers: ReadonlySet<string>, models: AgentModel[]): string[] {
  if (!model) return []
  return [...writers].filter((writer) => !model.reads.includes(writer))
    .map((writer) => models.find((candidate) => candidate.id === writer)?.label ?? writer)
}

/** A model as its menu says it: its prices and window, or why it cannot be chosen. */
export function modelSaid(model: AgentModel): string {
  if (model.available === false) return 'Not with this Claude API key'
  const price = (dollars: number) => `$${dollars < 1 ? dollars.toFixed(2) : dollars.toFixed(dollars % 1 ? 1 : 0)}`
  return `${price(model.prices.input)} / ${price(model.prices.output)} per million tokens · ${tokensText(model.window)} context`
}

// ── the context window ──

/** A count of tokens, short: 512, 84.2k, 1M. */
export function tokensText(tokens: number): string {
  if (tokens >= 1_000_000) return `${+(tokens / 1_000_000).toFixed(tokens % 1_000_000 ? 1 : 0)}M`
  if (tokens >= 1_000) return `${+(tokens / 1_000).toFixed(tokens >= 100_000 ? 0 : 1)}k`
  return String(tokens)
}

/** How full a window is, as a meter's tone: past three quarters it is worth watching, past nine tenths near its end. */
export function contextTone(ratio: number): 'neutral' | 'warning' | 'negative' {
  return ratio >= 0.9 ? 'negative' : ratio >= 0.75 ? 'warning' : 'neutral'
}

export const PART_LABEL: Record<AgentContextPart, string> = {
  system: 'System prompt',
  tools: 'Tool definitions',
  asked: 'Your requests, what was shared, the limits',
  files: 'The files you attached',
  thinking: "Claude's thinking",
  calls: 'Its tool calls',
  results: 'Tool results',
  answers: 'Its answers and notes',
  conversation: 'The conversation',
}

/** The parts of a window, largest first, as a list of bars: the tools with their number. */
export function contextItems(view: AgentContext): { label: string; value: number }[] {
  return view.parts
    .filter((part) => part.tokens > 0)
    .map((part) => ({
      label: (part.id === 'tools' || part.id === 'files') && part.count ? `${PART_LABEL[part.id]} (${part.count})` : PART_LABEL[part.id],
      value: part.tokens,
    }))
    .sort((a, b) => b.value - a.value)
}

/** The window's headline: what it holds of all it can, before a request is counted, from the last answer. */
export function contextUsed(tokens: number | null | undefined, window: number | undefined): { text: string; ratio: number } | null {
  if (tokens === null || tokens === undefined || !window) return null
  const ratio = tokens / window
  return { text: `${tokensText(tokens)} · ${ratio < 0.01 && tokens > 0 ? '<1' : Math.round(ratio * 100)}%`, ratio }
}

// ── the stream ──

export interface Delta {
  request: number
  part: 'text' | 'note'
  block: string
  offset: number
  text: string
}

/**
 * A piece of text streamed, folded into what was streamed before it. ``stale``: a piece missing (the page
 * stopped reading its stream a while): the conversation is read again.
 */
export function foldDelta(pending: AgentPending | null, delta: Delta): { pending: AgentPending; stale: boolean } {
  const current: AgentPending = pending && pending.request === delta.request
    ? pending
    : { request: delta.request, text: {}, note: {}, writing: null }
  const blocks = current[delta.part]
  const before = blocks[delta.block] ?? ''
  if (delta.offset < before.length) return { pending: current, stale: false }
  if (delta.offset > before.length) return { pending: current, stale: true }
  return {
    pending: { ...current, [delta.part]: { ...blocks, [delta.block]: before + delta.text },
      // A note or a text once a call was written: the call is over.
      writing: delta.part === 'text' ? null : current.writing },
    stale: false,
  }
}

export function foldWriting(pending: AgentPending | null, request: number, tool: string, chars: number): AgentPending {
  const current: AgentPending = pending && pending.request === request ? pending : { request, text: {}, note: {}, writing: null }
  return { ...current, writing: { tool, chars } }
}

// ── the sheet beside a run or a script ──

/** The address's parameter of the agent's sheet: a conversation's id, or NEW. */
export const AGENT_PARAM = 'agent'
export const NEW = 'new'

/** The address of a page with its agent's sheet open on a conversation (or a new one). */
export function withAgent(search: URLSearchParams, conversation: string): string {
  const next = new URLSearchParams(search)
  next.set(AGENT_PARAM, conversation)
  return `?${next.toString()}`
}

// ── a change of settings ──

/** A setting's value as a change of settings says it: on or off, a time, a number with its unit. */
export function settingSaid(value: number | boolean | string, unit: string): string {
  if (typeof value === 'boolean') return value ? 'On' : 'Off'
  const shown = unit && unit !== 'local time' && unit !== 'UTC' ? ` ${unit}` : ''
  return `${value}${shown}`
}

/** A run's figures in a line: its total, its trades and days, and how many random runs it beat. */
export function headlineSaid(headline: BacktestHeadline): string {
  const parts: string[] = []
  if (headline.total_percent !== null) parts.push(`${headline.total_percent >= 0 ? '+' : ''}${headline.total_percent.toFixed(2)} %`)
  if (headline.trades !== null) parts.push(`${headline.trades} trade${headline.trades === 1 ? '' : 's'}`)
  if (headline.days_tested !== null) parts.push(`${headline.days_tested} day${headline.days_tested === 1 ? '' : 's'}`)
  if (headline.beaten !== null) parts.push(`beat ${Math.round(headline.beaten)} % of random runs`)
  return parts.join(' · ')
}

// ── a research task ──

export const TASK_LABEL: Record<AgentTask['state'], string> = { working: 'Task working', done: 'Task done', ended: 'Task ended' }

export function taskTone(task: Pick<AgentTask, 'state'>): 'success' | 'warning' | 'neutral' {
  return task.state === 'working' ? 'neutral' : task.state === 'done' ? 'success' : 'warning'
}

/** A task's budget as its form starts it: a little of what the settings allow. */
export function taskDefaults(limits: { usd: number; backtests: number; hours: number } | undefined): { usd: number; backtests: number; hours: number } {
  return {
    usd: Math.min(5, limits?.usd ?? 5),
    backtests: Math.min(20, limits?.backtests ?? 20),
    hours: Math.min(4, limits?.hours ?? 4),
  }
}

/** What a task has spent of its budget, each as a share (0 to 1). */
export function taskShares(task: AgentTask, now: number): { usd: number; backtests: number; time: number } {
  const share = (used: number, of: number) => (of > 0 ? Math.min(1, Math.max(0, used / of)) : 1)
  const started = task.started_at ? Date.parse(task.started_at) : Date.parse(task.deadline) - task.budget.hours * 3_600_000
  const until = task.ended_at ? Date.parse(task.ended_at) : now
  return {
    usd: share(task.spent_usd, task.budget.usd),
    backtests: share(task.backtests, task.budget.backtests),
    time: share(until - started, Date.parse(task.deadline) - started),
  }
}

// ── what it shows on the pages ──

/** How each verdict reads, beside its line. */
export const VERDICT_SAID: Record<AgentVerdictTone, string> = {
  positive: 'It holds',
  mixed: 'In part',
  negative: 'It does not hold',
  neutral: 'Nothing to judge',
}

/** A verdict's tone as a badge's. */
export function verdictTone(tone: AgentVerdictTone): 'success' | 'warning' | 'danger' | 'neutral' {
  return tone === 'positive' ? 'success' : tone === 'mixed' ? 'warning' : tone === 'negative' ? 'danger' : 'neutral'
}

/** Where the thing a verdict is on is shown: a run's page, the signal rules, a day of the Report, a position. */
export function verdictLink(about: string): string {
  const [kind, id] = [about.slice(0, about.indexOf(':')), about.slice(about.indexOf(':') + 1)]
  if (kind === 'run') return `/backtests/${encodeURIComponent(id)}`
  if (kind === 'rule') return '/ta/signals'
  if (kind === 'day') return `/report?day=${encodeURIComponent(id)}`
  if (kind === 'luxalgo') return `/ta/luxalgo/indicators/${encodeURIComponent(id)}`
  return `/positions?find=${encodeURIComponent(id)}`
}

/** What a verdict is on, in words. */
export function verdictSubject(about: string): string {
  const [kind, id] = [about.slice(0, about.indexOf(':')), about.slice(about.indexOf(':') + 1)]
  return {
    run: `run ${id}`, rule: 'its signal rule', day: `the day of ${id}`, position: `position ${id}`,
    luxalgo: `LuxAlgo's ${id}`,
  }[kind] ?? about
}

/** A verdict on a thing changed since (a rule that runs something else): said to be from before. */
export function verdictBefore(verdict: Pick<AgentVerdict, 'at'>, changedAt: string | null | undefined): boolean {
  return !!changedAt && Date.parse(changedAt) > Date.parse(verdict.at)
}

/** The rows of a line chart as ioiti-ui's LineChart takes them: each moment and its series' numbers. */
export function chartPoints(chart: AgentChart): ({ t: string } & Record<string, number | string | null>)[] {
  return (chart.points ?? []).map((point) => {
    const row: { t: string } & Record<string, number | string | null> = { t: point.t }
    for (const series of chart.series) {
      const value = point[series.key]
      row[series.key] = typeof value === 'number' ? value : null
    }
    return row
  })
}

/** A chart's value with its unit: 1.5 %, 12 €, 3 points. */
export function chartValue(value: number, unit: string | null): string {
  const shown = Math.abs(value) >= 100 ? value.toFixed(0) : Math.abs(value) >= 10 ? value.toFixed(1) : value.toFixed(2)
  if (!unit) return shown
  return unit === '%' ? `${shown} %` : `${shown} ${unit}`
}

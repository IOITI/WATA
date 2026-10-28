// The AI agent's conversations as the pages show them (src/admin/agent/transcript.py projects them): each
// request's steps as activity, its answer as a response, what it rests on, the requests offered to start
// from, the text streamed folded in as it comes, why nothing can be asked, the model and effort a request
// goes with, and what its context window holds. Pure.

import type { AgentActivityItemData, AIResponseSource, AIResponseStatus, ActivityType } from 'ioiti-ui'

import type {
  AgentChoice,
  AgentContext,
  AgentContextPart,
  AgentConversation,
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
    default:
      return '/system'
  }
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
    default:
      return `Incident: ${subject.label}`
  }
}

// ── a subject in the address: a list's row asked about (?agent=new&about=position:5001) ──

export const ABOUT_PARAM = 'about'

/** A subject as the address carries it: its kind and id, a signal's day or an incident's moment after an @. */
export function aboutParam(subject: Pick<AgentSubject, 'kind' | 'id' | 'day' | 'at'>): string {
  const extra = subject.kind === 'signal' ? subject.day : subject.kind === 'incident' ? subject.at : null
  return `${subject.kind}:${subject.id}${extra ? `@${extra}` : ''}`
}

const ABOUT_KINDS = ['position', 'signal', 'day', 'incident'] as const

/** The subject the address names, or null; its label until the admin names it. */
export function subjectOf(about: string | null): AgentSubject | null {
  const match = about ? /^(position|signal|day|incident):([^@]{1,64})(?:@(.{1,16}))?$/.exec(about) : null
  if (!match || !(ABOUT_KINDS as readonly string[]).includes(match[1])) return null
  const [, kind, id, extra] = match
  if (kind === 'signal') return extra ? { kind, id, day: extra, label: `the signal of ${extra}` } : null
  if (kind === 'incident') return extra ? { kind, id, at: extra, label: `${id.replace(/_/g, ' ')} at ${extra} UTC` } : null
  return { kind: kind as 'position' | 'day', id, label: kind === 'day' ? id : id }
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
  rule_shadow_save: (input) => (typeof input.rule_id === 'string' && input.rule_id
    ? `Changed its shadow rule ${input.rule_id}` : `Saved the shadow rule "${short(input.name)}"`),
  rule_shadow_remove: (input) => `Removed its shadow rule ${short(input.rule_id)}`,
  rule_set_reference: (input) => `Set run ${short(input.run_id)} as its rule's reference`,
  task_done: () => 'Said the task is done',
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
  rule_shadow_save: 'writing',
  rule_shadow_remove: 'writing',
  rule_set_reference: 'writing',
  task_done: 'writing',
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
  rule_shadow_save: 'Saving a shadow rule',
  rule_shadow_remove: 'Removing a shadow rule',
  rule_set_reference: "Setting a rule's reference",
  task_done: 'Ending the task',
}

export function stepTitle(tool: string, input: unknown): string {
  const titled = TITLES[tool]
  return titled ? titled((input && typeof input === 'object' ? input : {}) as Input) : tool
}

export function writingTitle(tool: string | null | undefined): string {
  return (tool && WRITING[tool]) ?? `Writing a call to ${tool ?? 'a tool'}`
}

function seconds(from?: string | null, to?: string | null): number | null {
  if (!from || !to) return null
  const span = (Date.parse(to) - Date.parse(from)) / 1000
  return Number.isFinite(span) && span >= 0 ? span : null
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

/** A request's steps as the activity shows them, the step being written (pending) last. */
export function activityItems(
  request: AgentRequest,
  pending: AgentPending | null,
  time: (iso: string) => string,
): AgentActivityItemData[] {
  const items: AgentActivityItemData[] = request.steps.map((step) => {
    if (step.kind === 'note') {
      return { id: step.id, type: 'thinking', title: step.text ?? '', status: 'success', statusLabel: 'Note',
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
      duration: durationText(seconds(step.started_at, step.ended_at)),
      timestamp: step.started_at ? time(step.started_at) : undefined,
      details: { input: (step.input ?? undefined) as Record<string, unknown> | undefined, output: output(step.output) },
    }
  })
  if (pending && pending.request === request.index) {
    const note = joined(pending.note)
    if (note) items.push({ id: 'pending-note', type: 'thinking', title: note, status: 'running', statusLabel: 'Thinking' })
    if (pending.writing) {
      items.push({ id: 'pending-writing', type: 'writing', title: writingTitle(pending.writing.tool),
        description: pending.writing.chars > 0 ? `${pending.writing.chars.toLocaleString()} characters so far` : undefined,
        status: 'running', statusLabel: 'Writing' })
    }
  }
  return items
}

/** The blocks of a text streamed, in their order. */
export function joined(blocks: Record<string, string> | undefined): string {
  return Object.keys(blocks ?? {}).sort((a, b) => Number(a) - Number(b)).map((key) => blocks![key]).join('').trim()
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

/** What an answer rests on, as its sources: what was shared, the runs it started, what it proposed. */
export function sourcesOf(request: AgentRequest, proposals: AgentProposal[]): AIResponseSource[] {
  const sources: AIResponseSource[] = []
  if (request.shared) {
    sources.push({ id: `shared-${request.shared.id}`, title: subjectSaid(request.shared), url: subjectLink(request.shared),
      snippet: 'Shared with the request' })
  }
  for (const run of request.runs) {
    sources.push({ id: `run-${run}`, title: `Run ${run}`, url: `/backtests/${encodeURIComponent(run)}`,
      snippet: 'Started by the agent' })
  }
  for (const proposal of proposals.filter((entry) => request.proposals.includes(entry.id))) {
    sources.push({ id: proposal.id, title: proposal.title, url: `#proposal-${proposal.id}`,
      snippet: proposal.kind === 'script' ? 'A new version of a script, proposed' : 'A change to a rule, proposed' })
  }
  return sources
}

/** The tools a request used, for the response's summary. */
export function toolsOf(request: AgentRequest): { name: string; status: 'success' | 'running' | 'error'; duration?: string }[] {
  return request.steps
    .filter((step) => step.kind === 'tool')
    .map((step) => ({
      name: step.tool ?? '?',
      status: step.state === 'error' ? 'error' : step.state === 'running' ? 'running' : 'success',
      duration: durationText(seconds(step.started_at, step.ended_at)),
    }))
}

// ── what is offered and refused ──

export interface Preset {
  label: string
  prompt: string
}

/** Requests to start from, by what the conversation is about. */
export function presetsFor(kind: AgentSubject['kind'] | null): Preset[] {
  if (kind === 'run') {
    return [
      { label: 'Analyse it', prompt: 'Can this run be believed? Read its verdict checks, the edge of its entries, random entries, stress, regimes and trades. Say what it rests on and what to try next.' },
      { label: 'Find better settings', prompt: 'Look for settings that could do better than this run. Test the most promising with backtests within your limits, on the same days, compare them with this run, and say which holds and why.' },
      { label: 'Stress it', prompt: 'Stress this run and try its neighbours: does its result survive worse fills and settings near its own?' },
      { label: 'Improve its script', prompt: "Read this run's script and look for a change that could do better: save it as your own script, backtest it against this run on the same days, and propose it if it holds." },
    ]
  }
  if (kind === 'script') {
    return [
      { label: 'Review it', prompt: "Review this Pine script for bugs, repainting and WATA's rules for scripts. Propose the fixes as a new version." },
      { label: 'Explain it', prompt: 'Explain what this script does: its inputs, which alerts it raises and when.' },
      { label: 'Improve it', prompt: 'Suggest improvements to this script, backtest the most promising against the current version on the recorded days, and propose it if it holds.' },
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
    .map((part) => ({ label: part.id === 'tools' && part.count ? `${PART_LABEL.tools} (${part.count})` : PART_LABEL[part.id], value: part.tokens }))
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

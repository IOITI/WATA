import { describe, expect, it } from 'vitest'

import type { AgentContext, AgentConversation, AgentModel, AgentRequest, AgentState } from '@/lib/types'

import {
  activityItems,
  activitySummary,
  answerOf,
  aboutParam,
  actionFresh,
  actionPage,
  actionSaid,
  askAbout,
  askRefusal,
  chartPoints,
  chartValue,
  choiceOf,
  contextItems,
  contextTone,
  contextUsed,
  costText,
  durationText,
  foldDelta,
  foldWriting,
  headlineSaid,
  lastSentences,
  limitsSaid,
  modelSaid,
  outcomeSaid,
  presetsFor,
  settingSaid,
  shortModel,
  sourcesOf,
  statusTone,
  subjectLink,
  subjectOf,
  subjectSaid,
  stepTitle,
  taskDefaults,
  taskShares,
  tellSaid,
  tokensText,
  toolsOf,
  unreadBy,
  verdictBefore,
  verdictLink,
  verdictSubject,
  verdictTone,
  workingOf,
  writersOf,
  writingTitle,
} from './model'

const time = (iso: string) => iso.slice(11, 19)

function request(changes: Partial<AgentRequest> = {}): AgentRequest {
  return {
    index: 0, prompt: 'Analyse this run', shared: { kind: 'run', id: '20261027-101010-abcdef', label: 'Cross R60' },
    asked_at: '2026-10-28T08:00:00Z', started_at: '2026-10-28T08:00:01Z', ended_at: null, status: 'running',
    detail: null, steps: [], answer: null, proposals: [], runs: [], cost_usd: 0, feedback: null,
    ...changes,
  }
}

const STATE: AgentState = {
  enabled: true, model: 'claude-opus-5-5', model_label: 'Claude Opus 5.5', key: true, pause: null,
  month: { month: '2026-10', spent_usd: 3.2, budget_usd: 50 },
  limits: { tool_calls: 60, backtests: 8, request_usd: 5, running: 2 },
}

describe('a step', () => {
  it('is said from what its tool was given', () => {
    expect(stepTitle('backtest_result', { run_id: 'r1', part: 'edge' })).toBe('Read run r1 (edge)')
    expect(stepTitle('backtest_start', { request: { name: 'fast 14' } })).toBe('Started a backtest: fast 14')
    expect(stepTitle('backtest_derive', { run_id: 'r1', kind: 'stress' })).toBe('Stress-tested run r1')
    expect(stepTitle('backtest_wait', { run_ids: ['a', 'b'] })).toBe('Waited for 2 runs')
    expect(stepTitle('data_trades', { start_day: '2026-10-13', end_day: '2026-10-16' }))
      .toBe("Read WATA's trades of 2026-10-13 to 2026-10-16")
    expect(stepTitle('propose_rule_change', { title: 'Run v2' })).toBe('Proposed a change to a rule: Run v2')
    expect(stepTitle('memory_read', { names: ['range-ticks', 'answer-short'] })).toBe('Read range-ticks and answer-short from its memory')
    expect(stepTitle('memory_read', { names: ['a', 'b', 'c'] })).toBe('Read 3 entries from its memory')
    expect(stepTitle('memory_save', { name: 'range-ticks' })).toBe('Saved range-ticks to its memory')
    expect(stepTitle('memory_forget', { name: 'old-note' })).toBe('Forgot old-note from its memory')
    expect(stepTitle('luxalgo_tools', {})).toBe("Listed LuxAlgo's tools")
    expect(stepTitle('luxalgo_tools', { tool: 'propfirms_simulate' })).toBe("Read how LuxAlgo's propfirms_simulate is called")
    expect(stepTitle('luxalgo_call', { tool: 'library_search', arguments: { query: 'supertrend' } }))
      .toBe('Asked LuxAlgo: library_search (supertrend)')
    expect(stepTitle('luxalgo_call', { tool: 'propfirms_list_simulatable', arguments: {} })).toBe('Asked LuxAlgo: propfirms_list_simulatable')
    expect(stepTitle('luxalgo_save_script', { slug: 'tri-star' })).toBe("Took LuxAlgo's tri-star as its script")
    expect(writingTitle('luxalgo_save_script')).toBe("Taking one of LuxAlgo's indicators")
    expect(stepTitle('unknown_tool', {})).toBe('unknown_tool')
  })

  it('becomes an activity item with its state in words, its time and what it was given and answered; a note a note', () => {
    const items = activityItems(request({
      steps: [
        { kind: 'note', id: 'n', at: '2026-10-28T08:00:02Z', text: 'Reading the run first.' },
        { kind: 'tool', id: 't1', tool: 'backtest_result', input: { run_id: 'r1' }, started_at: '2026-10-28T08:00:03Z',
          ended_at: '2026-10-28T08:00:04.5Z', state: 'done', output: '{"trades":42}' },
        { kind: 'tool', id: 't2', tool: 'backtest_wait', input: { run_ids: ['r2'] }, started_at: '2026-10-28T08:00:05Z',
          state: 'running', progress: 'r2: 2nd in line' },
        { kind: 'tool', id: 't3', tool: 'pine_save', input: { name: 'X' }, started_at: '2026-10-28T08:00:06Z',
          ended_at: '2026-10-28T08:00:07Z', state: 'error', output: 'A new script needs a name\nmore' },
      ],
    }), null, time)

    expect(items.map((item) => [item.title, item.kind ?? 'step', item.status, item.statusLabel])).toEqual([
      ['Reading the run first.', 'note', 'success', undefined],
      ['Read run r1', 'step', 'success', 'Done'],
      ['Waited for run r2', 'step', 'running', 'Waiting'],
      ['Saved the script "X"', 'step', 'error', 'Refused'],
    ])
    expect(items[0].timestamp).toBe('08:00:02')
    expect(items[1]).toMatchObject({ duration: '1.5 s', timestamp: '08:00:03', details: { output: { trades: 42 } } })
    expect(items[2].description).toBe('r2: 2nd in line')
    expect(items[3].description).toBe('A new script needs a name')
  })

  it('shows what is being thought and written while the answer streams', () => {
    const items = activityItems(request(), { request: 0, text: {}, note: { 0: 'Looking at the edge' },
      writing: { tool: 'backtest_start', chars: 1240 } }, time)

    expect(items.map((item) => item.title)).toEqual(['Looking at the edge', 'Starting a backtest'])
    // The thought being written is a note, its call a step.
    expect(items[0]).toMatchObject({ kind: 'note', status: 'running' })
    expect(items[1].kind).toBeUndefined()
    expect(items[1]).toMatchObject({ description: '1,240 characters so far', statusLabel: 'Writing' })
  })

  it('says no time for a step quicker than a tenth of a second, where every read said "0.0 s"', () => {
    const steps: AgentRequest['steps'] = [
      { kind: 'tool', id: 'a', tool: 'status', state: 'done', started_at: '2026-10-28T08:00:00Z', ended_at: '2026-10-28T08:00:00.040Z' },
      { kind: 'tool', id: 'b', tool: 'rule_list', state: 'done', started_at: '2026-10-28T08:00:00Z', ended_at: '2026-10-28T08:00:00.100Z' },
    ]

    expect(activityItems(request({ steps }), null, time).map((item) => item.duration)).toEqual([undefined, '0.1 s'])
    expect(toolsOf(request({ steps })).map((tool) => tool.duration)).toEqual([undefined, '0.1 s'])
  })

  it('folds into a line: its tool calls, its notes, and the time it took once it ended', () => {
    const steps: AgentRequest['steps'] = [
      { kind: 'note', id: 'n0', text: 'First.' },
      { kind: 'tool', id: 'a', tool: 'status', state: 'done' },
      { kind: 'note', id: 'n1', text: 'Then.' },
      { kind: 'tool', id: 'b', tool: 'rule_list', state: 'done' },
    ]

    expect(activitySummary(request({ steps, status: 'done', ended_at: '2026-10-28T08:14:01Z' })))
      .toBe('2 tool calls · 2 notes · 14 min')
    expect(activitySummary(request({ steps: steps.slice(1, 2) }))).toBe('1 tool call')
  })

  it('says each tool while its call is written, without what it is not given yet', () => {
    const tools = ['status', 'data_assets', 'data_coverage', 'data_signals', 'data_trades', 'data_execution', 'pine_list',
      'pine_get', 'pine_check', 'pine_run', 'pine_signals', 'pine_compare', 'pine_save', 'backtest_options', 'backtest_list',
      'backtest_result', 'backtest_trades', 'backtest_days', 'backtest_signals', 'backtest_compare', 'backtest_start',
      'backtest_derive', 'backtest_wait', 'turbo_model', 'rule_list', 'rule_compare', 'exp_new', 'exp_get', 'exp_conclude',
      'propose_script_change', 'propose_rule_change']

    for (const tool of tools) expect(writingTitle(tool)).not.toMatch(/\?|Writing a call/)
    expect(writingTitle('backtest_derive')).toBe('Starting a run from a run')
    expect(writingTitle('data_bars')).toBe('Writing a call to data_bars')
  })
})

describe('an answer', () => {
  it('streams, then is complete, or says why it stopped', () => {
    expect(answerOf(request(), null)).toEqual({ content: '', status: 'generating' })
    expect(answerOf(request(), { request: 0, text: { 1: ' holds.', 0: 'It' }, note: {}, writing: null }))
      .toEqual({ content: 'It holds.', status: 'streaming' })
    expect(answerOf(request({ status: 'done', answer: { text: 'It holds.', model: 'claude-opus-5-5', stop_reason: 'end_turn' } }), null))
      .toEqual({ content: 'It holds.', status: 'complete' })
    expect(answerOf(request({ status: 'failed', detail: 'No key' }), null)).toEqual({ content: 'No key', status: 'error' })
    expect(answerOf(request({ status: 'limit', detail: 'Stopped at its cost limit ($5.00)' }), null))
      .toEqual({ content: 'Stopped at its cost limit ($5.00)', status: 'cancelled' })
  })

  it('says how it ended once, in the sentence the agent wrote when there is one', () => {
    expect(outcomeSaid('stopped', 'Stopped by the operator')).toBe('Stopped by the operator')
    expect(outcomeSaid('cut', 'The answer reached its length limit')).toBe('The answer reached its length limit')
    expect(outcomeSaid('done', null)).toBe('Answered')
    // A failure's sentence is its alert's.
    expect(outcomeSaid('failed', 'No key')).toBe('Failed')
  })

  it('rests on what was shared, the runs it started and what it proposed', () => {
    const sources = sourcesOf(request({ runs: ['r9'], proposals: ['p1'] }), [
      { id: 'p1', kind: 'script', request: 0, created_at: 'x', status: 'proposed', title: 'Slower line', rationale: 'r' },
      { id: 'p2', kind: 'rule', request: 1, created_at: 'x', status: 'proposed', title: 'Other', rationale: 'r' },
    ])

    expect(sources.map((source) => [source.title, source.url])).toEqual([
      ['Run Cross R60', '/backtests/20261027-101010-abcdef'],
      ['Run r9', '/backtests/r9'],
      ['Slower line', '#proposal-p1'],
    ])
    // Each kind of proposal says itself, a change of settings no longer called a rule's.
    const settings = sourcesOf(request({ proposals: ['p3'] }), [
      { id: 'p3', kind: 'settings', request: 0, created_at: 'x', status: 'proposed', title: 'Tighter stop', rationale: 'r' },
    ])
    expect(settings.at(-1)?.snippet).toBe('A change of settings, proposed')
  })

  it('counts the tools it used with their states', () => {
    const tools = toolsOf(request({ steps: [
      { kind: 'note', id: 'n', text: 'x' },
      { kind: 'tool', id: 'a', tool: 'status', state: 'done', started_at: '2026-10-28T08:00:00Z', ended_at: '2026-10-28T08:02:00Z' },
      { kind: 'tool', id: 'b', tool: 'pine_check', state: 'error' },
    ] }))
    expect(tools).toEqual([{ name: 'status', status: 'success', duration: '2 min' }, { name: 'pine_check', status: 'error', duration: undefined }])
  })
})

describe('what can be asked', () => {
  it('offers requests by what the conversation is about', () => {
    expect(presetsFor('run').map((preset) => preset.label)).toContain('Find better settings')
    expect(presetsFor('script').map((preset) => preset.label)).toContain('Review it')
    expect(presetsFor(null)).toHaveLength(1)
  })

  it('says why nothing can be asked, the first reason first', () => {
    expect(askRefusal(STATE)).toBeNull()
    expect(askRefusal({ enabled: false, model: 'claude-opus-5-5' })).toMatch(/is off/)
    expect(askRefusal({ ...STATE, key: false })).toMatch(/No Claude API key/)
    expect(askRefusal({ ...STATE, pause: { since: 'x', by: null } })).toMatch(/paused/)
    expect(askRefusal({ ...STATE, month: { month: '2026-10', spent_usd: 50, budget_usd: 50 } })).toMatch(/\$50\.00 is spent/)
  })

  it('says costs and durations plainly', () => {
    expect(costText(0.4182)).toBe('$0.42')
    expect(costText(0.004)).toBe('under $0.01')
    expect(costText(null)).toBe('—')
    expect(durationText(4.24)).toBe('4.2 s')
    expect(durationText(3725)).toBe('1 h 2 min')
    expect(statusTone('running')).toBe('neutral')
    expect(statusTone('idle', 'limit')).toBe('warning')
    expect(statusTone('failed')).toBe('danger')
  })
})

describe('the stream', () => {
  it('folds pieces of text at their offsets, and asks to read again when one is missing', () => {
    let folded = foldDelta(null, { request: 0, part: 'text', block: '0', offset: 0, text: 'It ' })
    folded = foldDelta(folded.pending, { request: 0, part: 'text', block: '0', offset: 3, text: 'holds.' })
    expect(folded).toEqual({ pending: { request: 0, text: { 0: 'It holds.' }, note: {}, writing: null }, stale: false })

    // Read twice (after a read of the whole conversation): kept once.
    expect(foldDelta(folded.pending, { request: 0, part: 'text', block: '0', offset: 0, text: 'It ' }).pending.text[0]).toBe('It holds.')
    expect(foldDelta(folded.pending, { request: 0, part: 'text', block: '0', offset: 20, text: 'x' }).stale).toBe(true)
    // Another request starts afresh.
    expect(foldDelta(folded.pending, { request: 1, part: 'note', block: '0', offset: 0, text: 'Next' }).pending)
      .toEqual({ request: 1, text: {}, note: { 0: 'Next' }, writing: null })
  })

  it("follows a call's input as it is written, until text comes again", () => {
    const writing = foldWriting(null, 0, 'pine_save', 820)
    expect(writing.writing).toEqual({ tool: 'pine_save', chars: 820 })
    expect(foldDelta(writing, { request: 0, part: 'text', block: '2', offset: 0, text: 'Saved.' }).pending.writing).toBeNull()
  })
})

describe('the model, the effort and the context window', () => {
  const model = (id: string, label: string, reads: string[], changes: Partial<AgentModel> = {}): AgentModel => ({
    id, label, window: 1_000_000, max_output: 128_000, prices: { input: 4, output: 20, cache_read: 0.2 },
    efforts: ['low', 'medium', 'high', 'xhigh', 'max'], available: true, reads, ...changes,
  })
  const OPUS = model('claude-opus-5-5', 'Claude Opus 5.5', ['claude-opus-5-5', 'claude-sonnet-5-5'])
  const SONNET = model('claude-sonnet-5-5', 'Claude Sonnet 5.5', ['claude-sonnet-5-5'], { prices: { input: 2, output: 10, cache_read: 0.2 } })
  const HAIKU = model('claude-haiku-5-5', 'Claude Haiku 5.5', ['claude-haiku-5-5'], { prices: { input: 0.1, output: 0.5, cache_read: 0.01 } })

  it("goes on with the conversation's model and effort, else the settings'", () => {
    const state = { enabled: true, model: 'claude-opus-5-5', effort: 'high' } as AgentState
    const conversation = { model: 'claude-sonnet-5-5', effort: 'low', requests: [] } as unknown as AgentConversation

    expect(choiceOf(state, null)).toEqual({ model: 'claude-opus-5-5', effort: 'high' })
    expect(choiceOf(state, conversation)).toEqual({ model: 'claude-sonnet-5-5', effort: 'low' })
    expect(choiceOf(undefined, null)).toBeNull()
  })

  it("says which models' notes a model cannot read, of those that wrote the conversation's", () => {
    const conversation = { requests: [{ model: 'claude-opus-5-5' }, { model: null }, { model: 'claude-sonnet-5-5' }] } as unknown as AgentConversation
    const writers = writersOf(conversation)

    expect([...writers]).toEqual(['claude-opus-5-5', 'claude-sonnet-5-5'])
    expect(unreadBy(OPUS, writers, [OPUS, SONNET])).toEqual([])
    expect(unreadBy(SONNET, writers, [OPUS, SONNET])).toEqual(['Claude Opus 5.5'])
    expect(unreadBy(undefined, writers, [OPUS])).toEqual([])
  })

  it('says a model by its prices and window, or why it cannot be chosen', () => {
    expect(modelSaid(OPUS)).toBe('$4 / $20 per million tokens · 1M context')
    expect(modelSaid(HAIKU)).toBe('$0.10 / $0.50 per million tokens · 1M context')
    expect(modelSaid({ ...OPUS, available: false })).toBe('Not with this Claude API key')
    expect(shortModel('Claude Haiku 5.5')).toBe('Haiku 5.5')
  })

  it('says tokens short, and how full a window is', () => {
    expect([tokensText(512), tokensText(84_213), tokensText(150_000), tokensText(1_000_000), tokensText(1_500_000)])
      .toEqual(['512', '84.2k', '150k', '1M', '1.5M'])
    expect(contextUsed(84_213, 1_000_000)).toEqual({ text: '84.2k · 8%', ratio: 0.084213 })
    expect(contextUsed(2_000, 1_000_000)?.text).toBe('2k · <1%')
    expect(contextUsed(null, 1_000_000)).toBeNull()
    expect([contextTone(0.5), contextTone(0.8), contextTone(0.95)]).toEqual(['neutral', 'warning', 'negative'])
  })

  it("lists a window's parts largest first, the tools with their number, the empty ones left out", () => {
    const view = {
      parts: [{ id: 'system', tokens: 6_000 }, { id: 'tools', tokens: 18_000, count: 31 }, { id: 'asked', tokens: 0 },
        { id: 'results', tokens: 31_000, estimated: true }],
    } as AgentContext

    expect(contextItems(view)).toEqual([
      { label: 'Tool results', value: 31_000 }, { label: 'Tool definitions (31)', value: 18_000 },
      { label: 'System prompt', value: 6_000 },
    ])
  })

  it("says a request's limits and the month's spending, the model being the menu's", () => {
    const state = { enabled: true, model: 'claude-opus-5-5', limits: { tool_calls: 60, backtests: 8, request_usd: 5, running: 2 },
      month: { month: '2026-10', spent_usd: 3.2, budget_usd: 50 } } as AgentState

    expect(limitsSaid(state)).toBe('up to $5.00 a request · $3.20 of $50.00 this month')
  })
})

describe('what it may be asked about', () => {
  it('carries a row of a list in the address, a signal with its day and an incident with its moment', () => {
    const signal = { kind: 'signal', id: 'c0ffee', day: '2026-10-28' } as const
    const incident = { kind: 'incident', id: 'position_monitor', at: '2026-10-28 15:31' } as const

    expect([aboutParam({ kind: 'position', id: '5001' }), aboutParam(signal), aboutParam(incident)])
      .toEqual(['position:5001', 'signal:c0ffee@2026-10-28', 'incident:position_monitor@2026-10-28 15:31'])
    expect(subjectOf('signal:c0ffee@2026-10-28')).toMatchObject(signal)
    expect(subjectOf('incident:position_monitor@2026-10-28 15:31')).toMatchObject({ ...incident, label: 'position monitor at 2026-10-28 15:31 UTC' })
    expect(subjectOf('day:2026-10-28')).toMatchObject({ kind: 'day', id: '2026-10-28' })
    // What no list carries, or a signal without its day: nothing.
    expect([subjectOf('run:x'), subjectOf('signal:c0ffee'), subjectOf(null), subjectOf('position:')]).toEqual([null, null, null, null])
    expect(askAbout(new URLSearchParams('status=all'), { kind: 'position', id: '5001' })).toBe('?status=all&agent=new&about=position%3A5001')
  })

  it('leads each subject to its page and names it', () => {
    expect(subjectLink({ kind: 'position', id: '5001', label: 'x' })).toBe('/positions?find=5001')
    expect(subjectLink({ kind: 'signal', id: 'c0ffee', label: 'x' })).toBe('/signals?find=c0ffee')
    expect(subjectLink({ kind: 'day', id: '2026-10-28', label: 'x' })).toBe('/report?day=2026-10-28')
    expect(subjectLink({ kind: 'incident', id: 'trader', label: 'x' })).toBe('/system')
    expect(subjectSaid({ kind: 'day', id: '2026-10-28', label: 'the day of 2026-10-28' })).toBe('The trading day 2026-10-28')
    expect(subjectSaid({ kind: 'incident', id: 'trader', label: 'trader at 15:31 UTC' })).toBe('Incident: trader at 15:31 UTC')
  })

  it('offers requests to start from for each kind', () => {
    for (const kind of ['position', 'signal', 'day', 'incident'] as const) {
      expect(presetsFor(kind).length).toBeGreaterThan(1)
    }
    expect(presetsFor('incident')[0].prompt).toMatch(/Act on nothing/)
  })
})

describe('what a proposal of settings and a research task say', () => {
  it('says a setting with its unit, and a run in a line', () => {
    expect(settingSaid(-7, '%')).toBe('-7 %')
    expect(settingSaid(true, '')).toBe('On')
    expect(settingSaid('21:55', 'local time')).toBe('21:55')
    expect(headlineSaid({ total_percent: -1.5, trades: 1, win_rate: null, worst_drawdown: null, sample: 'too_few',
      beaten: 40.4, days_tested: 3, profit_loss: null, cells: null })).toBe('-1.50 % · 1 trade · 3 days · beat 40 % of random runs')
  })

  it("starts a task with a little of what the settings allow, and counts what it spent", () => {
    expect(taskDefaults({ usd: 3, backtests: 40, hours: 12 })).toEqual({ usd: 3, backtests: 20, hours: 4 })
    const task = { budget: { usd: 4, backtests: 10, hours: 2 }, state: 'working' as const, started_at: '2026-10-28T12:00:00Z',
      deadline: '2026-10-28T14:00:00Z', spent_usd: 1, backtests: 12 }
    expect(taskShares(task, Date.parse('2026-10-28T13:00:00Z'))).toEqual({ usd: 0.25, backtests: 1, time: 0.5 })
    // Ended: its time stops at its end.
    expect(taskShares({ ...task, state: 'ended', ended_at: '2026-10-28T12:30:00Z' }, Date.parse('2026-10-28T20:00:00Z')).time)
      .toBe(0.25)
  })
})

describe('the tools of the live trading, the proposals and the tasks, as their steps say them', () => {
  it('titles each', () => {
    expect(stepTitle('data_position', { position_id: '5001' })).toBe('Read position 5001')
    expect(stepTitle('ops_logs', { service: 'trader', around: '2026-10-28 15:31' })).toBe('Read the logs of trader around 2026-10-28 15:31')
    expect(stepTitle('propose_settings_change', { title: 'A tighter stop' })).toBe('Proposed new settings: A tighter stop')
    expect(stepTitle('rule_shadow_save', { name: 'cross' })).toBe('Saved the shadow rule "cross"')
    expect(stepTitle('task_done', {})).toBe('Said the task is done')
    expect(writingTitle('propose_new_script')).toBe('Proposing a new script')
  })

  it('titles the reads of trading, the charts, the verdicts, the actions, the new rules and the questions', () => {
    expect(stepTitle('ops_trading_now', {})).toBe('Read trading as the dashboard shows it')
    expect(stepTitle('ops_changes', {})).toBe('Read what changed lately')
    expect(stepTitle('ops_changes', { hours: 2 })).toBe('Read what changed in the last 2 h')
    expect(stepTitle('ops_changes', { hours: 2, until: '2026-10-29 14:30' }))
      .toBe('Read what changed in the 2 h before 2026-10-29 14:30')
    expect(stepTitle('show_chart', { title: 'Its days' })).toBe('Drew a chart: Its days')
    expect(stepTitle('set_verdict', { about: 'run:20261030-204011-442342' }))
      .toBe('Said its verdict on run 20261030-204011-442342')
    expect(stepTitle('set_verdict', { about: 'day:2026-10-29' })).toBe('Said its verdict on the day of 2026-10-29')
    expect(stepTitle('propose_action', { title: 'Pause new entries' })).toBe('Proposed an action: Pause new entries')
    expect(stepTitle('propose_new_rule', { name: 'Cross 5' })).toBe('Proposed a new rule: Cross 5')
    expect(stepTitle('ask_operator', { question: 'Which days?' })).toBe('Asked you: Which days?')
    for (const tool of ['ops_trading_now', 'ops_changes', 'ops_engine', 'ops_release', 'show_chart', 'set_verdict',
      'propose_action', 'propose_new_rule', 'ask_operator']) {
      expect(writingTitle(tool)).not.toMatch(/\?|Writing a call/)
    }
  })
})

describe('what it is doing', () => {
  const wait: AgentRequest['steps'][number] = {
    kind: 'tool', id: 'w', tool: 'backtest_wait', input: { run_ids: ['a', 'b', 'c'] }, started_at: '2026-10-28T08:03:00Z',
    state: 'running', progress: 'a: done; b: done; c: signals, 288 of 356',
  }

  it('is nothing once a request ended, and its turn while it waits for one', () => {
    expect(workingOf(request({ status: 'done' }), null)).toBeNull()
    expect(workingOf(request({ status: 'queued', started_at: null }), null))
      .toEqual({ label: 'Waiting its turn', lines: [], since: '2026-10-28T08:00:00Z' })
  })

  it('says a wait run by run, then its latest thought sentence by sentence, then what it did so far', () => {
    const working = workingOf(request({
      status: 'waiting', runs: ['a', 'b', 'c'],
      steps: [
        { kind: 'note', id: 'n0', text: 'An old thought.' },
        { kind: 'tool', id: 't', tool: 'backtest_start', state: 'done' },
        { kind: 'note', id: 'n1', text: 'The paper run returned -9.01% over 35 trades. Its late days carry it.\nI stress it next.' },
        wait,
      ],
    }), null)

    expect(working).toEqual({
      label: 'Waiting for 3 runs',
      lines: ['a: done', 'b: done', 'c: signals, 288 of 356', 'The paper run returned -9.01% over 35 trades.',
        'Its late days carry it.', 'I stress it next.', '2 tool calls so far · 3 runs started'],
      since: '2026-10-28T08:00:01Z',
    })
  })

  it('says the call it writes, and the thought it streams before what was kept', () => {
    const working = workingOf(request({ steps: [{ kind: 'note', id: 'n', text: 'Kept.' }] }),
      { request: 0, text: {}, note: { 0: 'Streamed so far, still' }, writing: { tool: 'backtest_start', chars: 1240 } })

    expect(working?.label).toBe('Starting a backtest')
    expect(working?.lines).toEqual(['1,240 characters of its call written', 'Streamed so far, still'])
  })

  it('thinks between its steps, and says the first things a request does before anything is known', () => {
    const done = workingOf(request({ steps: [{ kind: 'tool', id: 't', tool: 'status', state: 'done' }] }), null)
    expect(done).toMatchObject({ label: 'Thinking', lines: ['1 tool call so far'] })
    // The stream of another request says nothing of this one.
    expect(workingOf(request(), { request: 1, text: {}, note: { 0: 'Elsewhere' }, writing: null })?.lines)
      .toEqual(['Reading the request and what was shared', 'Planning its first steps'])
    expect(workingOf(request({ shared: null }), null)?.lines).toEqual(['Reading the request', 'Planning its first steps'])
  })

  it('cuts a thought into its last sentences, a figure with its point kept whole, each to a line', () => {
    expect(lastSentences('One. Two! Three? Four… and five')).toEqual(['Two!', 'Three?', 'Four… and five'])
    expect(lastSentences('It made +1.25 % on 2.5 days.')).toEqual(['It made +1.25 % on 2.5 days.'])
    const long = lastSentences(`${'x'.repeat(300)}.`)[0]
    expect(long).toHaveLength(160)
    expect(long.endsWith('…')).toBe(true)
    expect(lastSentences('   ')).toEqual([])
  })
})


describe('what it shows on the pages', () => {
  it('says what Telegram is told, or why it cannot be', () => {
    expect(tellSaid({ ...STATE, telegram: false }, true)).toMatch(/^No Telegram bot is set/)
    expect(tellSaid({ ...STATE, telegram: true }, false)).toBe('Say on Telegram the first lines of the answer, once it is written.')
    expect(tellSaid(STATE, true, true)).toBe('Telegram says its conclusion, once the task ends.')
  })

  it('leads from a verdict to what it is on, and says one older than the thing\'s last change', () => {
    expect(verdictLink('run:20261027-101010-abcdef')).toBe('/backtests/20261027-101010-abcdef')
    expect(verdictLink('day:2026-10-28')).toBe('/report?day=2026-10-28')
    expect(verdictLink('position:5001')).toBe('/positions?find=5001')
    expect(verdictLink('rule:a1b2c3d4e5f6')).toBe('/ta/signals/a1b2c3d4e5f6')
    expect(verdictSubject('day:2026-10-28')).toBe('the day of 2026-10-28')
    expect(verdictTone('negative')).toBe('danger')
    expect(verdictBefore({ at: '2026-10-28T10:00:00Z' }, '2026-10-29T08:00:00Z')).toBe(true)
    expect(verdictBefore({ at: '2026-10-28T10:00:00Z' }, null)).toBe(false)
  })

  it("gives a line chart each series' numbers, a gap as null, and its values with their unit", () => {
    const points = chartPoints({
      kind: 'line', title: 't', unit: '%', references: [], source: 's',
      series: [{ key: 'run', label: 'Run' }, { key: 'random', label: 'Random' }],
      points: [{ t: '2026-10-13', run: 1.5 }, { t: '2026-10-14', run: -0.25, random: 0.1 }],
    })

    expect(points).toEqual([{ t: '2026-10-13', run: 1.5, random: null }, { t: '2026-10-14', run: -0.25, random: 0.1 }])
    expect(chartValue(1.5, '%')).toBe('1.50 %')
    expect(chartValue(123.4, '€')).toBe('123 €')
    expect(chartValue(12.34, null)).toBe('12.3')
  })
})

describe('an action proposed', () => {
  it('says what it does and where it is done by hand', () => {
    expect(actionSaid({ kind: 'close_position', position_id: '5001', instrument_name: 'Turbo long' })).toBe('Close position 5001 (Turbo long)')
    expect(actionSaid({ kind: 'restart', services: [{ id: 'trader', name: 'Trader' }, { id: 'web_server', name: 'Web server' },
      { id: 'admin', name: 'Admin' }] })).toBe('Restart the trader, web server and admin')
    expect(actionSaid({ kind: 'rule_out_of_live', rule: { id: 'r', name: 'Cross', mode: 'live', indice: 'us100' } }))
      .toBe('Take Cross out of live')
    expect(actionPage({ kind: 'resume_entries' })).toEqual({ to: '/', label: 'the dashboard' })
    expect(actionPage({ kind: 'close_position', position_id: '5001' }).to).toBe('/positions?find=5001')
    // Going live, or leaving it, is done by hand on the rule's own page.
    expect(actionPage({ kind: 'rule_live', rule: { id: 'a1b2c3d4e5f6', name: 'Cross', mode: 'shadow', indice: 'us100' } }))
      .toEqual({ to: '/ta/signals/a1b2c3d4e5f6', label: 'Cross' })
  })

  it('is applied from its card while fresh only', () => {
    const now = Date.parse('2026-10-28T14:20:00Z')
    expect(actionFresh({ fresh_until: '2026-10-28T14:30:00Z' }, now)).toBe(true)
    expect(actionFresh({ fresh_until: '2026-10-28T14:10:00Z' }, now)).toBe(false)
    expect(actionFresh({}, now)).toBe(false)
  })
})

describe("one of LuxAlgo's indicators asked about", () => {
  it('is read from the address, led to its page and named', () => {
    expect(subjectOf('luxalgo:ai-supertrend-clustering')).toEqual({ kind: 'luxalgo', id: 'ai-supertrend-clustering', label: 'ai-supertrend-clustering' })
    expect(aboutParam({ kind: 'luxalgo', id: 'tri-star' })).toBe('luxalgo:tri-star')
    expect(subjectLink({ kind: 'luxalgo', id: 'tri-star', label: "LuxAlgo's Tri-star" })).toBe('/ta/luxalgo/indicators/tri-star')
    expect(verdictLink('luxalgo:tri-star')).toBe('/ta/luxalgo/indicators/tri-star')
    expect(verdictSubject('luxalgo:tri-star')).toBe("LuxAlgo's tri-star")
    expect(subjectSaid({ kind: 'luxalgo', id: 'tri-star', label: "LuxAlgo's Tri-star" })).toBe("LuxAlgo's Tri-star")
  })

  it('is offered its tests, and a script or a run what LuxAlgo adds while the agent has its tools', () => {
    expect(presetsFor('luxalgo').map((preset) => preset.label)).toEqual(['Worth testing on WATA?', 'Test it', 'Make it a signal rule'])
    expect(presetsFor('luxalgo').map((preset) => preset.prompt).join(' ')).not.toMatch(/go live|take it live/i)
    for (const kind of ['script', 'rule'] as const) {
      expect(presetsFor(kind).map((preset) => preset.label)).not.toContain('Find similar in LuxAlgo')
      expect(presetsFor(kind, { luxalgo: true }).map((preset) => preset.label)).toContain('Find similar in LuxAlgo')
    }
    expect(presetsFor('run').map((preset) => preset.label)).not.toContain('Would it pass a funded challenge?')
    expect(presetsFor('run', { luxalgo: true }).map((preset) => preset.label)).toContain('Would it pass a funded challenge?')
  })
})

describe('a signal rule asked about', () => {
  it('is read from the address and offered its dossier first', () => {
    expect(subjectOf('rule:a1b2c3d4e5f6')).toEqual({ kind: 'rule', id: 'a1b2c3d4e5f6', label: 'a1b2c3d4e5f6' })
    expect(presetsFor('rule')[0].label).toBe('Is it ready to go live?')
    expect(subjectLink({ kind: 'rule', id: 'x', label: 'Cross' })).toBe('/ta/signals/x')
    expect(subjectSaid({ kind: 'draft', id: 'x', label: 'a draft of Cross' })).toBe('To review: a draft of Cross')
  })
})

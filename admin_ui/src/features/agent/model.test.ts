import { describe, expect, it } from 'vitest'

import type { AgentContext, AgentConversation, AgentModel, AgentRequest, AgentState } from '@/lib/types'

import {
  activityItems,
  answerOf,
  aboutParam,
  askAbout,
  askRefusal,
  choiceOf,
  contextItems,
  contextTone,
  contextUsed,
  costText,
  durationText,
  foldDelta,
  foldWriting,
  headlineSaid,
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
  tokensText,
  toolsOf,
  unreadBy,
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
    expect(stepTitle('unknown_tool', {})).toBe('unknown_tool')
  })

  it('becomes an activity item with its state in words, its time and what it was given and answered', () => {
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

    expect(items.map((item) => [item.title, item.status, item.statusLabel])).toEqual([
      ['Reading the run first.', 'success', 'Note'],
      ['Read run r1', 'success', 'Done'],
      ['Waited for run r2', 'running', 'Waiting'],
      ['Saved the script "X"', 'error', 'Refused'],
    ])
    expect(items[1]).toMatchObject({ duration: '1.5 s', timestamp: '08:00:03', details: { output: { trades: 42 } } })
    expect(items[2].description).toBe('r2: 2nd in line')
    expect(items[3].description).toBe('A new script needs a name')
  })

  it('shows what is being thought and written while the answer streams', () => {
    const items = activityItems(request(), { request: 0, text: {}, note: { 0: 'Looking at the edge' },
      writing: { tool: 'backtest_start', chars: 1240 } }, time)

    expect(items.map((item) => item.title)).toEqual(['Looking at the edge', 'Starting a backtest'])
    expect(items[1]).toMatchObject({ description: '1,240 characters so far', statusLabel: 'Writing' })
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
})

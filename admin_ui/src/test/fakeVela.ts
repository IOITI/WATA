// A stand-in for Vela in tests (jsdom has no canvas): its workspace (the Charts page) and its
// plain chart (a day of a backtest) record how they were built and what was asked of them, as
// fakeCharts.ts does for Lightweight Charts.

type Handler = (payload: unknown) => void

export interface FakeEntry {
  name: string
  script: string
  enabled: boolean
  category?: string
}

export class FakeCell {
  symbol: string
  timeframe: string
  added: FakeEntry[] = []
  symbols: string[] = []
  private handlers = new Map<string, Handler[]>()

  constructor(symbol: string, timeframe: string) {
    this.symbol = symbol
    this.timeframe = timeframe
  }

  readonly chart = {
    on: (event: string, handler: Handler) => {
      this.handlers.set(event, [...(this.handlers.get(event) ?? []), handler])
      return () => this.handlers.set(event, (this.handlers.get(event) ?? []).filter((one) => one !== handler))
    },
    ready: async () => undefined,
  }

  emit(event: string, payload: unknown) {
    for (const handler of this.handlers.get(event) ?? []) handler(payload)
  }

  setSymbol(symbol: string) {
    this.symbol = symbol
    this.symbols.push(symbol)
  }

  setTimeframe(timeframe: string) {
    this.timeframe = timeframe
  }

  onChartRows() {
    return this.added.map((entry) => ({ name: entry.name }))
  }

  addManifestInstance(entry: FakeEntry) {
    this.added.push(entry)
  }
}

export const workspaces: FakeWorkspace[] = []

export class FakeWorkspace {
  readonly options: Record<string, unknown>
  readonly active: FakeCell
  themes: unknown[] = []
  destroyed = false

  constructor(_host: unknown, options: Record<string, unknown>) {
    this.options = options
    this.active = new FakeCell(String(options.symbol), String(options.timeframe))
    workspaces.push(this)
  }

  setTheme(theme: unknown) {
    this.themes.push(theme)
  }

  destroy() {
    this.destroyed = true
  }
}

export const enginesMade: { workerUrl?: string }[] = []
/** The engines made, each with whether its worker was ended. */
export const engines: FakePineWorkerEngine[] = []

export class FakePineWorkerEngine {
  // As Vela-pinets' own.
  readonly language = 'pine'
  readonly capabilities = { streaming: true, visibleRange: true, inputs: true, props: true }
  terminated = false

  constructor(options: { workerUrl?: string } = {}) {
    enginesMade.push(options)
    engines.push(this)
  }

  terminate() {
    this.terminated = true
  }
}

export interface FakeIndicator {
  source: string
  options: Record<string, unknown>
  /** What updateCode gave it since, in order. */
  codes: string[]
}

export const velas: FakeVela[] = []

/** Vela's plain chart. */
export class FakeVela {
  readonly options: Record<string, unknown>
  readonly deps: { dataFeed?: FakeMultiProviderFeed; engines?: { language: string }[] }
  readonly indicators: FakeIndicator[] = []
  readonly ranges: unknown[] = []
  readonly themes: unknown[] = []
  readonly settings: [string, unknown][] = []
  destroyed = false
  private handlers = new Map<string, Handler[]>()

  constructor(_host: unknown, options: Record<string, unknown>, deps: FakeVela['deps'] = {}) {
    this.options = options
    this.deps = deps
    velas.push(this)
  }

  readonly renderer = {
    set: (key: string, value: unknown) => {
      this.settings.push([key, value])
    },
  }

  addIndicator(source: string, options: Record<string, unknown> = {}) {
    const indicator: FakeIndicator = { source, options, codes: [] }
    this.indicators.push(indicator)
    return {
      id: options.id,
      updateCode: async (code: string) => {
        indicator.codes.push(code)
        return { ok: true, error: null }
      },
    }
  }

  async ready() {}

  setVisibleRange(range: unknown) {
    this.ranges.push(range)
    return this
  }

  setTheme(theme: unknown) {
    this.themes.push(theme)
    return this
  }

  on(event: string, handler: Handler) {
    this.handlers.set(event, [...(this.handlers.get(event) ?? []), handler])
    return () => this.handlers.set(event, (this.handlers.get(event) ?? []).filter((one) => one !== handler))
  }

  emit(event: string, payload: unknown) {
    for (const handler of this.handlers.get(event) ?? []) handler(payload)
  }

  destroy() {
    this.destroyed = true
  }
}

export class FakeBarStore {}

/** Vela's data feed: the providers registered, and the symbols whose info was asked for. */
export class FakeMultiProviderFeed {
  readonly store: unknown
  readonly providers = new Map<string, unknown>()
  readonly informed: string[] = []

  constructor(store?: unknown) {
    this.store = store
  }

  async registerProvider(name: string, provider: unknown) {
    this.providers.set(name, provider)
  }

  async symbolInfoFor(symbol: string) {
    this.informed.push(symbol)
    return undefined
  }
}

/** What the tests mock '@luxalgo/vela' with. */
export const fakeVelaCore = { Vela: FakeVela, MultiProviderFeed: FakeMultiProviderFeed, BarStore: FakeBarStore }

/** What the tests mock '@luxalgo/vela/workspace' with: the layout codec kept as plain JSON. */
export const fakeVelaWorkspace = {
  VelaWorkspace: FakeWorkspace,
  decodeState: (raw: string) => {
    try {
      const state = JSON.parse(raw) as { version?: number }
      return state && state.version === 1 ? state : null
    } catch {
      return null
    }
  },
  encodeState: (state: unknown) => JSON.stringify(state),
}

export const fakeVelaPinets = { PineWorkerEngine: FakePineWorkerEngine }

export function resetVela() {
  workspaces.length = 0
  enginesMade.length = 0
  engines.length = 0
  velas.length = 0
}

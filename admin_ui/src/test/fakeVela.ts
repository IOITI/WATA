// A stand-in for Vela's workspace in tests (jsdom has no canvas): it records how it
// was built and what was asked of it, as fakeCharts.ts does for Lightweight Charts.

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

export class FakePineWorkerEngine {
  constructor(options: { workerUrl?: string } = {}) {
    enginesMade.push(options)
  }
}

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
}

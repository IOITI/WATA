// The pages' side of the Pine worker (pine.worker.ts): requests as promises, and
// the bars a script asks for through request.security fetched for it.

import type {
  Candle,
  FromWorker,
  RunRequest,
  SeriesQuestion,
  ToWorker,
  WorkerRequest,
  WorkerResponse,
} from './workerProtocol'

/** Fetches the bars of another symbol or timeframe for a script (request.security). */
export type SeriesFetcher = (
  symbol: string,
  timeframe: string,
  window: { from?: number; to?: number; limit?: number },
) => Promise<Candle[]>

export type CompileResponse = Extract<WorkerResponse, { op: 'compile' }> | Extract<WorkerResponse, { ok: false }>
export type RunResponse = Extract<WorkerResponse, { op: 'run' }> | Extract<WorkerResponse, { ok: false }>

/** What the client needs of a worker: the real one, or a stand-in in tests. */
export interface WorkerPort {
  postMessage(message: ToWorker): void
  addEventListener(type: 'message', listener: (event: MessageEvent<FromWorker>) => void): void
  terminate(): void
}

export function startPineWorker(): WorkerPort {
  return new Worker(new URL('./pine.worker.ts', import.meta.url), { type: 'module' }) as unknown as WorkerPort
}

export class PineWorkerClient {
  private readonly worker: WorkerPort
  private readonly pending = new Map<number, (response: WorkerResponse) => void>()
  private readonly fetchers = new Map<number, SeriesFetcher>()
  private nextId = 1

  constructor(worker: WorkerPort = startPineWorker()) {
    this.worker = worker
    worker.addEventListener('message', (event) => this.received(event.data))
  }

  compile(source: string): Promise<CompileResponse> {
    return this.send({ id: this.take(), op: 'compile', source }) as Promise<CompileResponse>
  }

  run(request: Omit<RunRequest, 'id' | 'op'>, fetchSeries: SeriesFetcher): Promise<RunResponse> {
    const id = this.take()
    this.fetchers.set(id, fetchSeries)
    return (this.send({ ...request, id, op: 'run' }) as Promise<RunResponse>).finally(() => this.fetchers.delete(id))
  }

  terminate(): void {
    this.worker.terminate()
    for (const resolve of this.pending.values()) {
      resolve({ id: 0, ok: false, error: { message: 'The Pine worker was stopped', line: null, column: null } })
    }
    this.pending.clear()
  }

  private take(): number {
    const id = this.nextId
    this.nextId += 1
    return id
  }

  private send(request: WorkerRequest): Promise<WorkerResponse> {
    return new Promise((resolve) => {
      this.pending.set(request.id, resolve)
      this.worker.postMessage(request)
    })
  }

  private received(message: FromWorker): void {
    if ('op' in message && message.op === 'fetch') {
      void this.answerFetch(message)
      return
    }
    const response = message as WorkerResponse
    const resolve = this.pending.get(response.id)
    this.pending.delete(response.id)
    resolve?.(response)
  }

  private async answerFetch(question: SeriesQuestion): Promise<void> {
    const fetchSeries = this.fetchers.get(question.id)
    try {
      if (!fetchSeries) throw new Error('No run is waiting for these bars')
      const bars = await fetchSeries(question.symbol, question.timeframe, {
        from: question.from,
        to: question.to,
        limit: question.limit,
      })
      this.worker.postMessage({ op: 'series', fetchId: question.fetchId, bars })
    } catch (error) {
      this.worker.postMessage({
        op: 'series',
        fetchId: question.fetchId,
        error: error instanceof Error ? error.message : String(error),
      })
    }
  }
}

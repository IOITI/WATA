// Vela-pinets runs a chart's scripts in its worker, a whole run each time Vela says
// the bars changed: the Charts page's engine cannot stream (src/features/ta/chart/
// pineEngine.ts), and runs a script on the bars that are over. One more bar over was a
// whole run over all of them again: about 2 s of PineTS for a day of R60 bars (9,500).
//
// This patch keeps, of a whole run, the PineTS that ran it beside its context, and gives
// the next bars to that context as the TA engine continues a rule's run (Runner.continueRun
// in src/features/ta/engine/runnerCore.ts): each new bar appended as PineTS's live mode
// appends one, then run on the context as a whole run runs its last bar. Vela's model is
// made again from the context, as after a whole run, and the alerts and warnings of the new
// bars are passed on.
//
// A whole run remains, as before: at the first run, at new inputs or a new visible range,
// when the bars before the new ones are not those the run had (history loaded again, a gap
// filled), every ten minutes, after a continued run that failed, and for a script the page
// did not mark as one whose run can continue (`wataContinue` on its prepared token, set by
// pineEngine.ts from wholeBecause in engine/wholeRuns.ts: barstate.*, request.*, a strategy...).
//
// Each change replaces one exact piece of the worker's code, and some only pin the names its
// functions have there, which the code added calls. The build stops when one is not there, so
// that a new version of Vela-pinets is looked at before it ships.

export interface WorkerPatch {
  /** What it changes, for the build's message. */
  what: string
  find: string
  replace: string
}

/** A whole run at least this often (ms), whatever continued it meanwhile: the engine's CHECK_SECONDS. */
export const WHOLE_EVERY_MS = 600_000

// What the worker calls its functions, pinned by the pieces below: Au (Vela's bars as PineTS's candles), Jc and
// ny (whether a context makes a model, and the model), ay and oy (an alert and a warning as Vela takes them),
// Je (a message to the page), hD (a whole run of a session, which says it is done).
const ADDED =
  `var __wataEvery=${WHOLE_EVERY_MS};` +
  'function __wataSame(a,b){return!!a&&!!b&&a.time===b.time&&a.open===b.open&&a.high===b.high&&a.low===b.low&&' +
  'a.close===b.close&&(a.volume??0)===(b.volume??0)}' +
  // The bars of a session continue its held run: more of them, after the very bars the run had.
  'function __wataContinues(t){let h=t.held;return!!h&&!t.stopped&&t.prepared?.token?.wataContinue===!0&&' +
  'Date.now()-h.at<__wataEvery&&t.bars.length>h.count&&__wataSame(t.bars[0],h.first)&&__wataSame(t.bars[h.count-1],h.last)}' +
  'async function __wataContinue(t){let h=t.held,b=t.bars;try{' +
  'let x=h.pine,f=t.lastCtx,c=Au(b.slice(h.count),t.market.timeframe,t.market.symbolInfo),a=x.data.length,' +
  'ka=(f.alerts??[]).length,kw=(f.warnings??[]).length;' +
  'for(let e of c){let m=f.marketData;Array.isArray(m)&&m!==x.data&&m.push(e),x._appendCandle(e)}' +
  'f.length=x.data.length;await x._executeIterations(f,x._transpiledCode,a,x.data.length);' +
  'h.count=b.length,h.last=b[b.length-1],py.__wataContinued=(py.__wataContinued??0)+1;if(t.stopped)return;' +
  'for(let r of(f.alerts??[]).slice(ka))Je({kind:"alert",sessionId:t.id,alert:ay(r)});' +
  'for(let r of(f.warnings??[]).slice(kw))Je({kind:"warning",sessionId:t.id,warning:oy(r)});' +
  'let e=t.prepared.token,o=Jc(f)?ny(f,e.instanceId,t.prepared,t.inputs,t.props??{},b[0]?.time):null;' +
  'o&&Je({kind:"model",sessionId:t.id,model:o}),Je({kind:"done",sessionId:t.id})' +
  // Part of the new bars may have run: what was held is let go, and the bars run whole.
  '}catch{t.held=null,await hD(t)}}' +
  'function __wataRun(t){t.chain=t.chain.then(()=>__wataContinues(t)?__wataContinue(t):hD(t))}'

export const CONTINUED_RUN_PATCHES: WorkerPatch[] = [
  {
    what: 'the names of the worker this patch calls (its post function and its sessions)',
    find: 'var py=self,Je=t=>py.postMessage(t),ur=new Map,',
    replace: 'var py=self,Je=t=>py.postMessage(t),ur=new Map,',
  },
  {
    what: 'the name of the function that makes candles of Vela\'s bars',
    find: 'let h=Au(n,r.timeframe,r.symbolInfo),',
    replace: 'let h=Au(n,r.timeframe,r.symbolInfo),',
  },
  {
    // The names of the model and of an alert and a warning are pinned here too.
    what: 'a whole run hands back the PineTS that ran it',
    find:
      'model:Jc(f)?ny(f,a,s,o,l??{},n[0]?.time):null,alerts:(f.alerts??[]).map(ay),warnings:(f.warnings??[]).map(oy),' +
      'reactsToViewport:m,ctx:f}}function ny(',
    replace:
      'model:Jc(f)?ny(f,a,s,o,l??{},n[0]?.time):null,alerts:(f.alerts??[]).map(ay),warnings:(f.warnings??[]).map(oy),' +
      'reactsToViewport:m,ctx:f,pine:d}}function ny(',
  },
  {
    what: 'a whole run is made on the bars of its start, which a message may replace meanwhile',
    find: 'async function hD(t){if(!t.stopped)try{let e=t.prepared.token,n=await ty({ind:Eu(t.cache,e.source,t.inputs,t.props),bars:t.bars,',
    replace:
      'async function hD(t){if(!t.stopped)try{let e=t.prepared.token,__b=t.bars,n=await ty({ind:Eu(t.cache,e.source,t.inputs,t.props),bars:__b,',
  },
  {
    what: 'a session keeps the run its next bars may continue',
    find: 't.lastCtx=n.ctx,n.model&&Je({kind:"model",sessionId:t.id,model:n.model}),Je({kind:"done",sessionId:t.id})',
    replace:
      't.lastCtx=n.ctx,t.held=n.pine?{pine:n.pine,count:__b.length,first:__b[0],last:__b[__b.length-1],at:Date.now()}:null,' +
      'n.model&&Je({kind:"model",sessionId:t.id,model:n.model}),Je({kind:"done",sessionId:t.id})',
  },
  {
    what: 'the bars a chart sends continue its run when they can',
    find: 'case"notifyBars":{let n=ur.get(e.sessionId);if(!n||n.mode==="live")return;n.bars=e.bars,n.deferred=!1,eo(n);return}',
    replace: 'case"notifyBars":{let n=ur.get(e.sessionId);if(!n||n.mode==="live")return;n.bars=e.bars,n.deferred=!1,__wataRun(n);return}',
  },
  {
    what: 'where a whole run is asked for, the functions a continued run is made with',
    find: 'function eo(t){t.chain=t.chain.then(()=>hD(t))}',
    replace: `${ADDED}function eo(t){t.chain=t.chain.then(()=>hD(t))}`,
  },
]

function occurrences(code: string, piece: string): number {
  let count = 0
  for (let at = code.indexOf(piece); at !== -1; at = code.indexOf(piece, at + piece.length)) count += 1
  return count
}

/** Vela-pinets' worker with every change made; throws naming the first change whose code is not there exactly once. */
export function patchContinuedRuns(worker: string, patches: WorkerPatch[] = CONTINUED_RUN_PATCHES): string {
  let patched = worker
  for (const patch of patches) {
    const found = occurrences(patched, patch.find)
    if (found !== 1) {
      throw new Error(
        `@luxalgo/vela-pinets changed: "${patch.what}" expects its code once in the worker, found ${found} times. ` +
          'Without it, the Charts page runs a script whole at each bar (see admin_ui/vite/velaContinuedRuns.ts).',
      )
    }
    patched = patched.replace(patch.find, () => patch.replace)
  }
  return patched
}

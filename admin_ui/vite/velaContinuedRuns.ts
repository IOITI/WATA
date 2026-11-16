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

// What the worker calls its functions, pinned by the pieces below: $h (Vela's bars as PineTS's candles), oh and
// C0 (whether a context makes a model, and the model), D0 and O0 (an alert and a warning as Vela takes them),
// q0 (the worker), st (a message to the page), u5 (a whole run of a session, which says it is done). Each build
// of Vela-pinets names them anew: 0.2.17's were Au, Jc, ny, ay, oy, py, Je and hD.
const ADDED =
  `var __wataEvery=${WHOLE_EVERY_MS};` +
  'function __wataSame(a,b){return!!a&&!!b&&a.time===b.time&&a.open===b.open&&a.high===b.high&&a.low===b.low&&' +
  'a.close===b.close&&(a.volume??0)===(b.volume??0)}' +
  // The bars of a session continue its held run: more of them, after the very bars the run had.
  'function __wataContinues(t){let h=t.held;return!!h&&!t.stopped&&t.prepared?.token?.wataContinue===!0&&' +
  'Date.now()-h.at<__wataEvery&&t.bars.length>h.count&&__wataSame(t.bars[0],h.first)&&__wataSame(t.bars[h.count-1],h.last)}' +
  'async function __wataContinue(t){let h=t.held,b=t.bars;try{' +
  'let x=h.pine,f=t.lastCtx,c=$h(b.slice(h.count),t.market.timeframe,t.market.symbolInfo),a=x.data.length,' +
  'ka=(f.alerts??[]).length,kw=(f.warnings??[]).length;' +
  'for(let e of c){let m=f.marketData;Array.isArray(m)&&m!==x.data&&m.push(e),x._appendCandle(e)}' +
  'f.length=x.data.length;await x._executeIterations(f,x._transpiledCode,a,x.data.length);' +
  'h.count=b.length,h.last=b[b.length-1],q0.__wataContinued=(q0.__wataContinued??0)+1;if(t.stopped)return;' +
  'for(let r of(f.alerts??[]).slice(ka))st({kind:"alert",sessionId:t.id,alert:D0(r)});' +
  'for(let r of(f.warnings??[]).slice(kw))st({kind:"warning",sessionId:t.id,warning:O0(r)});' +
  'let e=t.prepared.token,o=oh(f)?C0(f,e.instanceId,t.prepared,t.inputs,t.props??{},b[0]?.time):null;' +
  'o&&st({kind:"model",sessionId:t.id,model:o}),st({kind:"done",sessionId:t.id})' +
  // Part of the new bars may have run: what was held is let go, and the bars run whole.
  '}catch{t.held=null,await u5(t)}}' +
  'function __wataRun(t){t.chain=t.chain.then(()=>__wataContinues(t)?__wataContinue(t):u5(t))}'

export const CONTINUED_RUN_PATCHES: WorkerPatch[] = [
  {
    what: 'the names of the worker this patch calls (the worker and its post function)',
    find: 'var q0=self,st=t=>q0.postMessage(t);',
    replace: 'var q0=self,st=t=>q0.postMessage(t);',
  },
  {
    what: 'the name of the function that makes candles of Vela\'s bars',
    find: 'let h=$h(n,r.timeframe,r.symbolInfo),',
    replace: 'let h=$h(n,r.timeframe,r.symbolInfo),',
  },
  {
    // The names of the model and of an alert and a warning are pinned here too.
    what: 'a whole run hands back the PineTS that ran it',
    find:
      'model:oh(m)?C0(m,a,s,o,l??{},n[0]?.time):null,alerts:(m.alerts??[]).map(D0),warnings:(m.warnings??[]).map(O0),' +
      'reactsToViewport:y,ctx:m}}function C0(',
    replace:
      'model:oh(m)?C0(m,a,s,o,l??{},n[0]?.time):null,alerts:(m.alerts??[]).map(D0),warnings:(m.warnings??[]).map(O0),' +
      'reactsToViewport:y,ctx:m,pine:d}}function C0(',
  },
  {
    what: 'a whole run is made on the bars of its start, which a message may replace meanwhile',
    find: 'async function u5(t){if(!t.stopped)try{let e=t.prepared.token,n=await T0({ind:Vh(t.cache,e.source,t.inputs,t.props),bars:t.bars,',
    replace:
      'async function u5(t){if(!t.stopped)try{let e=t.prepared.token,__b=t.bars,n=await T0({ind:Vh(t.cache,e.source,t.inputs,t.props),bars:__b,',
  },
  {
    what: 'a session keeps the run its next bars may continue',
    find: 't.lastCtx=n.ctx,n.model&&st({kind:"model",sessionId:t.id,model:n.model}),st({kind:"done",sessionId:t.id})',
    replace:
      't.lastCtx=n.ctx,t.held=n.pine?{pine:n.pine,count:__b.length,first:__b[0],last:__b[__b.length-1],at:Date.now()}:null,' +
      'n.model&&st({kind:"model",sessionId:t.id,model:n.model}),st({kind:"done",sessionId:t.id})',
  },
  {
    what: 'the bars a chart sends continue its run when they can',
    find: 'case"notifyBars":{let n=Vr.get(e.sessionId);if(!n||n.mode==="live")return;n.bars=e.bars,n.deferred=!1,Zo(n);return}',
    replace: 'case"notifyBars":{let n=Vr.get(e.sessionId);if(!n||n.mode==="live")return;n.bars=e.bars,n.deferred=!1,__wataRun(n);return}',
  },
  {
    what: 'where a whole run is asked for, the functions a continued run is made with',
    find: 'function Zo(t){t.chain=t.chain.then(()=>u5(t))}',
    replace: `${ADDED}function Zo(t){t.chain=t.chain.then(()=>u5(t))}`,
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

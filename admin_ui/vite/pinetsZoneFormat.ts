// PineTS makes a new Intl.DateTimeFormat each time a script reads a time in a time
// zone: hour(time, "Europe/Paris"), minute(), dayofweek() and the others go through
// one function, timestamp("Europe/Paris", ...) through another. Making a formatter
// costs far more than using it: on a script that reads the hour and the minute of
// each bar seven times, it was 58 % of a run (measured 2026-10-21 on 49,107 bars:
// 19.8 s, and 8.1 s with one formatter kept per time zone, every plot and alert the
// same). A formatter never changes once made: this plugin keeps the one made for
// each time zone, where PineTS makes them.
//
// PineTS ships three times in the admin: in the TA engine's runner
// (vite.runner.config.ts, from pinets.min.es.js), in the pages' Pine worker
// (vite.config.ts, `worker.plugins`, from pinets.min.browser.es.js) and in
// Vela-pinets' worker, which carries a copy of its own (vite/velaPineWorker.ts).
// Each change replaces one exact piece of PineTS's code; the build stops when one is
// not there, so that a new version of PineTS is looked at before it ships. The same
// changes go to the dependencies Vite prepares for its dev server (`optimizeDeps`),
// where a plugin's transform never runs.

import type { Plugin } from 'vite'

export interface PinetsPatch {
  /** What it changes, for the build's message. */
  what: string
  find: string
  replace: string
}

/** Named after PineTS's version in the runner's hello ("0.11.1+zone-format"), which a run's made_with keeps. */
export const PINETS_PATCH_TAG = 'zone-format'

// The formatter `make` gives for `key` (what it formats, and its time zone), made once.
const HELPER = '__wataZoneFormat'
export const PINETS_HELPER =
  `\nfunction ${HELPER}(key,make){const kept=${HELPER}.kept||(${HELPER}.kept=new Map);` +
  'let format=kept.get(key);if(format===void 0){format=make();kept.set(key,format)}return format}\n'

// Both are made with the time zone as `e`, in the functions the patches change.
const FIELDS = 'year:"numeric",month:"numeric",day:"numeric",hour:"numeric",minute:"numeric",second:"numeric"'
const OF_A_MOMENT = `new Intl.DateTimeFormat("en-US",{timeZone:e,${FIELDS},weekday:"short",hour12:!1})`
const OF_A_TIMESTAMP = `new Intl.DateTimeFormat("en-US",{timeZone:e,${FIELDS},hour12:!1})`

export const PINETS_ZONE_PATCHES: PinetsPatch[] = [
  {
    what: 'the parts of a moment in a time zone (hour(), minute(), dayofweek()...)',
    find: `${OF_A_MOMENT}.formatToParts(new Date(t))`,
    replace: `${HELPER}("moment "+e,()=>${OF_A_MOMENT}).formatToParts(new Date(t))`,
  },
  {
    what: 'a moment from its date and time in a time zone (timestamp())',
    find: `${OF_A_TIMESTAMP}.formatToParts(l)`,
    replace: `${HELPER}("timestamp "+e,()=>${OF_A_TIMESTAMP}).formatToParts(l)`,
  },
]

function occurrences(code: string, piece: string): number {
  let count = 0
  for (let at = code.indexOf(piece); at !== -1; at = code.indexOf(piece, at + piece.length)) count += 1
  return count
}

/** A build of PineTS with every change made; throws naming the first change whose code is not there exactly once. */
export function patchPinets(code: string, patches: PinetsPatch[] = PINETS_ZONE_PATCHES): string {
  let patched = code
  for (const patch of patches) {
    const found = occurrences(patched, patch.find)
    if (found !== 1) {
      throw new Error(
        `pinets changed: "${patch.what}" expects its code once, found ${found} times. ` +
          'Scripts that read times in a time zone run 2.4 times slower without it (see admin_ui/vite/pinetsZoneFormat.ts).',
      )
    }
    patched = patched.replace(patch.find, () => patch.replace)
  }
  // A function declaration, so that it is there wherever it is read from, at the end of a module or of a script.
  return `${patched}${PINETS_HELPER}`
}

function isPinets(id: string): boolean {
  const path = id.split('?')[0].replaceAll('\\', '/')
  return /\/node_modules\/pinets\/dist\/pinets\.min\.[a-z.]*js$/.test(path)
}

/** The same changes for the dependencies Vite prepares for its dev server (`optimizeDeps.rolldownOptions.plugins`). */
export function pinetsZoneFormatDeps() {
  return {
    name: 'wata-pinets-zone-format-deps',
    transform(code: string, id: string) {
      return isPinets(id) ? { code: patchPinets(code) } : null
    },
  }
}

/** For a bundle that holds PineTS (the runner, the pages' worker): it stops when no build of PineTS came through. */
export function pinetsZoneFormat(): Plugin {
  let building = false
  let patched = 0
  return {
    name: 'wata-pinets-zone-format',
    configResolved(config) {
      building = config.command === 'build'
    },
    buildStart() {
      patched = 0
    },
    transform(code, id) {
      if (!isPinets(id)) return null
      patched += 1
      return { code: patchPinets(code), map: null }
    },
    buildEnd(error) {
      if (building && !error && patched === 0) {
        this.error(
          'pinets changed: no build of PineTS came through this bundle. ' +
            'Scripts that read times in a time zone run 2.4 times slower without it (see admin_ui/vite/pinetsZoneFormat.ts).',
        )
      }
    },
  }
}

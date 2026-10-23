// The editor's checks, run as the script is written: a Pine worker for the page,
// the TradingView reference loaded with the editor, and the libraries read from
// WATA as the script imports them.

import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useState } from 'react'

import { api, isNotFound } from '@/lib/api'

import type { LibraryResolver } from '../engine/prepare'
import { PineWorkerClient } from '../engine/pineWorkerClient'
import { checkScript } from './diagnostics'
import type { CheckResult, Diagnostic } from './diagnostics'
import type { PineFunction } from './pineLanguage'

/** How long the script must stay unchanged before it is checked again. */
export const CHECK_DELAY_MS = 500

/** The page's Pine worker: started with it, stopped when it goes. */
export function usePineWorker(): PineWorkerClient | null {
  const [worker, setWorker] = useState<PineWorkerClient | null>(null)
  useEffect(() => {
    const client = new PineWorkerClient()
    // The worker is an outside system started with the page: the effect is where it belongs.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setWorker(client)
    return () => client.terminate()
  }, [])
  return worker
}

export interface PineReference {
  validate: (source: string) => { line: number; column: number; length?: number; message: string; severity: number }[]
  functions: PineFunction[]
}

/** TradingView's rules and functions, loaded when the editor opens (a megabyte the other pages never need). */
export function usePineReference(): PineReference | null {
  const [reference, setReference] = useState<PineReference | null>(null)
  useEffect(() => {
    let alive = true
    import('pinescript-v6-validator').then(
      (module) => {
        if (!alive) return
        setReference({
          validate: module.validatePineScript,
          functions: Object.values(module.PINE_FUNCTIONS_MERGED).map((fn) => ({ name: fn.name, signature: fn.signature })),
        })
      },
      // Without them the script is still compiled by PineTS.
      () => undefined,
    )
    return () => {
      alive = false
    }
  }, [])
  return reference
}

/** The libraries a script imports, as WATA keeps them: null when there is none of that name. */
export function useLibraryResolver(): LibraryResolver {
  const queryClient = useQueryClient()
  return useCallback<LibraryResolver>(
    async (name) => {
      try {
        const library = await queryClient.fetchQuery({
          queryKey: ['ta-library', name],
          queryFn: () => api.taLibrary(name),
          staleTime: 10_000,
        })
        return { id: library.id, name: library.library_name ?? name, version: library.version, source: library.source }
      } catch (error) {
        if (isNotFound(error)) return null
        throw error
      }
    },
    [queryClient],
  )
}

export interface ScriptCheck {
  result: CheckResult | null
  checking: boolean
  diagnostics: Diagnostic[]
}

/** The checks of `source`, run again once it has stayed unchanged a moment. */
export function useScriptCheck(
  source: string,
  libraryName: string | null,
  worker: PineWorkerClient | null,
  reference: PineReference | null,
): ScriptCheck {
  const resolve = useLibraryResolver()
  // What was checked, and the result: still checking while it is not the text in the editor.
  const key = `${libraryName ?? ''}\u0000${source}`
  const [checked, setChecked] = useState<{ key: string; result: CheckResult } | null>(null)

  useEffect(() => {
    if (!worker) return
    let cancelled = false
    const timer = setTimeout(() => {
      checkScript(source, {
        libraryName,
        resolve,
        compile: (prepared) => worker.compile(prepared),
        validate: reference?.validate,
      }).then(
        (result) => {
          if (!cancelled) setChecked({ key, result })
        },
        (error: unknown) => {
          if (cancelled) return
          const message = error instanceof Error ? error.message : String(error)
          setChecked({
            key,
            result: {
              diagnostics: [{ line: 1, column: 1, severity: 'error', message, source: 'WATA' }],
              prepared: null,
              compiled: null,
            },
          })
        },
      )
    }, CHECK_DELAY_MS)
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [key, source, libraryName, worker, reference, resolve])

  const result = checked?.result ?? null
  return { result, checking: checked?.key !== key, diagnostics: result?.diagnostics ?? [] }
}

import { useQuery } from '@tanstack/react-query'
import type { IPineInput } from 'pinets'
import { useMemo } from 'react'

import { api } from '@/lib/api'

import { useLibraryResolver, usePineWorker } from '../editor/useScriptCheck'
import { scriptOutline } from '../engine/outline'
import type { ScriptOutline } from '../engine/outline'
import { prepareScript } from '../engine/prepare'

export interface ScriptInputs {
  source: string | null
  /** The script's inputs as PineTS reads them; null until read, or when the version does not compile. */
  meta: IPineInput[] | null
  outline: ScriptOutline | null
  pending: boolean
  error: Error | null
}

/** A version of a script, its outline, and its inputs as PineTS reads them in the page's worker. */
export function useScriptInputs(scriptId: string, version: number): ScriptInputs {
  const worker = usePineWorker()
  const resolve = useLibraryResolver()
  const versionQuery = useQuery({
    queryKey: ['ta-script-version', scriptId, version],
    queryFn: () => api.taScriptVersion(scriptId, version),
    enabled: Boolean(scriptId),
    staleTime: Infinity,
  })
  const source = versionQuery.data?.source ?? null
  const compileQuery = useQuery({
    queryKey: ['ta-rule-inputs', scriptId, version, worker !== null],
    enabled: Boolean(worker && source),
    retry: false,
    staleTime: Infinity,
    queryFn: async () => {
      const prepared = await prepareScript(source!, resolve)
      const compiled = await worker!.compile(prepared.source)
      if (!compiled.ok) throw new Error(compiled.error.message)
      return compiled.inputs
    },
  })
  const outline = useMemo(() => (source ? scriptOutline(source) : null), [source])
  return {
    source,
    meta: compileQuery.data ?? null,
    outline,
    pending: Boolean(source) && compileQuery.isPending,
    error: compileQuery.error,
  }
}

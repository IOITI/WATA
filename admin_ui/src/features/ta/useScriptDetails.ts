// A script's star, family and picture, changed from the Indicators list or its page: shown at once wherever the
// scripts are read, then read again from the server (src/admin/script_details.py).

import { useMutation, useQueryClient } from '@tanstack/react-query'
import type { QueryClient } from '@tanstack/react-query'
import { useToast } from 'ioiti-ui'

import { api } from '@/lib/api'
import type { TaScript, TaScriptSummary } from '@/lib/types'

/** A script as the server now gives it, put in the list and in its page's query. */
function shownEverywhere(queryClient: QueryClient, script: TaScript) {
  queryClient.setQueryData<TaScript>(['ta-script', script.id], (before) => (before ? { ...before, ...script } : script))
  queryClient.setQueryData<{ items: TaScriptSummary[] }>(['ta-scripts'], (data) =>
    data ? { ...data, items: data.items.map((item) => (item.id === script.id ? { ...item, ...script } : item)) } : data,
  )
}

const said = (error: unknown) => (error instanceof Error ? error.message : String(error))

export function useFavouriteScripts() {
  const queryClient = useQueryClient()
  const toast = useToast()
  return useMutation({
    mutationFn: ({ ids, favourite }: { ids: string[]; favourite: boolean }) => api.favouriteTaScripts(ids, favourite),
    onMutate: ({ ids, favourite }) => {
      const chosen = new Set(ids)
      queryClient.setQueryData<{ items: TaScriptSummary[] }>(['ta-scripts'], (data) =>
        data ? { ...data, items: data.items.map((item) => (chosen.has(item.id) ? { ...item, favourite } : item)) } : data,
      )
      for (const id of ids) queryClient.setQueryData<TaScript>(['ta-script', id], (script) => (script ? { ...script, favourite } : script))
    },
    onError: (error) => toast({ title: 'The favourites were not changed', description: said(error), variant: 'error' }),
    onSettled: (_answer, _error, { ids }) => {
      void queryClient.invalidateQueries({ queryKey: ['ta-scripts'] })
      for (const id of ids) void queryClient.invalidateQueries({ queryKey: ['ta-script', id] })
    },
  })
}

export function useScriptFamily() {
  const queryClient = useQueryClient()
  const toast = useToast()
  return useMutation({
    mutationFn: ({ id, family }: { id: string; family: string | null }) => api.setTaScriptFamily(id, family),
    onSuccess: (script) => shownEverywhere(queryClient, script),
    onError: (error) => toast({ title: 'The family was not changed', description: said(error), variant: 'error' }),
  })
}

/** A script's picture set (a file chosen, a chart taken) or removed. */
export function useScriptPicture() {
  const queryClient = useQueryClient()
  const toast = useToast()
  const set = useMutation({
    mutationFn: ({ id, picture, name }: { id: string; picture: Blob; name?: string }) => api.setTaScriptPicture(id, picture, name),
    onSuccess: (script) => {
      shownEverywhere(queryClient, script)
      toast({ title: `The picture of ${script.name} was kept`, variant: 'success' })
    },
    onError: (error) => toast({ title: 'The picture was not kept', description: said(error), variant: 'error' }),
  })
  const drop = useMutation({
    mutationFn: (id: string) => api.dropTaScriptPicture(id),
    onSettled: (_answer, _error, id) => {
      void queryClient.invalidateQueries({ queryKey: ['ta-scripts'] })
      void queryClient.invalidateQueries({ queryKey: ['ta-script', id] })
    },
    onError: (error) => toast({ title: 'The picture was not removed', description: said(error), variant: 'error' }),
  })
  return { set, drop }
}

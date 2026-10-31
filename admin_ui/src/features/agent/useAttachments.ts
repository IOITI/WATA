// What is attached in a composer before it is sent: WATA's things, and files uploaded as soon as they are chosen (an
// image scaled first, imageScale.ts), staged by the server until the request that sends them. Five at most in a
// conversation with what it holds already and the page's subject. A file taken away is removed from the server's
// staging; one never sent is dropped there after an hour.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { api } from '@/lib/api'
import type { AgentAttachment, AgentSubject } from '@/lib/types'

import type { Pending, PendingFile } from './attachments'
import { attachmentKey, fileKind, fullSaid, MAX_ATTACHED, MAX_BYTES, refusalOf, toAttach } from './attachments'
import { previewOf, scaledImage } from './imageScale'

export function useAttachments({ fixed, held = [] }: {
  /** The page's subject: the new conversation is about it, it cannot be taken away. */
  fixed?: AgentSubject | null
  /** What the conversation holds already. */
  held?: readonly AgentAttachment[]
} = {}) {
  const [pending, setPending] = useState<Pending[]>([])
  // Files over the limit, left out: said once under the composer.
  const [leftOut, setLeftOut] = useState<string | null>(null)
  // What is attached as an upload ends, or a chip is taken away: read then, not as the composer is drawn.
  const latest = useRef(pending)
  useEffect(() => {
    latest.current = pending
  })
  const counter = useRef(0)

  const keys = useMemo(() => new Set([
    ...(fixed ? [attachmentKey(fixed)] : []),
    ...held.map((entry) => attachmentKey(entry)),
    ...pending.flatMap((entry) => (entry.kind === 'item' ? [attachmentKey(entry.subject)] : [])),
  ]), [fixed, held, pending])
  const count = (fixed && !held.some((entry) => attachmentKey(entry) === attachmentKey(fixed)) ? 1 : 0) + held.length + pending.length
  const room = Math.max(0, MAX_ATTACHED - count)

  const change = useCallback((key: string, update: Partial<PendingFile>) => {
    setPending((current) => current.map((entry) => (entry.key === key && entry.kind === 'file' ? { ...entry, ...update } : entry)))
  }, [])

  const upload = useCallback(async (key: string, file: File, type: NonNullable<PendingFile['type']>) => {
    try {
      const blob = type === 'image' ? await scaledImage(file) : file
      const refused = refusalOf(type, blob.size)
      if (refused) {
        change(key, { state: 'error', error: refused })
        return
      }
      const preview = type === 'image' ? await previewOf(blob) : undefined
      const staged = await api.uploadAgentFile(blob, file.name)
      if (!latest.current.some((entry) => entry.key === key)) {
        // Taken away while it was uploaded: not kept waiting on the server either.
        void api.removeAgentFile(staged.id).catch(() => undefined)
        return
      }
      change(key, { state: 'ready', file: staged, size: staged.size, preview })
    } catch (error) {
      change(key, { state: 'error', error: error instanceof Error ? error.message : 'It could not be uploaded' })
    }
  }, [change])

  const addItem = useCallback((subject: AgentSubject) => {
    if (keys.has(attachmentKey(subject)) || room <= 0) return
    setLeftOut(null)
    setPending((current) => [...current, { key: `item-${attachmentKey(subject)}`, kind: 'item', subject }])
  }, [keys, room])

  const addFiles = useCallback((files: readonly File[]) => {
    const taken = files.slice(0, room)
    setLeftOut(files.length > taken.length
      ? `${fullSaid(MAX_ATTACHED)}: ${files.length - taken.length} file${files.length - taken.length === 1 ? '' : 's'} left out`
      : null)
    const added: PendingFile[] = taken.map((file) => {
      const type = fileKind(file.name, file.type)
      // An image is scaled before it is weighed; any other file is weighed as it is.
      const refused = type === 'image' ? refusalOf(type, Math.min(file.size, MAX_BYTES.image)) : refusalOf(type, file.size)
      counter.current += 1
      return { key: `file-${counter.current}`, kind: 'file', name: file.name, size: file.size, type,
        state: refused ? 'error' : 'uploading', error: refused ?? undefined }
    })
    setPending((current) => [...current, ...added])
    added.forEach((entry, index) => {
      if (entry.state === 'uploading' && entry.type) void upload(entry.key, taken[index], entry.type)
    })
  }, [room, upload])

  const remove = useCallback((key: string) => {
    const entry = latest.current.find((found) => found.key === key)
    if (entry?.kind === 'file' && entry.file) void api.removeAgentFile(entry.file.id).catch(() => undefined)
    setLeftOut(null)
    setPending((current) => current.filter((found) => found.key !== key))
  }, [])

  /** Once sent: the files are the conversation's now, nothing to remove. */
  const clear = useCallback(() => {
    setPending([])
    setLeftOut(null)
  }, [])

  return {
    pending, room, keys, leftOut, addItem, addFiles, remove, clear,
    attach: toAttach(pending),
    subjects: [...(fixed ? [fixed] : []), ...pending.flatMap((entry) => (entry.kind === 'item' ? [entry.subject] : []))],
  }
}

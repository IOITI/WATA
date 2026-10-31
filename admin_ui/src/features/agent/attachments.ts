// What the operator attaches to a request (src/admin/agent/service.py MAX_ATTACHED, src/admin/agent/uploads.py):
// WATA's things, named as a conversation's subject is, and files of their computer, uploaded as soon as they are
// chosen and sent with the request. Five at most in a conversation, the page's subject counted. Pure.

import type {
  AgentAttach,
  AgentAttachment,
  AgentConversation,
  AgentFile,
  AgentModel,
  AgentSubject,
} from '@/lib/types'

import type { Preset } from './model'
import { subjectSaid } from './model'

/** What a conversation holds attached at most, the page's subject counted: the server's MAX_ATTACHED. */
export const MAX_ATTACHED = 5
/** What a file may weigh by kind, as the server takes it (src/admin/agent/uploads.py MAX_BYTES). */
export const MAX_BYTES: Record<AgentFile['type'], number> = { image: 5 * 1024 * 1024, pdf: 5 * 1024 * 1024, text: 200 * 1024 }
/** What the file chooser offers: what the server takes. */
export const ACCEPT = 'image/png,image/jpeg,image/gif,image/webp,application/pdf,.pdf,.csv,.tsv,.pine,.txt,.json,.md'
const TEXT_SUFFIXES = ['.csv', '.tsv', '.pine', '.txt', '.json', '.md']
const KIND_SAID: Record<AgentFile['type'], string> = { image: 'An image', pdf: 'A PDF', text: 'A text file' }

/** Something attached in the composer, before it is sent: a thing of WATA's, or a file and where its upload is. */
export type Pending =
  | { key: string; kind: 'item'; subject: AgentSubject }
  | {
      key: string
      kind: 'file'
      name: string
      size: number
      type: AgentFile['type'] | null
      state: 'uploading' | 'ready' | 'error'
      /** What the server staged: its id, its size once kept (a text cleaned), the values masked. */
      file?: AgentFile
      /** A small picture of an image, as a data: address (the pages' CSP allows them). */
      preview?: string
      error?: string
    }

export type PendingFile = Extract<Pending, { kind: 'file' }>

/** What tells two things attached apart, as the server does: a script's versions, an incident's moments. */
export function attachmentKey(entry: { kind: string; id: string; version?: number | null; at?: string | null }): string {
  const extra = entry.version ? `@v${entry.version}` : entry.at ? `@${entry.at}` : ''
  return `${entry.kind}:${entry.id}${extra}`
}

/** What a request is sent to attach: the things, and the files once the server staged them. */
export function toAttach(pending: readonly Pending[]): AgentAttach[] {
  return pending.flatMap((entry): AgentAttach[] => {
    if (entry.kind === 'file') return entry.state === 'ready' && entry.file ? [{ kind: 'file', id: entry.file.id }] : []
    const { kind, id, version, day, at } = entry.subject
    return [{ kind, id, version: version ?? undefined, day: day ?? undefined, at: at ?? undefined }]
  })
}

/** What a conversation holds attached already: each request's things and files, its subject alone for one of before. */
export function heldBy(conversation: AgentConversation | null | undefined): AgentAttachment[] {
  return (conversation?.requests ?? []).flatMap((request) => request.attached ?? (request.shared ? [request.shared] : []))
}

/** What a file is, from its name and type, as the server will know it; null when it is none it takes. */
export function fileKind(name: string, type: string): AgentFile['type'] | null {
  const lower = name.toLowerCase()
  if (/^image\/(png|jpeg|gif|webp)$/.test(type)) return 'image'
  if (type === 'application/pdf' || lower.endsWith('.pdf')) return 'pdf'
  if (TEXT_SUFFIXES.some((suffix) => lower.endsWith(suffix))) return 'text'
  return null
}

export function sizeText(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} kB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

/** Why a file cannot be attached as it is (its kind, its weight), or null. */
export function refusalOf(type: AgentFile['type'] | null, size: number): string | null {
  if (type === null) return 'Attach an image, a PDF, a CSV, a .pine, .txt, .json or .md file'
  if (size > MAX_BYTES[type]) return `${KIND_SAID[type]} is ${sizeText(MAX_BYTES[type])} at most`
  return null
}

/** What a file's chip says beside its name. */
export function fileDetail(entry: PendingFile): string {
  if (entry.state === 'error') return entry.error ?? 'Could not be taken'
  if (entry.state === 'uploading') return 'Uploading…'
  const size = sizeText(entry.file?.size ?? entry.size)
  const masked = entry.file?.masked ?? 0
  return masked ? `${size} · ${masked} value${masked === 1 ? '' : 's'} masked` : size
}

/** What a chip names: a thing as a conversation's subject is said, a file by its name. */
export function attachmentSaid(entry: AgentAttachment): string {
  return entry.kind === 'file' ? entry.name : subjectSaid(entry)
}

/**
 * Why the request cannot be sent with what is attached: a file still uploading or refused, or a model that
 * cannot read a file the conversation holds; null when it can.
 */
export function sendBlocked(pending: readonly Pending[], model: AgentModel | undefined,
  held: readonly AgentAttachment[] = []): string | null {
  const files = pending.filter((entry): entry is PendingFile => entry.kind === 'file')
  const uploading = files.find((entry) => entry.state === 'uploading')
  if (uploading) return `Waiting for ${uploading.name} to be uploaded`
  const refused = files.find((entry) => entry.state === 'error')
  if (refused) return `${refused.name} could not be taken: remove it to send`
  const types = new Set([
    ...files.flatMap((entry) => (entry.file ? [entry.file.type] : [])),
    ...held.flatMap((entry) => (entry.kind === 'file' ? [entry.type] : [])),
  ])
  if (model?.images === false && types.has('image')) return `${model.label} does not read images: choose another model`
  if (model?.pdfs === false && types.has('pdf')) return `${model.label} does not read PDFs: choose another model`
  return null
}

/** Why nothing more can be attached, or null: five at most in a conversation, what it holds counted. */
export function fullSaid(count: number): string | null {
  return count >= MAX_ATTACHED ? `${MAX_ATTACHED} things attached at most in a conversation` : null
}

/** With two runs or more attached, a request to start from: the one that holds better. */
export function comparePreset(subjects: readonly AgentSubject[]): Preset | null {
  const runs = subjects.filter((subject) => subject.kind === 'run')
  if (runs.length < 2) return null
  return {
    label: 'Compare the runs',
    prompt: 'Which of these runs holds better? Compare their verdict checks, edge, random entries, stress and trades on the days they share, and say what the difference rests on.',
  }
}

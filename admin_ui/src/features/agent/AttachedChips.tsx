// What is attached, as chips (ioiti-ui's AttachmentChip): in a composer before it is sent, the page's subject first
// and kept, each other taken away by its ×; and in a conversation, what each request shared, a thing leading to its
// page and a file to itself (an image shown small).

import { AttachmentChip } from 'ioiti-ui'
import {
  Activity,
  BookOpen,
  CalendarDays,
  ChartCandlestick,
  FileSpreadsheet,
  FileText,
  FlaskConical,
  Image as ImageIcon,
  Radar,
  Radio,
  SquareFunction,
} from 'lucide-react'
import type { ComponentProps, ReactNode } from 'react'

import { agentFileUrl } from '@/lib/api'
import type { AgentAttachment, AgentFile, AgentSubject } from '@/lib/types'

import type { Pending } from './attachments'
import { fileDetail, sizeText } from './attachments'
import { subjectLink, subjectSaid } from './model'

// Each thing with the mark of its page in the menu.
const KIND_ICON: Partial<Record<AgentSubject['kind'], ReactNode>> = {
  run: <FlaskConical />,
  script: <SquareFunction />,
  rule: <Radar />,
  position: <ChartCandlestick />,
  signal: <Radio />,
  day: <CalendarDays />,
  incident: <Activity />,
  luxalgo: <BookOpen />,
}

function fileIcon(name: string, type: AgentFile['type'] | null): ReactNode {
  if (type === 'image') return <ImageIcon />
  if (/\.(csv|tsv)$/i.test(name)) return <FileSpreadsheet />
  if (/\.pine$/i.test(name)) return <SquareFunction />
  return <FileText />
}

/** A file kept with its conversation: opened by the browser itself, never by the router. */
function FileLink(props: ComponentProps<'a'>) {
  return <a {...props} target="_blank" rel="noreferrer" />
}

/** What a composer goes with: the page's subject first (kept), then what was attached. */
export function PendingChips({ fixed, pending, onRemove }: {
  fixed?: AgentSubject | null
  pending: readonly Pending[]
  onRemove: (key: string) => void
}) {
  return (
    <>
      {fixed && <AttachmentChip label={subjectSaid(fixed)} icon={KIND_ICON[fixed.kind]} />}
      {pending.map((entry) => entry.kind === 'item' ? (
        <AttachmentChip key={entry.key} label={subjectSaid(entry.subject)} icon={KIND_ICON[entry.subject.kind]}
          onRemove={() => onRemove(entry.key)} />
      ) : (
        <AttachmentChip key={entry.key} label={entry.name} detail={fileDetail(entry)} icon={fileIcon(entry.name, entry.type)}
          thumbnail={entry.preview} busy={entry.state === 'uploading'} tone={entry.state === 'error' ? 'danger' : 'neutral'}
          onRemove={() => onRemove(entry.key)} />
      ))}
    </>
  )
}

/** What a request shared: each thing leading to its page, each file to itself. */
export function SharedChips({ conversationId, attached }: { conversationId: string; attached: readonly AgentAttachment[] }) {
  return (
    <div className="flex flex-wrap gap-1.5" role="group" aria-label="Shared with the request">
      {attached.map((entry) => entry.kind === 'file' ? (
        <AttachmentChip key={entry.id} label={entry.name} detail={sizeText(entry.size)} icon={fileIcon(entry.name, entry.type)}
          thumbnail={entry.type === 'image' ? agentFileUrl(conversationId, entry.id) : undefined}
          href={agentFileUrl(conversationId, entry.id)} linkComponent={FileLink} />
      ) : (
        <AttachmentChip key={`${entry.kind}:${entry.id}:${entry.version ?? entry.at ?? ''}`} label={subjectSaid(entry)}
          icon={KIND_ICON[entry.kind]} href={subjectLink(entry)} />
      ))}
    </div>
  )
}

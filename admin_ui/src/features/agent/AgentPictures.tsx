// What the agent looked at: the pictures its tools gave in a request (chart_view's day), each kept with the
// conversation and sent the model with the tool's answer. Shown under what it did, in the order of its steps, each
// opening whole in a tab of its own; the day shared at the conversation's start first.

import { agentFileUrl } from '@/lib/api'
import type { AgentPicture } from '@/lib/types'

import { pictureCaption } from './model'

export function AgentPictures({ conversationId, pictures }: { conversationId: string; pictures: AgentPicture[] }) {
  if (pictures.length === 0) return null
  return (
    <section aria-label="What it looked at" className="space-y-2">
      <h3 className="text-xs font-medium text-muted-foreground">What it looked at</h3>
      <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {pictures.map((picture) => {
          const url = agentFileUrl(conversationId, picture.file)
          const caption = pictureCaption(picture)
          return (
            <li key={picture.file}>
              <figure className="glass-card overflow-hidden rounded-xl border border-border">
                <a href={url} target="_blank" rel="noreferrer" aria-label={`${caption}, whole`}>
                  {/* The chart drawn on white, as the server draws it, whatever the theme. */}
                  <img src={url} alt={caption} loading="lazy" width={1280} height={720}
                    className="block h-auto w-full bg-white" />
                </a>
                <figcaption className="px-3 py-2 text-xs text-muted-foreground">{caption}</figcaption>
              </figure>
            </li>
          )
        })}
      </ul>
    </section>
  )
}

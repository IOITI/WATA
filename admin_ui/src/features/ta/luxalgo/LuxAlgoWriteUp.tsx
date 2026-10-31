// LuxAlgo's write-up of an indicator or a concept: its Markdown, drawn by ioiti-ui's AIResponse inside the Panel that
// holds it. AIResponse's article is a card of its own (its surface, its border, and the glass material's, which a
// plain class does not outweigh): here it is drawn without one, so the Panel is the one card.

import { AIResponse, AIResponseContent } from 'ioiti-ui'

import { shownMarkdown } from './luxalgoModel'

const NO_CARD = 'bg-transparent! bg-none! p-0 shadow-none! ring-0 sm:p-0'

export function LuxAlgoWriteUp({ markdown }: { markdown: string }) {
  return (
    <AIResponse content={shownMarkdown(markdown)} className={NO_CARD}>
      <AIResponseContent className="py-0" />
    </AIResponse>
  )
}

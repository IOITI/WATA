// LuxAlgo's write-up of an indicator or a concept, as its Library's pages draw it (shownMarkdown): its own title,
// which the page's heading says, and its pictures left out, its links to the Library's pages WATA has led there.

import { WriteUp } from '../WriteUp'
import { shownMarkdown } from './luxalgoModel'

export function LuxAlgoWriteUp({ markdown }: { markdown: string }) {
  return <WriteUp markdown={shownMarkdown(markdown)} />
}

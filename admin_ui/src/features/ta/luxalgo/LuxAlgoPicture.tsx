// LuxAlgo's preview picture of an indicator, loaded by the browser from LuxAlgo's image host (the one other origin
// the admin's policy lets a picture come from), or a quiet stand-in tinted by its family when LuxAlgo names none or
// the picture does not load.

import { cn } from 'ioiti-ui'
import { ChartCandlestick } from 'lucide-react'
import { useState } from 'react'

import { familyTint, previewImage } from './luxalgoModel'

export function LuxAlgoPicture({ url, name, family, className }: {
  url?: string | null
  name: string
  family?: string | null
  className?: string
}) {
  const src = previewImage(url)
  const [failed, setFailed] = useState<string | null>(null)
  if (!src || failed === src) {
    return (
      <div aria-hidden className={cn('flex aspect-video w-full items-center justify-center', className)} style={{ background: familyTint(family) }}>
        <ChartCandlestick className="size-8 text-muted-foreground opacity-60" />
      </div>
    )
  }
  return (
    <img
      src={src}
      alt={`LuxAlgo's picture of ${name}`}
      loading="lazy"
      decoding="async"
      onError={() => setFailed(src)}
      className={cn('aspect-video w-full object-cover', className)}
    />
  )
}

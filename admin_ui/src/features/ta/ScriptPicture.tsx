// A script's picture: its own (from the admin), else LuxAlgo's (from its image host), else a quiet stand-in tinted by
// its family, as LuxAlgo's Library draws its own (LuxAlgoPicture). And how the Indicators list takes a new one: a file
// chosen, or an image dropped, on the table's thumbnail or a card's corner button.

import { Button, cn, Hint } from 'ioiti-ui'
import { ChartCandlestick, ImagePlus, LoaderCircle } from 'lucide-react'
import { useRef, useState } from 'react'
import type { ChangeEvent, DragEvent, ReactNode } from 'react'

import type { TaScriptSummary } from '@/lib/types'

import { pictureSource } from './indicatorsModel'
import { familyTint } from './luxalgo/luxalgoModel'
import { chosenPicture, PICTURE_TYPES } from './pictureFile'
import { useScriptPicture } from './useScriptDetails'

type Pictured = Pick<TaScriptSummary, 'id' | 'name' | 'family' | 'picture' | 'own_picture'>

export function ScriptPicture({ script, className }: { script: Pictured; className?: string }) {
  const src = pictureSource(script)
  const [failed, setFailed] = useState<string | null>(null)
  if (!src || failed === src) {
    return (
      <div aria-hidden className={cn('flex aspect-video w-full items-center justify-center', className)} style={{ background: familyTint(script.family || script.name) }}>
        <ChartCandlestick className="size-1/4 max-h-8 max-w-8 text-muted-foreground opacity-60" />
      </div>
    )
  }
  return (
    <img
      src={src}
      alt={`The picture of ${script.name}`}
      loading="lazy"
      decoding="async"
      onError={() => setFailed(src)}
      className={cn('aspect-video w-full object-cover', className)}
    />
  )
}

/** Choosing a script's picture: a hidden file input opened by its control, an image dropped on it, the upload. */
function usePictureChooser(script: Pictured) {
  const input = useRef<HTMLInputElement>(null)
  const [over, setOver] = useState(false)
  const [refused, setRefused] = useState<string | null>(null)
  const { set } = useScriptPicture()
  const take = async (file: File | undefined) => {
    if (!file) return
    setRefused(null)
    try {
      set.mutate({ id: script.id, picture: await chosenPicture(file), name: file.name })
    } catch (error) {
      setRefused(error instanceof Error ? error.message : String(error))
    }
  }
  const label = script.picture && script.own_picture ? `Change the picture of ${script.name}` : `Add a picture of ${script.name}`
  return {
    label,
    hint: refused ?? `${label}: choose an image, or drop one here (PNG, JPEG or WebP)`,
    refused,
    over,
    busy: set.isPending,
    choose: () => input.current?.click(),
    drop: {
      onDragOver: (event: DragEvent) => {
        event.preventDefault()
        setOver(true)
      },
      onDragLeave: () => setOver(false),
      onDrop: (event: DragEvent) => {
        event.preventDefault()
        setOver(false)
        void take([...event.dataTransfer.files].find((file) => file.type.startsWith('image/')))
      },
    },
    input: (
      <input
        ref={input}
        type="file"
        accept={PICTURE_TYPES.join(',')}
        className="hidden"
        tabIndex={-1}
        aria-hidden
        onChange={(event: ChangeEvent<HTMLInputElement>) => {
          const file = event.target.files?.[0]
          event.target.value = ''
          void take(file)
        }}
      />
    ) as ReactNode,
  }
}

/** The table's thumbnail: the picture as a button that takes a new one. Its size and place are its wrapper's (`className`). */
export function PictureButton({ script, className }: { script: Pictured; className?: string }) {
  const chooser = usePictureChooser(script)
  return (
    <>
      <Hint content={chooser.hint} className={className}>
        <button
          type="button"
          aria-label={chooser.label}
          aria-busy={chooser.busy}
          onClick={chooser.choose}
          {...chooser.drop}
          className={cn(
            'group relative block w-full overflow-hidden rounded-md border border-border focus-ring',
            chooser.over && 'ring-2 ring-brand',
            chooser.refused && 'border-negative',
          )}
        >
          <ScriptPicture script={script} />
          <span className="absolute inset-0 grid place-items-center bg-black/45 text-white opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100">
            {chooser.busy ? <LoaderCircle className="size-4 animate-spin" aria-hidden /> : <ImagePlus className="size-4" aria-hidden />}
          </span>
        </button>
      </Hint>
      {chooser.input}
    </>
  )
}

/** A card's corner button that takes a new picture, the picture itself leading to the script. Placed by `className`. */
export function PictureChooser({ script, className }: { script: Pictured; className?: string }) {
  const chooser = usePictureChooser(script)
  return (
    <div className={className}>
      <Hint content={chooser.hint}>
        <Button
          variant="outline"
          size="icon"
          aria-label={chooser.label}
          isLoading={chooser.busy}
          onClick={chooser.choose}
          {...chooser.drop}
          className={cn('size-8 bg-surface/90 backdrop-blur', chooser.over && 'ring-2 ring-brand')}
        >
          <ImagePlus className="size-4" aria-hidden />
        </Button>
      </Hint>
      {chooser.input}
    </div>
  )
}

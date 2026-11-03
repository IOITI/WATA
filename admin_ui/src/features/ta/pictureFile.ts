// A script's picture as it is uploaded: scaled in the browser to 1280 px on its long edge (enough for a card drawn
// twice as large on a phone), a chart taken as it is on its ground, and within the 2 MB the server keeps of a picture
// (MAX_PICTURE_BYTES of src/admin/script_details.py): a PNG stays one unless it is larger, then it becomes a JPEG.

export const PICTURE_EDGE = 1280
export const MAX_PICTURE_BYTES = 2 * 1024 * 1024
export const PICTURE_TYPES = ['image/png', 'image/jpeg', 'image/webp']

/** The size a picture is kept at, its proportions kept; null when it fits already. */
export function pictureSize(width: number, height: number): { width: number; height: number } | null {
  if (width <= 0 || height <= 0) return null
  const scale = Math.min(1, PICTURE_EDGE / Math.max(width, height))
  if (scale >= 1) return null
  return { width: Math.max(1, Math.round(width * scale)), height: Math.max(1, Math.round(height * scale)) }
}

function encoded(canvas: HTMLCanvasElement, type: string, quality?: number): Promise<Blob | null> {
  return new Promise((resolve) => {
    try {
      canvas.toBlob(resolve, type, quality)
    } catch {
      resolve(null)
    }
  })
}

/** ``source`` drawn at its kept size, then encoded as ``type`` when that fits the server's limit, else as a JPEG. */
async function kept(source: CanvasImageSource, width: number, height: number, type: string): Promise<Blob | null> {
  const size = pictureSize(width, height) ?? { width, height }
  const canvas = document.createElement('canvas')
  canvas.width = size.width
  canvas.height = size.height
  const context = canvas.getContext?.('2d')
  if (!context) return null
  context.drawImage(source, 0, 0, size.width, size.height)
  const blob = await encoded(canvas, type, type === 'image/png' ? undefined : 0.9)
  if (blob && blob.size <= MAX_PICTURE_BYTES) return blob
  return encoded(canvas, 'image/jpeg', 0.85)
}

/** A chart as it is on screen (its canvas, its ground filled by Vela), as the picture kept. */
export async function chartPicture(canvas: HTMLCanvasElement): Promise<Blob | null> {
  // Where no canvas can be drawn on (a test's), the chart's own as it is.
  return (await kept(canvas, canvas.width, canvas.height, 'image/png')) ?? encoded(canvas, 'image/png')
}

/** A picture chosen from the computer, scaled when it is larger than kept; refused when it is no PNG, JPEG or WebP. */
export async function chosenPicture(file: File): Promise<Blob> {
  if (!PICTURE_TYPES.includes(file.type)) throw new Error('A picture is a PNG, JPEG or WebP image')
  if (typeof createImageBitmap !== 'function') return file
  let bitmap: ImageBitmap
  try {
    bitmap = await createImageBitmap(file)
  } catch {
    throw new Error('This image could not be read')
  }
  try {
    if (!pictureSize(bitmap.width, bitmap.height) && file.size <= MAX_PICTURE_BYTES) return file
    return (await kept(bitmap, bitmap.width, bitmap.height, file.type)) ?? file
  } finally {
    bitmap.close()
  }
}

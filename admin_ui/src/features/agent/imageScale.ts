// An image attached is scaled in the browser to the largest the Claude API reads before it is uploaded: 2576 px on
// its long edge and 3.75 megapixels at most (the high-resolution tier of the models the agent offers, 2026-10). A
// larger one is read at that size anyway, and would only cost its upload and its bytes in each request. A screenshot
// stays a PNG unless that is more than the API takes of an image (5 MB), then it becomes a JPEG.

export const MAX_EDGE = 2576
export const MAX_PIXELS = 3_750_000
const MAX_IMAGE_BYTES = 5 * 1024 * 1024
const PREVIEW_EDGE = 48

/** The size an image is sent at, its proportions kept; null when it fits already. */
export function scaledSize(width: number, height: number): { width: number; height: number } | null {
  if (width <= 0 || height <= 0) return null
  const scale = Math.min(1, MAX_EDGE / Math.max(width, height), Math.sqrt(MAX_PIXELS / (width * height)))
  if (scale >= 1) return null
  return { width: Math.max(1, Math.floor(width * scale)), height: Math.max(1, Math.floor(height * scale)) }
}

async function bitmapOf(file: Blob): Promise<ImageBitmap | null> {
  if (typeof createImageBitmap !== 'function') return null
  try {
    return await createImageBitmap(file)
  } catch {
    return null
  }
}

function encoded(canvas: HTMLCanvasElement, type: string, quality?: number): Promise<Blob | null> {
  return new Promise((resolve) => canvas.toBlob(resolve, type, quality))
}

/** The image as it is uploaded: scaled to the API's largest when it is larger, else as it was chosen. */
export async function scaledImage(file: File): Promise<Blob> {
  const bitmap = await bitmapOf(file)
  if (!bitmap) return file
  const size = scaledSize(bitmap.width, bitmap.height)
  if (!size) {
    bitmap.close()
    return file
  }
  const canvas = document.createElement('canvas')
  canvas.width = size.width
  canvas.height = size.height
  canvas.getContext('2d')?.drawImage(bitmap, 0, 0, size.width, size.height)
  bitmap.close()
  const type = file.type === 'image/jpeg' || file.type === 'image/webp' ? file.type : 'image/png'
  const blob = await encoded(canvas, type, type === 'image/png' ? undefined : 0.92)
  if (blob && blob.size <= MAX_IMAGE_BYTES) return blob
  return (await encoded(canvas, 'image/jpeg', 0.9)) ?? file
}

/** A small picture of an image for its chip, as a data: address; none where it cannot be drawn. */
export async function previewOf(image: Blob): Promise<string | undefined> {
  const bitmap = await bitmapOf(image)
  if (!bitmap) return undefined
  const scale = Math.min(1, PREVIEW_EDGE / Math.max(bitmap.width, bitmap.height))
  const canvas = document.createElement('canvas')
  canvas.width = Math.max(1, Math.round(bitmap.width * scale))
  canvas.height = Math.max(1, Math.round(bitmap.height * scale))
  canvas.getContext('2d')?.drawImage(bitmap, 0, 0, canvas.width, canvas.height)
  bitmap.close()
  try {
    return canvas.toDataURL('image/png')
  } catch {
    return undefined
  }
}

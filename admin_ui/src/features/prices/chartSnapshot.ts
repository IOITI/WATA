// The chart on screen as a picture, for a question asked of the AI agent from the page it is on (a run's day):
// taken at once, while the chart has the size the operator sees (the agent's sheet narrows the page), then made
// a PNG file the composer attaches as any other. Drawn on the ground it stands on, a chart with a transparent
// background (Lightweight Charts') would be its text alone on the agent's side.

/** What a chart gives of itself now: a canvas of what it shows, or null while it has nothing to show. */
export type ChartSnapshot = () => HTMLCanvasElement | null

/** The colour behind an element: its own background, else the nearest of its parents', else white. */
export function groundOf(element: Element | null): string {
  for (let node: Element | null = element; node; node = node.parentElement) {
    const colour = getComputedStyle(node).backgroundColor
    if (colour && colour !== 'transparent' && !/^rgba\(.*,\s*0\)$/.test(colour)) return colour
  }
  return '#ffffff'
}

/** ``canvas`` drawn on ``colour``; itself where no canvas can be drawn on (a test's). */
export function onGround(canvas: HTMLCanvasElement, colour: string): HTMLCanvasElement {
  const filled = document.createElement('canvas')
  filled.width = canvas.width
  filled.height = canvas.height
  const context = filled.getContext?.('2d')
  if (!context) return canvas
  context.fillStyle = colour
  context.fillRect(0, 0, filled.width, filled.height)
  context.drawImage(canvas, 0, 0)
  return filled
}

/** A canvas as a PNG file named after what it shows; null when the browser could not make one. */
export function canvasFile(canvas: HTMLCanvasElement, name: string): Promise<File | null> {
  return new Promise((resolve) => {
    try {
      canvas.toBlob((blob) => resolve(blob ? new File([blob], name, { type: 'image/png' }) : null), 'image/png')
    } catch {
      resolve(null)
    }
  })
}

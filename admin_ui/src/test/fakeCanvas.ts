// A canvas the chart fakes give as their picture: jsdom draws on none, nor makes a PNG of one. Its PNG is the
// words it was made with, so that a test can tell which chart gave it.

export function fakeCanvas(content: string): HTMLCanvasElement {
  return {
    width: 1200,
    height: 448,
    toBlob(callback: BlobCallback, type?: string) {
      callback(new Blob([content], { type: type ?? 'image/png' }))
    },
  } as unknown as HTMLCanvasElement
}

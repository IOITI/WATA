/**
 * .pine files imported as scripts of the Indicators page, from its button or dropped on it. Several at once are
 * imported libraries first, so that a script importing one of them finds it there.
 */

import { api } from '@/lib/api'
import type { TaScript } from '@/lib/types'

import { declaredKind, declaredTitle } from './templates'

/** The most a file may weigh: a script is text, and a few thousand lines at most. */
export const MAX_SCRIPT_BYTES = 64 * 1024

/** The name a file's script is imported under: the one it declares, else the file's, without .pine or .txt. */
export function importedName(source: string, fileName: string): string {
  return declaredTitle(source) ?? fileName.replace(/\.(pine|txt)$/i, '')
}

/** The files, libraries first, each kind in the order given. */
export async function librariesFirst(files: File[]): Promise<File[]> {
  const kinds = await Promise.all(files.map(async (file) => declaredKind(await file.text())))
  return files
    .map((file, index) => ({ file, index, library: kinds[index] === 'library' }))
    .sort((first, second) => Number(second.library) - Number(first.library) || first.index - second.index)
    .map(({ file }) => file)
}

export async function importScript(file: File): Promise<TaScript> {
  const source = await file.text()
  return api.createTaScript({ name: importedName(source, file.name), source })
}

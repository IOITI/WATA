// The Pine editor: CodeMirror, with Pine's highlighting, the marks of the checks
// (diagnostics.ts) in the margin and under the words, and completion of the
// built-in functions. CodeMirror rather than Monaco: it works on a phone.

import { autocompletion, closeBrackets, closeBracketsKeymap, completionKeymap } from '@codemirror/autocomplete'
import { defaultKeymap, history, historyKeymap, indentWithTab } from '@codemirror/commands'
import { bracketMatching, indentOnInput, indentUnit, syntaxHighlighting } from '@codemirror/language'
import { lintGutter, setDiagnostics } from '@codemirror/lint'
import { highlightSelectionMatches, searchKeymap } from '@codemirror/search'
import { Compartment, EditorState } from '@codemirror/state'
import {
  drawSelection,
  EditorView,
  highlightActiveLine,
  highlightActiveLineGutter,
  keymap,
  lineNumbers,
} from '@codemirror/view'
import { cn } from 'ioiti-ui'
import { useEffect, useRef } from 'react'

import type { Diagnostic } from './diagnostics'
import { toCodeMirror } from './marks'
import { pineCompletions, pineHighlight, pineLanguage } from './pineLanguage'
import type { PineFunction } from './pineLanguage'

export interface PineEditorProps {
  value: string
  onChange: (value: string) => void
  diagnostics: Diagnostic[]
  /** Named for screen readers. */
  label: string
  readOnly?: boolean
  /** Ctrl+S / Cmd+S. */
  onSave?: () => void
  /** The built-in functions to complete (the TradingView reference), when loaded. */
  functions?: PineFunction[]
  className?: string
}

const theme = EditorView.theme({
  '&': {
    height: '100%',
    // The wrapper chooses the size: 16 px on a phone, where Safari zooms on a smaller one.
    fontSize: 'inherit',
    color: 'var(--text-primary)',
    backgroundColor: 'var(--surface)',
  },
  '.cm-scroller': { fontFamily: 'var(--font-mono)', lineHeight: '1.6' },
  '.cm-content': { caretColor: 'var(--text-primary)', paddingBlock: '8px' },
  '.cm-cursor, .cm-dropCursor': { borderLeftColor: 'var(--text-primary)' },
  '&.cm-focused': { outline: 'none' },
  '.cm-gutters': {
    backgroundColor: 'var(--bg)',
    color: 'var(--text-muted)',
    borderRight: '1px solid var(--border)',
  },
  '.cm-activeLine': { backgroundColor: 'var(--accent-subtle)' },
  '.cm-activeLineGutter': { backgroundColor: 'var(--accent-subtle)', color: 'var(--text-primary)' },
  '&.cm-focused > .cm-scroller > .cm-selectionLayer .cm-selectionBackground, .cm-selectionBackground': {
    backgroundColor: 'var(--code-selection)',
  },
  '.cm-matchingBracket': { backgroundColor: 'var(--accent-glow)', outline: '1px solid var(--border-hover)' },
  '.cm-tooltip': {
    backgroundColor: 'var(--surface)',
    color: 'var(--text-primary)',
    border: '1px solid var(--border)',
    borderRadius: 'var(--radius-sm)',
  },
  '.cm-tooltip-autocomplete > ul > li[aria-selected]': { backgroundColor: 'var(--surface-raised)', color: 'var(--text-primary)' },
  '.cm-diagnostic': { fontFamily: 'var(--font-sans)' },
})

export function PineEditor({ value, onChange, diagnostics, label, readOnly = false, onSave, functions, className }: PineEditorProps) {
  const host = useRef<HTMLDivElement>(null)
  const view = useRef<EditorView | null>(null)
  const callbacks = useRef({ onChange, onSave })
  const editable = useRef(new Compartment())
  const completion = useRef(new Compartment())

  useEffect(() => {
    callbacks.current = { onChange, onSave }
  }, [onChange, onSave])

  // The editor is made once; what changes afterwards is passed to it below.
  useEffect(() => {
    if (!host.current) return
    const save = () => {
      callbacks.current.onSave?.()
      return true
    }
    const editor = new EditorView({
      parent: host.current,
      state: EditorState.create({
        doc: value,
        extensions: [
          lineNumbers(),
          highlightActiveLineGutter(),
          history(),
          drawSelection(),
          indentOnInput(),
          bracketMatching(),
          closeBrackets(),
          highlightActiveLine(),
          highlightSelectionMatches(),
          lintGutter(),
          indentUnit.of('    '),
          EditorState.tabSize.of(4),
          pineLanguage,
          syntaxHighlighting(pineHighlight),
          theme,
          completion.current.of(autocompletion({ override: [] })),
          editable.current.of([EditorState.readOnly.of(readOnly), EditorView.editable.of(!readOnly)]),
          keymap.of([
            { key: 'Mod-s', run: save, preventDefault: true },
            ...closeBracketsKeymap,
            ...defaultKeymap,
            ...searchKeymap,
            ...historyKeymap,
            ...completionKeymap,
            indentWithTab,
          ]),
          EditorView.contentAttributes.of({ 'aria-label': label }),
          EditorView.updateListener.of((update) => {
            if (update.docChanged) callbacks.current.onChange(update.state.doc.toString())
          }),
        ],
      }),
    })
    view.current = editor
    return () => {
      editor.destroy()
      view.current = null
    }
    // Made once: value, diagnostics, readOnly and functions are applied by the effects below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // A source given from outside (a version restored, the script read again).
  useEffect(() => {
    const editor = view.current
    if (!editor || editor.state.doc.toString() === value) return
    editor.dispatch({ changes: { from: 0, to: editor.state.doc.length, insert: value } })
  }, [value])

  useEffect(() => {
    const editor = view.current
    if (!editor) return
    editor.dispatch(setDiagnostics(editor.state, toCodeMirror(editor.state.doc, diagnostics)))
  }, [diagnostics])

  useEffect(() => {
    view.current?.dispatch({
      effects: editable.current.reconfigure([EditorState.readOnly.of(readOnly), EditorView.editable.of(!readOnly)]),
    })
  }, [readOnly])

  useEffect(() => {
    if (!functions) return
    view.current?.dispatch({
      effects: completion.current.reconfigure(autocompletion({ override: [pineCompletions(functions)] })),
    })
  }, [functions])

  // Its frame shows where the focus is, as a field's border does: what takes text has no ring (ioiti-ui 0.5.3),
  // its search's fields included.
  return (
    <div
      ref={host}
      className={cn(
        'overflow-hidden rounded-xl border border-border text-base transition-colors sm:text-[13px]',
        'focus-within:border-neutral-500 dark:focus-within:border-[#4A4A4A] accent:focus-within:border-brand',
        className,
      )}
      data-testid="pine-editor"
    />
  )
}

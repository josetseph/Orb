"use client";

import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from "react";
import CodeMirror, { type ReactCodeMirrorRef } from "@uiw/react-codemirror";
import { markdown, markdownLanguage } from "@codemirror/lang-markdown";
import {
  EditorView,
  keymap,
  lineNumbers,
  placeholder as cmPlaceholder,
  drawSelection,
  highlightActiveLine,
  highlightActiveLineGutter,
} from "@codemirror/view";
import { EditorState, Compartment, Prec } from "@codemirror/state";
import {
  defaultKeymap,
  history,
  historyKeymap,
  indentWithTab,
} from "@codemirror/commands";
import { searchKeymap } from "@codemirror/search";
import { api } from "@/lib/api";
import { liveMarkdownExtensions } from "./markdownHighlight";
import { createLivePreviewHideMarks } from "./livePreviewHideMarks";
import {
  insertAtCursor as insertAtCursorCmd,
  runMarkdownAction,
} from "./markdownCommands";
import {
  createEntityDecorations,
  entityClickHandler,
  entityCompletionSource,
  type EntitySuggestion,
} from "./entityExtension";
import {
  createWikilinkDecorations,
  wikilinkClickHandler,
  wikilinkCompletionSource,
  wikilinkHoverHandler,
} from "./wikilinkExtension";
import { createMediaEmbedDecorations } from "./mediaEmbedExtension";
import { MarkdownToolbar } from "./MarkdownToolbar";
import type { Note } from "@/lib/types";
import {
  autocompletion,
  completionKeymap,
} from "@codemirror/autocomplete";

export interface MarkdownNoteEditorProps {
  value: string;
  onChange: (value: string) => void;
  onEntityClick?: (nodeId: string, name: string) => void;
  onWikilinkClick?: (target: string, alias?: string) => void;
  onWikilinkHover?: (target: string, rect: DOMRect, alias?: string) => void;
  onWikilinkLeave?: () => void;
  onAttachFile?: (e: React.ChangeEvent<HTMLInputElement>) => void;
  /** Upload files dropped onto the editor (OS drag-and-drop). */
  onDropFiles?: (files: FileList | File[]) => void | Promise<void>;
  attachDisabled?: boolean;
  kb?: string;
  /** Vault notes for `[[` wikilink autocomplete. */
  notes?: Note[];
  placeholder?: string;
  className?: string;
  /** Show formatting toolbar above the editor (default true). */
  showToolbar?: boolean;
}

export interface MarkdownNoteEditorHandle {
  insertAtCursor: (text: string) => void;
  focus: () => void;
  /** Compatibility shim — CM6 has no textarea; returns null. */
  textarea: HTMLTextAreaElement | null;
  getView: () => EditorView | null;
}

function formattingKeymap() {
  return keymap.of([
    {
      key: "Mod-b",
      run: (view) => {
        runMarkdownAction(view, "bold");
        return true;
      },
    },
    {
      key: "Mod-i",
      run: (view) => {
        runMarkdownAction(view, "italic");
        return true;
      },
    },
    {
      key: "Mod-Shift-s",
      run: (view) => {
        runMarkdownAction(view, "strikethrough");
        return true;
      },
    },
    {
      key: "Mod-e",
      run: (view) => {
        runMarkdownAction(view, "inlineCode");
        return true;
      },
    },
    {
      key: "Mod-k",
      run: (view) => {
        runMarkdownAction(view, "link");
        return true;
      },
    },
    {
      key: "Mod-Shift-c",
      run: (view) => {
        runMarkdownAction(view, "codeBlock");
        return true;
      },
    },
    {
      key: "Mod-1",
      run: (view) => {
        runMarkdownAction(view, "h1");
        return true;
      },
    },
    {
      key: "Mod-2",
      run: (view) => {
        runMarkdownAction(view, "h2");
        return true;
      },
    },
    {
      key: "Mod-3",
      run: (view) => {
        runMarkdownAction(view, "h3");
        return true;
      },
    },
    {
      key: "Mod-Shift-8",
      run: (view) => {
        runMarkdownAction(view, "bulletList");
        return true;
      },
    },
    {
      key: "Mod-Shift-7",
      run: (view) => {
        runMarkdownAction(view, "numberedList");
        return true;
      },
    },
    {
      key: "Mod-Shift-9",
      run: (view) => {
        runMarkdownAction(view, "taskList");
        return true;
      },
    },
    {
      key: "Mod-Shift-r",
      run: (view) => {
        runMarkdownAction(view, "horizontalRule");
        return true;
      },
    },
    {
      key: "Mod-Shift-.",
      run: (view) => {
        runMarkdownAction(view, "quote");
        return true;
      },
    },
  ]);
}

const MarkdownNoteEditor = forwardRef<
  MarkdownNoteEditorHandle,
  MarkdownNoteEditorProps
>(function MarkdownNoteEditor(
  {
    value,
    onChange,
    onEntityClick,
    onWikilinkClick,
    onWikilinkHover,
    onWikilinkLeave,
    onAttachFile,
    onDropFiles,
    attachDisabled,
    kb = "default",
    notes = [],
    placeholder = "Start writing...",
    className,
    showToolbar = true,
  },
  ref,
) {
  const cmRef = useRef<ReactCodeMirrorRef>(null);
  const entityDecorationsCompartment = useRef(new Compartment()).current;
  const [view, setView] = useState<EditorView | null>(null);
  const [scannedEntities, setScannedEntities] = useState<EntitySuggestion[]>(
    [],
  );
  const [isDraggingFiles, setIsDraggingFiles] = useState(false);
  const onDropFilesRef = useRef(onDropFiles);
  onDropFilesRef.current = onDropFiles;
  const attachDisabledRef = useRef(attachDisabled);
  attachDisabledRef.current = attachDisabled;
  const dragDepthRef = useRef(0);
  // Keep autocomplete in sync without rebuilding the whole extension set.
  const notesRef = useRef(notes);
  notesRef.current = notes;

  const hasOsFileDrag = useCallback((e: DragEvent | React.DragEvent) => {
    const types = e.dataTransfer?.types;
    if (!types) return false;
    return Array.from(types).includes("Files");
  }, []);

  const handleOsFileDrop = useCallback(
    (files: FileList | null | undefined) => {
      if (!files?.length || attachDisabledRef.current) return;
      void onDropFilesRef.current?.(files);
    },
    [],
  );

  useImperativeHandle(ref, () => ({
    insertAtCursor(text: string) {
      const v = cmRef.current?.view;
      if (!v) return;
      insertAtCursorCmd(v, text);
    },
    focus() {
      cmRef.current?.view?.focus();
    },
    get textarea() {
      return null;
    },
    getView() {
      return cmRef.current?.view ?? null;
    },
  }));

  // Scan note text for entity mentions (debounced while typing; remounts per note via key=)
  useEffect(() => {
    if (!value || value.length < 10) {
      setScannedEntities([]);
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(() => {
      api
        .scanTextEntities(value, kb)
        .then((entities) => {
          if (!cancelled) setScannedEntities(entities);
        })
        .catch(() => {
          if (!cancelled) setScannedEntities([]);
        });
    }, 600);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [kb, value]);

  // Rebuild entity decorations when the scanned list changes
  useEffect(() => {
    const v = cmRef.current?.view;
    if (!v) return;
    v.dispatch({
      effects: entityDecorationsCompartment.reconfigure(
        createEntityDecorations(scannedEntities),
      ),
    });
  }, [scannedEntities, entityDecorationsCompartment]);

  const extensions = useMemo(
    () => [
      lineNumbers(),
      highlightActiveLine(),
      highlightActiveLineGutter(),
      drawSelection(),
      history(),
      EditorView.lineWrapping,
      EditorState.allowMultipleSelections.of(true),
      markdown({ base: markdownLanguage }),
      ...liveMarkdownExtensions,
      createLivePreviewHideMarks(),
      cmPlaceholder(placeholder),
      createWikilinkDecorations(),
      createMediaEmbedDecorations(kb),
      entityDecorationsCompartment.of(
        createEntityDecorations(scannedEntities),
      ),
      autocompletion({
        override: [
          wikilinkCompletionSource(() => notesRef.current),
          entityCompletionSource(kb),
        ],
        closeOnBlur: true,
        activateOnTyping: true,
        maxRenderedOptions: 12,
      }),
      Prec.highest(keymap.of(completionKeymap)),
      entityClickHandler(onEntityClick),
      wikilinkClickHandler(onWikilinkClick),
      wikilinkHoverHandler(onWikilinkHover, onWikilinkLeave),
      // OS file drops → upload into the note (don't insert the path as text).
      EditorView.domEventHandlers({
        dragover(event) {
          if (!event.dataTransfer?.types || !Array.from(event.dataTransfer.types).includes("Files")) {
            return false;
          }
          event.preventDefault();
          event.dataTransfer.dropEffect = "copy";
          return true;
        },
        drop(event) {
          if (!event.dataTransfer?.files?.length) return false;
          event.preventDefault();
          if (!attachDisabledRef.current) {
            void onDropFilesRef.current?.(event.dataTransfer.files);
          }
          return true;
        },
      }),
      Prec.high(formattingKeymap()),
      keymap.of([
        ...defaultKeymap,
        ...historyKeymap,
        indentWithTab,
        ...searchKeymap,
      ]),
    ],
    // scannedEntities initial; updates via compartment.reconfigure
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [
      kb,
      placeholder,
      onEntityClick,
      onWikilinkClick,
      onWikilinkHover,
      onWikilinkLeave,
      entityDecorationsCompartment,
    ],
  );

  const handleCreateEditor = useCallback((v: EditorView) => {
    setView(v);
  }, []);

  const handleChange = useCallback(
    (doc: string) => {
      onChange(doc);
    },
    [onChange],
  );

  return (
    <div
      className={`relative flex h-full min-h-0 flex-col ${className ?? ""}`}
      onDragEnter={(e) => {
        if (!hasOsFileDrag(e) || attachDisabled) return;
        e.preventDefault();
        dragDepthRef.current += 1;
        setIsDraggingFiles(true);
      }}
      onDragOver={(e) => {
        if (!hasOsFileDrag(e) || attachDisabled) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = "copy";
      }}
      onDragLeave={(e) => {
        if (!hasOsFileDrag(e)) return;
        e.preventDefault();
        dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
        if (dragDepthRef.current === 0) setIsDraggingFiles(false);
      }}
      onDrop={(e) => {
        if (!hasOsFileDrag(e)) return;
        e.preventDefault();
        dragDepthRef.current = 0;
        setIsDraggingFiles(false);
        handleOsFileDrop(e.dataTransfer.files);
      }}
    >
      {showToolbar && (
        <MarkdownToolbar
          view={view}
          onAttachFile={onAttachFile}
          attachDisabled={attachDisabled}
        />
      )}
      <div className="relative min-h-0 flex-1 overflow-hidden px-4 py-3">
        <CodeMirror
          ref={cmRef}
          value={value}
          height="100%"
          theme="none"
          basicSetup={false}
          extensions={extensions}
          onChange={handleChange}
          onCreateEditor={handleCreateEditor}
          className="h-full [&_.cm-editor]:h-full"
        />
        {isDraggingFiles && (
          <div className="pointer-events-none absolute inset-3 z-10 flex items-center justify-center rounded-xl border-2 border-dashed border-teal-400/60 bg-teal-500/10 backdrop-blur-[2px]">
            <p className="rounded-lg bg-black/70 px-4 py-2 text-sm font-medium text-teal-100">
              Drop files to attach
            </p>
          </div>
        )}
      </div>
    </div>
  );
});

export default MarkdownNoteEditor;

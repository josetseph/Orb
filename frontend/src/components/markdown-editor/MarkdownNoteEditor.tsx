import { createExtractMarkerDecorations } from "./extractMarkerExtension";
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
  placeholder as cmPlaceholder,
  drawSelection,
  highlightActiveLine,
} from "@codemirror/view";
import { EditorState, Compartment, Prec } from "@codemirror/state";
import {
  defaultKeymap,
  history,
  historyField,
  historyKeymap,
  indentWithTab,
} from "@codemirror/commands";
import { searchKeymap } from "@codemirror/search";
import { api } from "@/lib/api";
import { useDebounced } from "@/lib/utils";
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
import { htmlToMarkdown } from "./htmlToMarkdown";
import type { AttachmentJob, Note } from "@/lib/types";
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
  /** Upload files dropped onto the editor (OS drag-and-drop). */
  onDropFiles?: (files: FileList | File[]) => void | Promise<void>;
  /** Identifies the note so its scroll position and cursor come back on return. */
  noteId?: string;
  /** Put the cursor in the body as soon as the editor mounts. */
  autoFocus?: boolean;
  attachDisabled?: boolean;
  kb?: string;
  /** Vault notes for `[[` wikilink autocomplete. */
  notes?: Note[];
  placeholder?: string;
  className?: string;
  /** Show formatting toolbar above the editor (default true). */
  /** "live" renders markdown as you type; "source" shows plain markdown. */
  viewMode?: "live" | "source";
  /** Per-attachment jobs keyed by raw markdown url (drives embed footers). */
  attachmentJobs?: Record<string, AttachmentJob>;
  /** Start "process this item only" for one attachment (force = redo). */
  onProcessAttachment?: (rawUrl: string, force: boolean) => void;
  /** Stop a running attachment job. */
  onCancelAttachment?: (rawUrl: string) => void;
  /** Open a vault file linked from the note (preview modal). */
  onOpenFile?: (url: string, filename: string) => void;
}

// Where each note was left: scroll offset, and the editor state with its
// undo history, serialised. Kept across remounts (the editor is keyed by
// note id) so coming back to a note lands where you were with ⌘Z still
// working. Bounded to the last twenty notes; the oldest entry goes first.
const HISTORY_FIELDS = { history: historyField };
const noteViewMemory = new Map<string, { scrollTop: number; json: unknown; doc: string }>();
const NOTE_VIEW_MEMORY_LIMIT = 20;

function rememberNoteView(noteId: string, view: EditorView) {
  noteViewMemory.delete(noteId);
  noteViewMemory.set(noteId, {
    scrollTop: view.scrollDOM.scrollTop,
    json: view.state.toJSON(HISTORY_FIELDS),
    doc: view.state.doc.toString(),
  });
  if (noteViewMemory.size > NOTE_VIEW_MEMORY_LIMIT) {
    noteViewMemory.delete(noteViewMemory.keys().next().value as string);
  }
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
    onDropFiles,
    noteId,
    autoFocus = false,
    attachDisabled,
    kb = "default",
    notes = [],
    placeholder = "Start writing...",
    className,
    viewMode = "live",
    attachmentJobs,
    onProcessAttachment,
    onCancelAttachment,
    onOpenFile,
  },
  ref,
) {
  const cmRef = useRef<ReactCodeMirrorRef>(null);
  const entityDecorationsCompartment = useRef(new Compartment()).current;
  const liveCompartment = useRef(new Compartment()).current;
  const mediaCompartment = useRef(new Compartment()).current;
  const onProcessRef = useRef(onProcessAttachment);
  onProcessRef.current = onProcessAttachment;
  // Stable identity so the widget's eq() does not churn on every render.
  const processHandler = useCallback(
    (rawUrl: string, force: boolean) => onProcessRef.current?.(rawUrl, force),
    [],
  );
  const onCancelRef = useRef(onCancelAttachment);
  onCancelRef.current = onCancelAttachment;
  const cancelHandler = useCallback((rawUrl: string) => onCancelRef.current?.(rawUrl), []);
  const onOpenFileRef = useRef(onOpenFile);
  onOpenFileRef.current = onOpenFile;
  const openFileHandler = useCallback(
    (url: string, filename: string) => onOpenFileRef.current?.(url, filename),
    [],
  );
  // Source shows the markup; it is not "strip everything". Media players and
  // the collapsed extraction blocks stay in both modes — only the syntax
  // hiding is what Source turns off.
  const liveExtensions = useCallback(
    (mode: "live" | "source") => [
      ...(mode === "live" ? [createLivePreviewHideMarks(kb, { onOpenFile: openFileHandler })] : []),
      createExtractMarkerDecorations(),
    ],
    [kb, openFileHandler],
  );
  const mediaExtension = useCallback(
    (_mode: "live" | "source", jobs?: Record<string, AttachmentJob>) =>
      createMediaEmbedDecorations(kb, { jobs, onProcess: processHandler, onCancel: cancelHandler }),
    [kb, processHandler, cancelHandler],
  );
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

  // The remembered state (selection + undo history) is handed to CodeMirror
  // at creation; it is only used when the note's text is what it was when
  // we left, since a history over different text would corrupt on undo.
  const remembered = noteId ? noteViewMemory.get(noteId) : undefined;
  const initialState = useMemo(() => {
    if (!remembered) return undefined;
    // The saved state carries the text as it stood when we left. If the note
    // has since changed underneath (another device, a re-ingest), a history
    // over the old text would corrupt on undo, so it is dropped.
    return remembered.doc === value ? { json: remembered.json, fields: HISTORY_FIELDS } : undefined;
    // Only at mount: the editor is remounted per note id.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [noteId]);

  // The wrapper creates the view in its own effect, after ours would run,
  // so mount-time work hangs off its creation callback: put the scroll back
  // and focus the body when asked. On unmount, remember where the note was.
  // The wrapper's own cleanup destroys the view before ours runs and nulls
  // its ref, so the view is captured at creation for the unmount snapshot.
  const liveViewRef = useRef<EditorView | null>(null);
  const handleCreateEditor = useCallback(
    (v: EditorView) => {
      liveViewRef.current = v;
      if (remembered) v.scrollDOM.scrollTop = remembered.scrollTop;
      if (autoFocus) v.focus();
    },
    // Once per mounted note: the editor is remounted per note id.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [noteId],
  );
  useEffect(() => {
    return () => {
      const v = liveViewRef.current;
      if (noteId && v) rememberNoteView(noteId, v);
    };
  }, [noteId]);

  // Scan note text for entity mentions. This is a backend round trip over the
  // whole note, so it waits for a real pause (2 s) rather than every gap
  // between words; the underlines catch up once you stop typing.
  const scanValue = useDebounced(value, 2000);
  useEffect(() => {
    if (!scanValue || scanValue.length < 10) {
      setScannedEntities([]);
      return;
    }
    let cancelled = false;
    api
      .scanTextEntities(scanValue, kb)
      .then((entities) => {
        if (!cancelled) setScannedEntities(entities);
      })
      .catch(() => {
        if (!cancelled) setScannedEntities([]);
      });
    return () => {
      cancelled = true;
    };
  }, [kb, scanValue]);

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

  // Live / source and attachment-job changes reconfigure their compartments
  // instead of rebuilding the whole extension set (which resets the view).
  useEffect(() => {
    const v = cmRef.current?.view;
    if (!v) return;
    v.dispatch({
      effects: [
        liveCompartment.reconfigure(liveExtensions(viewMode)),
        mediaCompartment.reconfigure(mediaExtension(viewMode, attachmentJobs)),
      ],
    });
  }, [viewMode, attachmentJobs, liveCompartment, mediaCompartment, liveExtensions, mediaExtension]);

  const extensions = useMemo(
    () => [
      highlightActiveLine(),
      drawSelection(),
      history(),
      EditorView.lineWrapping,
      EditorState.allowMultipleSelections.of(true),
      markdown({ base: markdownLanguage }),
      ...liveMarkdownExtensions,
      liveCompartment.of(liveExtensions(viewMode)),
      cmPlaceholder(placeholder),
      createWikilinkDecorations(),
      mediaCompartment.of(mediaExtension(viewMode, attachmentJobs)),
      // One .of() per compartment: a second registration of the same
      // Compartment is not resolvable, and this one seeded it empty.
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
        // Pasted files (a screenshot, a copied file) upload the same way.
        // Rich text from a browser or a document becomes Markdown; a paste
        // that is already plain text (or is text from another editor, which
        // carries no HTML) is left to the editor.
        paste(event, view) {
          const files = Array.from(event.clipboardData?.files ?? []);
          if (files.length > 0) {
            event.preventDefault();
            if (!attachDisabledRef.current) {
              void onDropFilesRef.current?.(files);
            }
            return true;
          }
          const html = event.clipboardData?.getData("text/html");
          if (!html) return false;
          const markdown = htmlToMarkdown(html);
          if (!markdown) return false;
          event.preventDefault();
          view.dispatch(view.state.replaceSelection(markdown));
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
    // scannedEntities / viewMode / attachmentJobs initial; updates via compartment.reconfigure
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [
      kb,
      placeholder,
      onEntityClick,
      onWikilinkClick,
      onWikilinkHover,
      onWikilinkLeave,
      entityDecorationsCompartment,
      liveCompartment,
      mediaCompartment,
      liveExtensions,
      mediaExtension,
    ],
  );


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
      <div className="relative min-h-0 flex-1 overflow-hidden px-4 py-3">
        <CodeMirror
          ref={cmRef}
          value={value}
          height="100%"
          theme="none"
          basicSetup={false}
          onCreateEditor={handleCreateEditor}
          initialState={initialState}
          extensions={extensions}
          onChange={handleChange}
          className="h-full [&_.cm-editor]:h-full"
        />
        {isDraggingFiles && (
          <div className="pointer-events-none absolute inset-3 z-10 flex items-center justify-center rounded-md border border-dashed border-accent bg-accent/8">
            <p className="rounded-md bg-surface px-3 py-1.5 text-[12.5px] font-medium text-accent-200 shadow-sm">
              Drop files to attach
            </p>
          </div>
        )}
      </div>
    </div>
  );
});

export default MarkdownNoteEditor;

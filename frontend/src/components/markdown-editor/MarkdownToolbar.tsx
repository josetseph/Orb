import type { ComponentType, ChangeEvent, ReactNode } from "react";
import type { EditorView } from "@codemirror/view";
import {
  Bold,
  Italic,
  Strikethrough,
  Code,
  Link,
  FileCode,
  Quote,
  Heading1,
  Heading2,
  Heading3,
  List,
  ListOrdered,
  ListTodo,
  Minus,
  Paperclip,
} from "lucide-react";
import { cn } from "@/lib/utils";
import {
  runMarkdownAction,
  type MarkdownAction,
} from "./markdownCommands";

interface ToolItem {
  action: MarkdownAction;
  icon: ComponentType<{ className?: string }>;
  label: string;
  shortcut?: string;
}

const formattingTools: ToolItem[] = [
  { action: "bold", icon: Bold, label: "Bold", shortcut: "⌘B" },
  { action: "italic", icon: Italic, label: "Italic", shortcut: "⌘I" },
  {
    action: "strikethrough",
    icon: Strikethrough,
    label: "Strikethrough",
    shortcut: "⌘⇧S",
  },
  { action: "inlineCode", icon: Code, label: "Inline code", shortcut: "⌘E" },
];

const insertTools: ToolItem[] = [
  { action: "link", icon: Link, label: "Link", shortcut: "⌘K" },
  { action: "codeBlock", icon: FileCode, label: "Code block", shortcut: "⌘⇧C" },
  { action: "quote", icon: Quote, label: "Quote", shortcut: "⌘⇧." },
];

const structureTools: ToolItem[] = [
  { action: "h1", icon: Heading1, label: "Heading 1", shortcut: "⌘1" },
  { action: "h2", icon: Heading2, label: "Heading 2", shortcut: "⌘2" },
  { action: "h3", icon: Heading3, label: "Heading 3", shortcut: "⌘3" },
  { action: "bulletList", icon: List, label: "Bullet list", shortcut: "⌘⇧8" },
  {
    action: "numberedList",
    icon: ListOrdered,
    label: "Numbered list",
    shortcut: "⌘⇧7",
  },
  { action: "taskList", icon: ListTodo, label: "Task list", shortcut: "⌘⇧9" },
  {
    action: "horizontalRule",
    icon: Minus,
    label: "Horizontal rule",
    shortcut: "⌘⇧R",
  },
];

const TOOL_BUTTON =
  "flex h-6 w-6 items-center justify-center rounded-[5px] text-n-500 transition-colors hover:bg-n-900 hover:text-text";

function ToolButton({
  tool,
  onAction,
}: {
  tool: ToolItem;
  onAction: (action: MarkdownAction) => void;
}) {
  const Icon = tool.icon;
  return (
    <button
      type="button"
      onMouseDown={(e) => {
        e.preventDefault();
        onAction(tool.action);
      }}
      title={tool.shortcut ? `${tool.label} (${tool.shortcut})` : tool.label}
      aria-label={tool.label}
      className={TOOL_BUTTON}
    >
      <Icon className="h-3.5 w-3.5" />
    </button>
  );
}

function ToolGroup({
  tools,
  onAction,
}: {
  tools: ToolItem[];
  onAction: (action: MarkdownAction) => void;
}) {
  return (
    <div className="flex items-center gap-px">
      {tools.map((tool) => (
        <ToolButton key={tool.action} tool={tool} onAction={onAction} />
      ))}
    </div>
  );
}

export interface MarkdownToolbarProps {
  view: EditorView | null;
  className?: string;
  /** Extra controls (e.g. Attach) rendered after structure tools. */
  trailing?: ReactNode;
  onAttachFile?: (e: ChangeEvent<HTMLInputElement>) => void;
  attachDisabled?: boolean;
}

export function MarkdownToolbar({
  view,
  className,
  trailing,
  onAttachFile,
  attachDisabled,
}: MarkdownToolbarProps) {
  const onAction = (action: MarkdownAction) => {
    runMarkdownAction(view, action);
  };

  return (
    <div className={cn("flex flex-wrap items-center gap-1.5 px-3 py-1.5", className)}>
      <ToolGroup tools={formattingTools} onAction={onAction} />
      <div className="mx-0.5 h-4 w-px bg-divider" />
      <ToolGroup tools={insertTools} onAction={onAction} />
      <div className="mx-0.5 h-4 w-px bg-divider" />
      <ToolGroup tools={structureTools} onAction={onAction} />
      {onAttachFile && (
        <>
          <div className="mx-0.5 h-4 w-px bg-divider" />
          <input
            type="file"
            id="md-toolbar-file-upload"
            className="hidden"
            onChange={onAttachFile}
            disabled={attachDisabled}
          />
          <label
            htmlFor="md-toolbar-file-upload"
            title="Attach file"
            aria-label="Attach file"
            className={cn(
              TOOL_BUTTON,
              "cursor-pointer",
              attachDisabled && "pointer-events-none opacity-40",
            )}
          >
            <Paperclip className="h-3.5 w-3.5" />
          </label>
        </>
      )}
      {trailing}
    </div>
  );
}

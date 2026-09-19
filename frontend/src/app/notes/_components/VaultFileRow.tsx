import { Paperclip, Pencil, Trash2 } from "lucide-react";
import { cn } from "@/lib/utils";

type VaultFileRowProps = {
  name: string;
  relPath: string;
  depth: number;
  isDragging: boolean;
  isSelected: boolean;
  /** Every selected file; dragging a selected row moves all of them. */
  selectedRels: Set<string>;
  onToggleSelected: (relPath: string) => void;
  onDragStart: (relPath: string) => void;
  onDragEnd: () => void;
  onClick: (relPath: string, name: string) => void;
  onRename: (relPath: string) => void;
  onDelete: (relPath: string, name: string) => void;
};

export function VaultFileRow({
  name,
  relPath,
  depth,
  isDragging,
  isSelected,
  selectedRels,
  onToggleSelected,
  onDragStart,
  onDragEnd,
  onClick,
  onRename,
  onDelete,
}: VaultFileRowProps) {
  return (
    <div
      draggable
      onDragStart={(e) => {
        onDragStart(relPath);
        e.dataTransfer.setData("text/vault-file", relPath);
        if (isSelected && selectedRels.size > 1) {
          e.dataTransfer.setData("text/vault-files", JSON.stringify([...selectedRels]));
        }
        e.dataTransfer.effectAllowed = "move";
      }}
      onDragEnd={onDragEnd}
      className={cn(
        "group/file relative flex w-full items-center gap-1 rounded-[6px] px-1.5 py-1 text-left text-[12px] text-n-400 hover:bg-n-900 hover:text-n-200",
        isSelected && "bg-accent-900/60 text-text",
        isDragging && "opacity-50",
      )}
      style={{ paddingLeft: 22 + depth * 12 }}
    >
      <button
        type="button"
        onClick={(e) => {
          if (e.metaKey || e.ctrlKey) onToggleSelected(relPath);
          else onClick(relPath, name);
        }}
        onDoubleClick={() => onRename(relPath)}
        className="flex min-w-0 flex-1 items-center gap-1.5 text-left"
        title={`${relPath}\n⌘-click to select several, then drag · double-click to rename`}
      >
        <Paperclip className="h-3 w-3 shrink-0 text-n-600" />
        <span className="truncate">{name}</span>
      </button>
      <button
        type="button"
        title="Rename"
        onClick={(e) => {
          e.stopPropagation();
          onRename(relPath);
        }}
        className="grid h-5 w-5 shrink-0 place-items-center rounded opacity-0 hover:bg-n-800 group-hover/file:opacity-100"
      >
        <Pencil className="h-3 w-3 text-n-400" />
      </button>
      <button
        type="button"
        title="Delete"
        onClick={(e) => {
          e.stopPropagation();
          void onDelete(relPath, name);
        }}
        className="grid h-5 w-5 shrink-0 place-items-center rounded opacity-0 hover:bg-danger/15 group-hover/file:opacity-100"
      >
        <Trash2 className="h-3 w-3 text-danger-text" />
      </button>
    </div>
  );
}

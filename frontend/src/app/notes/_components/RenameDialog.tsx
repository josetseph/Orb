import { useLayoutEffect, useRef } from "react";
import type { RenameDialogState } from "../_lib/types";

type RenameDialogProps = {
  renameDialog: RenameDialogState;
  onNameChange: (name: string) => void;
  onSubmit: () => void;
  onCancel: () => void;
};

export function RenameDialog({
  renameDialog,
  onNameChange,
  onSubmit,
  onCancel,
}: RenameDialogProps) {
  const ref = useRef<HTMLDialogElement>(null);
  useLayoutEffect(() => ref.current?.showModal(), []);
  return (
    <dialog
      ref={ref}
      onClose={onCancel}
      onClick={(e) => e.target === e.currentTarget && onCancel()}
      className="dialog max-w-[360px]"
    >
      <div className="dialog-title">Rename file</div>
      <p className="dialog-body truncate font-mono text-[11.5px]">{renameDialog.rel_path}</p>
      <input
        autoFocus
        value={renameDialog.name}
        onChange={(e) => onNameChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") void onSubmit();
        }}
        placeholder="File name"
        className="input"
      />
      <div className="dialog-actions">
        <button type="button" onClick={onCancel} className="btn btn-secondary">
          Cancel
        </button>
        <button type="button" onClick={() => void onSubmit()} className="btn btn-primary">
          Rename
        </button>
      </div>
    </dialog>
  );
}

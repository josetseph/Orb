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
  return (
    <div className="dialog-backdrop">
      <div className="dialog max-w-[360px]">
        <div className="dialog-title">Rename file</div>
        <p className="dialog-body truncate font-mono text-[11.5px]">{renameDialog.rel_path}</p>
        <input
          autoFocus
          value={renameDialog.name}
          onChange={(e) => onNameChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void onSubmit();
            if (e.key === "Escape") onCancel();
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
      </div>
    </div>
  );
}

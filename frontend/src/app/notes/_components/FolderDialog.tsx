"use client";

import type { FolderDialogState } from "../_lib/types";

type FolderDialogProps = {
  folderDialog: FolderDialogState;
  vaultName: string;
  onNameChange: (name: string) => void;
  onSubmit: () => void;
  onCancel: () => void;
};

export function FolderDialog({
  folderDialog,
  vaultName,
  onNameChange,
  onSubmit,
  onCancel,
}: FolderDialogProps) {
  return (
    <div className="dialog-backdrop">
      <div className="dialog max-w-[360px]">
        <div className="dialog-title">New folder</div>
        <p className="dialog-body">Inside {folderDialog.parent || vaultName}</p>
        <input
          autoFocus
          value={folderDialog.name}
          onChange={(e) => onNameChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void onSubmit();
            if (e.key === "Escape") onCancel();
          }}
          placeholder="Folder name"
          className="input"
        />
        <div className="dialog-actions">
          <button type="button" onClick={onCancel} className="btn btn-secondary">
            Cancel
          </button>
          <button type="button" onClick={() => void onSubmit()} className="btn btn-primary">
            Create
          </button>
        </div>
      </div>
    </div>
  );
}

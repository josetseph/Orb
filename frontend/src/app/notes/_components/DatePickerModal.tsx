"use client";

type DatePickerModalProps = {
  createdAt: string;
  pendingDateChange: string | null;
  onPendingChange: (isoDate: string) => void;
  onClose: () => void;
};

export function DatePickerModal({
  createdAt,
  pendingDateChange,
  onPendingChange,
  onClose,
}: DatePickerModalProps) {
  return (
    <div
      className="dialog-backdrop"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="dialog">
        <div className="dialog-title">Change date</div>
        <p className="dialog-body">The date this note sorts and is remembered by.</p>
        <input
          type="datetime-local"
          defaultValue={new Date(createdAt).toISOString().slice(0, 16)}
          onChange={(e) => {
            if (e.target.value) onPendingChange(new Date(e.target.value).toISOString());
          }}
          className="input"
          autoFocus
          aria-label="Select note date"
        />
        <div className="dialog-actions">
          <button onClick={onClose} className="btn btn-primary">
            {pendingDateChange ? "Save" : "Close"}
          </button>
        </div>
      </div>
    </div>
  );
}

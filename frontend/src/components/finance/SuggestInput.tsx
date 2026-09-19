import { useState } from "react";

export function SuggestInput({
  value,
  onChange,
  suggestions,
  placeholder,
}: {
  value: string;
  onChange: (value: string) => void;
  suggestions: string[];
  placeholder?: string;
}) {
  const [open, setOpen] = useState(false);
  const filtered = suggestions
    .filter((item) => item.toLowerCase().includes((value || "").toLowerCase()))
    .slice(0, 8);
  return (
    <div className="relative">
      <input
        value={value}
        onChange={(e) => {
          onChange(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => {
          // Delay so click on suggestion registers.
          window.setTimeout(() => setOpen(false), 120);
        }}
        className="input"
        placeholder={placeholder}
        autoComplete="off"
      />
      {open && filtered.length > 0 && (
        <ul className="popover absolute z-20 mt-1 max-h-48 w-full overflow-auto">
          {filtered.map((item) => (
            <li key={item}>
              <button
                type="button"
                className="menu-item"
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => {
                  onChange(item);
                  setOpen(false);
                }}
              >
                {item}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

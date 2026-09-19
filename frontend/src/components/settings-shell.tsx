import { Link } from "react-router-dom";
import { useLocation } from "react-router-dom";
import { Cpu, FolderOpen, HardDrive, Info } from "lucide-react";
import { cn } from "@/lib/utils";

const TABS = [
  { label: "Workspace", href: "/kb", icon: FolderOpen },
  { label: "Models", href: "/models", icon: Cpu },
  { label: "Storage", href: "/setup", icon: HardDrive },
  { label: "About", href: "/settings", icon: Info },
];

/**
 * The settings sheet from the design: a left tab rail and a scrolling body.
 * Each settings route renders inside it so the four pages read as one place.
 */
export function SettingsShell({
  title,
  intro,
  children,
}: {
  title: string;
  intro?: string;
  children: React.ReactNode;
}) {
  const { pathname } = useLocation();
  return (
    <div className="screen items-start justify-center overflow-auto p-6">
      <div className="flex min-h-[600px] w-full max-w-[920px] overflow-hidden rounded-lg bg-surface shadow-lg">
        <div className="flex w-[200px] shrink-0 flex-col gap-0.5 border-r border-divider px-2.5 py-4">
          <div className="px-2.5 pb-3 pt-1 text-[15px] font-medium">Settings</div>
          {TABS.map((t) => {
            const Icon = t.icon;
            const active = pathname.startsWith(t.href);
            return (
              <Link
                key={t.href}
                to={t.href}
                className={cn(
                  "flex items-center gap-2.5 rounded-md px-2.5 py-2 text-[13px] no-underline hover:bg-n-900",
                  active ? "bg-n-900 text-text" : "text-n-400",
                )}
              >
                <Icon className="h-[15px] w-[15px]" />
                {t.label}
              </Link>
            );
          })}
          <div className="mt-auto px-2.5 text-[11px] text-n-500">
            Orb · everything stays on this machine
          </div>
        </div>
        <div className="min-w-0 flex-1 overflow-auto px-7 py-6">
          <h2 className="mb-0.5 text-[18px] font-medium">{title}</h2>
          {intro && <p className="mb-5 text-[12.5px] text-n-500">{intro}</p>}
          {children}
        </div>
      </div>
    </div>
  );
}

/** A labelled row with a control on the right, as in the design's Workspace tab. */
export function SettingRow({
  icon,
  title,
  description,
  children,
  className,
}: {
  icon?: React.ReactNode;
  title: string;
  description?: string;
  children?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex items-center gap-3 rounded-md px-3.5 py-3 shadow-sm", className)}>
      {icon && <span className="text-accent-300">{icon}</span>}
      <div className="min-w-0 flex-1">
        <div className="text-[13px]">{title}</div>
        {description && <div className="text-[11.5px] text-n-500">{description}</div>}
      </div>
      {children}
    </div>
  );
}

/** On/off switch matching the design's pill toggle. */
export function Toggle({
  on,
  onChange,
  disabled,
  label,
}: {
  on: boolean;
  onChange: (next: boolean) => void;
  disabled?: boolean;
  label: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!on)}
      className={cn(
        "relative h-5 w-9 shrink-0 rounded-full transition-colors disabled:opacity-50",
        on ? "bg-accent-700" : "bg-n-800",
      )}
    >
      <span
        className={cn(
          "absolute top-0.5 h-4 w-4 rounded-full bg-n-100 transition-[left]",
          on ? "left-[18px]" : "left-0.5",
        )}
      />
    </button>
  );
}

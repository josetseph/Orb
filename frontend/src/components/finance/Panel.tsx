import type { ReactNode } from "react";

export function Panel({
  title,
  icon,
  children,
}: {
  title: string;
  icon?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="card-outline">
      <div className="mb-2 flex items-center gap-1.5 px-2 text-n-500 [&_svg]:h-3.5 [&_svg]:w-3.5">
        {icon}
        <h2 className="kicker text-[11px]">{title}</h2>
      </div>
      {children}
    </div>
  );
}

"use client";

export type ProximityLabel = {
  id: string;
  name: string;
  nodeType: string;
  sx: number;
  sy: number;
  opacity: number;
};

export type LinkLabel = {
  id: string;
  label: string;
  sx: number;
  sy: number;
  opacity: number;
};

export function ProximityLabelLayer({
  proximityLabels,
  linkLabels,
}: {
  proximityLabels: ProximityLabel[];
  linkLabels: LinkLabel[];
}) {
  return (
    <div className="pointer-events-none absolute inset-0 z-20 overflow-hidden select-none">
      {proximityLabels.map((lbl) => (
        <div
          key={lbl.id}
          className="absolute whitespace-nowrap rounded-[6px] px-2 py-0.5 text-[11px] font-medium text-n-100 shadow-sm"
          style={{
            left: lbl.sx,
            top: lbl.sy,
            transform: "translate(-50%, calc(-100% - 10px))",
            background: "rgba(35,37,50,0.85)",
            opacity: lbl.opacity,
          }}
        >
          {lbl.name}
        </div>
      ))}
      {linkLabels.map((lbl) => (
        <div
          key={lbl.id}
          className="absolute whitespace-nowrap text-[10.5px] italic text-n-400"
          style={{
            left: lbl.sx,
            top: lbl.sy,
            transform: "translate(-50%, -50%)",
            opacity: lbl.opacity,
            textShadow: "0 1px 3px rgba(0,0,0,0.8)",
          }}
        >
          {lbl.label}
        </div>
      ))}
    </div>
  );
}

"use client";

import { useEffect, useState, type MutableRefObject, type RefObject } from "react";
import * as THREE from "three";
import type {
  LinkLabel,
  ProximityLabel,
} from "@/components/graph3d/ProximityLabelLayer";

export function useProximityLabels({
  graphRef,
  nodeCount,
  linkCount,
  currentKB,
  nodesRef,
  linksRef,
}: {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  graphRef: RefObject<any>;
  nodeCount: number;
  linkCount: number;
  currentKB: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  nodesRef: MutableRefObject<any[]>;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  linksRef: MutableRefObject<any[]>;
}) {
  const [proximityLabels, setProximityLabels] = useState<ProximityLabel[]>([]);
  const [linkLabels, setLinkLabels] = useState<LinkLabel[]>([]);

  // ── Proximity labels — Obsidian/notes-graph style fade when camera is close ──
  useEffect(() => {
    if (!nodeCount) return;

    const MAX_NODE_LABELS = 16;
    const MAX_LINK_LABELS = 8;
    const DEFAULT_TEXT_FADE = 0.55; // same default as notes-graph
    let rafId = 0;
    let lastUpdate = 0;

    const loadTextFade = (): number => {
      try {
        const raw = localStorage.getItem(
          `orb:notes-graph-controls:${currentKB || "default"}`,
        );
        if (!raw) return DEFAULT_TEXT_FADE;
        const parsed = JSON.parse(raw) as { textFade?: number };
        return typeof parsed.textFade === "number"
          ? parsed.textFade
          : DEFAULT_TEXT_FADE;
      } catch {
        return DEFAULT_TEXT_FADE;
      }
    };

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const posOf = (idOrObj: any): [number, number, number] => {
      if (idOrObj && typeof idOrObj === "object") {
        return [
          idOrObj.fx ?? idOrObj.x ?? 0,
          idOrObj.fy ?? idOrObj.y ?? 0,
          idOrObj.fz ?? idOrObj.z ?? 0,
        ];
      }
      return [0, 0, 0];
    };

    const loop = () => {
      rafId = requestAnimationFrame(loop);
      const now = Date.now();
      if (now - lastUpdate < 100) return;
      lastUpdate = now;

      const fg = graphRef.current;
      if (!fg) return;
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const camera = (fg as any).camera?.();
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const renderer = (fg as any).renderer?.();
      if (!camera || !renderer) return;

      // CSS pixels — NOT buffer width (DPR would push labels off-screen).
      const canvas = renderer.domElement as HTMLCanvasElement;
      const width = canvas.clientWidth || canvas.width;
      const height = canvas.clientHeight || canvas.height;
      if (!width || !height) return;

      const camPos = camera.position as THREE.Vector3;
      const textFade = loadTextFade();

      // Adaptive radius from scene extent so labels work after zoomToFit.
      let extent = 1;
      for (const node of nodesRef.current) {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const n = node as any;
        const nx = n.fx ?? n.x ?? 0;
        const ny = n.fy ?? n.y ?? 0;
        const nz = n.fz ?? n.z ?? 0;
        extent = Math.max(extent, Math.sqrt(nx * nx + ny * ny + nz * nz));
      }
      const nodeRadius = Math.max(150, extent * 0.55, camPos.length() * 0.22);
      const linkRadius = nodeRadius * 0.55;

      // ── Node labels ─────────────────────────────────────────────────────────
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const nearby: Array<{ node: any; dist: number }> = [];
      for (const node of nodesRef.current) {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const n = node as any;
        const name = String(n.name ?? "").trim();
        if (!name) continue;
        const nx = n.fx ?? n.x ?? 0;
        const ny = n.fy ?? n.y ?? 0;
        const nz = n.fz ?? n.z ?? 0;
        const dx = camPos.x - nx;
        const dy = camPos.y - ny;
        const dz = camPos.z - nz;
        const dist = Math.sqrt(dx * dx + dy * dy + dz * dz);
        if (dist < nodeRadius) nearby.push({ node, dist });
      }

      if (!nearby.length) {
        setProximityLabels((prev) => (prev.length ? [] : prev));
        setLinkLabels((prev) => (prev.length ? [] : prev));
        return;
      }

      nearby.sort((a, b) => a.dist - b.dist);
      const top = nearby.slice(0, MAX_NODE_LABELS);

      const labels: ProximityLabel[] = [];
      for (const { node, dist } of top) {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const n = node as any;
        const nx = n.fx ?? n.x ?? 0;
        const ny = n.fy ?? n.y ?? 0;
        const nz = n.fz ?? n.z ?? 0;

        const vec = new THREE.Vector3(nx, ny, nz);
        vec.project(camera);
        if (vec.z < -1 || vec.z > 1) continue; // outside clip volume
        if (vec.x < -1.2 || vec.x > 1.2 || vec.y < -1.2 || vec.y > 1.2)
          continue;

        const sx = ((vec.x + 1) / 2) * width;
        const sy = ((1 - vec.y) / 2) * height;
        // Mirror notes-graph: opacity = (zoomLike - textFade + 0.35) / 0.7
        // where zoomLike rises as distance falls (closer → higher).
        const proximity = 1 - dist / nodeRadius;
        const zoomLike = proximity * 2;
        const opacity = Math.max(
          0,
          Math.min(1, (zoomLike - textFade + 0.35) / 0.7),
        );
        if (opacity <= 0.02) continue;

        labels.push({
          id: n.node_id ?? String(n.id),
          name: String(n.name).slice(0, 40),
          nodeType: n.node_type ?? "",
          sx,
          sy,
          opacity,
        });
      }
      setProximityLabels(labels);

      // ── Link labels ─────────────────────────────────────────────────────────
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const nearbyLinks: Array<{
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        link: any;
        dist: number;
        mx: number;
        my: number;
        mz: number;
      }> = [];
      for (const link of linksRef.current) {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const l = link as any;
        const label: string = (l.type ?? "").replace(/_/g, " ");
        if (!label || label === "MEMBER OF") continue;
        const [sx, sy, sz] = posOf(l.source);
        const [tx, ty, tz] = posOf(l.target);
        const mx = (sx + tx) / 2;
        const my = (sy + ty) / 2;
        const mz = (sz + tz) / 2;
        const dx = camPos.x - mx;
        const dy = camPos.y - my;
        const dz = camPos.z - mz;
        const dist = Math.sqrt(dx * dx + dy * dy + dz * dz);
        if (dist < linkRadius) nearbyLinks.push({ link: l, dist, mx, my, mz });
      }
      nearbyLinks.sort((a, b) => a.dist - b.dist);
      const linkOut: LinkLabel[] = [];
      for (const { link: l, dist, mx, my, mz } of nearbyLinks.slice(
        0,
        MAX_LINK_LABELS,
      )) {
        const vec = new THREE.Vector3(mx, my, mz);
        vec.project(camera);
        if (vec.z < -1 || vec.z > 1) continue;
        const proximity = 1 - dist / linkRadius;
        const zoomLike = proximity * 2;
        const opacity = Math.max(
          0,
          Math.min(1, (zoomLike - textFade + 0.35) / 0.7),
        );
        if (opacity <= 0.02) continue;
        const linkId =
          (typeof l.source === "object" ? l.source.id : l.source) +
          "→" +
          (typeof l.target === "object" ? l.target.id : l.target) +
          ":" +
          (l.type ?? "");
        linkOut.push({
          id: linkId,
          label: (l.type ?? "").replace(/_/g, " "),
          sx: ((vec.x + 1) / 2) * width,
          sy: ((1 - vec.y) / 2) * height,
          opacity: opacity * 0.85,
        });
      }
      setLinkLabels(linkOut);
    };

    rafId = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(rafId);
  }, [nodeCount, linkCount, currentKB, graphRef, linksRef, nodesRef]);

  return { proximityLabels, linkLabels };
}

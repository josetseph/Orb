import {
  useCallback,
  useEffect,
  useRef,
  type Dispatch,
  type MutableRefObject,
  type SetStateAction,
} from "react";
import * as THREE from "three";
import type { KnowledgeNode } from "@/components/graph3d/types";

export function useGraph3DCamera({
  nodeCount,
  selectedNode,
  searchOpen,
  setSearchOpen,
  setSelectedNode,
  searchOpenRef,
  userNavigatedRef,
}: {
  nodeCount: number;
  selectedNode: KnowledgeNode | null;
  searchOpen: boolean;
  setSearchOpen: Dispatch<SetStateAction<boolean>>;
  setSelectedNode: Dispatch<SetStateAction<KnowledgeNode | null>>;
  searchOpenRef: MutableRefObject<boolean>;
  userNavigatedRef: MutableRefObject<boolean>;
}) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const graphRef = useRef<any>(null);

  // FPS camera rig — all refs so event handlers never stale-close over state
  const pendingDrag = useRef<{ button: number; x: number; y: number } | null>(
    null,
  );
  const dragging = useRef(false);
  const rightDrag = useRef(false);
  const last = useRef({ x: 0, y: 0 });
  const keysRef = useRef<Set<string>>(new Set());
  const wasdRafRef = useRef<number>(0);
  const rigCleanup = useRef<(() => void) | null>(null);
  // Quaternion-based look: store yaw/pitch as plain numbers to avoid gimbal lock
  const pitchRef = useRef(0);
  const yawRef = useRef(0);
  // Track modal/overlay open state in a ref so camera handlers always see the latest value
  const modalOpenRef = useRef(false);

  useEffect(() => {
    searchOpenRef.current = searchOpen;
    modalOpenRef.current = selectedNode !== null || searchOpen;
  }, [selectedNode, searchOpen, searchOpenRef]);

  // ── Fly camera to a node position (smooth 1.2 s animated approach) ──
  const flyToNode = useCallback(
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (node: any) => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const fg = graphRef.current as any;
      if (!fg) return;
      const camera = fg.camera?.();
      if (!camera) return;

      const nx = node.fx ?? node.x ?? 0;
      const ny = node.fy ?? node.y ?? 0;
      const nz = node.fz ?? node.z ?? 0;
      const target = new THREE.Vector3(nx, ny, nz);

      // Approach from the current direction and stop eight radii out, so a
      // hub node frames the same way a leaf does (a leaf's radius is 10 → 80).
      // Radius mirrors Graph3DCanvas: cbrt(nodeVal) × nodeRelSize (10).
      const APPROACH_DIST = Math.cbrt(Math.max(1, Number(node.val) || 1)) * 10 * 8;
      const from = camera.position.clone();
      const dir = from.clone().sub(target);
      const destination =
        dir.length() > APPROACH_DIST
          ? target.clone().add(dir.normalize().multiplyScalar(APPROACH_DIST))
          : from.clone();

      const startPos = from.clone();
      const startTime = performance.now();
      const DURATION = 1200;
      let rafId = 0;

      const loop = () => {
        const t = Math.min((performance.now() - startTime) / DURATION, 1);
        const ease = 1 - Math.pow(1 - t, 3); // cubic ease-out
        camera.position.lerpVectors(startPos, destination, ease);

        // Always look at the target, keeping pitchRef/yawRef in sync
        const lookDir = target.clone().sub(camera.position).normalize();
        pitchRef.current = Math.asin(Math.max(-1, Math.min(1, lookDir.y)));
        yawRef.current = Math.atan2(-lookDir.x, -lookDir.z);
        const euler = new THREE.Euler(
          pitchRef.current,
          yawRef.current,
          0,
          "YXZ",
        );
        camera.quaternion.setFromEuler(euler);

        if (t < 1) rafId = requestAnimationFrame(loop);
      };
      rafId = requestAnimationFrame(loop);
      return () => cancelAnimationFrame(rafId);
    },
    [],
  );

  // Install FPS camera controls once ForceGraph3D is mounted.
  // Poll every 100 ms until graphRef.current exposes camera() + renderer(),
  // then disable built-in OrbitControls and take over with our own listeners.
  useEffect(() => {
    if (!nodeCount) return;

    const install = () => {
      if (!graphRef.current) return false;
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const fg = graphRef.current as any;
      const camera = fg.camera?.();
      const renderer = fg.renderer?.();
      if (!camera || !renderer) return false;

      const controls = fg.controls?.();
      if (controls) controls.enabled = false;

      // Seed pitch/yaw from the camera's current orientation
      camera.rotation.order = "YXZ";
      pitchRef.current = camera.rotation.x;
      yawRef.current = camera.rotation.y;

      const canvas = renderer.domElement as HTMLCanvasElement;
      const DRAG_THRESHOLD = 5;

      const onDown = (e: MouseEvent) => {
        if (modalOpenRef.current) return;
        pendingDrag.current = { button: e.button, x: e.clientX, y: e.clientY };
      };

      const onMove = (e: MouseEvent) => {
        if (pendingDrag.current && !dragging.current) {
          const dx = e.clientX - pendingDrag.current.x;
          const dy = e.clientY - pendingDrag.current.y;
          if (Math.sqrt(dx * dx + dy * dy) > DRAG_THRESHOLD) {
            dragging.current = true;
            rightDrag.current = pendingDrag.current.button === 2;
            last.current = {
              x: pendingDrag.current.x,
              y: pendingDrag.current.y,
            };
            pendingDrag.current = null;
          }
          return;
        }
        if (!dragging.current) return;

        const dx = e.clientX - last.current.x;
        const dy = e.clientY - last.current.y;
        last.current = { x: e.clientX, y: e.clientY };

        userNavigatedRef.current = true;
        if (rightDrag.current) {
          // Scale pan speed with camera distance so it feels consistent at any zoom level
          const panSpeed = camera.position.length() * 0.001;
          camera.translateX(-dx * panSpeed);
          camera.translateY(dy * panSpeed);
        } else {
          // Accumulate yaw and pitch as plain numbers, then compose into a
          // quaternion — avoids gimbal lock so horizontal drag never stops.
          yawRef.current -= dx * 0.003;
          pitchRef.current -= dy * 0.003;
          pitchRef.current = Math.max(
            -Math.PI / 2 + 0.01,
            Math.min(Math.PI / 2 - 0.01, pitchRef.current),
          );
          const euler = new THREE.Euler(
            pitchRef.current,
            yawRef.current,
            0,
            "YXZ",
          );
          camera.quaternion.setFromEuler(euler);
        }
      };

      const onUp = () => {
        dragging.current = false;
        pendingDrag.current = null;
      };

      const onWheel = (e: WheelEvent) => {
        if (modalOpenRef.current) return;
        e.preventDefault();
        userNavigatedRef.current = true;
        // Distance-scaled fly so near-field scroll still moves, without
        // overshooting through the origin and feeling "pulled back".
        const dist = Math.max(40, camera.position.length());
        const step = Math.min(80, dist * 0.08) * (e.deltaY > 0 ? 1 : -1);
        // Normalize trackpad/mouse wheel units (pixels vs lines).
        const units = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 800 : 1;
        camera.translateZ(
          step * Math.min(3, Math.abs(e.deltaY) * units * 0.01),
        );
      };

      const onContextMenu = (e: Event) => e.preventDefault();

      // WASD fly controls — don't capture when a form element has focus or overlay is open
      const onKeyDown = (e: KeyboardEvent) => {
        // Toggle search with / or Ctrl+K / Cmd+K
        if (e.key === "/" && !searchOpenRef.current && !modalOpenRef.current) {
          e.preventDefault();
          setSearchOpen(true);
          return;
        }
        if (e.key === "k" && (e.ctrlKey || e.metaKey)) {
          e.preventDefault();
          setSearchOpen((v) => !v);
          return;
        }
        if (modalOpenRef.current) return;
        const tag = (document.activeElement as HTMLElement)?.tagName;
        if (tag === "INPUT" || tag === "TEXTAREA") return;
        if ((document.activeElement as HTMLElement)?.isContentEditable) return;
        keysRef.current.add(e.key.toLowerCase());
      };
      const onKeyUp = (e: KeyboardEvent) => {
        keysRef.current.delete(e.key.toLowerCase());
      };

      // Smooth WASD loop — speed adapts to camera distance (same feel at any zoom)
      let lastWasdTime = performance.now();
      const wasdLoop = () => {
        wasdRafRef.current = requestAnimationFrame(wasdLoop);
        const now = performance.now();
        const dt = Math.min(now - lastWasdTime, 50);
        lastWasdTime = now;
        if (modalOpenRef.current) {
          keysRef.current.clear();
          return;
        }
        const keys = keysRef.current;
        if (!keys.size) return;
        userNavigatedRef.current = true;
        const speed =
          (Math.max(40, camera.position.length()) * 0.006 * dt) / 16.67;
        if (keys.has("w")) camera.translateZ(-speed);
        if (keys.has("s")) camera.translateZ(speed);
        if (keys.has("a")) camera.translateX(-speed);
        if (keys.has("d")) camera.translateX(speed);
        if (keys.has("q")) camera.position.y += speed;
        if (keys.has("e")) camera.position.y -= speed;
      };
      wasdRafRef.current = requestAnimationFrame(wasdLoop);

      window.addEventListener("mousedown", onDown);
      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", onUp);
      window.addEventListener("wheel", onWheel, { passive: false });
      window.addEventListener("keydown", onKeyDown);
      window.addEventListener("keyup", onKeyUp);
      canvas.addEventListener("contextmenu", onContextMenu);

      rigCleanup.current = () => {
        window.removeEventListener("mousedown", onDown);
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("mouseup", onUp);
        window.removeEventListener("wheel", onWheel);
        window.removeEventListener("keydown", onKeyDown);
        window.removeEventListener("keyup", onKeyUp);
        cancelAnimationFrame(wasdRafRef.current);
        canvas.removeEventListener("contextmenu", onContextMenu);
      };

      return true;
    };

    if (!install()) {
      const id = setInterval(() => {
        if (install()) clearInterval(id);
      }, 100);
      return () => {
        clearInterval(id);
        rigCleanup.current?.();
        rigCleanup.current = null;
      };
    }

    return () => {
      rigCleanup.current?.();
      rigCleanup.current = null;
    };
  }, [nodeCount]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleNodeClick = useCallback(
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (node: any) => {
      if (dragging.current) return;
      const n: KnowledgeNode = {
        node_id: node.node_id ?? String(node.id ?? ""),
        name: node.name ?? "",
        node_type: node.node_type ?? "unknown",
        description: node.description ?? "",
        community_id: node.community_id,
        x: node.x ?? 0,
        y: node.y ?? 0,
        z: node.z ?? 0,
      };
      setSelectedNode(n);
    },
    [setSelectedNode],
  );

  return { graphRef, flyToNode, handleNodeClick };
}

import { useEffect } from "react";

/**
 * Suppresses the THREE.Clock deprecation warning emitted by react-force-graph-3d.
 * THREE.Clock was deprecated in three.js r168 in favour of THREE.Timer, but the
 * library has not yet updated its internal usage. This intercept is safe to remove
 * once react-force-graph-3d ships a build that uses Timer instead.
 */
export function SuppressThreeWarnings() {
    useEffect(() => {
        const original = console.warn.bind(console);
        console.warn = (...args: unknown[]) => {
            if (
                typeof args[0] === "string" &&
                args[0].includes("THREE.Clock") &&
                args[0].includes("deprecated")
            ) {
                return;
            }
            original(...args);
        };
        return () => {
            console.warn = original;
        };
    }, []);

    return null;
}

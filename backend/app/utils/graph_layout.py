"""
Deterministic 3D layout for the knowledge graph.

``compute_solar_positions`` — hierarchical L0/L1/L2 solar-system layout,
computed per request by ``GraphService.get_full_3d_graph`` (nothing is
persisted) so the frontend does not run a physics simulation.
"""

from __future__ import annotations

import hashlib
import math

# ── Helpers ──────────────────────────────────────────────────────────────────


def _fibonacci_sphere(n: int, radius: float) -> list[tuple[float, float, float]]:
    """Return n evenly-distributed points on the surface of a sphere."""
    if n <= 0:
        return []
    points: list[tuple[float, float, float]] = []
    golden = math.pi * (math.sqrt(5.0) - 1.0)  # golden angle in radians
    for i in range(n):
        y = 1.0 - (i / max(n - 1, 1)) * 2.0
        r = math.sqrt(max(0.0, 1.0 - y * y))
        theta = golden * i
        x = math.cos(theta) * r
        z = math.sin(theta) * r
        points.append((x * radius, y * radius, z * radius))
    return points


def _deterministic_jitter(seed: str, max_offset: float) -> tuple[float, float, float]:
    """Produce a stable small offset from a string seed to avoid z-fighting."""
    h = int(hashlib.md5(seed.encode(), usedforsecurity=False).hexdigest(), 16)
    jx = ((h & 0xFF) / 255.0 - 0.5) * 2.0 * max_offset
    jy = (((h >> 8) & 0xFF) / 255.0 - 0.5) * 2.0 * max_offset
    jz = (((h >> 16) & 0xFF) / 255.0 - 0.5) * 2.0 * max_offset
    return jx, jy, jz


def _majority_key(votes: dict[str, int]) -> str:
    """Return the key with the highest vote count (deterministic on ties via max)."""
    return max(votes, key=votes.__getitem__)


# ── Solar-system layout constants ────────────────────────────────────────────
#
# Orbit depth ratio per level: ~4× between successive levels so hierarchy is
# clearly visible at any zoom. For the most-common case (1 L2 per L1, 8 nodes):
#   node cluster ≈ 50 units  (moon cloud around L2 planet)
#   L2 orbit     = 150 units (L2 planet around L1 star)  → 3× node
#   L1 orbit     = 500 units (L1 star around L0 galaxy)  → 3.3× L2+node radius
#
SOLAR_UNIVERSE_RADIUS = 1800.0  # L0 galaxies placed on this sphere
SOLAR_L0_RING_BASE = 500.0  # L1 orbit radius (per sqrt of child count)
SOLAR_L0_RING_MAX = 1200.0
SOLAR_L1_RING_BASE = 150.0  # L2 orbit radius around L1 star
SOLAR_L1_RING_MAX = 400.0
SOLAR_L2_NODE_BASE = 18.0  # node orbit radius (per sqrt of member count)
SOLAR_L2_NODE_MAX = 55.0


def compute_solar_positions(  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    communities: list[dict],
    node_level_map: dict[str, dict[int, str]],
    all_node_ids: list[str] | None = None,
) -> dict[str, tuple[float, float, float]]:
    """Solar-system hierarchical 3D layout.

    Produces a 4-tier nested-sphere arrangement:

        L0 "stars"   — placed on a large Fibonacci sphere (radius SOLAR_UNIVERSE_RADIUS)
        L1 "planets" — placed on a sphere around their parent L0 star
        L2 "moons"   — placed on a sphere around their parent L1 planet
        Nodes        — placed on a sphere around their parent L2 moon

    The parent of each L2 community is the L1 community that the majority of
    its member nodes belong to (and similarly L1 → L0).  This is inferred
    entirely from ``node_level_map`` without requiring explicit parent edges.

    Args:
        communities:    list of community dicts, each with ``community_id``
                        and ``community_level`` (0 | 1 | 2).
        node_level_map: {node_id: {level: community_id}} — each node's
                        community assignment at every level it belongs to.
        all_node_ids:   optional full list of regular node IDs; nodes not
                        found in any L2 community are placed on a fallback
                        inner sphere.

    Returns:
        {id: (x, y, z)} for every node id and every community id.
    """
    positions: dict[str, tuple[float, float, float]] = {}

    # ── Build community membership lists and parent-vote tallies ─────────────
    l2_members: dict[str, list[str]] = {}  # l2_cid → [node_id, ...]
    l2_l1_votes: dict[str, dict[str, int]] = {}  # l2_cid → {l1_cid: vote_count}
    l1_l0_votes: dict[str, dict[str, int]] = {}  # l1_cid → {l0_cid: vote_count}

    for node_id, level_map in node_level_map.items():
        l2 = level_map.get(2)
        l1 = level_map.get(1)
        l0 = level_map.get(0)
        if l2:
            l2_members.setdefault(l2, []).append(node_id)
            if l1:
                l2_l1_votes.setdefault(l2, {})
                l2_l1_votes[l2][l1] = l2_l1_votes[l2].get(l1, 0) + 1
        if l1 and l0:
            l1_l0_votes.setdefault(l1, {})
            l1_l0_votes[l1][l0] = l1_l0_votes[l1].get(l0, 0) + 1

    l2_parent: dict[str, str] = {
        cid: _majority_key(votes) for cid, votes in l2_l1_votes.items()
    }
    l1_parent: dict[str, str] = {
        cid: _majority_key(votes) for cid, votes in l1_l0_votes.items()
    }

    l0_l1_children: dict[str, list[str]] = {}
    for l1_cid, l0_cid in l1_parent.items():
        l0_l1_children.setdefault(l0_cid, []).append(l1_cid)

    l1_l2_children: dict[str, list[str]] = {}
    for l2_cid, l1_cid in l2_parent.items():
        l1_l2_children.setdefault(l1_cid, []).append(l2_cid)

    for children in l0_l1_children.values():
        children.sort()
    for children in l1_l2_children.values():
        children.sort()

    l0_cids = sorted(
        c["community_id"] for c in communities if c.get("community_level") == 0
    )
    l1_cids = sorted(
        c["community_id"] for c in communities if c.get("community_level") == 1
    )
    l2_cids = sorted(
        c["community_id"] for c in communities if c.get("community_level") == 2
    )

    # ── 1. Place L0 stars on the universe sphere ──────────────────────────────
    l0_positions: dict[str, tuple[float, float, float]] = {}
    l0_pts = _fibonacci_sphere(len(l0_cids), SOLAR_UNIVERSE_RADIUS)
    for i, l0_cid in enumerate(l0_cids):
        jx, jy, jz = _deterministic_jitter(l0_cid, 5.0)
        px, py, pz = l0_pts[i]
        pos: tuple[float, float, float] = (px + jx, py + jy, pz + jz)
        l0_positions[l0_cid] = pos
        positions[l0_cid] = pos

    # ── 2. Place L1 planets around their L0 star ──────────────────────────────
    l1_positions: dict[str, tuple[float, float, float]] = {}

    for l0_cid, l1_children in l0_l1_children.items():
        l0_pos = l0_positions.get(l0_cid)
        if not l0_pos:
            continue
        cx, cy, cz = l0_pos
        n = len(l1_children)
        ring_r = min(SOLAR_L0_RING_BASE * math.sqrt(max(n, 1)), SOLAR_L0_RING_MAX)
        pts = _fibonacci_sphere(n, ring_r)
        for i, l1_cid in enumerate(l1_children):
            jx, jy, jz = _deterministic_jitter(l1_cid, 3.0)
            px, py, pz = pts[i]
            pos = (cx + px + jx, cy + py + jy, cz + pz + jz)
            l1_positions[l1_cid] = pos
            positions[l1_cid] = pos

    for l1_cid in l1_cids:
        if l1_cid not in l1_positions:
            jx, jy, jz = _deterministic_jitter(l1_cid, SOLAR_UNIVERSE_RADIUS * 0.8)
            pos = (jx, jy, jz)
            l1_positions[l1_cid] = pos
            positions[l1_cid] = pos

    # ── 3. Place L2 moons around their L1 planet ──────────────────────────────
    l2_positions: dict[str, tuple[float, float, float]] = {}

    for l1_cid, l2_children in l1_l2_children.items():
        l1_pos = l1_positions.get(l1_cid)
        if not l1_pos:
            continue
        cx, cy, cz = l1_pos
        n = len(l2_children)
        ring_r = min(SOLAR_L1_RING_BASE * math.sqrt(max(n, 1)), SOLAR_L1_RING_MAX)
        pts = _fibonacci_sphere(n, ring_r)
        for i, l2_cid in enumerate(l2_children):
            jx, jy, jz = _deterministic_jitter(l2_cid, 2.0)
            px, py, pz = pts[i]
            pos = (cx + px + jx, cy + py + jy, cz + pz + jz)
            l2_positions[l2_cid] = pos
            positions[l2_cid] = pos

    for l2_cid in l2_cids:
        if l2_cid not in l2_positions:
            jx, jy, jz = _deterministic_jitter(l2_cid, SOLAR_UNIVERSE_RADIUS * 0.5)
            pos = (jx, jy, jz)
            l2_positions[l2_cid] = pos
            positions[l2_cid] = pos

    # ── 4. Place nodes around their L2 moon ───────────────────────────────────
    for l2_cid, members in l2_members.items():
        l2_pos = l2_positions.get(l2_cid)
        if not l2_pos:
            continue
        cx, cy, cz = l2_pos
        n = len(members)
        node_r = min(SOLAR_L2_NODE_BASE * math.sqrt(max(n, 1)), SOLAR_L2_NODE_MAX)
        pts = _fibonacci_sphere(n, node_r)
        for i, node_id in enumerate(sorted(members)):
            jx, jy, jz = _deterministic_jitter(node_id, 2.0)
            px, py, pz = pts[i]
            positions[node_id] = (cx + px + jx, cy + py + jy, cz + pz + jz)

    # ── 5. Fallback: unclustered nodes ────────────────────────────────────────
    # Prefer clustering near L1/L0 ancestors over a solid central orphan sphere.
    if all_node_ids:
        orphans = [nid for nid in all_node_ids if nid not in positions]
        if orphans:
            l1_orphan_groups: dict[str, list[str]] = {}
            l0_orphan_groups: dict[str, list[str]] = {}
            truly_orphan: list[str] = []

            for node_id in orphans:
                level_map = node_level_map.get(node_id, {})
                l1 = level_map.get(1)
                l0 = level_map.get(0)
                if l1 and l1 in l1_positions:
                    l1_orphan_groups.setdefault(l1, []).append(node_id)
                elif l0 and l0 in l0_positions:
                    l0_orphan_groups.setdefault(l0, []).append(node_id)
                else:
                    truly_orphan.append(node_id)

            for l1_cid, members in l1_orphan_groups.items():
                cx, cy, cz = l1_positions[l1_cid]
                n = len(members)
                node_r = min(
                    SOLAR_L1_RING_BASE * 0.7 * math.sqrt(max(n, 1)),
                    SOLAR_L1_RING_MAX * 0.5,
                )
                pts = _fibonacci_sphere(n, node_r)
                for i, node_id in enumerate(sorted(members)):
                    jx, jy, jz = _deterministic_jitter(node_id, 2.0)
                    px, py, pz = pts[i]
                    positions[node_id] = (cx + px + jx, cy + py + jy, cz + pz + jz)

            for l0_cid, members in l0_orphan_groups.items():
                cx, cy, cz = l0_positions[l0_cid]
                n = len(members)
                node_r = min(
                    SOLAR_L0_RING_BASE * 0.4 * math.sqrt(max(n, 1)),
                    SOLAR_L0_RING_MAX * 0.3,
                )
                pts = _fibonacci_sphere(n, node_r)
                for i, node_id in enumerate(sorted(members)):
                    jx, jy, jz = _deterministic_jitter(node_id, 2.0)
                    px, py, pz = pts[i]
                    positions[node_id] = (cx + px + jx, cy + py + jy, cz + pz + jz)

            # Truly orphan — scatter through the universe volume (cube-root density).
            if truly_orphan:
                max_r = SOLAR_UNIVERSE_RADIUS * 1.4
                min_r = 80.0
                for node_id in truly_orphan:
                    h = int(
                        hashlib.md5(
                            node_id.encode(), usedforsecurity=False
                        ).hexdigest(),
                        16,
                    )
                    cos_theta = ((h & 0xFFFF) / 65535.0) * 2.0 - 1.0
                    phi = ((h >> 16) & 0xFFFF) / 65535.0 * 2.0 * math.pi
                    sin_theta = math.sqrt(max(0.0, 1.0 - cos_theta * cos_theta))
                    nx = sin_theta * math.cos(phi)
                    ny = cos_theta
                    nz = sin_theta * math.sin(phi)
                    r_frac = ((h >> 32) & 0xFFFFFF) / float(0xFFFFFF)
                    r = min_r + (r_frac ** (1.0 / 3.0)) * (max_r - min_r)
                    positions[node_id] = (nx * r, ny * r, nz * r)

    return positions

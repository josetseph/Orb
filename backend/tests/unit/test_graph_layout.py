"""Unit tests for app/utils/graph_layout.py — pure geometry functions.

All functions under test are deterministic and require no I/O or mocking.
"""

import math

import pytest

from app.utils.graph_layout import (
    SOLAR_UNIVERSE_RADIUS,
    _deterministic_jitter,
    _fibonacci_sphere,
    compute_solar_positions,
)


def _dist(p, q=(0.0, 0.0, 0.0)):
    """Euclidean distance between two 3D points."""
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(p, q)))


def _comm(cid, level):
    return {"community_id": cid, "community_level": level, "name": f"C-{cid}"}


# ── _fibonacci_sphere ─────────────────────────────────────────────────────────


class TestFibonacciSphere:
    def test_empty_for_zero(self):
        assert _fibonacci_sphere(0, 100.0) == []

    def test_empty_for_negative(self):
        assert _fibonacci_sphere(-5, 100.0) == []

    def test_returns_exactly_n_points(self):
        assert len(_fibonacci_sphere(15, 100.0)) == 15

    def test_all_points_on_sphere_surface(self):
        radius = 300.0
        for p in _fibonacci_sphere(30, radius):
            d = _dist(p)
            assert (
                abs(d - radius) / radius < 0.01
            ), f"Point distance {d:.3f} is not within 1% of radius {radius}"

    def test_all_points_distinct(self):
        pts = _fibonacci_sphere(20, 100.0)
        assert len(set(pts)) == len(pts)

    def test_single_point_on_radius(self):
        pts = _fibonacci_sphere(1, 500.0)
        assert len(pts) == 1
        assert abs(_dist(pts[0]) - 500.0) < 0.001


# ── _deterministic_jitter ─────────────────────────────────────────────────────


class TestDeterministicJitter:
    def test_same_seed_same_result(self):
        assert _deterministic_jitter("alice", 10.0) == _deterministic_jitter(
            "alice", 10.0
        )

    def test_different_seeds_produce_different_results(self):
        assert _deterministic_jitter("alice", 10.0) != _deterministic_jitter(
            "bob", 10.0
        )

    def test_components_within_range(self):
        max_offset = 15.0
        jx, jy, jz = _deterministic_jitter("test_seed", max_offset)
        assert -max_offset <= jx <= max_offset
        assert -max_offset <= jy <= max_offset
        assert -max_offset <= jz <= max_offset

    def test_zero_offset_returns_zeros(self):
        jx, jy, jz = _deterministic_jitter("any_seed", 0.0)
        assert jx == pytest.approx(0.0, abs=1e-9)
        assert jy == pytest.approx(0.0, abs=1e-9)
        assert jz == pytest.approx(0.0, abs=1e-9)


# ── compute_solar_positions ───────────────────────────────────────────────────


class TestComputeSolarPositions:
    """Behavioral black-box tests for the solar-system hierarchical layout."""

    def _make_input(self):
        communities = [
            _comm("l0_a", 0),
            _comm("l1_a", 1),
            _comm("l2_a", 2),
        ]
        node_level_map = {
            "n1": {0: "l0_a", 1: "l1_a", 2: "l2_a"},
            "n2": {0: "l0_a", 1: "l1_a", 2: "l2_a"},
        }
        all_node_ids = ["n1", "n2", "orphan1"]
        return communities, node_level_map, all_node_ids

    def test_all_node_ids_in_output(self):
        communities, node_level_map, all_node_ids = self._make_input()
        positions = compute_solar_positions(communities, node_level_map, all_node_ids)
        for nid in all_node_ids:
            assert nid in positions, f"Node {nid!r} missing from solar positions"

    def test_all_community_ids_in_output(self):
        communities, node_level_map, all_node_ids = self._make_input()
        positions = compute_solar_positions(communities, node_level_map, all_node_ids)
        for c in communities:
            assert c["community_id"] in positions

    def test_l0_centres_near_universe_radius(self):
        communities, node_level_map, all_node_ids = self._make_input()
        positions = compute_solar_positions(communities, node_level_map, all_node_ids)
        d = _dist(positions["l0_a"])
        assert d == pytest.approx(
            SOLAR_UNIVERSE_RADIUS, rel=0.05
        ), f"L0 community at d={d:.1f}, expected ~{SOLAR_UNIVERSE_RADIUS}"

    def test_output_is_deterministic(self):
        communities, node_level_map, all_node_ids = self._make_input()
        first = compute_solar_positions(communities, node_level_map, all_node_ids)
        second = compute_solar_positions(communities, node_level_map, all_node_ids)
        assert first == second

    def test_no_communities_all_nodes_are_orphans(self):
        positions = compute_solar_positions([], {}, ["x", "y"])
        assert "x" in positions
        assert "y" in positions

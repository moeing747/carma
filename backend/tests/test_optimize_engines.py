"""Both OptimizationEngine implementations against the same contract.

The engines differ in how well they spend the hold budget, not in what a
valid plan is — the shared property tests pin the contract, the
known-optimal instances pin CP-SAT's objective.
"""

import random

import pytest

from carma.adapters.optimize_cpsat import CpSatOptimizationEngine
from carma.adapters.optimize_heuristic import HeuristicOptimizationEngine
from carma.application.ports import OptimizationEngine, OptimizationRequest
from carma.domain.headway import (
    MAX_HOLD_SECONDS,
    LineVehicle,
    gaps,
    max_abs_deviation,
)
from carma.domain.models import TripId

ENGINES: tuple[OptimizationEngine, ...] = (
    CpSatOptimizationEngine(),
    HeuristicOptimizationEngine(),
)


def _request(positions: list[float]) -> OptimizationRequest:
    vehicles = tuple(
        LineVehicle(
            trip_id=TripId(f"trip-{index}"),
            position_seconds=position,
            delay_seconds=0,
            next_stop_id=f"S{index}",
            next_stop_name=f"Stop {index}",
        )
        for index, position in enumerate(positions)
    )
    return OptimizationRequest(route_short_name="M10", direction="Endstop", vehicles=vehicles)


@pytest.mark.parametrize("engine", ENGINES, ids=lambda engine: engine.name)
def test_bunched_pair_is_held_apart(engine: OptimizationEngine) -> None:
    # Vehicle 1 sits 100s behind the leader with 900s of clear road behind
    # it. Optimal within the 300s budget: hold it the full 300s.
    plan = engine.solve(_request([1000.0, 900.0, 0.0]))

    assert [rec.hold_seconds for rec in plan.recommendations] == [0, 300, 0]
    assert [rec.headway_after_seconds for rec in plan.recommendations] == [None, 400.0, 600.0]
    assert plan.summary.headway_stddev_before_seconds == 400.0
    assert plan.summary.headway_stddev_after_seconds == 100.0


@pytest.mark.parametrize("engine", ENGINES, ids=lambda engine: engine.name)
def test_even_line_gets_the_zero_plan(engine: OptimizationEngine) -> None:
    plan = engine.solve(_request([1000.0, 500.0, 0.0]))

    assert all(rec.hold_seconds == 0 for rec in plan.recommendations)
    assert plan.summary.headway_stddev_after_seconds == 0.0


def test_cpsat_solves_the_exactly_balanceable_instance() -> None:
    # Gaps [100, 500]: holding the middle vehicle 200s makes both 300 —
    # a perfectly even line, provably optimal (deviation 0).
    plan = CpSatOptimizationEngine().solve(_request([600.0, 500.0, 0.0]))

    assert [rec.hold_seconds for rec in plan.recommendations] == [0, 200, 0]
    assert plan.summary.headway_stddev_after_seconds == 0.0


@pytest.mark.parametrize("engine", ENGINES, ids=lambda engine: engine.name)
@pytest.mark.parametrize("seed", range(12))
def test_contract_properties_on_random_fleets(engine: OptimizationEngine, seed: int) -> None:
    rng = random.Random(seed)
    count = rng.randint(3, 8)
    positions = sorted(
        (rng.uniform(0, 3600) for _ in range(count)), reverse=True
    )

    plan = engine.solve(_request(positions))

    holds = [rec.hold_seconds for rec in plan.recommendations]
    # Holds stay within budget, and the leader-first order is request order.
    assert all(0 <= hold <= MAX_HOLD_SECONDS for hold in holds)
    assert [rec.trip_id.value for rec in plan.recommendations] == [
        f"trip-{index}" for index in range(count)
    ]
    # Held vehicles never overtake each other backwards: projected headways
    # are non-negative and consistent with the reported holds.
    shifted = [position - hold for position, hold in zip(positions, holds, strict=True)]
    after = gaps(shifted)
    assert all(gap >= 0 for gap in after)
    assert [rec.headway_after_seconds for rec in plan.recommendations][1:] == list(after)
    # Advice never worsens the spread — on either measure.
    summary = plan.summary
    assert summary.headway_stddev_after_seconds <= summary.headway_stddev_before_seconds + 1e-9
    assert max_abs_deviation(after) <= max_abs_deviation(gaps(positions)) + 1e-9


@pytest.mark.parametrize("engine", ENGINES, ids=lambda engine: engine.name)
def test_degenerate_fleets_get_empty_holds(engine: OptimizationEngine) -> None:
    for positions in ([], [500.0], [500.0, 100.0]):
        plan = engine.solve(_request(positions))
        assert all(rec.hold_seconds == 0 for rec in plan.recommendations)
        assert plan.summary.vehicle_count == len(positions)

"""OR-Tools CP-SAT implementation of the OptimizationEngine port.

Deliberately naive min-max headway model over integer seconds:

    variables    h_i in [0, H]         hold at vehicle i's next stop
    positions    p'_i = p_i - h_i      holding moves a vehicle back in
                                       pattern time (carma.domain.headway)
    gaps         g_k = p'_k - p'_{k+1} consecutive headways, leader first
    constraints  g_k >= 0              holds must not reorder vehicles
    objective    minimize max_k |(n-1) * g_k - sum(g)|
                                       the largest deviation from the mean
                                       gap, scaled by (n-1) to stay integer;
                                       ties broken toward fewer held seconds

Zero holds are always feasible, so the optimum never spreads headways
further apart than doing nothing. A real dispatching engine would swap in
behind the same port without touching anything else.
"""

from dataclasses import dataclass

from ortools.sat.python import cp_model

from carma.application.ports import OptimizationRequest
from carma.domain.headway import HeadwayPlan, build_plan, order_leader_first

_SOLVE_TIME_LIMIT_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class CpSatOptimizationEngine:
    name: str = "cpsat"

    def solve(self, request: OptimizationRequest) -> HeadwayPlan:
        vehicles = order_leader_first(request.vehicles)
        count = len(vehicles)
        if count < 2:
            return build_plan(vehicles, [0] * count)
        positions = [round(vehicle.position_seconds) for vehicle in vehicles]
        gap_count = count - 1
        max_hold = request.max_hold_seconds

        model = cp_model.CpModel()
        holds = [model.new_int_var(0, max_hold, f"hold_{i}") for i in range(count)]
        gaps = [
            positions[k] - holds[k] - (positions[k + 1] - holds[k + 1])
            for k in range(gap_count)
        ]
        for gap in gaps:
            model.add(gap >= 0)
        total = sum(gaps)
        deviation_bound = gap_count * ((positions[0] - positions[-1]) + 2 * max_hold + 1)
        deviation = model.new_int_var(0, deviation_bound, "max_deviation")
        for gap in gaps:
            model.add(gap_count * gap - total <= deviation)
            model.add(total - gap_count * gap <= deviation)
        # Lexicographic: spread first, then the fewest held seconds — so an
        # already even line gets the zero plan, not an arbitrary tie.
        model.minimize(deviation * (count * max_hold + 1) + sum(holds))

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = _SOLVE_TIME_LIMIT_SECONDS
        solver.parameters.num_workers = 1  # deterministic plans
        status = solver.solve(model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            raise RuntimeError(f"CP-SAT ended with status {solver.status_name(status)}")
        return build_plan(vehicles, [int(solver.value(hold)) for hold in holds])

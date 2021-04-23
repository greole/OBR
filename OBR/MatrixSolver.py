#!/usr/bin/env python3

from OBR.Setter import Setter
from pathlib import Path
import OBR.setFunctions as sf


class SolverSetter(Setter):
    def __init__(
        self,
        base_path,
        solver,
        field,
        case_name,
        preconditioner="none",
        tolerance="1e-06",
        min_iters="0",
        max_iters="1000",
        update_sys_matrix="no",
    ):

        super().__init__(
            base_path=base_path,
            variation_name="{}-{}".format(field, solver),
            case_name=case_name,
        )
        self.solver = solver
        self.preconditioner = preconditioner
        self.update_sys_matrix = update_sys_matrix
        self.tolerance = tolerance
        self.min_iters = min_iters
        self.max_iters = max_iters

    def set_domain(self, domain):
        self.domain = self.avail_domain_handler[domain]
        self.add_property(self.domain.name)
        return self

    def set_executor(self, executor):
        self.domain.executor = executor
        self.add_property(executor.name)

    def set_up(self):
        print("setting solver", self.prefix, self.solver, self.domain.executor.name)
        matrix_solver = self.prefix + self.solver
        # fmt: off
        solver_str = (
            '"p.*"{\\n'
            + "solver {};\
\\ntolerance {};\
\\nrelTol 0.0;\
\\nsmoother none;\
\\npreconditioner {};\
\\nminIter {};\
\\nmaxIter {};\
\\nupdateSysMatrix {};\
\\nsort yes;\
\\nexecutor {};".format(
                matrix_solver,
                self.tolerance,
                self.preconditioner,
                self.min_iters,
                self.max_iters,
                self.update_sys_matrix,
                self.domain.executor.name
            )
        )
        # fmt: on
        print(solver_str, self.controlDict)
        sf.sed(self.fvSolution, "p{}", solver_str)


# Executor


class GKOExecutor:
    def __init__(self, name):
        self.name = name


class RefExecutor(GKOExecutor):
    def __init__(self):
        super().__init__(name="Reference")


class OMPExecutor(GKOExecutor):
    def __init__(self):
        super().__init__(name="omp")


class CUDAExecutor(GKOExecutor):
    def __init__(self):
        super().__init__(name="cuda")


# Domain handler


class OF:

    name = "OF"
    executor_support = ["MPI", "Ref"]
    executor = None

    def __init__(self, prefix="P"):
        self.prefix = prefix


class GKO:

    name = "GKO"
    prefix = "GKO"
    executor_support = ["OMP", "CUDA", "Ref"]
    executor = None

    def __init__(self):
        pass


# Solver


class CG(SolverSetter):
    def __init__(
        self,
        base_path,
        field,
        case_name,
    ):
        name = "CG"
        super().__init__(
            base_path=base_path,
            solver=name,
            field=field,
            case_name=case_name,
        )
        self.avail_domain_handler = {"OF": OF(), "GKO": GKO()}


class BiCGStab(SolverSetter):
    def __init__(
        self,
        base_path,
        field,
        case_name,
    ):
        name = "BiCGStab"
        super().__init__(
            base_path=base_path,
            solver=name,
            field=field,
            case_name=case_name,
        )
        self.avail_domain_handler = {"OF": OF(), "GKO": GKO()}


class smooth(SolverSetter):
    def __init__(
        self,
        base_path,
        field,
        case_name,
    ):
        name = "smooth"
        super().__init__(
            base_path=base_path,
            solver=name,
            field=field,
            case_name=case_name,
        )
        self.avail_domain_handler = {"OF": OF(prefix="")}


class IR(SolverSetter):
    def __init__(
        self,
        base_path,
        field,
        case_name,
    ):
        name = "IR"
        super().__init__(
            base_path=base_path,
            solver=name,
            field=field,
            case_name=case_name,
        )
        self.avail_domain_handler = {"GKO": GKO()}
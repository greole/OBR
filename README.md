**[Installation](#Installation)** |
**[Usage](#Usage)** |
**[Workspace](#Workspace)** |
**[Environmental variables](#Environmental_variables)** |
---
# OBR - OpenFOAM Benchmark Runner
![Tests](https://github.com/hpsim/obr/actions/workflows/test.yaml/badge.svg)
![Coverage](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/greole/70b77e941a906fc3863661697ea8e864/raw/covbadge.json)
[![Documentation](https://img.shields.io/badge/Documentation-blue.svg)](https://obr.readthedocs.io/)
[![License](https://img.shields.io/badge/License-BSD_3--Clause-blue.svg)](https://opensource.org/licenses/BSD-3-Clause)
<!-- Overview -->
The OpenFOAM Benchmark Runner (OBR) is an experimental workflow manager for
simple provisioning of complex parameter studies and reproducible simulations.
A typical OpenFOAM workflow of setting up a case, performing various parameter
manipulations, and serial or parallel execution, can be defined using the yaml markup language. OBR is build on
top of [signac](https://github.com/glotzerlab/signac).

<!-- Installation -->
## Installation
As of now, we recommend to clone the repository and to perform a symlink install of OBR into your virtual environment via pip:

```
pip install -e .
```

## Usage

The benchmark runner is split into several layers:
1. [case definition](https://obr.readthedocs.io/en/latest/overview/case.html) via yaml files
2. [case generation](https://obr.readthedocs.io/en/latest/overview/generate.html) via `obr init` and `obr run -o generate`
3. [run or submit solver execution](https://obr.readthedocs.io/en/latest/overview/submit.html) via `obr run -o runParallelSolver` or `obr submit -o runParallelSolver`
4. [postprocessing cases](https://obr.readthedocs.io/en/latest/overview/postProcessing.html) **Experimental** via, `obr apply` and `obr archive`

### 1. Case definition
The [micro_benchmarks repository](https://github.com/exasim-project/micro_benchmarks.git) provides a good point to start learning from. After cloning the repository, `cd` into the `LidDrivenCavity3D` directory, where an [example yaml](https://github.com/exasim-project/micro_benchmarks/blob/main/LidDrivenCavity3D/assets/scaling.yaml) file can be found in the case assets folder. Another example of a workflow is shown next

```
case:
    type: CaseOnDisk
    solver: pimpleFoam
    origin: ${{yaml.location}}/../basicSetup
variation:
    - operation: fvSolution
      schema: "linear_solver/{solver}{preconditioner}{executor}"
      values:
        - set: solvers/p
          preconditioner: none
          solver: GKOCG
          forceHostBuffer: 1
          verbose: 1
          executor: ${{env.GINKGO_EXECUTOR}}
        - set: solvers/p
          preconditioner: IC
          solver: GKOCG
          forceHostBuffer: 1
          verbose: 1
          executor: ${{env.GINKGO_EXECUTOR}}
```

The workflow copies an existing case on disk and creates a [Workspace](#Workspace).

### 2. Case generation

In general, to create a tree of case variations run

    obr init --folder [path] --config path-to-config.yaml

Within the context of the `micro_benchmarks` example, simply run

    obr init --folder . --config assets/scaling.yaml

OBR should now print some output, followed by `INFO: successfully initialized`.

Finally,  operations on a tree can be run with the `obr run` command-line option, for example `fetchCase`, which is responsible for copying the base case into the workspace:

    obr run -o fetchCase --folder path-to-tree

Or, in this specific example (the default of `--folder` is `.`):

    obr run -o fetchCase

Within `LidDrivenCavity3D/workspace` should now have appeared a multitude of directories (=jobs), which are in the form of a UID eg. `78e2de3e6205144311d04282001fe21f`. Each job represents a distinct operation such as modifying the blockMeshDict and call blockMesh including its dependencies. The order in which operations can be applied are defined by the `config.json`, however, running

    obr run -o generate

also runs all defined operations in the appropriate order.

### 3. Job submission on HPC cluster

On HPC cluster OBR can submit operations via the job queue. For example

    obr submit -o blockMesh

will submit the `blockMesh` operation to the cluster manager for every job that is eligible. OBR detects the installed job queuing system, eg. slurm, pbs, etc. A jobs ubmission script will be generated automatically. For fine grained control over the submission script the `--template` argument allows to specify the location of a submission script template. Since OBR uses signac for job submission more details on how to write job submission templates can be found [here](https://docs.signac.io/en/latest/templates.html). To avoid submitting numereous jobs individually, the `--bundling-key` argument can be used to bundle all jobs for which the bundling key has the same value into the same job.

### 4. Postprocessing cases

Since OBR aims at performing parameter studies containing a larger number of individual cases, postprocessing cases manually should be avoided. Its is recommended to use `obr apply` instead.

    obr apply --file script.py --campaign ogl_170

The passed `script.py` file must implement a `call(jobs: list[Job], kwargs={})` function. On execution this gets a list of jobs which allow access to the case paths.

The mandatory `--campaign` argument is used to separate (families of) experiments. The optional argument `--tag <str>` can be used to further differentiate between experiments within a campaign. Internally, this will create subfolders as such:

    fd52708db5c296d1fa52b056701be4ee
        ├── campaign1
        │   ├── tag1
        │   │   ├── decomposePar_2024-01-05_17:38:44.log
        │   │   ├── instrumentedPimpleFoam_2024-01-05_17:44:32.log
        │   │   └── solverExitCode.log
        │   └── tag2
        │       ├── decomposePar_2024-01-05_17:38:44.log
        │       ├── instrumentedPimpleFoam_2024-01-05_17:44:32.log
        │       └── solverExitCode.log
        └── campaign2    

## Workspace

## Environmental variables

OBR workflows often rely on environmental variables to adapt a workflow to specific node or cluster properties. In workflow.yaml files for example `${{env.HOST}}` is replaced by
`$HOST`. Additionally, `OBR_RUN_CMD` defines the exact command to execute for parallel runs and `OBR_PREFLIGHT` can call a script to verify your environment just before the solver execution.

    export OBR_RUN_CMD="mpirun --bind-to core --map-by core -np {np} {solver} -parallel -case {path}/case >  {path}/case/{solver}_{timestamp}.log 2>&1"
    export OBR_PREFLIGHT="python3 $HOME/data/code/exasim_project/micro_benchmarks/common/preflight.py"

Additionally, `OBR_SKIP_COMPLETE` defines if a already complete run should be repeated.


## Contributing

To contribute to this project, we suggest creating an issue. From then on, we can discuss further development.

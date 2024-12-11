#!/usr/bin/env python3
import os
import re
import logging

from typing import Union, Generator, Tuple, Any
from pathlib import Path
from subprocess import check_output
from signac.job import Job
from datetime import datetime
from Owls.parser.FoamDict import FileParser
from Owls.parser.LogFile import LogFile

from ..core.core import (
    logged_execute,
    logged_func,
    modifies_file,
    path_to_key,
    TemporaryFolder,
    DelinkFolder,
    find_time_folder,
)
from .BlockMesh import BlockMesh, calculate_simple_partition

logger = logging.getLogger("OBR")

OF_HEADER_REGEX = r"""(/\*--------------------------------\*- C\+\+ -\*----------------------------------\*\\
(\||)\s*=========                 \|(\s*\||)
(\||)\s*\\\\      /  F ield         \| (OpenFOAM:|foam-extend:)\s*[\d\w\W]*\s*(\||)
(\||)\s*\\\\    /   O peration     \| (Version:|Website:)\s*[\d\w\W]*\s*(\||)
(\||)\s*\\\\  /    A nd           \| (Web:|Version:|Website)\s*[\d\w\W]*\s*(\||)
(\||)\s*\\\\/     M anipulation  \|(\s*\||)
\\\*---------------------------------------------------------------------------\*/)"""


class File(FileParser):
    def __init__(self, **kwargs):
        # forwards all unused arguments
        self._file = kwargs["file"]
        self._folder = kwargs["folder"]
        self.job = kwargs["job"]
        kwargs["path"] = Path(self._folder) / self._file
        if not kwargs["path"].exists():
            self.path = kwargs["path"]
            self.missing = True  # indicate that the file is currently missing
            return
        super().__init__(**kwargs, skip_update=True)
        self._md5sum = None

    def get(self, name: str):
        """Get a value from an OpenFOAM dictionary file"""
        # TODO replace with a safer option
        # also consider moving that to Owls
        self.update()
        try:
            return eval(super().get(name))
        except:
            return super().get(name)

    def md5sum(self, refresh=False) -> str:
        """Compute a files md5sum"""
        if not self.path.exists():
            raise FileNotFoundError(self.path)
        if not self._md5sum or refresh:
            self._md5sum = check_output(["md5sum", str(self.path)], text=True)
        return self._md5sum

    def is_modified(self) -> bool:
        if not self._md5sum:
            return False
        current_md5sum = check_output(["md5sum", str(self.path)], text=True)
        return self._md5sum != current_md5sum

    # @decorator_modifies_file
    def set(self, args: dict):
        """modifies the current controlDict by the given dictionary

        if the key exists in the controlDict the values are replaced
        non-existent keys are added
        """
        args_copy = {k: v for k, v in args.items()}

        modifies_file(self.path)
        self.update()
        if self.job:
            logged_func(
                self.set_key_value_pairs,
                # FIXME add job.doc reference
                self.job.doc,
                dictionary=args_copy,
            )
        else:
            self.set_key_value_pairs(args_copy)

        self.update()
        self.md5sum(refresh=True)


class OpenFOAMCase(BlockMesh):
    """A class for simple access to typical OpenFOAM files"""

    latest_log_path_: Path = Path()

    def __init__(self, path, job):
        self.path_ = Path(path)
        self.job: Job = job

        # Non-optional files system folder files
        self.controlDict = File(folder=self.system_folder, file="controlDict", job=job)
        self.fvSolution = File(folder=self.system_folder, file="fvSolution", job=job)
        self.fvSchemes = File(folder=self.system_folder, file="fvSchemes", job=job)

        self.transportProperties = File(
            folder=self.constant_folder, file="transportProperties", job=job
        )

        # optional but commonly used files
        self.decomposeParDict = False
        if Path(self.system_folder / "decomposeParDict").exists():
            self.decomposeParDict = File(
                folder=self.system_folder, file="decomposeParDict", job=job
            )

        self.turbulenceProperties = False
        if Path(self.constant_folder / "turbulenceProperties").exists():
            self.turbulenceProperties = File(
                folder=self.constant_folder, file="turbulenceProperties", job=job
            )

        self.file_dict: dict[str, File] = dict()
        self.config_file_tree

    @property
    def path(self) -> Path:
        return self.path_

    @property
    def system_folder(self) -> Path:
        return self.path / "system"

    @property
    def constant_folder(self) -> Path:
        return self.path / "constant"

    @property
    def const_polyMesh_folder(self) -> Path:
        return self.constant_folder / "polyMesh"

    @property
    def system_include_folder(self) -> Path:
        return self.system_folder / "include"

    @property
    def zero_folder(self) -> Path:
        """TODO check for 0.orig folder"""
        return self.path / "0"

    @property
    def init_p(self) -> Path:
        return self.zero_folder / "p"

    @property
    def init_U(self) -> Path:
        return self.zero_folder / "U.orig"

    @property
    def is_decomposed(self) -> bool:
        proc_zero = self.path / "processor0"
        if not proc_zero.exists():
            return False
        return True

    @property
    def time_folder(self) -> list[Path]:
        """Returns all timestep folder"""
        return find_time_folder(self.path)

    @property
    def processor_folder(self) -> list[Path]:
        if not self.is_decomposed:
            return []
        _, folds, files = next(os.walk(self.path))
        proc_folds = [self.path / f for f in folds if "processor" in f]
        return proc_folds

    def config_files_in_folder(
        self, folder: Path
    ) -> Generator[Tuple[File, str], Any, None]:
        """Yields all OF config files  in given folder"""
        if not folder:
            # const_polymesh and system_include can be None
            return
        if folder.is_dir():
            for f_path in folder.iterdir():
                if f_path.is_file() and not f_path.is_symlink():
                    if self.has_openfoam_header(f_path):
                        rel_path = str(f_path.relative_to(self.path))
                        file_obj = File(folder=folder, file=f_path.name, job=self.job)
                        yield file_obj, rel_path

    @property
    def current_time(self) -> float:
        """Returns the current timestep of the simulation"""
        self.fetch_latest_log()
        return self.latest_log.latestTime.time

    @property
    def progress(self) -> float:
        """Returns the progress of the simulation in percent"""
        return self.current_time / float(self.controlDict.get("endTime"))

    @property
    def latest_solver_log_path(self) -> Path:
        """Returns the absolute path to the latest log"""
        self.fetch_latest_log()
        return self.latest_log_path_

    @property
    def latest_log(self) -> LogFile:
        """Returns handle to the latest log"""
        log = self.latest_solver_log_path
        if not log.exists():
            raise ValueError("No Logfile found")
        self.latest_log_handle_ = LogFile(log, matcher=[])
        return self.latest_log_handle_

    @property
    def finished(self) -> bool:
        """check if the latest simulation run has finished gracefully"""
        if self.process_latest_time_stats():
            return self.latest_log.footer.completed
        return False

    @property
    def solver(self):
        return self.controlDict.get("application")

    def fetch_logs(self) -> list[Path]:
        solver = self.solver
        root, _, files = next(os.walk(self.path))
        log_files = [
            Path(root) / f for f in files if f.endswith(".log") and f.startswith(solver)
        ]
        log_files.sort()
        return log_files

    def fetch_latest_log(self) -> None:
        log_files = self.fetch_logs()
        if log_files:
            self.latest_log_path_ = log_files[-1]
        else:
            self.latest_log_path_ = None

    @property
    def config_file_tree(self) -> list[str]:
        """Iterates through case file tree and returns a list of paths to non-symlinked files."""
        for file, rel_path in self.config_files_in_folder(self.system_folder):
            self.file_dict[rel_path] = file
        for file, rel_path in self.config_files_in_folder(self.constant_folder):
            self.file_dict[rel_path] = file
        for file, rel_path in self.config_files_in_folder(self.system_include_folder):
            self.file_dict[rel_path] = file
        # TODO dont try to create File object for polyMesh files because that might
        # take very long
        # for file, rel_path in self.config_files_in_folder(self.const_polyMesh_folder):
        #     self.file_dict[rel_path] = file
        return list(self.file_dict.keys())

    def get(self, key: str) -> Union[File, None]:
        return self.file_dict.get(key, None)

    def has_openfoam_header(self, path: Path) -> bool:
        with path.open() as f:
            try:
                header = "".join(f.readlines()[:7])
                return re.match(OF_HEADER_REGEX, header) is not None
            except UnicodeDecodeError:
                return False

    def _exec_operation(self, operation) -> Path:
        return logged_execute(operation, self.path, self.job.doc)

    @property
    def esi_version(self) -> bool:
        """Check if esi version of OpenFOAM is sourced"""
        wm_project_dir = os.environ.get("WM_PROJECT_DIR")
        if not wm_project_dir:
            raise AssertionError("OpenFOAM not sourced. Cannot check OpenFOAM version")
        return (Path(wm_project_dir) / "META-INFO").exists()

    def reset_case(self):
        """Removes all artifacts after case generation"""
        self.remove_solver_logs()

        self.job.doc["state"]["global"] = "ready"

    def decomposePar(self, args={}):
        """Sets decomposeParDict and calls decomposePar. If no decomposeParDict exists a new one
        gets created"""
        tmp_zero = None
        if not self.time_folder:
            logger.warning(
                f"Problem decomposing case {self.job.id}. No time folder found!"
                " Decomposition might lead to an unusable case."
            )
            zero_orig_path = self.path / "0.orig"
            if zero_orig_path.exists():
                logger.info(
                    f"Decomposing case {self.job.id}, Using existing 0.orig folder"
                )
                zero_target_path = self.path / "0"
                tmp_zero = TemporaryFolder(
                    zero_orig_path, zero_target_path, not self.esi_version
                )

        tmp_constant_folder = None
        tmp_zero_folder = None
        if not self.esi_version:
            logger.warning(f"Non ESI version of foam detected! Delinking symlinks")
            tmp_constant_folder = DelinkFolder(self.constant_folder)
            tmp_zero_folder = DelinkFolder(self.path / "0")

        if not self.decomposeParDict:
            decomposeParDictFile = Path(self.system_folder / "decomposeParDict")
            with open(decomposeParDictFile, "a") as fh:
                # call get to trigger read
                self.controlDict.update()
                fh.write("".join(self.controlDict.of_comment_header))
                fh.write("".join(self.controlDict.of_header))
                fh.write("\n")
            self.decomposeParDict = File(
                folder=self.system_folder, file="decomposeParDict", job=self.job
            )

        method = args["method"]
        numberSubDomains = int(float(args.get("numberOfSubdomains", 0)))
        if method == "simple":
            if not numberSubDomains:
                coeffs = [int(i) for i in args["coeffs"]]
                numberSubDomains = coeffs[0] * coeffs[1] * coeffs[2]
            else:
                coeffs = args.get("coeffs", None)
                if not coeffs:
                    coeffs = calculate_simple_partition(numberSubDomains, [1, 1, 1])

            self.decomposeParDict.set({
                "method": method,
                "numberOfSubdomains": numberSubDomains,
                "simpleCoeffs": {"n": coeffs},
            })
        else:
            self.decomposeParDict.set({
                "method": method,
                "numberOfSubdomains": numberSubDomains,
            })

        log = self._exec_operation(["decomposePar", "-force"])

        if tmp_zero_folder:
            tmp_zero_folder.tear_down()
        if tmp_constant_folder:
            tmp_constant_folder.tear_down()

        fvSolutionArgs = args.get("fvSolution", {})
        if fvSolutionArgs:
            self.fvSolution.set(fvSolutionArgs)

        if not find_time_folder(self.path / "processor0"):
            logger.warning(
                f"Decomposing case {self.job.id}. No time in processor folder found."
                " This indicates an unusable decomposition."
            )
        return log

    def setKeyValuePair(self, args: dict):
        path = Path(args.pop("file"))
        file_handle = File(
            folder=self.path_ / path.parents[0], file=path.parts[-1], job=self.job
        )
        file_handle.set_key_value_pairs(args)

    def run(self, args: dict):
        solver = self.controlDict.get("application")
        return self._exec_operation([solver])

    def is_file_modified(self, file: str) -> bool:
        """Checks if a file has been modified by comparing the current md5sum with
        the previously saved one inside `self.job.dict`.
        """
        if "md5sum" not in self.job.doc["cache"]:
            return False  # no md5sum has been calculated for this file
        current_md5sum, last_modified = self.job.doc["cache"]["md5sum"].get(file)
        if os.path.getmtime(self.path / file) == last_modified:
            # if modification dates dont differ, the md5sums wont, either
            return False
        md5sum = check_output(["md5sum", file], text=True)
        return current_md5sum != md5sum

    def is_tree_modified(self) -> list[str]:
        """Iterates all files inside the case tree and returns a list of files that
        were modified, based on their md5sum.
        """
        m_files = []
        for file in self.config_file_tree:
            if self.is_file_modified(file):
                m_files.append(file)
        return m_files

    def process_latest_time_stats(self) -> bool:
        """This function parses the latest time step log and stores the results in
        the job document.

        Return: A boolean indication whether processing was successful
        """
        if not self.latest_log:
            return False

        # TODO eventually this should be part of OWLS
        # Check for failure states
        if "There are not enough slots available" in self.latest_log.footer.content:
            self.job.doc["state"]["global"] = "failure"
            self.job.doc["state"]["failureState"] = "MPI startup error"
            # No reason for further parsing
            return False

        if "ERROR" in self.latest_log.footer.content:
            self.job.doc["state"]["global"] = "failure"
            self.job.doc["state"]["failureState"] = "FOAM ERROR"

        if "error" in self.latest_log.footer.content:
            self.job.doc["state"]["global"] = "failure"

        try:
            self.job.doc["state"]["global"] = "incomplete"
            self.job.doc["state"]["latestTime"] = self.latest_log.latestTime.time
            self.job.doc["state"][
                "continuityErrors"
            ] = self.latest_log.latestTime.continuity_errors
            self.job.doc["state"][
                "CourantNumber"
            ] = self.latest_log.latestTime.Courant_number
            self.job.doc["state"]["ExecutionTime"] = (
                self.latest_log.latestTime.execution_time["ExecutionTime"]
            )
            self.job.doc["state"]["ClockTime"] = (
                self.latest_log.latestTime.execution_time["ClockTime"]
            )
            if self.latest_log.footer.completed:
                self.job.doc["state"]["global"] = "completed"
            return True
        except Exception:
            # if parsing of log file fails, check failure handler
            self.job.doc["state"]["global"] = "failure"
            return False

    def detailed_update(self):
        """Perform a detailed update on the job doc state"""
        self.process_latest_time_stats()

    def remove_solver_logs(self):
        """Search for solver logs and deletes them"""
        for log in self.fetch_logs():
            log = self._exec_operation(["rm", str(log)])

    def perform_post_md5sum_calculations(self):
        """
        calculates md5sums for all case files. Primarily called from `dispatch_post_hooks`
        """
        for case_path in self.config_file_tree:
            case_file = Path(self.job.path) / "case" / case_path
            md5sum = check_output(["md5sum", case_file], text=True)
            if "md5sum" not in self.job.doc["cache"]:
                self.job.doc["cache"]["md5sum"] = dict()
            signac_friendly_path = path_to_key(
                str(case_path)
            )  # signac does not allow . inside paths or job.doc keys
            last_modified = os.path.getmtime(case_file)
            self.job.doc["cache"]["md5sum"][signac_friendly_path] = (
                md5sum.split()[0],
                last_modified,
            )

    def was_successful(self) -> bool:
        """Returns True, if both its label and the last OBR operation returned successful, False otherwise."""
        # check state of last obr operation
        last_op_state = "Failure"
        if "cache" not in self.job.doc:
            logger.info(f"Job with {self.job.id} has no cache key.")
            # TODO possibly debatable if this should return false
            return False
        else:
            # find last obr operation
            last_time = datetime(1, 1, 1, 1, 1, 1)
            for k, v in self.job.doc["cache"].items():
                # skip md5sums
                if k == "md5sum":
                    continue

                last_of_obr_op = v[-1]
                if not (time := last_of_obr_op.get("timestamp", None)):
                    continue

                # timestamps are not standardized and can have to formats (in our case)
                try:
                    dt = datetime.strptime(time, "%Y-%m-%d_%H:%M:%S")
                except ValueError:
                    dt = datetime.strptime(time, "%Y-%m-%d %H:%M:%S.%f")

                if dt > last_time:
                    last_time = dt
                    last_op_state = last_of_obr_op["state"]
        label_state = self.job.doc["state"]
        return last_op_state == label_state == "success"

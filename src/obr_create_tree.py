#!/usr/bin/python
"""
    run ogl benchmarks

    Usage:
        runBenchmark.py [options]

    Options:
        -h --help           Show this screen
        -v --version        Print version and exit
        --clean             Remove existing cases [default: False].
        --parameters=<json> pass the parameters for given parameter study
        --folder=<folder>   Target folder  [default: Test].
        --init=<ts>         Run the base case for ts timesteps [default: 100].
"""

import os
import sys
from collections.abc import MutableMapping

from pathlib import Path
from subprocess import check_output
import hashlib


def obr_create_tree(project, config, arguments, config_file):
    if not os.environ.get("FOAM_ETC"):
        print("[OBR] Error OpenFOAM not sourced")
        sys.exit(-1)

    # TODO figure out how operations should be handled for statepoints
    base_case_dict = {"case": config["case"]["type"], "has_child": True}
    of_case = project.open_job(base_case_dict)
    of_case.doc["state"] = "ready"
    of_case.doc["is_base"] = True
    of_case.doc["parameters"] = config["case"]
    of_case.doc["pre_build"] = config["case"].get("pre_build", [])
    of_case.doc["post_build"] = config["case"].get("post_build", [])
    of_case.init()

    operations = []
    id_path_mapping = {of_case.id: "base/"}

    def flatten(d, parent_key="", sep="/"):
        items = []
        for k, v in d.items():
            new_key = parent_key + sep + k if parent_key else k
            if isinstance(v, MutableMapping):
                items.extend(flatten(v, new_key, sep=sep).items())
            else:
                items.append((new_key, v))
        return dict(items)

    def add_variations(variation, parent_job, base_dict):
        base = parent_job.id
        for operation in variation:
            sub_variation = operation.get("variation")
            key = operation.get("key", "").replace(".", "_dot_")
            parent = operation.get("parent", {})
            # Filter out variations that have not the specified parent statepoint
            # TODO make consistent with filter in cli.py
            if parent:
                intersect_keys = parent.keys() & parent_job.sp.keys()
                intersect_dict = {
                    k: parent[k]
                    for k in intersect_keys
                    if parent[k] == parent_job.sp[k]
                }
                if not intersect_dict:
                    continue
                # does not work on python 3.8
                # if not dict(parent.items() & parent_job.sp.items()):
                #    continue

            for value in operation["values"]:
                # support if statetment for key value pairs
                if not key and not value.get("if", True):
                    continue

                # if operation is shell take only script name instead of full path
                if not key:
                    path = operation["schema"].format(**flatten(value)) + "/"
                    args = value
                    keys = list(value.keys())
                else:
                    args = {key: value}
                    if operation.get("operation") == "shell":
                        key_ = str(Path(key.replace("_dot_", ".")).parts[-1])
                    else:
                        key_ = key
                    path = "{}/{}/".format(key_, value)
                    keys = [key]

                base_dict.update(
                    {
                        "case": config["case"]["type"],
                        "operation": operation["operation"],
                        "has_child": True if sub_variation else False,
                        **args,
                    }
                )
                h = hashlib.new("md5")
                h.update((str(operation) + str(value)).encode())
                job = project.open_job(base_dict)
                job.doc["operation_hash"] = h.hexdigest()
                job.doc["base_id"] = base
                job.doc["keys"] = keys
                job.doc["parameters"] = operation.get("parameters", [])
                job.doc["pre_build"] = operation.get("pre_build", [])
                job.doc["post_build"] = operation.get("post_build", [])
                job.doc["state"] = ""
                job.init()
                path = path.replace(" ", "_").replace("(", "").replace(")", "")
                path = path.split(">")[-1]
                id_path_mapping[job.id] = id_path_mapping.get(base, "") + path
                if sub_variation:
                    add_variations(sub_variation, job, base_dict)
            operations.append(operation.get("operation"))

    add_variations(config["variation"], of_case, base_case_dict)

    operations = list(set(operations))

    if arguments.get("execute"):
        project.run(names=["fetch_case"])
        project.run(names=operations, np=arguments.get("tasks", -1))

    def ln(src, dst):
        src = Path(src)
        dst = Path(dst)
        check_output(["ln", "-s", src, dst])

    obr_store = Path(arguments["folder"]) / ".obr_store"
    if not obr_store.exists():
        check_output(["mkdir", "-p", obr_store])

    if not (Path(arguments["folder"]) / "view").exists():
        # FIXME this copies to views instead of linking
        project.find_jobs(filter={"has_child": False}).export_to(
            arguments["folder"],
            path=lambda job: "view/" + id_path_mapping[job.id],
            copytree=lambda src, dst: ln(src, dst),
        )

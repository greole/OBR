"""
Module that contains the command line app.

Why does this file exist, and why not put this in __main__?

  You might be tempted to import things from __main__ later, but that will cause
  problems: the code will get executed twice:

  - When you run `python -mobr` python will execute
    ``__main__.py`` as a script. That means there won't be any
    ``obr.__main__`` in ``sys.modules``.
  - When you import __main__ it will get executed again (as a module) because
    there's no ``obr.__main__`` in ``sys.modules``.

  Also see (1) from http://click.pocoo.org/5/setuptools/#setuptools-integration
"""

import click
import yaml  # type: ignore[import]
import os
import sys
import logging
import shutil
import functools

from pathlib import Path
from subprocess import check_output

from git.repo import Repo
from git.util import Actor
from git import InvalidGitRepositoryError
from datetime import datetime

from .signac_wrapper.operations import OpenFOAMProject, needs_initialization
from .signac_wrapper.submit import submit_impl
from .create_tree import create_tree
from .core.parse_yaml import read_yaml
from .cli_util import (
    query_impl,
    check_cli_operations,
    cli_cmd_setup,
    copy_to_archive,
)
from .core.core import map_view_folder_to_job_id, profile_call
from .core.logger_setup import logger, setup_logging


def common_params(func):
    @click.option(
        "--debug", is_flag=True, help="Increase verbosity of the output to debug mode"
    )
    @click.option("-f", "--folder", default=".", help="Path to OBR workspace folder")
    @click.option(
        "--filter",
        type=str,
        multiple=True,
        help=(
            "Pass a <key><predicate><value> value pair per occurrence of --filter."
            " Predicates include ==, !=, <=, <, >=, >. For instance, obr query --filter"
            "solver==pisoFoam"
        ),
    )
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if kwargs.get("debug"):
            logger = logging.getLogger("OBR")
            logger.setLevel(logging.DEBUG)
            logger.info("Setting output level to debug")
        return func(*args, **kwargs)

    return wrapper


@click.group()
@click.version_option()
@click.option("--debug/--no-debug", default=False)
@click.option("-v", "--version", is_flag=True)
@click.pass_context
def cli(ctx: click.Context, **kwargs):
    # ensure that ctx.obj exists and is a dict (in case `cli()` is called
    # by means other than the `if` block below)
    ctx.ensure_object(dict)
    ctx.obj["DEBUG"] = kwargs.get("debug")


@cli.command()
@click.option("-f", "--folder", default=".")
@click.option(
    "-p", "--pretend", is_flag=True, help="Set flag to only print submission script"
)
@click.option(
    "-o",
    "--operations",
    default="",
    required=True,
    help=(
        "Specify the operation(s) to run. Pass multiple operations after -o, separated"
        " by commata (NO space), e.g. obr run -o shell,apply. Run with --help to list"
        " available operations."
    ),
)
@click.option(
    "--template",
    default="",
    help="Path to sumbission script template.",
)
@click.option(
    "-l",
    "--list_operations",
    is_flag=True,
    help="Prints all available operations and returns.",
)
@click.option(
    "--filter",
    type=str,
    multiple=True,
    help=(
        "Pass a <key><predicate><value> value pair per occurrence of --filter."
        " Predicates include ==, !=, <=, <, >=, >. For instance, obr submit --filter"
        ' "solver==pisoFoam"'
    ),
)
@click.option("--bundling_key", default=None, help="")
@click.option(
    "--max_queue_size",
    default=None,
    help=(
        "Maximum Number of submissions for the scheduler. If more jobs are eligible"
        " jobs are bundled together."
    ),
)
@click.option("-p", "--partition", default="cpuonly")
@click.option("-t", "--time", default="60")
@click.option("--account", default="")
@click.option("--pretend", is_flag=True)
@click.option(
    "--scheduler_args",
    default="",
    help="Currently required to be in --key1 value --key2 value2 form",
)
@click.pass_context
def submit(ctx: click.Context, **kwargs):
    project, jobs = cli_cmd_setup(kwargs)
    operations = kwargs.get("operations", "").split(",")
    list_operations = kwargs.get("list_operations")
    if not check_cli_operations(project, operations, list_operations):
        return

    submit_impl(
        project,
        jobs,
        operations=operations,
        template=Path(kwargs.get("template")),
        account=kwargs.get("account"),
        partition=kwargs.get("partition"),
        time=kwargs.get("time"),
        pretend=kwargs["pretend"],
        bundling_key=kwargs["bundling_key"],
        max_queue_size=int(kwargs.get("max_queue_size", 100)),
        scheduler_args=kwargs.get("scheduler_args"),
    )


@cli.command()
@common_params
@click.option("-f", "--folder", default=".")
@click.option(
    "-o",
    "--operations",
    default="",
    required=True,
    help=(
        "Specify the operation(s) to run. Pass multiple operations after -o, separated"
        " by commata (NO space), e.g. obr run -o shell,apply. Run with --help to list"
        " available operations."
    ),
)
@click.option(
    "-l",
    "--list_operations",
    is_flag=True,
    help="Prints all available operations and returns.",
)
@click.option(
    "--filter",
    type=str,
    multiple=True,
    help=(
        "Pass a <key><predicate><value> value pair per occurrence of --filter."
        " Predicates include ==, !=, <=, <, >=, >. For instance, obr run -o"
        ' runParallelSolver --filter "solver==pisoFoam"'
    ),
)
@click.option("-j", "--job")
@click.option("--args", default="")
@click.option("-t", "--tasks", default=-1)
@click.option("-a", "--aggregate", is_flag=True)
@click.option("--args", default="")
@click.pass_context
def run(ctx: click.Context, **kwargs):
    """Run specified operations"""
    project, jobs = cli_cmd_setup(kwargs)

    operations = kwargs.get("operations", "").split(",")
    list_operations = kwargs.get("list_operations")
    if not check_cli_operations(project, operations, list_operations):
        return

    if kwargs.get("args"):
        os.environ["OBR_CALL_ARGS"] = kwargs.get("args", "")

    if kwargs.get("job"):
        os.environ["OBR_JOB"] = kwargs.get("job", "")

    if kwargs.get("operations") == "apply":
        logger.warning(
            "Calling the apply operation via run has been discontinued. Call 'obr"
            " apply' directly"
        )
        return

    if kwargs.get("operations") == "runParallelSolver":
        # NOTE if tasks is not set explicitly we set it to 1 for parallelSolverSolver
        # to avoid oversubsrciption
        ntasks: int = kwargs["tasks"] if kwargs.get("tasks", 0) >= 1 else 1
        if not kwargs.get("tasks", False):
            sys.argv.append("-t")
            sys.argv.append(str(ntasks))
        project.run(
            jobs=jobs,
            names=operations,
            progress=True,
            np=ntasks,
        )
        return

    if not kwargs.get("aggregate"):
        GLOBAL_UNINIT_COUNT = 0
        for job in jobs:
            if job.sp().get("operation") in operations and needs_initialization(job):
                GLOBAL_UNINIT_COUNT += 1
        os.environ["GLOBAL_UNINIT_COUNT"] = str(GLOBAL_UNINIT_COUNT)

        profile_call(
            project.run,
            names=operations,
            jobs=jobs,
            progress=True,
            np=kwargs.get("tasks", -1),
        )
    else:
        # calling for aggregates does not work with jobs
        profile_call(project.run, names=operations, np=kwargs.get("tasks", -1))
    logger.success("Completed all operations")


@cli.command()
@common_params
@click.option(
    "-g", "--generate", is_flag=True, help="Call generate directly after init."
)
@click.option("-c", "--config", required=True, help="Path to configuration file.")
@click.option("-e", "--env", is_flag=True, help="Shows required environment variables and exits.")
@click.option(
    "-t",
    "--tasks",
    default=-1,
    help="Number of tasks to run concurrently for generate call.",
)
@click.option("-u", "--url", default=None, help="Url to a configuration yaml")
@click.pass_context
def init(ctx: click.Context, **kwargs):
    # needs folder/.obr to exists before logger can be initialised
    ws_fold = kwargs.get("folder")

    # Check if workspace already exists
    if (Path(ws_fold) / "workspace").exists():
        logger.warn(
            f"Existing workspace found! Re-initializing over existing  workspace might"
            f" lead to unexpected behaviour. Consider purging existing workspace with"
            f" obr reset -w"
        )
        confirmed = click.confirm("Do you want to continue?", default=True)
        if not confirmed:
            return

    if ws_fold:
        (Path(ws_fold) / ".obr").mkdir(parents=True, exist_ok=True)
    else:
        Path(".obr").mkdir(parents=True, exist_ok=True)
    setup_logging(log_fold=ws_fold)

    config_str = read_yaml(kwargs)
    config_str = config_str.replace("\n\n", "\n")
    if (kwargs.get("env")):
        sys.exit(0)

    config = yaml.safe_load(config_str)

    project = OpenFOAMProject.init_project(path=ws_fold)
    create_tree(project, config, kwargs)

    logger.success("Successfully initialised")

    if kwargs.get("generate"):
        logger.info("Generating workspace")
        project.run(
            names=["generate"],
            progress=True,
            np=kwargs.get("tasks", -1),
        )


@cli.command()
@common_params
@click.option("-d", "--detailed", is_flag=True)
@click.option("-S", "--summarize", type=int, default=0)
@click.option(
    "-S",
    "--summarize",
    type=int,
    required=False,
    default=0,
    help="summarize the by joining the last N view levels.",
)
@click.pass_context
def status(ctx: click.Context, **kwargs):
    project, jobs = cli_cmd_setup(kwargs)
    sum = int(kwargs.get("summarize", 0))
    id_view_map = map_view_folder_to_job_id("view")
    grouped_jobs = project.group_jobs(jobs, id_view_map, summarize=sum)

    if len(grouped_jobs) == 0:
        logger.warning(f"No jobs can be displayed for summarize depth {sum}")
        return

    max_view_len = len(max(grouped_jobs.keys(), key=lambda k: len(k)))
    for view, jobs in sorted(grouped_jobs.items()):
        finished = unfinished = 0
        for job in jobs:
            if "finished" in project.labels(job):
                finished += 1
            else:
                unfinished += 1
        pad = " " * (max_view_len - len(view) + 1)
        logger.info(f"{view}:{pad}| {finished}x Completed | {unfinished}x Incomplete |")


@cli.command()
@common_params
@click.option("-d", "--detailed", is_flag=True)
@click.option("-a", "--all", is_flag=True)
@click.option(
    "-q",
    "--query",
    required=True,
    multiple=True,
    help=(
        "Pass a <key><predicate><value> value pair per occurrence of --query."
        " Predicates include ==, !=, <=, <, >=, >. For instance, obr query --query"
        " solver==pisoFoam"
    ),
)
@click.option(
    "--export_to",
    required=False,
    multiple=False,
    help="Write results to a json file.",
)
@click.option(
    "--validate_against",
    required=False,
    multiple=False,
    help="Validate the query output against the specified file.",
)
@click.option(
    "--quiet", required=False, is_flag=True, help="Don't print out query results."
)
@click.pass_context
def query(ctx: click.Context, **kwargs):
    project, jobs = cli_cmd_setup(kwargs)

    input_queries: tuple[str] = kwargs.get("query", ())
    quiet: bool = kwargs.get("quiet", False)
    json_file: str = kwargs.get("export_to", "")
    validation_file: str = kwargs.get("validate_against", "")
    filters: list[str] = kwargs.get("filter", [])
    profile_call(
        query_impl, project, input_queries, filters, quiet, json_file, validation_file
    )


@cli.command()
@click.option(
    "--campaign",
    type=str,
    multiple=False,
    help="",
)
@click.option(
    "--file",
    type=str,
    required=True,
    multiple=False,
    help="Path to script to apply to the workspace",
)
@click.option(
    "--filter",
    type=str,
    multiple=True,
    default=[],
    help=(
        "Pass a <key><predicate><value> value pair per occurrence of --filter."
        " Predicates include ==, !=, <=, <, >=, >. For instance, obr submit --filter"
        ' "solver==pisoFoam"'
    ),
)
@click.pass_context
def apply(ctx: click.Context, **kwargs):
    apply_file_path = Path(kwargs["file"]).resolve()
    if not apply_file_path.exists():
        logger.error(f"Could not find {kwargs['file']}")
        sys.exit(1)

    global filtered_jobs
    project, filtered_jobs = cli_cmd_setup(kwargs)

    os.environ["OBR_APPLY_FILE"] = str(apply_file_path)
    os.environ["OBR_APPLY_CAMPAIGN"] = kwargs.get("campaign", "")
    sys.argv.append("--aggregate")
    sys.argv.append("-t")
    sys.argv.append("1")
    project.run(
        names=["apply"],
        progress=True,
        np=1,
    )
    logger.success("Successfully applied")


@cli.command()
@click.option("-c", "--config", required=True, help="Path to configuration file.")
@click.option(
    "--filter",
    type=str,
    multiple=True,
    default=[],
    help=(
        "Pass a <key><predicate><value> value pair per occurrence of --filter."
        " Predicates include ==, !=, <=, <, >=, >. For instance, obr submit --filter"
        ' "solver==pisoFoam"'
    ),
)
@click.pass_context
def postProcess(ctx: click.Context, **kwargs):
    from Owls.parser.LogFile import LogFile, transportEqn, customMatcher
    from obr.core.core import get_latest_log, get_timestamp_from_log
    from .core.queries import build_filter_query

    def convert_to_numbers(df):                                        
        """convert all columns to float if they dont have Name in it"""
        return df.astype({col: "float" for col in df.columns if not "Name" in col})

    project, filtered_jobs = cli_cmd_setup(kwargs)

    config_str = read_yaml(kwargs)
    config_str = config_str.replace("\n\n", "\n")
    config = yaml.safe_load(config_str)

    d = config["postProcess"]

    matcher = {"transpEqn": lambda args: transportEqn(**args)}
    matcher_args = {"transpEqn": ["name"]}

    for m in d['matcher']:
        regex = m['regexp']
        matcher[m['name']] = lambda args: customMatcher(args['name'], regex.format(**args))  
        matcher_args[m['name']] = m['args']

    queries: list[Query] = build_filter_query(d['queries'])
    query_results = project.query(jobs=filtered_jobs, query=queries)

    records = []
    for job in filtered_jobs:
        record = {}
        log = get_latest_log(job)
        if not log:
            continue
        log_path = Path(job.path)/ "case"/ log

        try:
            record = query_results[job.id]
            record['jobid'] = job.id
            for l in d['log']:
                matcher_name =l['matcher'] 
                pass_args = {k:v for k,v in zip(matcher_args[matcher_name],l['args'])}
                m = matcher[matcher_name](pass_args)

                log_file_parser = LogFile(log_path, matcher=[m]) 
                df = convert_to_numbers(log_file_parser.parse_to_df())
                for col in df.columns:
                    try:
                        record[col] = df.iloc[1:][col].mean() 
                    except:
                        pass


        except Exception as e:
            print(e)
        if record:
            records.append(record)
    
    print(records)
    logger.success("Successfully applied")


@cli.command()
@common_params
@click.option("-w", "--workspace", is_flag=True, help="remove all obr project files")
@click.option(
    "-c",
    "--case",
    is_flag=True,
    help="Reset the state of a case by deleting solver logs",
)
@click.option(
    "-y",
    is_flag=True,
    help="Confirm resetting",
)
@click.option(
    "-v", "--view", default="", help="remove case completely specified by a view folder"
)
@click.pass_context
def reset(ctx: click.Context, **kwargs):
    """deletes workspace or cases"""

    def safe_delete(fn):
        """This functions deletes the given path. If the path does not exist, it does nothing"""
        path = Path(fn)
        if path.exists():
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()

    project, jobs = cli_cmd_setup(kwargs)

    confirmed = kwargs.get("y", False)
    if kwargs.get("workspace"):
        logger.warn(
            f"Removing current obr workspace. This will remove all simulation results"
        )
        if not confirmed:
            confirmed = click.confirm("Do you want to continue?", default=True)
        if confirmed:
            safe_delete("workspace")
            safe_delete("view")
            safe_delete("signac.rc")
            safe_delete(".signac")
            return

    if kwargs.get("case"):
        jobids = [j.id for j in jobs]
        logger.warn(
            "Resetting obr cases. This will remove all generated simulation results of"
            f" the following {len(jobids)} jobs {jobids}."
        )
        logger.warn(
            f"obr reset --case, is not fully implemented and will only remove log"
            f" solver logs."
        )

        if not confirmed:
            confirmed = click.confirm("Do you want to continue?", default=True)
        if confirmed:
            project.run(
                jobs=jobs,
                names=["resetCase"],
                progress=True,
                np=-1,
            )

    if kwargs.get("view"):
        logger.error("Resetting by view path is not yet supported")
    logger.success("Reset successful")


@cli.command()
@common_params
@click.option(
    "-r",
    "--repo",
    required=True,
    type=str,
    help=(
        "Path to data repository. If this is a valid Github repository, files will be"
        " automatically added."
    ),
)
@click.option(
    "-s",
    "--skip-logs",
    required=False,
    is_flag=True,
    help=(
        "If set, .log files will not be archived. This does not affect .log files"
        " passed via the --file option."
    ),
)
@click.option(
    "-a",
    "--file",
    required=False,
    multiple=True,
    help="Path(s) to non-logfile(s) to be also added to the repository.",
)
@click.option(
    "--campaign",
    required=True,
    type=str,
    help="Specify the campaign",
)
@click.option(
    "--tag",
    required=False,
    type=str,
    help=(
        "Specify prefix of branch name. Will checkout new branch with timestamp"
        " <tag>-<timestamp>."
    ),
)
@click.option(
    "--amend",
    is_flag=True,
    required=False,
    help="Add to existing branch instead of creating new one.",
)
@click.option(
    "--push",
    required=False,
    is_flag=True,
    help="Push changes directly to origin and switch to previous branch.",
)
@click.option(
    "--dry-run",
    required=False,
    is_flag=True,
    help=(
        "If set, will log which files WOULD be copied and committed, without actually"
        " doing it."
    ),
)
@click.pass_context
def archive(ctx: click.Context, **kwargs):
    target_folder: Path = Path(kwargs.get("repo", "")).absolute()
    if current_path := kwargs.get("folder", "."):
        os.chdir(current_path)
        current_path = Path(current_path).absolute()
    setup_logging()

    # setup project and jobs
    project, jobs = cli_cmd_setup(kwargs)

    dry_run = kwargs.get("dry_run", False)
    branch_name = None
    previous_branch = None
    campaign = kwargs["campaign"]
    tag = kwargs.get("tag", "")
    # check if given path is actually a github repository
    use_git_repo = False
    try:
        repo = Repo(path=str(target_folder), search_parent_directories=True)
        previous_branch = repo.active_branch.name
        use_git_repo = True
    except InvalidGitRepositoryError:
        logger.warn(
            f"Given directory {target_folder=} is not a github repository. Will only"
            " copy files."
        )
    if use_git_repo:
        if kwargs.get("amend"):
            branches = [
                (branch.name[-16 : len(branch.name)], branch.name)
                for branch in repo.branches
                if branch.name.startswith(campaign)
            ]
            branches.sort()

            if not branches:
                # throw error, return
                logger.error(
                    f"Cannot amend to {campaign} branch. Existing"
                    f" branches include {repo.git.branch()}."
                )
                return
            branch_name = branches[-1][1]
            if dry_run:
                logger.info(f"Would amend to {branch_name}.")
            else:
                logger.info(f"Amending to {branch_name} branch")
                repo.git.checkout(branch_name)
        else:
            time_stamp = (
                str(datetime.now())
                .rsplit(":", 1)[0]
                .replace(" ", ":")
                .replace(":", "_")
            )
            branch_name = f"{campaign}/{time_stamp}"
            if dry_run:
                logger.info(f"Would checkout {branch_name}.")
            else:
                logger.info(f"checkout {branch_name}")
                repo.git.checkout("HEAD", b=branch_name)

    # setup target folder
    if not target_folder.exists():
        if dry_run:
            logger.info(f"Would Create {str(target_folder)}")
        else:
            logger.info(f"creating {str(target_folder)}")
            target_folder.mkdir()

    skip = kwargs.get("skip_logs")
    if not skip:
        # iterate cases and copy log files into target repo
        for job in jobs:
            # copy signac state point
            signac_statepoint = Path(job.path) / "signac_statepoint.json"
            if not signac_statepoint.exists():
                continue
            target_file = target_folder / f"workspace/{job.id}/signac_statepoint.json"
            if dry_run:
                logger.info(f"Would copy {signac_statepoint} to {target_file}.")
            else:
                logger.debug(f"{target_folder}, {signac_statepoint}")
                copy_to_archive(repo, use_git_repo, signac_statepoint, target_file)

            # copy signac state point
            signac_job_document = Path(job.path) / "signac_job_document.json"
            if not signac_job_document.exists():
                continue

            md5sum = check_output(
                ["md5sum", str(signac_job_document)], text=True
            ).split()[0]
            target_file = (
                target_folder / f"workspace/{job.id}/signac_job_document_{md5sum}.json"
            )
            if dry_run:
                logger.info(f"Would copy {signac_job_document} to {target_file}.")
            else:
                logger.debug(f"{target_folder}, {signac_job_document}")
                copy_to_archive(repo, use_git_repo, signac_job_document, target_file)

            case_folder = Path(job.path) / "case"
            if not case_folder.exists():
                logger.info(f"Job with {job.id=} has no case folder.")
                continue

            # TODO: implement archival only of non-failed jobs
            # skip if either the most recent obr action failed or the label is set to "not success"
            # case = OpenFOAMCase(str(case_folder), job)
            # if not case.was_successful():
            #     logging.info(
            #         f"Skipping Job with {job.id=} due to recent failure states."
            #     )
            #     continue

            root, _, files = next(os.walk(case_folder))
            tags = "/".join(tag.split(","))
            for file in files:
                src_file = Path(root) / file
                if src_file.is_relative_to(current_path):
                    src_file = src_file.relative_to(current_path)
                if file.endswith("log"):
                    target_file = (
                        target_folder / f"workspace/{job.id}/{campaign}/{tags}/{file}"
                    )
                    if target_file.is_relative_to(current_path):
                        target_file = target_file.relative_to(current_path)
                    if dry_run:
                        logger.info(f"Would copy {src_file} to {target_file}.")
                    else:
                        copy_to_archive(repo, use_git_repo, src_file, target_file)

            # copy CLI-passed files into data repo and add if possible
            extra_files: tuple[str] = kwargs.get("file", ())
            for file in extra_files:
                f = case_folder / file
                target_file = (
                    target_folder / f"workspace/{job.id}/{campaign}/{tags}/{file}"
                )
                if not f.exists():
                    logger.info(f"invalid path {f}. Skipping.")
                    continue
                if dry_run:
                    logger.info(f"Would copy {f} to {f.absolute}.")
                else:
                    copy_to_archive(repo, use_git_repo, f, target_file)

    # commit and push
    if use_git_repo and repo and branch_name:
        message = f"Add new logs -> {str(target_folder)}"
        author = Actor(repo.git.config("user.name"), repo.git.config("user.email"))
        logger.info(f"Actor with {author.conf_name=} and {author.conf_email=}")
        logger.info(
            f'config: {repo.git.config("user.name"), repo.git.config("user.email")}'
        )
        if dry_run:
            logger.info(
                f"Would commit changes to repo {repo.working_dir.rsplit('/',1)[1]} with"
                f" {message=} and remote name {repo.remote().name}"
            )

        else:
            logger.info(
                f"Committing changes to repo {repo.working_dir.rsplit('/',1)[1]} with"
                f" {message=} and remote name {repo.remote().name}"
            )
            try:
                repo.index.commit(message, author=author, committer=author)
                if kwargs.get("push"):
                    repo.git.push("origin", "-u", branch_name)
                    logger.info(f"Switching back to branch '{previous_branch}'")
                    repo.git.checkout(previous_branch)
            except Exception as e:
                logger.error(e)


def main():
    cli(obj={})


if __name__ == "__main__":
    cli(obj={})

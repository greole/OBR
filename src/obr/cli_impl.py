import json

from .signac_wrapper.operations import OpenFOAMProject


def query_impl(
    project: OpenFOAMProject,
    input_queries: tuple[str],
    quiet: bool,
    json_file: str,
    validation_file: str,
):
    if input_queries == "":
        logging.warning("--query argument cannot be empty!")
        return
    queries: list[Query] = build_filter_query(input_queries)
    jobs = project.filter_jobs(filters=list(filters))
    query_results = project.query(jobs=jobs, query=queries)
    if not quiet:
        for job_id, query_res in deepcopy(query_results).items():
            out_str = f"{job_id}:"
            for k, v in query_res.items():
                out_str += f" {k}: {v}"
            logging.info(out_str)

    if json_file:
        with open(json_file, "w") as outfile:
            # json_data refers to the above JSON
            json.dump(query_results, outfile)
    if validation_file:
        with open(validation_file, "r") as infile:
            # json_data refers to the above JSON
            validation_dict = json.load(infile)
            if validation_dict.get("$schema"):
                logging.info("using json schema for validation")
                from jsonschema import validate

                validate(query_results, validation_dict)
            else:
                from deepdiff import DeepDiff

                logging.info("using deepdiff for validation")
                difference_dict = DeepDiff(validation_dict, query_results)

                if difference_dict:
                    print(difference_dict)
                    logging.warn("validation failed")
                    sys.exit(1)
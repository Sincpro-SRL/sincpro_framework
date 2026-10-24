"""The project's file: the document as written."""

import yaml


def load_yaml_file(file_path: str) -> dict:
    """The YAML file at `file_path`, parsed with `safe_load` — no tag of the file builds a
    Python object.

        load_yaml_file("conf/payments.yml")   ->  {"sincpro_payments_sdk": {"qr": {...}}}
    """
    with open(file_path) as file:
        return yaml.safe_load(file)

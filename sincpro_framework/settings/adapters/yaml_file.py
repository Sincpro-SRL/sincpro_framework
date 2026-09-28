"""The project's file: the document as written."""

import yaml


def load_yaml_file(file_path: str) -> dict:
    """Load a yaml file and return the content as a dictionary"""
    with open(file_path) as file:
        return yaml.safe_load(file)

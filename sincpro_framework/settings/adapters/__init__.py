"""Where the document comes from: the project's YAML file, and the environment by path."""

from sincpro_framework.settings.adapters.environment import with_environment_by_path
from sincpro_framework.settings.adapters.yaml_file import load_yaml_file

__all__ = ["load_yaml_file", "with_environment_by_path"]

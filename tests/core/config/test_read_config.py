"""Read the config file and return the resolved config object."""

from sincpro_framework.sincpro_conf import load_yaml_file


def test_load_yaml_file(test_conf_yaml_path):
    assert isinstance(load_yaml_file(test_conf_yaml_path), dict)

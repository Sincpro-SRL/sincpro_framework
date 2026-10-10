"""Test how to create a new instance of Config file"""

import os

import pytest

from sincpro_framework.sincpro_conf import SincproConfig, build_config_obj


def test_create_config_obj(test_conf_yaml_path: str):
    """Model the configuration object with (/tests/config/resources/test_conf.yml)"""

    class DatabaseFirstConf(SincproConfig):
        user: str = "user"
        password: str = "password"
        port: int = 5432

    class FirstConf(SincproConfig):
        log_level: str = "DEBUG"
        list_of_values: list[str] = ["value1", "value2"]
        database: DatabaseFirstConf = DatabaseFirstConf()

    class DigitalOceanSecondConf(SincproConfig):
        droplet: str = "droplet_default"
        password: str = "password_default"
        token: str = "TOKEN EMPTY"

    class SecondConf(SincproConfig):
        digital_ocean: DigitalOceanSecondConf = DigitalOceanSecondConf()

    class BundledContextAppConfig(SincproConfig):
        first_conf: FirstConf = FirstConf()
        second_conf: SecondConf = SecondConf()

    expected_load_env = "EXPECTED LOAD ENV"
    os.environ.setdefault("ANY_TOKEN", expected_load_env)

    config_obj = build_config_obj(BundledContextAppConfig, test_conf_yaml_path)
    assert config_obj.first_conf.log_level == "INFO"
    assert config_obj.second_conf.digital_ocean.token == expected_load_env
    print(config_obj)


def test_sub_key_scopes_the_config_to_one_section(test_conf_yaml_path: str):
    """A bounded context maps only its own section of a shared conf file."""

    class DatabaseConf(SincproConfig):
        user: str = "user"
        port: int = 5432

    class FirstConf(SincproConfig):
        log_level: str = "DEBUG"
        database: DatabaseConf = DatabaseConf()

    config_obj = build_config_obj(FirstConf, test_conf_yaml_path, "first_conf")

    assert config_obj.log_level == "INFO"
    assert config_obj.database.user == "postgres"


def test_missing_sub_key_fails_loudly(test_conf_yaml_path: str):
    """A typo in the section name must not silently boot the app on defaults."""

    class AnyConf(SincproConfig):
        log_level: str = "DEBUG"

    with pytest.raises(ValueError, match="third_conf"):
        build_config_obj(AnyConf, test_conf_yaml_path, "third_conf")

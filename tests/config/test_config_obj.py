"""Test how to create a new instance of Config file"""

import os

from sincpro_framework.sincpro_conf import SincproConfig, build_config_obj, load_yaml_file


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
    config_dict = load_yaml_file(test_conf_yaml_path)

    config_obj = build_config_obj(BundledContextAppConfig, config_dict)
    assert config_obj.first_conf.log_level == "INFO"
    assert config_obj.second_conf.digital_ocean.token == expected_load_env
    print(config_obj)

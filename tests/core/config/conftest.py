"""Shared resources, fixtures for conf module."""

import os

import pytest


@pytest.fixture
def test_conf_yaml_path() -> str:
    """"""
    return str(os.path.dirname(__file__) + "/resources/test_conf.yml")

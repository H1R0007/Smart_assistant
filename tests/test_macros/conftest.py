# tests/test_macros/conftest.py

"""
Фикстуры для тестов макросов.
"""

import pytest


@pytest.fixture
def tmp_macros_dir(tmp_path):
    """Временная директория для макросов."""
    macros_dir = tmp_path / "macros"
    macros_dir.mkdir()
    return macros_dir


@pytest.fixture
def tmp_macros_file(tmp_macros_dir):
    """Временный YAML-файл для макросов."""
    return str(tmp_macros_dir / "generated_macros.yaml")
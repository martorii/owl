import importlib

import pytest

import owl


def test_package_has_version() -> None:
    assert owl.__version__


@pytest.mark.parametrize("layer", ["domain", "application", "infrastructure", "entrypoints"])
def test_layers_import(layer: str) -> None:
    assert importlib.import_module(f"owl.{layer}")

"""Every module must import against the pinned Home Assistant release.

This is the test that earns its keep: when a helper is renamed or removed
upstream, the first sign is otherwise somebody's instance refusing to load the
integration after an update.
"""

import importlib

import pytest

MODULES = [
    "custom_components.ibok",
    "custom_components.ibok.api",
    "custom_components.ibok.button",
    "custom_components.ibok.config_flow",
    "custom_components.ibok.const",
    "custom_components.ibok.coordinator",
    "custom_components.ibok.entity",
    "custom_components.ibok.sensor",
]


@pytest.mark.parametrize("name", MODULES)
def test_module_imports(name: str) -> None:
    assert importlib.import_module(name) is not None

"""Test configuration.

At the repository root rather than under `tests/`, because that is what puts the root on
`sys.path` and lets the integration be imported as `custom_components.ibok` -- the name
Home Assistant's own loader uses.

No autouse Home Assistant fixture here: the checks that only read JSON are worth being
runnable without the full test plugin installed. A test that needs a `hass` object should
request `enable_custom_integrations` itself.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import custom_components

# pytest-homeassistant-custom-component ships its own `custom_components` package, with an
# __init__.py. A regular package beats a namespace directory whatever the sys.path order, so
# `import custom_components` finds the plugin's and never ours. Adding our directory to the
# package that was found is additive -- the plugin's own test components stay visible.
_OURS = str(Path(__file__).parent / "custom_components")
if _OURS not in custom_components.__path__:
    custom_components.__path__.append(_OURS)

"""Test configuration.

pytest puts the test file's own directory on sys.path, not the repository root,
so without this the `custom_components.ibok` import in test_imports.py fails
with a collection error rather than a useful message.

No autouse Home Assistant fixture here on purpose: it would make the plain
JSON checks depend on the full Home Assistant test plugin, and they are worth
being runnable on their own. Tests that need a `hass` object should request
`enable_custom_integrations` themselves.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

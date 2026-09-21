"""Pin every test run to a throwaway DATA_DIR.

The desktop app's ~/Library/.../Orb/paths.json points DATA_DIR and the default
vault at real user data, and the app binds both at import. Set before any
``app.*`` import; the integration suite overrides these with its own temp dir.
"""

import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="orb-tests-")
os.environ.setdefault("ORB_DATA_DIR", _tmp)
os.environ.setdefault("ORB_PATHS_FILE", os.path.join(_tmp, "no-paths.json"))

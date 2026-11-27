# Owned unittest temporary workspace

This example is original standard-library Python code, licensed MIT as recorded in its metadata. Install `revisions/0001/owned-temp-d039e626.py` as `tests/repo_temp.py` in the authorized repository before using it. Its location defines the repository root; do not invoke it directly from the archive directory.

```python
import unittest
from repo_temp import workspace

class ExampleTests(unittest.TestCase):
    def setUp(self):
        self.directory = workspace(self)
        # Cleanup is already registered if the rest of setup raises.
```

Each test receives a unique `_temp-files/test-*` directory. Python's cached temporary directory and `TEMP`, `TMP`, `TMPDIR` point there during the test, so child processes using those conventions share the allowed location. Unittest cleanup restores the settings and removes that test's directory, including when setup fails. Join children and close files before cleanup.

Use serial tests: environment variables and Python's temp cache are process-wide. Tools that ignore those settings need their own explicit output/cache options. This is not an operating-system sandbox, a crash-proof cleaner, or permission to delete existing files. Unknown and active siblings are preserved; inspect leftovers after forced termination. Windows junction rejection and real child-process temp creation were exercised; hostile concurrent filesystem replacement was not.

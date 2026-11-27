"""Repository-local disposable unittest workspace; Python standard library only."""

import os
from pathlib import Path
import stat
import tempfile
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def workspace(test_case):
    """Register cleanup immediately, then isolate this test and its child temp files."""
    base = ROOT / '_temp-files'
    base.mkdir(exist_ok=True)
    info = base.lstat()
    if (stat.S_ISLNK(info.st_mode)
            or getattr(info, 'st_file_attributes', 0) & 0x400
            or base.resolve().parent != ROOT):
        raise ValueError('test temporary directory must stay inside the repository')
    temporary = tempfile.TemporaryDirectory(prefix='test-', dir=base)
    test_case.addCleanup(temporary.cleanup)
    directory = Path(temporary.name)
    overrides = {name: str(directory) for name in ('TEMP', 'TMP', 'TMPDIR')}
    # A previously cached Python tempdir otherwise ignores changed environment variables.
    environment = patch.dict(os.environ, overrides)
    environment.start()
    test_case.addCleanup(environment.stop)
    cached = patch.object(tempfile, 'tempdir', str(directory))
    cached.start()
    test_case.addCleanup(cached.stop)
    return directory

import os
import tempfile

# Use offscreen rendering so GUI smoke tests run without a physical display.
# setdefault preserves any QT_QPA_PLATFORM the developer has already set.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Redirect the whole on-disk tree (core.paths.base_dir) to a throwaway dir for
# the entire test session, so tests never write reports/manifests/state into the
# real ~/Documents/STSyncTool. Set before any core module is imported so the
# module-level path constants resolve to the tmp base. Honour an existing value.
os.environ.setdefault("ST_SYNC_HOME", tempfile.mkdtemp(prefix="st_sync_test_home_"))

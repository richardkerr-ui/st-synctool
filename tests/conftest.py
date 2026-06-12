import os

# Use offscreen rendering so GUI smoke tests run without a physical display.
# setdefault preserves any QT_QPA_PLATFORM the developer has already set.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

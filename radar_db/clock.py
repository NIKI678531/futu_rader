"""Single wall-clock seam for operational audit timestamps.

Business date windows still come from database anchors.  Callers use this
only for run ids, leases, and audit records, and inject a value in tests.
"""

from datetime import datetime


def now(tz=None):
    return datetime.now(tz)

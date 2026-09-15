import threading
import time
import uuid
from datetime import datetime, timedelta

from sqlalchemy import delete, insert, update
from sqlalchemy.exc import IntegrityError

from .schema import runtime_leases


class WorkerLease:
    def __init__(self, engine, name, seconds=180):
        self.engine, self.name, self.seconds = engine, name, seconds
        self.owner = uuid.uuid4().hex
        self.stopped = threading.Event()
        self.lost = False

    def __enter__(self):
        now = datetime.utcnow()
        with self.engine.begin() as conn:
            claimed = conn.execute(update(runtime_leases).where(
                runtime_leases.c.name == self.name, runtime_leases.c.expires_at < now,
            ).values(owner=self.owner, expires_at=now + timedelta(seconds=self.seconds)))
            if not claimed.rowcount:
                try:
                    with conn.begin_nested():
                        conn.execute(insert(runtime_leases).values(
                            name=self.name, owner=self.owner, expires_at=now + timedelta(seconds=self.seconds)))
                except IntegrityError:
                    raise RuntimeError("An analysis worker already holds the lease") from None
        self.thread = threading.Thread(target=self._renew, daemon=True)
        self.last_renewed = time.monotonic()
        self.thread.start()
        return self

    def _renew(self):
        while not self.stopped.wait(self.seconds / 3):
            try:
                with self.engine.begin() as conn:
                    result = conn.execute(update(runtime_leases).where(
                        runtime_leases.c.name == self.name, runtime_leases.c.owner == self.owner,
                    ).values(expires_at=datetime.utcnow() + timedelta(seconds=self.seconds)))
                if not result.rowcount:
                    self.lost = True
                    return
                self.last_renewed = time.monotonic()
            except Exception:
                if time.monotonic() - self.last_renewed >= self.seconds:
                    self.lost = True
                    return

    def __exit__(self, *args):
        self.stopped.set()
        self.thread.join()
        with self.engine.begin() as conn:
            conn.execute(delete(runtime_leases).where(runtime_leases.c.name == self.name,
                                                     runtime_leases.c.owner == self.owner))
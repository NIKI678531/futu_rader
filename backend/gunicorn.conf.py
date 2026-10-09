import logging
import os

bind = "0.0.0.0:8008"
workers = 2
timeout = 900
graceful_timeout = 45
accesslog = "-"
errorlog = "/dev/stdout"
loglevel = os.getenv("RADAR_LOG_LEVEL", "info").lower()
access_log_format = '%(t)s %(p)s "%(r)s" %(s)s %(L)s'


class HealthcheckFilter(logging.Filter):
    def filter(self, record):
        atoms = record.args
        return not (
            isinstance(atoms, dict)
            and atoms.get("U") == "/health"
            and str(atoms.get("s")) == "200"
        )


def on_starting(server):
    server.log.access_log.addFilter(HealthcheckFilter())
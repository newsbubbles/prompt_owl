# Diagnostics go to stderr so stdout stays clean for results.
# Swap the sink to capture or silence: log.sink = lambda level, message: None

import sys

class log:

    @staticmethod
    def sink(level, message):
        print(f"[{level}] {message}", file=sys.stderr, flush=True)

    @staticmethod
    def info(message):
        log.sink('INFO', message)

    @staticmethod
    def warn(message):
        log.sink('WARN', message)

    @staticmethod
    def error(message):
        log.sink('ERROR', message)

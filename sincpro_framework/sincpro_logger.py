import logging
import os

import logzero

log_level = os.getenv("SP_FRAMEWORK_LOG_LEVEL", "DEBUG")
log_output_file = os.getenv("SP_FRAMEWORK_LOG_OUTPUT_FILE", "/tmp/sp_framework.log")
logzero.loglevel(log_level)

if log_level == "DEBUG":
    logzero.loglevel(logging.DEBUG)

logzero.logfile(log_output_file)
logger = logzero.logger

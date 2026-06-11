import logging
import os
from logging.handlers import RotatingFileHandler

class LevelFilter(logging.Filter):
    def __init__(self, level):
        self.level = level

    def filter(self, record):
        return record.levelno == self.level

def setup_logging():
    # Ensure logs directory exists at backend/logs
    log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "logs")
    os.makedirs(log_dir, exist_ok=True)

    log_format = logging.Formatter(
        fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # 1. All Logs
    all_handler = RotatingFileHandler(
        os.path.join(log_dir, "all.log"), maxBytes=10*1024*1024, backupCount=5, encoding='utf8'
    )
    all_handler.setFormatter(log_format)
    all_handler.setLevel(logging.INFO)

    # 2. Error Logs (Only ERROR and CRITICAL)
    error_handler = RotatingFileHandler(
        os.path.join(log_dir, "error.log"), maxBytes=10*1024*1024, backupCount=5, encoding='utf8'
    )
    error_handler.setFormatter(log_format)
    error_handler.setLevel(logging.ERROR)

    # 3. Warning Logs (Only WARNING)
    warning_handler = RotatingFileHandler(
        os.path.join(log_dir, "warning.log"), maxBytes=10*1024*1024, backupCount=5, encoding='utf8'
    )
    warning_handler.setFormatter(log_format)
    warning_handler.setLevel(logging.WARNING)
    warning_handler.addFilter(LevelFilter(logging.WARNING))

    # 4. Success / Info Logs (Only INFO)
    info_handler = RotatingFileHandler(
        os.path.join(log_dir, "success.log"), maxBytes=10*1024*1024, backupCount=5, encoding='utf8'
    )
    info_handler.setFormatter(log_format)
    info_handler.setLevel(logging.INFO)
    info_handler.addFilter(LevelFilter(logging.INFO))

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    
    # Avoid duplicate handlers if setup is called multiple times (like during hot reload)
    root_logger.handlers = [h for h in root_logger.handlers if not isinstance(h, RotatingFileHandler)]
    
    root_logger.addHandler(all_handler)
    root_logger.addHandler(error_handler)
    root_logger.addHandler(warning_handler)
    root_logger.addHandler(info_handler)

    # Merge Uvicorn loggers into root logger
    for logger_name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        logger = logging.getLogger(logger_name)
        logger.handlers.clear()
        logger.propagate = True

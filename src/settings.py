"""Small-machine resource budgets, validated once at startup."""
import os
from dotenv import load_dotenv

load_dotenv()


def integer(name, default, minimum=1, maximum=1000000):
    value = int(os.getenv(name, default))
    if not minimum <= value <= maximum:
        raise ValueError(f'{name} must be between {minimum} and {maximum}')
    return value


HTTP_WORKERS = integer('HTTP_WORKERS', 2, maximum=4)
SCRIPT_WORKERS = integer('SCRIPT_WORKERS', 1, maximum=2)
QUEUE_LIMIT = integer('TASK_QUEUE_LIMIT', 32, maximum=256)
NOTIFY_QUEUE_LIMIT = integer('NOTIFY_QUEUE_LIMIT', 128, maximum=512)
HTTP_BODY_LIMIT = integer('HTTP_BODY_LIMIT', 128 * 1024, maximum=1024 * 1024)
HTTP_DEADLINE = integer('HTTP_DEADLINE', 30, maximum=120)
SCRIPT_TIMEOUT = integer('SCRIPT_TIMEOUT', 60, maximum=300)
SCRIPT_MEMORY_MB = integer('SCRIPT_MEMORY_MB', 192, minimum=64, maximum=512)
SCRIPT_PROCESSES = integer('SCRIPT_PROCESSES', 8, maximum=32)
WEB_THREADS = integer('WEB_THREADS', 4, maximum=8)

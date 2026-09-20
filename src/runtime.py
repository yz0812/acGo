"""Explicit single-process service lifecycle."""
from pathlib import Path
import os
from .models import DATA_DIR


class InstanceLock:
    def __init__(self):
        self.file = None

    def acquire(self):
        self.file = open(Path(DATA_DIR) / 'runtime.lock', 'a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                self.file.seek(0)
                if not self.file.read(1):
                    self.file.write(b'0')
                    self.file.flush()
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.file.close()
            self.file = None
            raise RuntimeError('该数据目录已有服务运行；仅支持单进程启动') from exc

    def release(self):
        if self.file:
            self.file.close()
            self.file = None


instance_lock = InstanceLock()


def start():
    instance_lock.acquire()
    try:
        from .app import initialize_app
        from .scheduler import start_scheduler
        from .notifier import outbox
        initialize_app()
        outbox.start()
        start_scheduler()
    except BaseException:
        stop()
        raise


def stop():
    from .scheduler import stop_scheduler
    from .notifier import outbox
    try:
        stop_scheduler()
        outbox.stop()
    finally:
        instance_lock.release()

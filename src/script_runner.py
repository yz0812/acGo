"""Run administrator-authored scripts in a separate, time-limited process."""
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import threading
import uuid
import psutil
from .settings import SCRIPT_TIMEOUT, SCRIPT_MEMORY_MB, SCRIPT_PROCESSES

OUTPUT_LIMIT = 128 * 1024
SCRIPT_TYPES = ('javascript', 'python')


def validate_task(data, current=None):
    """Normalize task fields; omitted type preserves legacy Curl accounts."""
    if 'name' in data and (not isinstance(data['name'], str) or not 1 <= len(data['name'].strip()) <= 100):
        raise ValueError('账号名称必须为 1～100 个字符')
    if 'cron_expr' in data and (not isinstance(data['cron_expr'], str) or not 1 <= len(data['cron_expr']) <= 50):
        raise ValueError('Cron 表达式必须为 1～50 个字符')
    if 'enabled' in data and type(data['enabled']) is not bool:
        raise ValueError('enabled 必须为布尔值')
    for field, minimum, maximum in [('retry_count', 0, 10), ('retry_interval', 1, 86400)]:
        if field in data and (type(data[field]) is not int or not minimum <= data[field] <= maximum):
            raise ValueError(f'{field} 必须是 {minimum} 到 {maximum} 之间的整数')
    task_type = data.get('task_type', getattr(current, 'task_type', 'curl'))
    if task_type not in ('curl', *SCRIPT_TYPES):
        raise ValueError('执行方式必须是 curl、javascript 或 python')
    field = 'curl_command' if task_type == 'curl' else 'script_content'
    content = data.get(field, getattr(current, field, ''))
    if not isinstance(content, str) or not content.strip():
        raise ValueError('请填写 Curl 命令' if task_type == 'curl' else '请填写脚本内容')
    if len(content) > 50000:
        raise ValueError('任务内容不能超过 50000 字符')
    return dict(task_type=task_type,
                curl_command=content if task_type == 'curl' else '',
                script_content=content if task_type != 'curl' else '')


class _Cgroup:
    """Optional delegated cgroup v2: kernel-enforced total memory/process cap."""
    def __init__(self):
        self.path = None
        parent = os.getenv('SCRIPT_CGROUP_ROOT')
        if parent and sys.platform == 'linux':
            self.path = Path(parent).resolve() / ('acgo-' + uuid.uuid4().hex)
            try:
                self.path.mkdir()
                required = ('memory.max', 'memory.swap.max', 'memory.oom.group', 'pids.max', 'cgroup.procs', 'cgroup.kill')
                if not all((self.path / name).is_file() for name in required):
                    raise OSError('需要已委派 memory/pids 控制器的 cgroup v2 目录')
                (self.path / 'memory.max').write_text(str(SCRIPT_MEMORY_MB * 1024 * 1024))
                (self.path / 'memory.swap.max').write_text('0')
                (self.path / 'pids.max').write_text(str(SCRIPT_PROCESSES * 8))
                (self.path / 'memory.oom.group').write_text('1')
            except Exception:
                if self.path.exists():
                    self.path.rmdir()
                raise

    def close(self):
        if self.path:
            try:
                (self.path / 'cgroup.kill').write_text('1')
            finally:
                for _ in range(50):
                    try:
                        self.path.rmdir()
                        break
                    except OSError:
                        time.sleep(0.02)
                else:
                    raise RuntimeError('无法清理脚本 cgroup')


def _stop_process(process, children=()):
    if os.name != 'nt':
        # Kill the group even if its leader already exited: children can still
        # hold pipe handles or consume CPU/memory.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    for child in children:
        try:
            child.kill()
        except psutil.Error:
            pass
    if process.poll() is None:
        process.kill()
    process.wait()


def run_script(task_type, source, timeout=SCRIPT_TIMEOUT):
    if task_type not in SCRIPT_TYPES:
        raise ValueError('不支持的脚本类型')
    executable = sys.executable if task_type == 'python' else shutil.which('node')
    if not executable:
        return dict(success=False, exit_code=None, output='', error='未找到 Node.js，请安装并将 node 加入 PATH 后重启服务')
    try:
        group = _Cgroup()
    except OSError as exc:
        return dict(success=False, exit_code=None, output='', error=f'脚本资源限制不可用: {exc}')
    try:
        with tempfile.TemporaryDirectory(prefix='acgo-script-') as folder:
            script = Path(folder) / ('task.py' if task_type == 'python' else 'task.cjs')
            script.write_text(source, encoding='utf-8')
            command = [executable, '-u', str(script)] if task_type == 'python' else [
                executable, f'--max-old-space-size={max(32, SCRIPT_MEMORY_MB // 2)}', str(script)]
            if os.name != 'nt':
                command = [sys.executable, str(Path(__file__).with_name('script_limits.py')),
                           str(SCRIPT_MEMORY_MB), str(max(1, int(timeout))), str(group.path or '-'), *command]
            env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUTF8='1')
            options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {'start_new_session': True}
            try:
                process = subprocess.Popen(command, cwd=folder, env=env, stdin=subprocess.DEVNULL,
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, **options)
            except OSError as exc:
                return dict(success=False, exit_code=None, output='', error=f'无法启动脚本: {exc}')
            buffers = [bytearray(), bytearray()]
            output_lock = threading.Lock()
            exceeded = threading.Event()
            size = [0]

            def drain(pipe, index):
                try:
                    while True:
                        chunk = pipe.read1(4096)
                        if not chunk:
                            break
                        with output_lock:
                            remaining = OUTPUT_LIMIT - size[0]
                            buffers[index].extend(chunk[:remaining])
                            size[0] += min(len(chunk), remaining)
                            if len(chunk) > remaining:
                                exceeded.set()
                                break
                finally:
                    pipe.close()

            readers = [threading.Thread(target=drain, args=(pipe, index), daemon=True)
                       for index, pipe in enumerate((process.stdout, process.stderr))]
            for reader in readers:
                reader.start()
            children = {}
            error = None
            deadline = time.monotonic() + timeout
            try:
                try:
                    root = psutil.Process(process.pid)
                except psutil.NoSuchProcess:
                    root = None
                while True:
                    if root is None:
                        break
                    try:
                        descendants = root.children(recursive=True)
                        children.update((child.pid, child) for child in descendants)
                        rss = root.memory_info().rss
                        live = 1
                        for child in list(children.values()):
                            try:
                                rss += child.memory_info().rss
                                live += 1
                            except psutil.Error:
                                pass
                        if rss > SCRIPT_MEMORY_MB * 1024 * 1024:
                            error = f'脚本进程树内存超过 {SCRIPT_MEMORY_MB} MiB'
                        elif live > SCRIPT_PROCESSES:
                            error = f'脚本进程数超过 {SCRIPT_PROCESSES}'
                    except psutil.NoSuchProcess:
                        pass
                    except psutil.AccessDenied:
                        error = '无法读取脚本资源占用，已终止执行'
                    if exceeded.is_set():
                        error = '脚本输出超过 128 KiB 限制'
                    if error or process.poll() is not None:
                        break
                    if time.monotonic() >= deadline:
                        error = f'脚本执行超时（{timeout} 秒）'
                        break
                    exceeded.wait(0.02)
            finally:
                _stop_process(process, list(children.values()))
                group.close()
                group.path = None
                for reader in readers:
                    reader.join(timeout=2)
            if exceeded.is_set():
                error = error or '脚本输出超过 128 KiB 限制'
            output = buffers[0].decode('utf-8', errors='replace')
            errors = buffers[1].decode('utf-8', errors='replace')
            if errors:
                output = output[:3500] + '\n[stderr]\n' + errors[:1400]
            if process.returncode != 0:
                error = error or f'脚本退出码 {process.returncode}: {errors[:400]}'
            return dict(success=error is None, exit_code=process.returncode,
                        output=output[:5000], error=error)
    finally:
        group.close()

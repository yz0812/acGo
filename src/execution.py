"""SQLite-backed bounded queue with independent HTTP/script workers."""
from datetime import datetime, timedelta
import json
import logging
import threading

from . import settings
from .models import Account, CheckinLog, Execution, ACTIVE_STATES, connection, db

logger = logging.getLogger(__name__)


def execution_data(item):
    return {'id': item.id, 'account_id': item.account_id, 'state': item.state,
            'attempt': item.attempt, 'due_at': item.due_at.isoformat(),
            'result': json.loads(item.result)}


def cancel_pending(account_id):
    """In-flight attempts finish; queued work and retries become stale immediately."""
    with connection():
        Execution.update(state='cancelled', updated_at=datetime.now(),
                         result=json.dumps({'error': '账号已修改、禁用或删除'}, ensure_ascii=False)).where(
            (Execution.account == account_id) & Execution.state.in_(('queued', 'retry_wait'))).execute()


def submit(account_id, *, manual=False, delay=0):
    with connection(), db.atomic('IMMEDIATE'):
        account = Account.get_or_none(Account.id == account_id)
        if account is None:
            return {'status': 'missing', 'error': '账号不存在'}
        if not manual and not account.enabled:
            return {'status': 'skipped', 'error': '账号已禁用'}
        existing = Execution.get_or_none((Execution.account == account_id) & Execution.state.in_(ACTIVE_STATES))
        if existing:
            return {'status': 'accepted', 'duplicate': True, 'execution': execution_data(existing)}
        if Execution.select().where(Execution.state.in_(ACTIVE_STATES)).count() >= settings.QUEUE_LIMIT:
            if not manual:
                CheckinLog.create(account=account_id, status='failed', error_message='执行队列已满，本次触发被拒绝')
                logger.warning('Task queue full; scheduled account %s rejected', account_id)
            return {'status': 'busy', 'error': '执行队列已满，请稍后再试'}
        item = Execution.create(account=account_id, account_version=account.version,
                                kind='curl' if account.task_type == 'curl' else 'script', manual=manual,
                                due_at=datetime.now() + timedelta(seconds=delay))
    engine.wake.set()
    return {'status': 'accepted', 'duplicate': False, 'execution': execution_data(item)}


class ExecutionEngine:
    def __init__(self):
        self.stop_event = threading.Event()
        self.wake = threading.Event()
        self.threads = []

    def recover(self):
        # A crash can occur after an external side effect. Never silently replay
        # running attempts; expose an interrupted result requiring manual review.
        with connection(), db.atomic('IMMEDIATE'):
            for item in Execution.select(Execution.account).where(Execution.state == 'running').iterator():
                CheckinLog.create(account=item.account_id, status='failed',
                                  error_message='服务在执行中退出，结果未知，请确认后手动重试')
            Execution.update(state='interrupted', updated_at=datetime.now(),
                             result=json.dumps({'error': '服务在执行中退出，结果未知，请确认后手动重试'}, ensure_ascii=False)
                             ).where(Execution.state == 'running').execute()

    def start(self):
        if self.threads:
            return
        self.recover()
        self.stop_event.clear()
        for kind, count in (('curl', settings.HTTP_WORKERS), ('script', settings.SCRIPT_WORKERS)):
            for number in range(count):
                thread = threading.Thread(target=self._loop, args=(kind,),
                                          name=f'acgo-{kind}-{number}', daemon=True)
                self.threads.append(thread)
                thread.start()

    def stop(self):
        self.stop_event.set()
        self.wake.set()
        for thread in self.threads:
            thread.join()
        self.threads.clear()

    def _loop(self, kind):
        while not self.stop_event.is_set():
            try:
                if self.run_one(kind):
                    continue
            except Exception:
                logger.exception('Task worker error')
            self.wake.wait(1)
            self.wake.clear()

    def run_one(self, kind, now=None):
        """Claim and run one attempt. Waiting/retry times never hold a worker."""
        now = now or datetime.now()
        with connection():
            # Read-only empty-queue check avoids idle write locks/fsyncs.
            due = ((Execution.kind == kind) & Execution.state.in_(('queued', 'retry_wait')) & (Execution.due_at <= now))
            if not Execution.select().where(due).exists():
                return False
            with db.atomic('IMMEDIATE'):
                item = Execution.select().where(due).order_by(Execution.due_at, Execution.id).first()
                if item is None:
                    return False
                account = Account.get_or_none(Account.id == item.account_id)
                if account is None or account.version != item.account_version or (not item.manual and not account.enabled):
                    item.state = 'cancelled'
                    item.updated_at = now
                    item.result = json.dumps({'error': '账号配置已变更'}, ensure_ascii=False)
                    item.save()
                    return True
                item.state = 'running'
                item.updated_at = now
                item.save()
        # No DB connection/transaction is held during HTTP or script execution.
        try:
            from .scheduler import execute_attempt
            result = execute_attempt(account)
        except Exception as exc:
            logger.exception('Attempt failed for account %s', account.id)
            result = {'status': 'failed', 'error': str(exc)[:500]}

        with connection(), db.atomic('IMMEDIATE'):
            current = Account.get_or_none(Account.id == account.id)
            if current is None:  # Account deletion cascades execution records.
                return True
            item.updated_at = datetime.now()
            # Large output already lives in CheckinLog; status requests only
            # need outcome metadata and a log id.
            public_result = {key: value for key, value in result.items() if key != 'response_body'}
            item.result = json.dumps(public_result, ensure_ascii=False)
            if current.version != item.account_version:
                item.state = 'cancelled'
            elif result['status'] == 'failed' and item.attempt < account.retry_count:
                item.attempt += 1
                item.state = 'retry_wait'
                item.due_at = datetime.now() + timedelta(seconds=account.retry_interval)
            else:
                item.state = result['status']
                from .notifier import send_all_notifications
                accepted = send_all_notifications(account.name, result['status'], result.get('code'),
                                                  result.get('error') or '签到成功', result.get('response_body'))
                if accepted is False:
                    public_result['notification_error'] = '通知队列已满'
                    item.result = json.dumps(public_result, ensure_ascii=False)
                    if result.get('log_id'):
                        CheckinLog.update(error_message=(result.get('error') or '')[:450] + ' [通知队列已满]').where(
                            CheckinLog.id == result['log_id']).execute()
            item.save()
        return True


engine = ExecutionEngine()

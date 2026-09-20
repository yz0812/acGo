"""定时任务调度模块"""
import logging
import re
import json
import random
import shlex
import time
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Tuple
from . import http_client as requests
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED
from .models import Account, CheckinLog, Config, Execution, Notification, ACTIVE_STATES, db, connection
from .script_runner import run_script
from .execution import submit, engine

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 全局调度器实例
scheduler = BackgroundScheduler(executors={'default': {'type': 'threadpool', 'max_workers': 2}},
                                job_defaults={'misfire_grace_time': 300, 'coalesce': True, 'max_instances': 1})


def parse_curl_command(curl_cmd: str) -> Dict[str, Any]:
    """
    解析 curl 命令为 requests 参数（使用 shlex 正确处理引号）

    支持各种 curl 格式，包括：
    - 不同的选项顺序
    - 单引号、双引号或无引号
    - 短格式 (-X, -H, -d) 和长格式 (--request, --header, --data)
    - URL 在任意位置

    Args:
        curl_cmd: curl 命令字符串

    Returns:
        包含 url, method, headers, data 等的字典
    """
    try:
        # 输入长度限制，防止 DoS 攻击
        if len(curl_cmd) > 50000:
            raise ValueError('curl 命令过长（超过 50000 字符）')

        # 只移除行继续符，保留内部空格
        curl_cmd = curl_cmd.replace('\\\n', ' ').replace('\\n', ' ')
        curl_cmd = curl_cmd.strip()

        # 确保命令以 curl 开头
        if not curl_cmd.startswith('curl'):
            curl_cmd = 'curl ' + curl_cmd

        # 使用 shlex 正确解析命令行参数
        try:
            tokens = shlex.split(curl_cmd)
        except ValueError as e:
            raise ValueError(f'无效的引号或转义: {e}')

        # 初始化变量
        url = None
        method = 'GET'
        headers = {}
        cookies = {}
        data_parts = []

        i = 0
        while i < len(tokens):
            token = tokens[i]

            # 跳过 'curl' 命令本身
            if token == 'curl':
                i += 1
                continue

            # 提取 URL (--url 或第一个非选项 http(s) 参数)
            if token == '--url' and i + 1 < len(tokens):
                url = tokens[i + 1]
                i += 2
                continue
            elif not token.startswith('-') and token.startswith(('http://', 'https://')) and url is None:
                url = token
                i += 1
                continue

            # 提取 Method
            if token in ('-X', '--request') and i + 1 < len(tokens):
                method = tokens[i + 1].upper()
                i += 2
                continue

            # 提取 Headers
            if token in ('-H', '--header') and i + 1 < len(tokens):
                header_value = tokens[i + 1]
                if ':' in header_value:
                    key, value = header_value.split(':', 1)
                    headers[key.strip()] = value.strip()
                i += 2
                continue

            # 提取 User-Agent
            if token in ('-A', '--user-agent') and i + 1 < len(tokens):
                headers['User-Agent'] = tokens[i + 1]
                i += 2
                continue

            # 提取 Referer
            if token in ('-e', '--referer') and i + 1 < len(tokens):
                headers['Referer'] = tokens[i + 1]
                i += 2
                continue

            # 提取 Cookies
            if token in ('-b', '--cookie') and i + 1 < len(tokens):
                cookie_str = tokens[i + 1]
                for item in cookie_str.split(';'):
                    item = item.strip()
                    if '=' in item:
                        k, v = item.split('=', 1)
                        cookies[k.strip()] = v.strip()
                i += 2
                continue

            # 提取 Data (支持多个 -d)
            if token in ('-d', '--data', '--data-raw', '--data-binary', '--data-urlencode') and i + 1 < len(tokens):
                data_parts.append(tokens[i + 1])
                if method == 'GET':
                    method = 'POST'
                i += 2
                continue

            # 提取 Form Data
            if token in ('-F', '--form') and i + 1 < len(tokens):
                data_parts.append(tokens[i + 1])
                if method == 'GET':
                    method = 'POST'
                i += 2
                continue

            # 其他未识别的参数，跳过
            i += 1

        # 验证 URL
        if not url:
            raise ValueError('无法解析 URL，请确保 curl 命令包含完整的 URL（http:// 或 https://）')

        # 合并多个 data 参数
        data = '&'.join(data_parts) if data_parts else None

        return {
            'url': url,
            'method': method,
            'headers': headers,
            'cookies': cookies,
            'data': data
        }

    except Exception as e:
        logger.error(f'解析 curl 命令失败: {e}')
        raise ValueError(f'无效的 curl 命令: {e}')


def parse_random_cron(cron_expr: str) -> Tuple[str, Optional[int]]:
    """
    解析支持随机时间窗口的 Cron 表达式
    
    支持格式：
    - 标准 Cron: "0 8 * * *" → 每天 8:00 执行
    - 随机窗口: "R(09:00-09:30) * * *" → 每天 9:00-9:30 之间随机执行
    
    Args:
        cron_expr: Cron 表达式字符串
        
    Returns:
        (标准 cron 表达式, 随机延迟秒数上限) 
        如果不是随机模式，随机延迟为 None
    """
    # 检测随机时间窗口语法 R(HH:MM-HH:MM)
    random_pattern = r'^R\((\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})\)\s+(.+)$'
    match = re.match(random_pattern, cron_expr.strip())
    
    if not match:
        # 标准 Cron 表达式，直接返回
        return cron_expr, None
    
    # 解析随机时间窗口
    start_hour, start_minute, end_hour, end_minute, rest = match.groups()
    start_hour, start_minute = int(start_hour), int(start_minute)
    end_hour, end_minute = int(end_hour), int(end_minute)
    
    # 验证时间有效性
    if not (0 <= start_hour <= 23 and 0 <= start_minute <= 59):
        raise ValueError(f'起始时间无效: {start_hour}:{start_minute}')
    if not (0 <= end_hour <= 23 and 0 <= end_minute <= 59):
        raise ValueError(f'结束时间无效: {end_hour}:{end_minute}')
    
    # 计算时间窗口（分钟）
    start_total_minutes = start_hour * 60 + start_minute
    end_total_minutes = end_hour * 60 + end_minute
    
    if end_total_minutes <= start_total_minutes:
        raise ValueError('结束时间必须晚于起始时间（暂不支持跨天）')
    
    window_minutes = end_total_minutes - start_total_minutes
    max_delay_seconds = window_minutes * 60
    
    # 构造标准 Cron（使用窗口开始时间）
    standard_cron = f'{start_minute} {start_hour} {rest}'
    
    return standard_cron, max_delay_seconds


def execute_checkin_with_random_delay(account_id: int, max_delay_seconds=None):
    return submit(account_id, delay=random.randint(0, max_delay_seconds or 0))


def execute_checkin(account_id: int, retry_attempt=0, skip_enabled_check=False):
    """All entry points submit to the same bounded queue."""
    return submit(account_id, manual=skip_enabled_check)


def execute_attempt(account):
    """One attempt only; the durable execution queue handles retries."""
    fields = dict(account=account.id)
    result = {'status': 'failed'}
    try:
        if account.task_type in ('python', 'javascript'):
            fields.update(request_method=account.task_type.upper(), request_data=account.script_content)
            outcome = run_script(account.task_type, account.script_content)
            fields.update(exit_code=outcome['exit_code'], response_body=outcome['output'], error_message=outcome['error'])
            result.update(status='success' if outcome['success'] else 'failed', exit_code=outcome['exit_code'],
                          error=outcome['error'], response_body=outcome['output'])
        elif account.task_type == 'curl':
            params = parse_curl_command(account.curl_command)
            fields.update(request_method=params['method'], request_url=params['url'], request_data=params['data'],
                          request_headers=json.dumps(params['headers'], ensure_ascii=False),
                          request_cookies=json.dumps(params['cookies'], ensure_ascii=False))
            response = requests.request(**params)
            success = 200 <= response.status_code < 300
            error = None if success else f'HTTP {response.status_code}'
            fields.update(response_code=response.status_code, response_body=response.text, error_message=error)
            result.update(status='success' if success else 'failed', code=response.status_code, error=error,
                          response_body=response.text, truncated=response.truncated)
        else:
            raise ValueError('不支持的执行方式')
    except Exception as exc:
        fields['error_message'] = str(exc)[:500]
        result['error'] = str(exc)[:500]
    fields['status'] = result['status']
    with connection():
        current = Account.get_or_none(Account.id == account.id)
        if current is None:
            return {'status': 'cancelled', 'error': '账号已删除'}
        if current.version != account.version:
            return {'status': 'cancelled', 'error': '执行期间账号已变更，结果未写入当前账号'}
        log = CheckinLog.create(**fields)
        result['log_id'] = log.id
    return result


def cron_trigger(cron_expr):
    standard, delay = parse_random_cron(cron_expr)
    return CronTrigger.from_crontab(standard), delay


def add_job(account_id: int, cron_expr: str):
    trigger, delay = cron_trigger(cron_expr)
    scheduler.add_job(execute_checkin_with_random_delay, trigger=trigger,
                      args=[account_id, delay], id=f'account_{account_id}', replace_existing=True)


def remove_job(account_id: int):
    job_id = f'account_{account_id}'
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)


def reload_all_jobs():
    with connection():
        for account in Account.select(Account.id, Account.cron_expr).where(Account.enabled == True).iterator():
            try:
                add_job(account.id, account.cron_expr)
            except Exception:
                logger.exception('Invalid schedule for account %s', account.id)


def start_scheduler():
    if scheduler.running:
        return
    reload_all_jobs()
    scheduler.add_job(auto_clean_logs, 'interval', seconds=10, id='auto_clean_logs', replace_existing=True)
    engine.start()
    scheduler.start()


def auto_clean_logs():
    """Short 200-row deletes, with a one-second work budget per cleanup tick."""
    with connection():
        config = dict(Config.select(Config.key, Config.value).where(
            Config.key.in_(('auto_clean_logs', 'max_logs_count'))).tuples())
        if config.get('auto_clean_logs', 'true') == 'true':
            try:
                keep = max(100, int(config.get('max_logs_count', '500')))
            except ValueError:
                keep = 500
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                cutoff = (CheckinLog.select(CheckinLog.id).order_by(CheckinLog.executed_at.desc(), CheckinLog.id.desc())
                          .offset(keep).limit(200))
                if CheckinLog.delete().where(CheckinLog.id.in_(cutoff)).execute() < 200:
                    break
        # Queue history is bounded independently of the user log-retention switch.
        cutoff = datetime.now() - timedelta(days=1)
        for model, predicate in ((Execution, (~Execution.state.in_(ACTIVE_STATES)) & (Execution.updated_at < cutoff)),
                                 (Notification, (Notification.state.in_(('sent', 'failed', 'cancelled'))) & (Notification.due_at < cutoff))):
            old = model.select(model.id).where(predicate).order_by(model.id.desc()).offset(100).limit(200)
            model.delete().where(model.id.in_(old)).execute()
        # Keep recent terminal records bounded even during a high-volume day.
        for model, predicate in ((Execution, ~Execution.state.in_(ACTIVE_STATES)),
                                 (Notification, Notification.state.in_(('sent', 'failed', 'cancelled')))):
            old = model.select(model.id).where(predicate).order_by(model.id.desc()).offset(1000).limit(200)
            model.delete().where(model.id.in_(old)).execute()


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=True)
    engine.stop()


def _schedule_error(event):
    if not event.job_id.startswith('account_'):
        return
    account_id = int(event.job_id.removeprefix('account_'))
    try:
        with connection():
            if Account.get_or_none(Account.id == account_id):
                CheckinLog.create(account=account_id, status='failed',
                                  error_message='定时触发失败或超过 5 分钟宽限，本次未执行，请检查服务负载')
    except Exception:
        logger.exception('Unable to record missed trigger for %s', account_id)


scheduler.add_listener(_schedule_error, EVENT_JOB_ERROR | EVENT_JOB_MISSED)

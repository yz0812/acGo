"""定时任务调度模块"""
import time
import logging
import re
import json
import random
import shlex
from datetime import datetime
from typing import Dict, Any, Optional, Tuple
import requests
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from .models import Account, CheckinLog, Config, db

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 全局调度器实例
scheduler = BackgroundScheduler()


def redact_sensitive_data(headers: dict, cookies: dict) -> tuple:
    """
    脱敏敏感的 Headers 和 Cookies

    Args:
        headers: HTTP 请求头字典
        cookies: Cookie 字典

    Returns:
        (脱敏后的 headers, 脱敏后的 cookies)
    """
    # 定义需要脱敏的敏感键（不区分大小写）
    sensitive_header_keys = [
        'authorization',
        'x-api-key',
        'x-auth-token',
        'api-key',
        'token',
        'secret',
        'password',
        'apikey'
    ]

    # 脱敏 Headers
    redacted_headers = {}
    for key, value in headers.items():
        if key.lower() in sensitive_header_keys or 'auth' in key.lower():
            redacted_headers[key] = '***REDACTED***'
        else:
            redacted_headers[key] = value

    # 脱敏 Cookies（所有 Cookie 都可能包含敏感信息）
    redacted_cookies = {}
    for key, value in cookies.items():
        # 只保留前 4 个字符用于识别，其余用 * 替换
        if len(value) > 4:
            redacted_cookies[key] = value[:4] + '***'
        else:
            redacted_cookies[key] = '***'

    return redacted_headers, redacted_cookies


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


def execute_checkin_with_random_delay(account_id: int, max_delay_seconds: Optional[int] = None):
    """
    带随机延迟的签到执行包装函数
    
    Args:
        account_id: 账号ID
        max_delay_seconds: 最大随机延迟秒数，None 表示立即执行
    """
    if max_delay_seconds:
        # 随机延迟 0 到 max_delay_seconds 秒
        delay = random.randint(0, max_delay_seconds)
        logger.info(f'账号 {account_id} 将在 {delay} 秒后执行签到（随机延迟）')
        time.sleep(delay)
    
    # 执行签到
    execute_checkin(account_id)


def send_webhook_notification(account_name: str, status: str, response_code: int = None, message: str = '', response_body: str = None):
    """
    发送 Webhook 通知

    Args:
        account_name: 账号名称
        status: 状态 (success/failed)
        response_code: 响应状态码
        message: 消息内容
        response_body: 响应内容（可选）
    """
    try:
        # 获取 Webhook 配置
        enabled_config = Config.get_or_none(Config.key == 'webhook_enabled')
        if not enabled_config or enabled_config.value != 'true':
            return  # Webhook 未启用

        url_config = Config.get_or_none(Config.key == 'webhook_url')
        if not url_config or not url_config.value:
            return  # 未配置 URL

        method_config = Config.get_or_none(Config.key == 'webhook_method')
        method = method_config.value if method_config else 'POST'

        headers_config = Config.get_or_none(Config.key == 'webhook_headers')
        headers = {}
        if headers_config and headers_config.value:
            try:
                headers = json.loads(headers_config.value)
                # 类型验证：确保是字典
                if not isinstance(headers, dict):
                    logger.warning('Webhook headers 不是有效的 JSON 对象，已重置为空字典')
                    headers = {}
            except json.JSONDecodeError:
                logger.error('Webhook headers JSON 格式错误')
                headers = {}

        # 检查是否包含响应内容
        include_response_config = Config.get_or_none(Config.key == 'webhook_include_response')
        include_response = include_response_config and include_response_config.value == 'true'

        # 构造数据
        payload = {
            'title': account_name,
            'account_name': account_name,
            'status': status,
            'response_code': response_code,
            'date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'message': message
        }

        # 如果启用了包含响应内容，且有响应内容，则添加
        if include_response and response_body:
            payload['response_body'] = response_body

        # 发送请求
        if method.upper() == 'POST':
            # 检测 Content-Type，决定发送格式
            content_type = headers.get('Content-Type', 'application/json').lower()

            if 'multipart/form-data' in content_type:
                # Multipart 格式：移除 Content-Type，让 requests 自动生成 boundary
                headers.pop('Content-Type', None)
                # 将 payload 转换为 files 格式
                files = {k: (None, str(v)) for k, v in payload.items()}
                response = requests.post(
                    url_config.value,
                    files=files,
                    headers=headers,
                    timeout=10
                )
            elif 'application/x-www-form-urlencoded' in content_type:
                # Form 表单格式
                response = requests.post(
                    url_config.value,
                    data=payload,
                    headers=headers,
                    timeout=10
                )
            else:
                # 默认 JSON 格式
                headers['Content-Type'] = 'application/json'
                response = requests.post(
                    url_config.value,
                    json=payload,
                    headers=headers,
                    timeout=10
                )
        else:  # GET
            response = requests.get(
                url_config.value,
                params=payload,
                headers=headers,
                timeout=10
            )

      #  if 200 <= response.status_code < 300:
      #      logger.info(f'Webhook 通知发送成功: {account_name}')
      #  else:
      #      logger.warning(f'Webhook 通知失败: HTTP {response.status_code}')

    except Exception as e:
        logger.error(f'Webhook 通知异常: {e}')


def execute_checkin(account_id: int, retry_attempt: int = 0, skip_enabled_check: bool = False) -> Dict[str, Any]:
    """
    执行签到任务

    Args:
        account_id: 账号ID
        retry_attempt: 当前重试次数
        skip_enabled_check: 是否跳过禁用状态检查（手动签到时为 True）

    Returns:
        执行结果字典
    """
    db.connect(reuse_if_open=True)

    # 初始化变量，避免异常时未定义
    account = None
    req_params = {}

    try:
        account = Account.get_by_id(account_id)
    except Exception as e:
        logger.error(f'获取账号失败: account_id={account_id}, error={e}')
        db.close()
        return {'status': 'failed', 'error': f'账号不存在: {account_id}'}

    try:
        if not skip_enabled_check and not account.enabled:
           # logger.info(f'账号 {account.name} 已禁用，跳过签到')
            return {'status': 'skipped', 'message': '账号已禁用'}

        # 解析 curl 命令
        req_params = parse_curl_command(account.curl_command)

        # 使用循环重试，避免递归导致栈溢出和线程阻塞
        for attempt in range(account.retry_count + 1):
            try:
                # 执行请求
                # logger.info(f'开始执行签到: {account.name} (尝试 {attempt + 1}/{account.retry_count + 1})')

                response = requests.request(
                    method=req_params['method'],
                    url=req_params['url'],
                    headers=req_params['headers'],
                    data=req_params['data'],
                    cookies=req_params['cookies'],
                    timeout=30
                )

                # 判断是否成功（2xx 状态码）
                is_success = 200 <= response.status_code < 300

                # 脱敏敏感信息
                redacted_headers, redacted_cookies = redact_sensitive_data(
                    req_params.get('headers', {}),
                    req_params.get('cookies', {})
                )

                # 记录日志（保存请求参数，敏感信息已脱敏）
                log = CheckinLog.create(
                    account=account,
                    status='success' if is_success else 'failed',
                    response_code=response.status_code,
                    response_body=response.text[:5000],  # 限制长度（增加到5000字符）
                    error_message=None if is_success else f'HTTP {response.status_code}',
                    executed_at=datetime.now(),
                    # 保存请求参数（敏感信息已脱敏）
                    request_method=req_params['method'],
                    request_url=req_params['url'],
                    request_headers=json.dumps(redacted_headers, ensure_ascii=False) if redacted_headers else None,
                    request_cookies=json.dumps(redacted_cookies, ensure_ascii=False) if redacted_cookies else None,
                    request_data=req_params['data']
                )

                if is_success:
                    # logger.info(f'签到成功: {account.name} - HTTP {response.status_code}')

                    # 调用 Webhook
                    send_webhook_notification(
                        account_name=account.name,
                        status='success',
                        response_code=response.status_code,
                        message='签到成功',
                        response_body=response.text[:5000]  # 传递响应内容
                    )

                    return {
                        'status': 'success',
                        'code': response.status_code,
                        'log_id': log.id
                    }
                else:
                    # 失败且未达到重试上限，继续重试
                    if attempt < account.retry_count:
                        logger.warning(f'签到失败，{account.retry_interval}秒后重试: {account.name}')
                        time.sleep(account.retry_interval)
                        continue  # 继续下一次重试
                    else:
                        logger.error(f'签到失败（已达重试上限）: {account.name}')

                        # 调用 Webhook
                        send_webhook_notification(
                            account_name=account.name,
                            status='failed',
                            response_code=response.status_code,
                            message=f'签到失败: HTTP {response.status_code}',
                            response_body=response.text[:5000]  # 传递响应内容
                        )

                        return {
                            'status': 'failed',
                            'code': response.status_code,
                            'log_id': log.id
                        }

            except requests.RequestException as e:
                # 网络错误
                error_msg = str(e)
                logger.error(f'请求异常: {account.name} - {error_msg}')

                # 脱敏敏感信息
                redacted_headers, redacted_cookies = redact_sensitive_data(
                    req_params.get('headers', {}),
                    req_params.get('cookies', {})
                )

                CheckinLog.create(
                    account=account,
                    status='failed',
                    response_code=None,
                    response_body=None,
                    error_message=error_msg[:500],
                    executed_at=datetime.now(),
                    # 保存请求参数（敏感信息已脱敏）
                    request_method=req_params.get('method'),
                    request_url=req_params.get('url'),
                    request_headers=json.dumps(redacted_headers, ensure_ascii=False) if redacted_headers else None,
                    request_cookies=json.dumps(redacted_cookies, ensure_ascii=False) if redacted_cookies else None,
                    request_data=req_params.get('data')
                )

                # 重试逻辑
                if attempt < account.retry_count:
                    logger.warning(f'网络异常，{account.retry_interval}秒后重试: {account.name}')
                    time.sleep(account.retry_interval)
                    continue  # 继续下一次重试

                # 最后一次失败，调用 Webhook
                send_webhook_notification(
                    account_name=account.name,
                    status='failed',
                    response_code=None,
                    message=f'网络异常: {error_msg}'
                )

                return {'status': 'failed', 'error': error_msg}

    except Exception as e:
        logger.error(f'未知错误: {account.name} - {e}')

        # 脱敏敏感信息
        redacted_headers, redacted_cookies = redact_sensitive_data(
            req_params.get('headers', {}),
            req_params.get('cookies', {})
        )

        CheckinLog.create(
            account=account,
            status='failed',
            response_code=None,
            response_body=None,
            error_message=str(e)[:500],
            executed_at=datetime.now(),
            # 保存请求参数（敏感信息已脱敏）
            request_method=req_params.get('method'),
            request_url=req_params.get('url'),
            request_headers=json.dumps(redacted_headers, ensure_ascii=False) if redacted_headers else None,
            request_cookies=json.dumps(redacted_cookies, ensure_ascii=False) if redacted_cookies else None,
            request_data=req_params.get('data')
        )

        # 调用 Webhook
        send_webhook_notification(
            account_name=account.name,
            status='failed',
            response_code=None,
            message=f'未知错误: {str(e)}'
        )

        return {'status': 'failed', 'error': str(e)}
        
    finally:
        db.close()


def add_job(account_id: int, cron_expr: str):
    """
    添加定时任务
    
    支持标准 Cron 和随机时间窗口语法：
    - 标准: "0 8 * * *" → 每天 8:00 执行
    - 随机: "R(09:00-09:30) * * *" → 每天 9:00-9:30 随机执行
    
    Args:
        account_id: 账号ID
        cron_expr: Cron 表达式
    """
    job_id = f'account_{account_id}'
    
    # 移除旧任务（如果存在）
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
    
    # 解析 Cron 表达式（支持随机时间窗口）
    standard_cron, max_delay_seconds = parse_random_cron(cron_expr)
    
    # 解析标准 Cron 表达式
    parts = standard_cron.split()
    if len(parts) != 5:
        raise ValueError('Cron 表达式格式错误，应为 5 个字段（分 时 日 月 周）')
    
    minute, hour, day, month, day_of_week = parts
    
    # 根据是否有随机延迟选择执行函数
    if max_delay_seconds:
        # 随机模式：使用带延迟的包装函数
        func = execute_checkin_with_random_delay
        args = [account_id, max_delay_seconds]
        logger.info(f'已添加随机定时任务: account_id={account_id}, cron={cron_expr}, 随机窗口={max_delay_seconds}秒')
    else:
        # 标准模式：直接执行
        func = execute_checkin
        args = [account_id]
        # logger.info(f'已添加定时任务: account_id={account_id}, cron={cron_expr}')
    
    # 添加新任务
    scheduler.add_job(
        func=func,
        trigger=CronTrigger(
            minute=minute,
            hour=hour,
            day=day,
            month=month,
            day_of_week=day_of_week
        ),
        args=args,
        id=job_id,
        replace_existing=True
    )


def remove_job(account_id: int):
    """移除定时任务"""
    job_id = f'account_{account_id}'
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
       # logger.info(f'已移除定时任务: account_id={account_id}')


def reload_all_jobs():
    """重新加载所有启用的账号任务"""
    db.connect(reuse_if_open=True)
    
    try:
        # 清空所有任务
        scheduler.remove_all_jobs()
        
        # 加载启用的账号
        accounts = Account.select().where(Account.enabled == True)
        
        for account in accounts:
            try:
                add_job(account.id, account.cron_expr)
            except Exception as e:
                logger.error(f'加载任务失败: {account.name} - {e}')
        
      #  logger.info(f'已重新加载 {len(accounts)} 个定时任务')
        
    finally:
        db.close()


def start_scheduler():
    """启动调度器"""
    if not scheduler.running:
        scheduler.start()
        reload_all_jobs()
        
        # 添加自动清理任务（每天凌晨 3:00 执行）
        scheduler.add_job(
            func=auto_clean_logs,
            trigger=CronTrigger(hour=3, minute=0),
            id='auto_clean_logs',
            replace_existing=True
        )
        logger.info('调度器已启动，自动清理任务已添加')


def auto_clean_logs():
    """自动清理超出限制的签到记录"""
    db.connect(reuse_if_open=True)

    try:
        # 检查是否启用自动清理
        auto_clean_config = Config.get_or_none(Config.key == 'auto_clean_logs')
        if not auto_clean_config or auto_clean_config.value != 'true':
            logger.info('自动清理未启用，跳过')
            return

        # 获取最大记录数，添加类型验证
        max_logs_config = Config.get_or_none(Config.key == 'max_logs_count')
        try:
            max_logs = int(max_logs_config.value) if max_logs_config else 500
        except (ValueError, AttributeError):
            max_logs = 500
            logger.warning('无效的 max_logs_count 配置，使用默认值 500')

        # 获取当前记录总数
        total_logs = CheckinLog.select().count()

        if total_logs <= max_logs:
            logger.info(f'当前记录数 {total_logs} 未超过限制 {max_logs}，无需清理')
            return

        # 计算需要删除的记录数
        to_delete = total_logs - max_logs

        # 使用事务保护，避免并发问题
        with db.atomic():
            # 使用子查询直接删除，避免内存问题和参数限制
            subquery = (CheckinLog
                        .select(CheckinLog.id)
                        .order_by(CheckinLog.executed_at.asc())
                        .limit(to_delete))

            deleted = (CheckinLog
                       .delete()
                       .where(CheckinLog.id.in_(subquery))
                       .execute())

        logger.info(f'自动清理完成：删除了 {deleted} 条旧记录，保留最新 {max_logs} 条')

    except Exception as e:
        logger.error(f'自动清理失败: {e}')

    finally:
        db.close()


def stop_scheduler():
    """停止调度器"""
    if scheduler.running:
        scheduler.shutdown()
      #  logger.info('调度器已停止')

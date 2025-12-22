"""定时任务调度模块"""
import time
import logging
import re
import json
from datetime import datetime
from typing import Dict, Any
import requests
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from models import Account, CheckinLog, Config, db

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 全局调度器实例
scheduler = BackgroundScheduler()


def parse_curl_command(curl_cmd: str) -> Dict[str, Any]:
    """
    解析 curl 命令为 requests 参数

    Args:
        curl_cmd: curl 命令字符串

    Returns:
        包含 url, method, headers, data 等的字典
    """
    try:
        # 移除换行符和多余空格
        curl_cmd = curl_cmd.replace('\\\n', ' ').replace('\\n', ' ')
        curl_cmd = re.sub(r'\s+', ' ', curl_cmd).strip()

        # 提取 URL
        url_match = re.search(r"curl\s+['\"]?([^'\">\s]+)['\"]?", curl_cmd)
        if not url_match:
            raise ValueError('无法解析 URL')
        url = url_match.group(1)

        # 提取 Method（默认 GET）
        method_match = re.search(r"-X\s+([A-Z]+)", curl_cmd)
        method = method_match.group(1) if method_match else 'GET'

        # 提取 Headers
        headers = {}
        header_matches = re.findall(r"-H\s+['\"]([^:]+):\s*([^'\"]+)['\"]", curl_cmd)
        for key, value in header_matches:
            headers[key.strip()] = value.strip()

        # 提取 Cookies
        cookies = {}
        cookie_match = re.search(r"-b\s+['\"]([^'\"]+)['\"]", curl_cmd)
        if cookie_match:
            cookie_str = cookie_match.group(1)
            for item in cookie_str.split(';'):
                if '=' in item:
                    k, v = item.split('=', 1)
                    cookies[k.strip()] = v.strip()

        # 提取 Data
        data = None
        data_match = re.search(r"--data-raw\s+['\"](.+?)['\"](?:\s|$)", curl_cmd, re.DOTALL)
        if not data_match:
            data_match = re.search(r"--data\s+['\"](.+?)['\"](?:\s|$)", curl_cmd, re.DOTALL)
        if not data_match:
            data_match = re.search(r"-d\s+['\"](.+?)['\"](?:\s|$)", curl_cmd, re.DOTALL)

        if data_match:
            data = data_match.group(1)
            # 如果是 POST 但没有指定 method，自动设置
            if method == 'GET':
                method = 'POST'

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
            except json.JSONDecodeError:
                logger.error('Webhook headers JSON 格式错误')

        # 检查是否包含响应内容
        include_response_config = Config.get_or_none(Config.key == 'webhook_include_response')
        include_response = include_response_config and include_response_config.value == 'true'

        # 构造数据
        payload = {
            'account_name': account_name,
            'status': status,
            'response_code': response_code,
            'executed_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'message': message
        }

        # 如果启用了包含响应内容，且有响应内容，则添加
        if include_response and response_body:
            payload['response_body'] = response_body

        # 发送请求
        if method.upper() == 'POST':
            headers.setdefault('Content-Type', 'application/json')
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


def execute_checkin(account_id: int, retry_attempt: int = 0) -> Dict[str, Any]:
    """
    执行签到任务
    
    Args:
        account_id: 账号ID
        retry_attempt: 当前重试次数
        
    Returns:
        执行结果字典
    """
    db.connect(reuse_if_open=True)
    
    try:
        account = Account.get_by_id(account_id)
        
        if not account.enabled:
           # logger.info(f'账号 {account.name} 已禁用，跳过签到')
            return {'status': 'skipped', 'message': '账号已禁用'}
        
        # 解析 curl 命令
        req_params = parse_curl_command(account.curl_command)
        
        # 执行请求
       # logger.info(f'开始执行签到: {account.name} (尝试 {retry_attempt + 1}/{account.retry_count + 1})')
        
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
        
        # 记录日志
        log = CheckinLog.create(
            account=account,
            status='success' if is_success else 'failed',
            response_code=response.status_code,
            response_body=response.text[:5000],  # 限制长度（增加到5000字符）
            error_message=None if is_success else f'HTTP {response.status_code}',
            executed_at=datetime.now()
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
            # 失败且未达到重试上限，进行重试
            if retry_attempt < account.retry_count:
                logger.warning(f'签到失败，{account.retry_interval}秒后重试: {account.name}')
                time.sleep(account.retry_interval)
                return execute_checkin(account_id, retry_attempt + 1)
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
        
        CheckinLog.create(
            account=account,
            status='failed',
            response_code=None,
            response_body=None,
            error_message=error_msg[:500],
            executed_at=datetime.now()
        )
        
        # 重试逻辑
        if retry_attempt < account.retry_count:
            logger.warning(f'网络异常，{account.retry_interval}秒后重试: {account.name}')
            time.sleep(account.retry_interval)
            return execute_checkin(account_id, retry_attempt + 1)

        # 调用 Webhook
        send_webhook_notification(
            account_name=account.name,
            status='failed',
            response_code=None,
            message=f'网络异常: {error_msg}'
        )

        return {'status': 'failed', 'error': error_msg}

    except Exception as e:
        logger.error(f'未知错误: {account.name} - {e}')

        CheckinLog.create(
            account=account,
            status='failed',
            response_code=None,
            response_body=None,
            error_message=str(e)[:500],
            executed_at=datetime.now()
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
    
    Args:
        account_id: 账号ID
        cron_expr: Cron 表达式（如 "0 8 * * *"）
    """
    job_id = f'account_{account_id}'
    
    # 移除旧任务（如果存在）
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
    
    # 解析 Cron 表达式
    parts = cron_expr.split()
    if len(parts) != 5:
        raise ValueError('Cron 表达式格式错误，应为 5 个字段（分 时 日 月 周）')
    
    minute, hour, day, month, day_of_week = parts
    
    # 添加新任务
    scheduler.add_job(
        func=execute_checkin,
        trigger=CronTrigger(
            minute=minute,
            hour=hour,
            day=day,
            month=month,
            day_of_week=day_of_week
        ),
        args=[account_id],
        id=job_id,
        replace_existing=True
    )
    
   # logger.info(f'已添加定时任务: account_id={account_id}, cron={cron_expr}')


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
       # logger.info('调度器已启动')


def stop_scheduler():
    """停止调度器"""
    if scheduler.running:
        scheduler.shutdown()
      #  logger.info('调度器已停止')

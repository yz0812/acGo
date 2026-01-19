"""通知模块 - 统一管理所有通知渠道"""
import time
import hmac
import hashlib
import base64
import json
import urllib.parse
import logging
from datetime import datetime
from typing import Optional

import requests

from .models import Config

logger = logging.getLogger(__name__)

# 通知配置键
NOTIFY_CONFIG_KEYS = [
    'webhook_enabled', 'webhook_url', 'webhook_method', 'webhook_headers', 'webhook_include_response',
    'telegram_enabled', 'telegram_bot_token', 'telegram_user_id', 'telegram_api_url',
    'wecom_enabled', 'wecom_webhook_key', 'wecom_api_url',
    'dingtalk_enabled', 'dingtalk_access_token', 'dingtalk_secret', 'dingtalk_api_url',
    'feishu_enabled', 'feishu_webhook_url', 'feishu_secret'
]


def _get_config(key: str) -> Optional[str]:
    """获取配置值"""
    config = Config.get_or_none(Config.key == key)
    return config.value if config else None


def _is_enabled(key: str) -> bool:
    """检查是否启用"""
    return _get_config(key) == 'true'


def _send_webhook(account_name: str, status: str, response_code: int = None,
                  message: str = '', response_body: str = None) -> bool:
    """发送通用 Webhook 通知"""
    try:
        if not _is_enabled('webhook_enabled'):
            return False

        url = _get_config('webhook_url')
        if not url:
            return False

        method = _get_config('webhook_method') or 'POST'

        headers = {}
        headers_str = _get_config('webhook_headers')
        if headers_str:
            try:
                headers = json.loads(headers_str)
                if not isinstance(headers, dict):
                    headers = {}
            except json.JSONDecodeError:
                headers = {}

        include_response = _is_enabled('webhook_include_response')

        payload = {
            'title': account_name,
            'account_name': account_name,
            'status': status,
            'response_code': response_code,
            'date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'message': message
        }

        if include_response and response_body:
            payload['response_body'] = response_body

        if method.upper() == 'POST':
            content_type = headers.get('Content-Type', 'application/json').lower()

            if 'multipart/form-data' in content_type:
                headers.pop('Content-Type', None)
                files = {k: (None, str(v)) for k, v in payload.items()}
                requests.post(url, files=files, headers=headers, timeout=10)
            elif 'application/x-www-form-urlencoded' in content_type:
                requests.post(url, data=payload, headers=headers, timeout=10)
            else:
                headers['Content-Type'] = 'application/json'
                requests.post(url, json=payload, headers=headers, timeout=10)
        else:
            requests.get(url, params=payload, headers=headers, timeout=10)

        return True

    except Exception as e:
        logger.error(f'Webhook 通知异常: {e}')
        return False


def _send_telegram(message: str) -> bool:
    """发送 Telegram 消息"""
    try:
        if not _is_enabled('telegram_enabled'):
            return False

        bot_token = _get_config('telegram_bot_token')
        user_id = _get_config('telegram_user_id')
        if not bot_token or not user_id:
            return False

        api_url = _get_config('telegram_api_url')
        base_url = api_url.rstrip('/') if api_url else 'https://api.telegram.org'
        url = f"{base_url}/bot{bot_token}/sendMessage"

        payload = {
            'chat_id': user_id,
            'text': message,
            'parse_mode': 'HTML'
        }

        requests.post(url, json=payload, timeout=10)
        return True

    except Exception as e:
        logger.error(f'Telegram 通知异常: {e}')
        return False


def _send_dingtalk(message: str) -> bool:
    """发送钉钉消息"""
    try:
        if not _is_enabled('dingtalk_enabled'):
            return False

        access_token = _get_config('dingtalk_access_token')
        if not access_token:
            return False

        api_url = _get_config('dingtalk_api_url')
        base_url = api_url.rstrip('/') if api_url else 'https://oapi.dingtalk.com'
        url = f"{base_url}/robot/send?access_token={access_token}"

        secret = _get_config('dingtalk_secret')
        if secret:
            timestamp = str(round(time.time() * 1000))
            string_to_sign = f'{timestamp}\n{secret}'
            hmac_code = hmac.new(secret.encode('utf-8'), string_to_sign.encode('utf-8'),
                                 digestmod=hashlib.sha256).digest()
            sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
            url += f"&timestamp={timestamp}&sign={sign}"

        payload = {
            'msgtype': 'text',
            'text': {'content': message}
        }

        requests.post(url, json=payload, timeout=10)
        return True

    except Exception as e:
        logger.error(f'钉钉通知异常: {e}')
        return False


def _send_wecom(message: str) -> bool:
    """发送企业微信消息"""
    try:
        if not _is_enabled('wecom_enabled'):
            return False

        webhook_key = _get_config('wecom_webhook_key')
        if not webhook_key:
            return False

        api_url = _get_config('wecom_api_url')
        base_url = api_url.rstrip('/') if api_url else 'https://qyapi.weixin.qq.com'
        url = f"{base_url}/cgi-bin/webhook/send?key={webhook_key}"

        payload = {
            'msgtype': 'text',
            'text': {'content': message}
        }

        requests.post(url, json=payload, timeout=10)
        return True

    except Exception as e:
        logger.error(f'企业微信通知异常: {e}')
        return False


def _send_feishu(message: str) -> bool:
    """发送飞书消息"""
    try:
        if not _is_enabled('feishu_enabled'):
            return False

        webhook_url = _get_config('feishu_webhook_url')
        if not webhook_url:
            return False

        payload = {
            'msg_type': 'text',
            'content': {'text': message}
        }

        secret = _get_config('feishu_secret')
        if secret:
            timestamp = str(int(time.time()))
            string_to_sign = f'{timestamp}\n{secret}'
            hmac_code = hmac.new(secret.encode('utf-8'), string_to_sign.encode('utf-8'),
                                 digestmod=hashlib.sha256).digest()
            sign = base64.b64encode(hmac_code).decode('utf-8')
            payload['timestamp'] = timestamp
            payload['sign'] = sign

        requests.post(webhook_url, json=payload, timeout=10)
        return True

    except Exception as e:
        logger.error(f'飞书通知异常: {e}')
        return False


def send_all_notifications(account_name: str, status: str, response_code: int = None,
                           message: str = '', response_body: str = None):
    """
    发送所有启用的通知

    Args:
        account_name: 账号名称
        status: 状态 (success/failed)
        response_code: 响应状态码
        message: 消息内容
        response_body: 响应内容（可选）
    """
    # 构造通用消息文本（用于 IM 通知）
    status_emoji = '✅' if status == 'success' else '❌'
    text_message = f"{status_emoji} {account_name}\n{message}"
    if response_code:
        text_message += f"\nHTTP: {response_code}"

    # 发送所有启用的通知渠道
    _send_webhook(account_name, status, response_code, message, response_body)
    _send_telegram(text_message)
    _send_dingtalk(text_message)
    _send_wecom(text_message)
    _send_feishu(text_message)


# 导出供 app.py 测试接口使用的单独发送函数
def send_telegram(bot_token: str, user_id: str, message: str, api_url: str = '') -> dict:
    """发送 Telegram 消息（供测试接口使用）"""
    base_url = api_url.rstrip('/') if api_url else 'https://api.telegram.org'
    url = f"{base_url}/bot{bot_token}/sendMessage"
    payload = {'chat_id': user_id, 'text': message, 'parse_mode': 'HTML'}
    response = requests.post(url, json=payload, timeout=10)
    return {'status_code': response.status_code, 'text': response.text}


def send_dingtalk(access_token: str, message: str, secret: str = '', api_url: str = '') -> dict:
    """发送钉钉消息（供测试接口使用）"""
    base_url = api_url.rstrip('/') if api_url else 'https://oapi.dingtalk.com'
    url = f"{base_url}/robot/send?access_token={access_token}"

    if secret:
        timestamp = str(round(time.time() * 1000))
        string_to_sign = f'{timestamp}\n{secret}'
        hmac_code = hmac.new(secret.encode('utf-8'), string_to_sign.encode('utf-8'),
                             digestmod=hashlib.sha256).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
        url += f"&timestamp={timestamp}&sign={sign}"

    payload = {'msgtype': 'text', 'text': {'content': message}}
    response = requests.post(url, json=payload, timeout=10)
    return {'status_code': response.status_code, 'text': response.text}


def send_wecom(webhook_key: str, message: str, api_url: str = '') -> dict:
    """发送企业微信消息（供测试接口使用）"""
    base_url = api_url.rstrip('/') if api_url else 'https://qyapi.weixin.qq.com'
    url = f"{base_url}/cgi-bin/webhook/send?key={webhook_key}"
    payload = {'msgtype': 'text', 'text': {'content': message}}
    response = requests.post(url, json=payload, timeout=10)
    return {'status_code': response.status_code, 'text': response.text}


def send_feishu(webhook_url: str, message: str, secret: str = '') -> dict:
    """发送飞书消息（供测试接口使用）"""
    payload = {'msg_type': 'text', 'content': {'text': message}}

    if secret:
        timestamp = str(int(time.time()))
        string_to_sign = f'{timestamp}\n{secret}'
        hmac_code = hmac.new(secret.encode('utf-8'), string_to_sign.encode('utf-8'),
                             digestmod=hashlib.sha256).digest()
        sign = base64.b64encode(hmac_code).decode('utf-8')
        payload['timestamp'] = timestamp
        payload['sign'] = sign

    response = requests.post(webhook_url, json=payload, timeout=10)
    return {'status_code': response.status_code, 'text': response.text}

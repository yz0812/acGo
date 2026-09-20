"""Bounded durable notification outbox, independent from check-in workers."""
import time
import hmac
import hashlib
import base64
import json
import urllib.parse
import logging
import threading
from datetime import datetime, timedelta
from . import http_client as requests
from . import settings
from .models import Config, Notification, db, connection

logger = logging.getLogger(__name__)

NOTIFY_CONFIG_KEYS = [
    'webhook_enabled', 'webhook_url', 'webhook_method', 'webhook_headers', 'webhook_include_response',
    'telegram_enabled', 'telegram_bot_token', 'telegram_user_id', 'telegram_api_url',
    'wecom_enabled', 'wecom_webhook_key', 'wecom_api_url',
    'dingtalk_enabled', 'dingtalk_access_token', 'dingtalk_secret', 'dingtalk_api_url',
    'feishu_enabled', 'feishu_webhook_url', 'feishu_secret'
]


def get_config():
    with connection():
        return dict(Config.select(Config.key, Config.value).where(Config.key.in_(NOTIFY_CONFIG_KEYS)).tuples())


def send_all_notifications(account_name, status, response_code=None, message='', response_body=None):
    """Persist one item per enabled channel; no network I/O on task workers."""
    with connection(), db.atomic('IMMEDIATE'):
        config = get_config()
        channels = [name for name in ('webhook', 'telegram', 'dingtalk', 'wecom', 'feishu')
                    if config.get(name + '_enabled') == 'true']
        if not channels:
            return True
        pending = Notification.select().where(Notification.state.in_(('queued', 'sending'))).count()
        if pending + len(channels) > settings.NOTIFY_QUEUE_LIMIT:
            logger.error('Notification queue full for %s', account_name)
            return False
        payload = json.dumps(dict(account_name=account_name[:100], status=status, response_code=response_code,
                                  message=(message or '')[:500], response_body=(response_body or '')[:5000]), ensure_ascii=False)
        Notification.insert_many([{'channel': name, 'payload': payload} for name in channels]).execute()
    outbox.wake.set()
    return True


def _deliver(channel, payload, config):
    text = ('✅' if payload['status'] == 'success' else '❌') + ' ' + payload['account_name'] + '\n' + payload['message']
    if payload.get('response_code'):
        text += f"\nHTTP: {payload['response_code']}"
    if channel == 'telegram':
        return send_telegram(config.get('telegram_bot_token', ''), config.get('telegram_user_id', ''), text, config.get('telegram_api_url', ''))
    if channel == 'dingtalk':
        return send_dingtalk(config.get('dingtalk_access_token', ''), text, config.get('dingtalk_secret', ''), config.get('dingtalk_api_url', ''))
    if channel == 'wecom':
        return send_wecom(config.get('wecom_webhook_key', ''), text, config.get('wecom_api_url', ''))
    if channel == 'feishu':
        return send_feishu(config.get('feishu_webhook_url', ''), text, config.get('feishu_secret', ''))
    headers = json.loads(config.get('webhook_headers') or '{}')
    if not isinstance(headers, dict):
        raise ValueError('Webhook headers must be an object')
    body = dict(payload, title=payload['account_name'], date=datetime.now().isoformat())
    if config.get('webhook_include_response') != 'true':
        body.pop('response_body', None)
    method = config.get('webhook_method', 'POST').upper()
    content_type = next((v.lower() for k, v in headers.items() if k.lower() == 'content-type'), 'application/json')
    kwargs = {'headers': headers}
    if method != 'POST':
        kwargs['params'] = body
    elif 'multipart/form-data' in content_type:
        kwargs['headers'] = {k:v for k,v in headers.items() if k.lower() != 'content-type'}
        kwargs['files'] = {k:(None,str(v)) for k,v in body.items()}
    elif 'application/x-www-form-urlencoded' in content_type:
        kwargs['data'] = body
    else:
        kwargs['json'] = body
    response = requests.request(method, config.get('webhook_url', ''), timeout=(5, 10), **kwargs)
    return {'status_code': response.status_code, 'text': response.text}


class NotificationWorker:
    def __init__(self):
        self.stop_event = threading.Event()
        self.wake = threading.Event()
        self.thread = None

    def start(self):
        if self.thread:
            return
        with connection():
            Notification.update(state='queued').where(Notification.state == 'sending').execute()
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._loop, name='acgo-notifications', daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.wake.set()
        if self.thread:
            self.thread.join()
            self.thread = None

    def _loop(self):
        while not self.stop_event.is_set():
            try:
                if self.run_one():
                    continue
            except Exception:
                logger.exception('Notification worker error')
            self.wake.wait(2)
            self.wake.clear()

    def run_one(self, now=None):
        with connection():
            item = Notification.select().where((Notification.state == 'queued') & (
                Notification.due_at <= (now or datetime.now()))).order_by(Notification.due_at, Notification.id).first()
            if item is None:
                return False
            config = get_config()
            if config.get(item.channel + '_enabled') != 'true':
                item.state = 'cancelled'
                item.save()
                return True
            item.state = 'sending'
            item.save()
        try:
            response = _deliver(item.channel, json.loads(item.payload), config)
            if not 200 <= response['status_code'] < 300:
                raise ValueError(f"HTTP {response['status_code']}: {response['text'][:200]}")
            item.state = 'sent'
            item.error = ''
        except Exception as exc:
            item.attempt += 1
            item.error = str(exc)[:500]
            item.state = 'failed' if item.attempt >= 3 else 'queued'
            item.due_at = datetime.now() + timedelta(seconds=15 * 2 ** (item.attempt - 1))
            logger.warning('Notification %s %s: %s', item.id, item.state, item.error)
        with connection():
            item.save()
        return True


outbox = NotificationWorker()

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

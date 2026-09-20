"""Flask 主程序"""
import os
import json
import time
import hmac
import hashlib
import base64
import urllib.parse
from datetime import datetime
from . import http_client as requests
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, send_from_directory, Response, stream_with_context
from peewee import fn
from .models import Account, CheckinLog, Config, Execution, Notification, ACTIVE_STATES, db, init_db, connection, save_configs
from .execution import submit, cancel_pending, execution_data
from .notifier import get_config as notification_config
from .script_runner import validate_task
from .auth import login_required, check_password
from .scheduler import (
    start_scheduler,
    stop_scheduler,
    add_job,
    remove_job,
    execute_checkin,
    parse_curl_command,
    parse_random_cron,
    cron_trigger
)
from .notifier import send_telegram, send_dingtalk, send_wecom, send_feishu, NOTIFY_CONFIG_KEYS

# 获取项目根目录（src 的父目录）
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 创建 Flask 应用，指定模板和静态文件路径
app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, 'templates'),
    static_folder=os.path.join(BASE_DIR, 'static')
)
app.secret_key = os.getenv('SECRET_KEY', 'a8f5f167f44f4964e6c998dee827110c5b92c0f8d1e3a7b2c4f6e8d0a2b4c6e8')

app.config.update(MAX_CONTENT_LENGTH=2 * 1024 * 1024)


def initialize_app():
    """Explicit startup; importing this module never starts jobs or migrates data."""
    init_db()


@app.route('/health')
def health():
    return jsonify({'status': 'ok'})


def pagination(default=50):
    try:
        page = int(request.args.get('page', 1))
        size = int(request.args.get('page_size', default))
    except (ValueError, TypeError):
        raise ValueError('分页参数必须为整数')
    if page < 1 or page > 100000 or not 1 <= size <= 100:
        raise ValueError('page 必须为 1～100000，page_size 必须为 1～100')
    return page, size


def account_data(account, detail=False):
    result = {name: getattr(account, name) for name in (
        'id', 'name', 'task_type', 'cron_expr', 'retry_count', 'retry_interval', 'enabled')}
    result['created_at'] = account.created_at.strftime('%Y-%m-%d %H:%M:%S')
    if detail:
        result.update(curl_command=account.curl_command, script_content=account.script_content)
    return result


@app.route('/favicon.ico')
def favicon():
    """返回 favicon"""
    return send_from_directory(app.static_folder, 'favicon.ico', mimetype='image/vnd.microsoft.icon')


@app.route('/login', methods=['GET', 'POST'])
def login():
    """登录页面"""
    if request.method == 'POST':
        password = request.form.get('password', '')
        
        if check_password(password):
            session['logged_in'] = True
            next_url = request.args.get('next', url_for('index'))
            return redirect(next_url)
        else:
            return render_template('login.html', error='密码错误')
    
    return render_template('login.html')


@app.route('/logout')
def logout():
    """登出"""
    session.pop('logged_in', None)
    return redirect(url_for('login'))


@app.route('/')
@login_required
def index():
    """主页面"""
    return render_template('index.html')


@app.route('/notify')
@login_required
def notify():
    """推送通知渠道页面"""
    return render_template('notify.html')


@app.route('/api/accounts', methods=['GET'])
@login_required
def get_accounts():
    try:
        page, size = pagination(20)
    except ValueError as exc:
        return jsonify(success=False, message=str(exc)), 400
    db.connect(reuse_if_open=True)
    try:
        fields = [Account.id, Account.name, Account.task_type, Account.cron_expr, Account.retry_count,
                  Account.retry_interval, Account.enabled, Account.created_at]
        accounts = list(Account.select(*fields).order_by(Account.created_at.desc(), Account.id.desc()).paginate(page, size))
        ids = [account.id for account in accounts]
        active = {item.account_id: execution_data(item) for item in Execution.select().where(
            Execution.account.in_(ids) & Execution.state.in_(ACTIVE_STATES))} if ids else {}
        data = [dict(account_data(account), execution=active.get(account.id)) for account in accounts]
        return jsonify(success=True, data=data, total=Account.select().count(), page=page, page_size=size)
    finally:
        db.close()


@app.route('/api/accounts/<int:account_id>', methods=['GET'])
@login_required
def get_account(account_id):
    db.connect(reuse_if_open=True)
    try:
        account = Account.get_by_id(account_id)
        return jsonify(success=True, data=account_data(account, detail=True))
    except Account.DoesNotExist:
        return jsonify(success=False, message='账号不存在'), 404
    finally:
        db.close()


@app.route('/api/accounts', methods=['POST'])
@login_required
def create_account():
    """创建账号"""
    data = request.get_json()
    if not isinstance(data, dict):
        return jsonify({'success': False, 'message': '请求内容必须是 JSON 对象'}), 400
    
    # 验证必填字段
    required_fields = ['name', 'cron_expr']
    for field in required_fields:
        if not data.get(field):
            return jsonify({'success': False, 'message': f'缺少必填字段: {field}'}), 400
    
    db.connect(reuse_if_open=True)
    
    try:
        # 验证 curl 命令
        try:
            task = validate_task(data)
            if task['task_type'] == 'curl':
                parse_curl_command(task['curl_command'])
        except ValueError as e:
            return jsonify({'success': False, 'message': str(e)}), 400

        # 验证 Cron 表达式（在创建账号前）
        if data.get('enabled', True):
            try:
                # 使用 parse_random_cron 验证（支持随机语法）
                cron_trigger(data['cron_expr'])
            except Exception as e:
                return jsonify({'success': False, 'message': f'Cron 表达式错误: {e}'}), 400

        # 创建账号
        account = Account.create(
            name=data['name'],
            **task,
            cron_expr=data['cron_expr'],
            retry_count=data.get('retry_count', 3),
            retry_interval=data.get('retry_interval', 60),
            enabled=data.get('enabled', True)
        )

        # 添加定时任务
        if account.enabled:
            try:
                add_job(account.id, account.cron_expr)
            except Exception as e:
                # 如果添加任务失败，删除已创建的账号
                account.delete_instance()
                return jsonify({'success': False, 'message': f'Cron 表达式错误: {e}'}), 400
        
        return jsonify({
            'success': True,
            'message': '账号创建成功',
            'data': {'id': account.id}
        })
        
    finally:
        db.close()


@app.route('/api/accounts/<int:account_id>', methods=['PUT'])
@login_required
def update_account(account_id):
    """更新账号"""
    data = request.get_json()
    if not isinstance(data, dict):
        return jsonify({'success': False, 'message': '请求内容必须是 JSON 对象'}), 400
    
    db.connect(reuse_if_open=True)
    
    try:
        account = Account.get_by_id(account_id)
        
        try:
            task = validate_task(data, account)
            if task['task_type'] == 'curl':
                parse_curl_command(task['curl_command'])
        except ValueError as e:
            return jsonify({'success': False, 'message': str(e)}), 400

        try:
            cron_trigger(data.get('cron_expr', account.cron_expr))
        except Exception as exc:
            return jsonify(success=False, message=f'Cron 表达式错误: {exc}'), 400

        # 更新字段
        if 'name' in data:
            account.name = data['name']
        for field, value in task.items():
            setattr(account, field, value)
        if 'cron_expr' in data:
            account.cron_expr = data['cron_expr']
        if 'retry_count' in data:
            account.retry_count = data['retry_count']
        if 'retry_interval' in data:
            account.retry_interval = data['retry_interval']
        if 'enabled' in data:
            account.enabled = data['enabled']
        
        with db.atomic('IMMEDIATE'):
            account.version = Account.get_by_id(account_id).version + 1
            account.save()
            cancel_pending(account_id)
        
        # 更新定时任务
        if account.enabled:
            try:
                add_job(account.id, account.cron_expr)
            except Exception as e:
                return jsonify({'success': False, 'message': f'Cron 表达式错误: {e}'}), 400
        else:
            remove_job(account.id)
        
        return jsonify({'success': True, 'message': '账号更新成功'})
        
    except Account.DoesNotExist:
        return jsonify({'success': False, 'message': '账号不存在'}), 404
    finally:
        db.close()


@app.route('/api/accounts/<int:account_id>', methods=['DELETE'])
@login_required
def delete_account(account_id):
    """删除账号"""
    db.connect(reuse_if_open=True)

    try:
        account = Account.get_by_id(account_id)

        # 移除定时任务
        remove_job(account_id)

        cancel_pending(account_id)

        # 删除账号（级联删除日志）
        account.delete_instance()

        return jsonify({'success': True, 'message': '账号删除成功'})

    except Account.DoesNotExist:
        return jsonify({'success': False, 'message': '账号不存在'}), 404
    finally:
        db.close()


@app.route('/api/accounts/<int:account_id>/preview', methods=['GET'])
@login_required
def preview_account_request(account_id):
    """预览账号的请求详情"""
    db.connect(reuse_if_open=True)

    try:
        account = Account.get_by_id(account_id)

        if account.task_type != 'curl':
            return jsonify({'success': True, 'data': {
                'task_type': account.task_type, 'script_content': account.script_content
            }})

        # 解析 curl 命令
        req_params = parse_curl_command(account.curl_command)

        return jsonify({
            'success': True,
            'data': {
                'method': req_params['method'],
                'url': req_params['url'],
                'headers': req_params['headers'],
                'cookies': req_params['cookies'],
                'data': req_params['data']
            }
        })

    except Account.DoesNotExist:
        return jsonify({'success': False, 'message': '账号不存在'}), 404
    except Exception as e:
        return jsonify({'success': False, 'message': f'解析失败: {str(e)}'}), 400
    finally:
        db.close()


@app.route('/api/accounts/export', methods=['GET'])
@login_required
def export_accounts():
    with connection():
        highest = Account.select(fn.MAX(Account.id)).scalar() or 0

    def generate():
        yield '{"success":true,"data":['
        last_id, first = 0, True
        while last_id < highest:
            with connection():
                accounts = list(Account.select().where((Account.id > last_id) & (Account.id <= highest)).order_by(Account.id).limit(20))
            if not accounts:
                break
            for account in accounts:
                data = account_data(account, detail=True)
                data.pop('id')
                data.pop('created_at')
                yield ('' if first else ',') + json.dumps(data, ensure_ascii=False)
                first = False
                last_id = account.id
        yield ']}'

    return Response(stream_with_context(generate()), mimetype='application/json')


@app.route('/api/accounts/import', methods=['POST'])
@login_required
def import_accounts():
    """导入账号"""
    data = request.get_json()

    if not data or 'accounts' not in data:
        return jsonify({'success': False, 'message': '缺少 accounts 参数'}), 400

    accounts = data['accounts']

    if isinstance(accounts, list) and len(accounts) > 100:
        return jsonify(success=False, message='每次最多导入 100 个账号，请分批导入'), 400
    if not isinstance(accounts, list):
        return jsonify({'success': False, 'message': 'accounts 必须是数组'}), 400

    db.connect(reuse_if_open=True)

    try:
        imported = 0
        failed = 0
        renamed = 0
        errors = []

        # 获取现有账号名称
        existing_names = set(acc.name for acc in Account.select(Account.name))

        for idx, acc_data in enumerate(accounts):
            try:
                # 验证必填字段
                required_fields = ['name']
                for field in required_fields:
                    if field not in acc_data or not acc_data[field]:
                        raise ValueError(f'缺少必填字段: {field}')

                # 验证 curl 命令
                try:
                    task = validate_task(acc_data)
                    if task['task_type'] == 'curl':
                        parse_curl_command(task['curl_command'])
                except ValueError as e:
                    raise ValueError(f'任务内容无效: {e}')

                # 处理重名账号（自动重命名）
                original_name = acc_data['name']
                account_name = original_name
                counter = 1

                while account_name in existing_names:
                    account_name = f"{original_name}_导入{counter}"
                    counter += 1
                    renamed += 1

                # 添加到已存在名称集合
                existing_names.add(account_name)

                # 创建账号
                account = Account.create(
                    name=account_name,
                    **task,
                    cron_expr=acc_data.get('cron_expr', '0 8 * * *'),
                    retry_count=acc_data.get('retry_count', 3),
                    retry_interval=acc_data.get('retry_interval', 60),
                    enabled=acc_data.get('enabled', True)
                )

                # 添加定时任务
                if account.enabled:
                    try:
                        add_job(account.id, account.cron_expr)
                    except Exception as e:
                        # 如果添加任务失败，删除账号并记录错误
                        account.delete_instance()
                        raise ValueError(f'Cron 表达式错误: {e}')

                imported += 1

            except Exception as e:
                failed += 1
                errors.append(f'第 {idx + 1} 个账号: {str(e)}')

        # 构造响应消息
        message = f'导入完成：成功 {imported} 个，失败 {failed} 个'
        if renamed > 0:
            message += f'，重命名 {renamed} 个'

        if errors:
            message += f'\n\n错误详情:\n' + '\n'.join(errors[:5])  # 最多显示 5 个错误
            if len(errors) > 5:
                message += f'\n... 还有 {len(errors) - 5} 个错误'

        return jsonify({
            'success': True,
            'message': message,
            'imported': imported,
            'failed': failed,
            'renamed': renamed
        })

    finally:
        db.close()



@app.route('/api/checkin/<int:account_id>', methods=['POST'])
@login_required
def manual_checkin(account_id):
    result = submit(account_id, manual=True)
    if result['status'] == 'missing':
        return jsonify(success=False, message=result['error']), 404
    if result['status'] == 'busy':
        return jsonify(success=False, message=result['error']), 429, {'Retry-After': '5'}
    return jsonify(success=True, message='该账号已有任务在执行或等待' if result['duplicate'] else '已加入执行队列',
                   data=result['execution']), 202


@app.route('/api/executions/<int:execution_id>', methods=['GET'])
@login_required
def get_execution(execution_id):
    db.connect(reuse_if_open=True)
    try:
        item = Execution.get_by_id(execution_id)
        return jsonify(success=True, data=execution_data(item))
    except Execution.DoesNotExist:
        return jsonify(success=False, message='执行记录不存在或已清理'), 404
    finally:
        db.close()


@app.route('/api/logs', methods=['GET'])
@login_required
def get_logs():
    """获取签到日志"""
    try:
        page, page_size = pagination()
    except ValueError as exc:
        return jsonify(success=False, message=str(exc)), 400
    status_filter = request.args.get('status', '')
    if status_filter not in ('', 'success', 'failed'):
        return jsonify(success=False, message='状态筛选无效'), 400

    db.connect(reuse_if_open=True)

    try:
        # 构建查询
        query = (CheckinLog
                 .select(CheckinLog.id, CheckinLog.account, CheckinLog.status, CheckinLog.response_code,
                         CheckinLog.exit_code, CheckinLog.request_method, CheckinLog.error_message,
                         fn.SUBSTR(CheckinLog.response_body, 1, 100).alias('response_preview'),
                         CheckinLog.executed_at, Account.id, Account.name)
                 .join(Account)
                 .order_by(CheckinLog.executed_at.desc(), CheckinLog.id.desc()))

        # 应用状态筛选
        if status_filter:
            query = query.where(CheckinLog.status == status_filter)

        # 分页查询
        logs = query.paginate(page, page_size)

        # 总数（根据筛选条件）
        if status_filter:
            total = CheckinLog.select().where(CheckinLog.status == status_filter).count()
        else:
            total = CheckinLog.select().count()

        data = [{
            'id': log.id,
            'account_name': log.account.name,
            'status': log.status,
            'response_code': log.response_code,
            'exit_code': log.exit_code,
            'task_type': log.request_method.lower() if log.request_method in ('PYTHON', 'JAVASCRIPT') else 'curl',
            'response_body': log.response_preview,
            'error_message': log.error_message,
            'executed_at': log.executed_at.strftime('%Y-%m-%d %H:%M:%S')
        } for log in logs]

        return jsonify({
            'success': True,
            'data': data,
            'total': total,
            'page': page,
            'page_size': page_size
        })

    finally:
        db.close()


@app.route('/api/logs/<int:log_id>/response', methods=['GET'])
@login_required
def log_response(log_id):
    db.connect(reuse_if_open=True)
    try:
        log = CheckinLog.select(CheckinLog.response_body).where(CheckinLog.id == log_id).get()
        return jsonify(success=True, data={'response_body': log.response_body})
    except CheckinLog.DoesNotExist:
        return jsonify(success=False, message='日志不存在或已清理'), 404
    finally:
        db.close()


@app.route('/api/logs/<int:log_id>/preview', methods=['GET'])
@login_required
def preview_log_request(log_id):
    """预览日志的请求详情"""
    db.connect(reuse_if_open=True)

    try:
        log = CheckinLog.get_by_id(log_id)

        if log.request_method in ('PYTHON', 'JAVASCRIPT'):
            return jsonify({'success': True, 'data': {
                'task_type': log.request_method.lower(), 'script_content': log.request_data
            }})

        # 解析 JSON 字符串
        headers = json.loads(log.request_headers) if log.request_headers else {}
        cookies = json.loads(log.request_cookies) if log.request_cookies else {}

        return jsonify({
            'success': True,
            'data': {
                'method': log.request_method,
                'url': log.request_url,
                'headers': headers,
                'cookies': cookies,
                'data': log.request_data
            }
        })

    except CheckinLog.DoesNotExist:
        return jsonify({'success': False, 'message': '日志不存在'}), 404
    except Exception as e:
        return jsonify({'success': False, 'message': f'获取详情失败: {str(e)}'}), 400
    finally:
        db.close()


@app.route('/api/stats', methods=['GET'])
@login_required
def get_stats():
    """获取统计数据"""
    db.connect(reuse_if_open=True)

    try:
        total_accounts, enabled_accounts = Account.select(fn.COUNT(Account.id), fn.COALESCE(fn.SUM(Account.enabled), 0)).tuples().get()
        total_logs, success_logs = CheckinLog.select(fn.COUNT(CheckinLog.id), fn.COALESCE(fn.SUM(CheckinLog.status == 'success'), 0)).tuples().get()

        return jsonify({
            'success': True,
            'data': {
                'total_accounts': total_accounts,
                'enabled_accounts': enabled_accounts,
                'total_logs': total_logs,
                'success_logs': success_logs
            }
        })

    finally:
        db.close()


@app.route('/api/logs/clear', methods=['DELETE'])
@login_required
def clear_logs():
    """清除签到日志"""
    days = request.args.get('days', type=int)

    db.connect(reuse_if_open=True)

    try:
        if days:
            # 清除N天前的日志
            from datetime import datetime, timedelta
            cutoff_date = datetime.now() - timedelta(days=days)

            deleted = CheckinLog.delete().where(
                CheckinLog.executed_at < cutoff_date
            ).execute()

            return jsonify({
                'success': True,
                'message': f'已清除 {deleted} 条 {days} 天前的记录'
            })
        else:
            # 清除全部日志
            deleted = CheckinLog.delete().execute()

            return jsonify({
                'success': True,
                'message': f'已清除全部 {deleted} 条记录'
            })

    finally:
        db.close()


@app.route('/api/webhook/config', methods=['GET'])
@login_required
def get_webhook_config():
    """获取 Webhook 配置"""
    db.connect(reuse_if_open=True)

    try:
        config_rows = {row.key: row for row in Config.select().where(Config.key.startswith('webhook_'))}
        # 获取配置
        enabled_config = config_rows.get('webhook_enabled')
        include_response_config = config_rows.get('webhook_include_response')
        url_config = config_rows.get('webhook_url')
        method_config = config_rows.get('webhook_method')
        headers_config = config_rows.get('webhook_headers')

        return jsonify({
            'success': True,
            'data': {
                'enabled': enabled_config.value == 'true' if enabled_config else False,
                'include_response': include_response_config.value == 'true' if include_response_config else False,
                'url': url_config.value if url_config else '',
                'method': method_config.value if method_config else 'POST',
                'headers': headers_config.value if headers_config else ''
            }
        })

    finally:
        db.close()


@app.route('/api/webhook/config', methods=['POST'])
@login_required
def save_webhook_config():
    """保存 Webhook 配置"""
    data = request.get_json()

    db.connect(reuse_if_open=True)

    try:
        # Webhook 配置项
        webhook_configs = {
            'webhook_enabled': 'true' if data.get('enabled') else 'false',
            'webhook_include_response': 'true' if data.get('include_response') else 'false',
            'webhook_url': data.get('url', ''),
            'webhook_method': data.get('method', 'POST'),
            'webhook_headers': data.get('headers', '')
        }

        save_configs(webhook_configs)

        return jsonify({
            'success': True,
            'message': 'Webhook 配置保存成功'
        })

    except Exception as e:
        return jsonify({'success': False, 'message': f'保存失败: {str(e)}'}), 500

    finally:
        db.close()


@app.route('/api/webhook/test', methods=['POST'])
@login_required
def test_webhook():
    """测试 Webhook"""
    db.connect(reuse_if_open=True)

    try:
        config_rows = {row.key: row for row in Config.select().where(Config.key.startswith('webhook_'))}
        # 获取 Webhook 配置
        enabled_config = config_rows.get('webhook_enabled')
        url_config = config_rows.get('webhook_url')
        method_config = config_rows.get('webhook_method')
        headers_config = config_rows.get('webhook_headers')
        include_response_config = config_rows.get('webhook_include_response')

        # 验证配置
        if not url_config or not url_config.value:
            return jsonify({
                'success': False,
                'message': '请先配置 Webhook URL'
            }), 400

        # 解析配置
        method = method_config.value if method_config else 'POST'
        headers = {}
        if headers_config and headers_config.value:
            try:
                headers = json.loads(headers_config.value)
            except json.JSONDecodeError as e:
                return jsonify({
                    'success': False,
                    'message': f'自定义请求头 JSON 格式错误: {str(e)}'
                }), 400

        include_response = include_response_config and include_response_config.value == 'true'

        # 构造测试数据
        payload = {
            'title': '测试账号',
            'account_name': '测试账号',
            'status': 'success',
            'response_code': 200,
            'date': datetime.now().strftime('%Y-%m-%dT%H:%M:%SZ'),
            'message': '这是一条测试通知'
        }

        if include_response:
            payload['response_body'] = '{"test": true, "message": "Webhook 测试成功"}'

        # 发送请求
        db.close()
        from . import http_client as requests

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

        # 检查响应
        if 200 <= response.status_code < 300:
            return jsonify({
                'success': True,
                'message': f'测试通知发送成功！\n\nHTTP {response.status_code}\n响应内容: {response.text[:200]}'
            })
        else:
            return jsonify({
                'success': False,
                'message': f'Webhook 返回错误状态码: HTTP {response.status_code}\n响应内容: {response.text[:200]}'
            }), 400

    except requests.exceptions.Timeout:
        return jsonify({
            'success': False,
            'message': '请求超时（10秒），请检查 Webhook URL 是否可访问'
        }), 500

    except requests.exceptions.ConnectionError as e:
        return jsonify({
            'success': False,
            'message': f'连接失败，请检查 Webhook URL 是否正确: {str(e)}'
        }), 500

    except requests.exceptions.RequestException as e:
        return jsonify({
            'success': False,
            'message': f'请求异常: {str(e)}'
        }), 500

    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'测试失败: {str(e)}'
        }), 500

    finally:
        db.close()


@app.route('/api/system/config', methods=['GET'])
@login_required
def get_system_config():
    """获取系统配置"""
    db.connect(reuse_if_open=True)

    try:
        # 获取配置
        auto_clean_config = Config.get_or_none(Config.key == 'auto_clean_logs')
        max_logs_config = Config.get_or_none(Config.key == 'max_logs_count')

        return jsonify({
            'success': True,
            'data': {
                'auto_clean_logs': auto_clean_config.value == 'true' if auto_clean_config else False,
                'max_logs_count': int(max_logs_config.value) if max_logs_config else 500
            }
        })

    finally:
        db.close()


def get_webhook_config_dict():
    """获取 Webhook 配置字典（内部使用）"""
    config_rows = {row.key: row for row in Config.select().where(Config.key.startswith('webhook_'))}
    enabled_config = config_rows.get('webhook_enabled')
    url_config = config_rows.get('webhook_url')
    method_config = config_rows.get('webhook_method')
    headers_config = config_rows.get('webhook_headers')
    include_response_config = config_rows.get('webhook_include_response')
    
    return {
        'enabled': enabled_config and enabled_config.value == 'true',
        'url': url_config.value if url_config else '',
        'method': method_config.value if method_config else 'POST',
        'headers': headers_config.value if headers_config else '',
        'include_response': include_response_config and include_response_config.value == 'true'
    }


@app.route('/api/system/config', methods=['POST'])
@login_required
def save_system_config():
    data = request.get_json()
    if not isinstance(data, dict):
        return jsonify(success=False, message='请求内容必须是 JSON 对象'), 400
    values = {}
    if 'auto_clean_logs' in data:
        values['auto_clean_logs'] = 'true' if data['auto_clean_logs'] else 'false'
    if 'max_logs_count' in data:
        if type(data['max_logs_count']) is not int or not 100 <= data['max_logs_count'] <= 100000:
            return jsonify(success=False, message='日志保留条数必须是 100～100000 的整数'), 400
        values['max_logs_count'] = str(data['max_logs_count'])
    save_configs(values)
    return jsonify(success=True, message='系统配置保存成功')


@app.route('/api/system/password', methods=['POST'])
@login_required
def change_password():
    """修改管理员密码"""
    data = request.get_json()

    # 验证必填字段
    if not data.get('old_password') or not data.get('new_password'):
        return jsonify({'success': False, 'message': '缺少必填字段'}), 400

    db.connect(reuse_if_open=True)

    try:
        from datetime import datetime

        # 验证旧密码
        if not check_password(data['old_password']):
            return jsonify({'success': False, 'message': '旧密码错误'}), 400

        # 验证新密码长度
        if len(data['new_password']) < 6:
            return jsonify({'success': False, 'message': '新密码长度不能少于 6 位'}), 400

        # 更新密码
        Config.update(
            value=data['new_password'],
            updated_at=datetime.now()
        ).where(Config.key == 'admin_password').execute()

        return jsonify({
            'success': True,
            'message': '密码修改成功，请重新登录'
        })

    finally:
        db.close()


# ==================== 推送通知渠道 API ====================

# 通知渠道配置键名列表

@app.route('/api/notify/config', methods=['GET'])
@login_required
def get_notify_config():
    """获取通知渠道配置"""
    db.connect(reuse_if_open=True)

    try:
        result = {}
        values = notification_config()
        for key in NOTIFY_CONFIG_KEYS:
            config = Config(key=key, value=values[key]) if key in values else None
            if config:
                # 布尔值转换
                if key.endswith('_enabled'):
                    result[key] = config.value == 'true'
                else:
                    result[key] = config.value
            else:
                result[key] = False if key.endswith('_enabled') else ''

        return jsonify({'success': True, 'data': result})

    finally:
        db.close()


@app.route('/api/notify/config', methods=['POST'])
@login_required
def save_notify_config():
    """保存通知渠道配置"""
    data = request.get_json()

    if not data:
        return jsonify({'success': False, 'message': '未收到数据'}), 400

    db.connect(reuse_if_open=True)

    try:
        values = {}
        for key in NOTIFY_CONFIG_KEYS:
            if key in data:
                value = data[key]
                values[key] = ('true' if value else 'false') if isinstance(value, bool) else str(value or '')
        save_configs(values)
        saved_count = len(values)

        return jsonify({'success': True, 'message': f'通知渠道配置保存成功，共 {saved_count} 项'})

    except Exception as e:
        return jsonify({'success': False, 'message': f'保存失败: {str(e)}'}), 500

    finally:
        db.close()


def _get_notify_config(prefix: str) -> dict:
    """获取指定前缀的通知配置（内部函数）"""
    result = {}
    values = notification_config()
    for key in NOTIFY_CONFIG_KEYS:
        if key.startswith(prefix):
            config = Config(key=key, value=values[key]) if key in values else None
            short_key = key[len(prefix) + 1:]  # 移除前缀和下划线
            if config:
                result[short_key] = config.value == 'true' if key.endswith('_enabled') else config.value
            else:
                result[short_key] = False if key.endswith('_enabled') else ''
    return result


@app.route('/api/notify/test/telegram', methods=['POST'])
@login_required
def test_telegram():
    """测试 Telegram 通知"""
    db.connect(reuse_if_open=True)

    try:
        cfg = _get_notify_config('telegram')
        db.close()

        if not cfg.get('bot_token') or not cfg.get('user_id'):
            return jsonify({'success': False, 'message': '请先配置 Bot Token 和 User ID'}), 400

        message = f"🔔 ACGO 签到系统测试通知\n\n时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n状态: 测试成功"

        result = send_telegram(
            cfg['bot_token'],
            cfg['user_id'],
            message,
            cfg.get('api_url', '')
        )

        if 200 <= result['status_code'] < 300:
            return jsonify({'success': True, 'message': 'Telegram 测试通知发送成功'})
        else:
            return jsonify({'success': False, 'message': f"发送失败: HTTP {result['status_code']}\n{result['text'][:200]}"}), 400

    except Exception as e:
        return jsonify({'success': False, 'message': f'发送失败: {str(e)}'}), 500

    finally:
        db.close()


@app.route('/api/notify/test/wecom', methods=['POST'])
@login_required
def test_wecom():
    """测试企业微信通知"""
    db.connect(reuse_if_open=True)

    try:
        cfg = _get_notify_config('wecom')
        db.close()

        if not cfg.get('webhook_key'):
            return jsonify({'success': False, 'message': '请先配置 Webhook Key'}), 400

        message = f"🔔 ACGO 签到系统测试通知\n\n时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n状态: 测试成功"

        result = send_wecom(
            cfg['webhook_key'],
            message,
            cfg.get('api_url', '')
        )

        if 200 <= result['status_code'] < 300:
            return jsonify({'success': True, 'message': '企业微信测试通知发送成功'})
        else:
            return jsonify({'success': False, 'message': f"发送失败: HTTP {result['status_code']}\n{result['text'][:200]}"}), 400

    except Exception as e:
        return jsonify({'success': False, 'message': f'发送失败: {str(e)}'}), 500

    finally:
        db.close()


@app.route('/api/notify/test/dingtalk', methods=['POST'])
@login_required
def test_dingtalk():
    """测试钉钉通知"""
    db.connect(reuse_if_open=True)

    try:
        cfg = _get_notify_config('dingtalk')
        db.close()

        if not cfg.get('access_token'):
            return jsonify({'success': False, 'message': '请先配置 Access Token'}), 400

        message = f"🔔 ACGO 签到系统测试通知\n\n时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n状态: 测试成功"

        result = send_dingtalk(
            cfg['access_token'],
            message,
            cfg.get('secret', ''),
            cfg.get('api_url', '')
        )

        if 200 <= result['status_code'] < 300:
            return jsonify({'success': True, 'message': '钉钉测试通知发送成功'})
        else:
            return jsonify({'success': False, 'message': f"发送失败: HTTP {result['status_code']}\n{result['text'][:200]}"}), 400

    except Exception as e:
        return jsonify({'success': False, 'message': f'发送失败: {str(e)}'}), 500

    finally:
        db.close()


@app.route('/api/notify/test/feishu', methods=['POST'])
@login_required
def test_feishu():
    """测试飞书通知"""
    db.connect(reuse_if_open=True)

    try:
        cfg = _get_notify_config('feishu')
        db.close()

        if not cfg.get('webhook_url'):
            return jsonify({'success': False, 'message': '请先配置 Webhook 地址'}), 400

        message = f"🔔 ACGO 签到系统测试通知\n\n时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n状态: 测试成功"

        result = send_feishu(
            cfg['webhook_url'],
            message,
            cfg.get('secret', '')
        )

        if 200 <= result['status_code'] < 300:
            return jsonify({'success': True, 'message': '飞书测试通知发送成功'})
        else:
            return jsonify({'success': False, 'message': f"发送失败: HTTP {result['status_code']}\n{result['text'][:200]}"}), 400

    except Exception as e:
        return jsonify({'success': False, 'message': f'发送失败: {str(e)}'}), 500

    finally:
        db.close()


@app.teardown_appcontext
def close_db(error):
    """请求结束时关闭数据库连接"""
    if not db.is_closed():
        db.close()


if __name__ == '__main__':
    raise SystemExit('请从项目根目录运行 python run.py')

"""Flask 主程序"""
import os
import json
from datetime import datetime
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from .models import Account, CheckinLog, Config, db, init_db
from .auth import login_required, check_password
from .scheduler import (
    start_scheduler,
    stop_scheduler,
    add_job,
    remove_job,
    execute_checkin,
    parse_curl_command,
    parse_random_cron
)

# 获取项目根目录（src 的父目录）
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 创建 Flask 应用，指定模板和静态文件路径
app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, 'templates'),
    static_folder=os.path.join(BASE_DIR, 'static')
)
app.secret_key = os.getenv('SECRET_KEY', 'a8f5f167f44f4964e6c998dee827110c5b92c0f8d1e3a7b2c4f6e8d0a2b4c6e8')

# 初始化数据库
init_db()

# 启动调度器
start_scheduler()


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


@app.route('/api/accounts', methods=['GET'])
@login_required
def get_accounts():
    """获取账号列表"""
    db.connect(reuse_if_open=True)
    
    try:
        accounts = Account.select().order_by(Account.created_at.desc())
        
        data = [{
            'id': acc.id,
            'name': acc.name,
            'curl_command': acc.curl_command,
            'cron_expr': acc.cron_expr,
            'retry_count': acc.retry_count,
            'retry_interval': acc.retry_interval,
            'enabled': acc.enabled,
            'created_at': acc.created_at.strftime('%Y-%m-%d %H:%M:%S')
        } for acc in accounts]
        
        return jsonify({'success': True, 'data': data})
        
    finally:
        db.close()


@app.route('/api/accounts', methods=['POST'])
@login_required
def create_account():
    """创建账号"""
    data = request.get_json()
    
    # 验证必填字段
    required_fields = ['name', 'curl_command', 'cron_expr']
    for field in required_fields:
        if not data.get(field):
            return jsonify({'success': False, 'message': f'缺少必填字段: {field}'}), 400
    
    db.connect(reuse_if_open=True)
    
    try:
        # 验证 curl 命令
        try:
            parse_curl_command(data['curl_command'])
        except ValueError as e:
            return jsonify({'success': False, 'message': str(e)}), 400

        # 验证 Cron 表达式（在创建账号前）
        if data.get('enabled', True):
            try:
                # 使用 parse_random_cron 验证（支持随机语法）
                parse_random_cron(data['cron_expr'])
            except Exception as e:
                return jsonify({'success': False, 'message': f'Cron 表达式错误: {e}'}), 400

        # 创建账号
        account = Account.create(
            name=data['name'],
            curl_command=data['curl_command'],
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
    
    db.connect(reuse_if_open=True)
    
    try:
        account = Account.get_by_id(account_id)
        
        # 验证 curl 命令（如果有更新）
        if 'curl_command' in data:
            try:
                parse_curl_command(data['curl_command'])
            except ValueError as e:
                return jsonify({'success': False, 'message': str(e)}), 400
        
        # 更新字段
        if 'name' in data:
            account.name = data['name']
        if 'curl_command' in data:
            account.curl_command = data['curl_command']
        if 'cron_expr' in data:
            account.cron_expr = data['cron_expr']
        if 'retry_count' in data:
            account.retry_count = data['retry_count']
        if 'retry_interval' in data:
            account.retry_interval = data['retry_interval']
        if 'enabled' in data:
            account.enabled = data['enabled']
        
        account.save()
        
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
    """导出所有账号"""
    db.connect(reuse_if_open=True)

    try:
        accounts = Account.select()

        # 转换为可导出的格式（排除 id 和 created_at）
        export_data = []
        for acc in accounts:
            export_data.append({
                'name': acc.name,
                'curl_command': acc.curl_command,
                'cron_expr': acc.cron_expr,
                'retry_count': acc.retry_count,
                'retry_interval': acc.retry_interval,
                'enabled': acc.enabled
            })

        return jsonify({
            'success': True,
            'data': export_data
        })

    finally:
        db.close()


@app.route('/api/accounts/import', methods=['POST'])
@login_required
def import_accounts():
    """导入账号"""
    data = request.get_json()

    if not data or 'accounts' not in data:
        return jsonify({'success': False, 'message': '缺少 accounts 参数'}), 400

    accounts = data['accounts']

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
                required_fields = ['name', 'curl_command']
                for field in required_fields:
                    if field not in acc_data or not acc_data[field]:
                        raise ValueError(f'缺少必填字段: {field}')

                # 验证 curl 命令
                try:
                    parse_curl_command(acc_data['curl_command'])
                except ValueError as e:
                    raise ValueError(f'curl 命令无效: {e}')

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
                    curl_command=acc_data['curl_command'],
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
    """手动立即签到"""
    db.connect(reuse_if_open=True)

    try:
        account = Account.get_by_id(account_id)

        # 执行签到（手动签到时跳过禁用状态检查）
        result = execute_checkin(account_id, skip_enabled_check=True)

        return jsonify({
            'success': result['status'] == 'success',
            'message': '签到成功' if result['status'] == 'success' else '签到失败',
            'data': result
        })

    except Account.DoesNotExist:
        return jsonify({'success': False, 'message': '账号不存在'}), 404
    finally:
        db.close()


@app.route('/api/logs', methods=['GET'])
@login_required
def get_logs():
    """获取签到日志"""
    page = int(request.args.get('page', 1))
    page_size = int(request.args.get('page_size', 50))
    status_filter = request.args.get('status', '')  # 状态筛选：'' (全部) / 'success' / 'failed'

    db.connect(reuse_if_open=True)

    try:
        # 构建查询
        query = (CheckinLog
                 .select(CheckinLog, Account)
                 .join(Account)
                 .order_by(CheckinLog.executed_at.desc()))

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
            'response_body': log.response_body,  # 返回完整内容
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


@app.route('/api/logs/<int:log_id>/preview', methods=['GET'])
@login_required
def preview_log_request(log_id):
    """预览日志的请求详情"""
    db.connect(reuse_if_open=True)

    try:
        log = CheckinLog.get_by_id(log_id)

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
        total_accounts = Account.select().count()
        enabled_accounts = Account.select().where(Account.enabled == True).count()
        total_logs = CheckinLog.select().count()
        success_logs = CheckinLog.select().where(CheckinLog.status == 'success').count()

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
        # 获取配置
        enabled_config = Config.get_or_none(Config.key == 'webhook_enabled')
        include_response_config = Config.get_or_none(Config.key == 'webhook_include_response')
        url_config = Config.get_or_none(Config.key == 'webhook_url')
        method_config = Config.get_or_none(Config.key == 'webhook_method')
        headers_config = Config.get_or_none(Config.key == 'webhook_headers')

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
        from datetime import datetime

        # 保存或更新配置
        Config.update(
            value='true' if data.get('enabled') else 'false',
            updated_at=datetime.now()
        ).where(Config.key == 'webhook_enabled').execute()

        Config.update(
            value='true' if data.get('include_response') else 'false',
            updated_at=datetime.now()
        ).where(Config.key == 'webhook_include_response').execute()

        Config.update(
            value=data.get('url', ''),
            updated_at=datetime.now()
        ).where(Config.key == 'webhook_url').execute()

        Config.update(
            value=data.get('method', 'POST'),
            updated_at=datetime.now()
        ).where(Config.key == 'webhook_method').execute()

        Config.update(
            value=data.get('headers', ''),
            updated_at=datetime.now()
        ).where(Config.key == 'webhook_headers').execute()

        return jsonify({
            'success': True,
            'message': 'Webhook 配置保存成功'
        })

    finally:
        db.close()


@app.route('/api/webhook/test', methods=['POST'])
@login_required
def test_webhook():
    """测试 Webhook"""
    db.connect(reuse_if_open=True)

    try:
        # 获取 Webhook 配置
        enabled_config = Config.get_or_none(Config.key == 'webhook_enabled')
        url_config = Config.get_or_none(Config.key == 'webhook_url')
        method_config = Config.get_or_none(Config.key == 'webhook_method')
        headers_config = Config.get_or_none(Config.key == 'webhook_headers')
        include_response_config = Config.get_or_none(Config.key == 'webhook_include_response')

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
            'date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'message': '这是一条测试通知'
        }

        if include_response:
            payload['response_body'] = '{"test": true, "message": "Webhook 测试成功"}'

        # 发送请求
        import requests

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
    enabled_config = Config.get_or_none(Config.key == 'webhook_enabled')
    url_config = Config.get_or_none(Config.key == 'webhook_url')
    method_config = Config.get_or_none(Config.key == 'webhook_method')
    headers_config = Config.get_or_none(Config.key == 'webhook_headers')
    include_response_config = Config.get_or_none(Config.key == 'webhook_include_response')
    
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
    """保存系统配置"""
    data = request.get_json()

    db.connect(reuse_if_open=True)

    try:
        from datetime import datetime

        # 保存自动清理配置
        if 'auto_clean_logs' in data:
            Config.update(
                value='true' if data['auto_clean_logs'] else 'false',
                updated_at=datetime.now()
            ).where(Config.key == 'auto_clean_logs').execute()

        # 保存最大记录数配置
        if 'max_logs_count' in data:
            max_logs = int(data['max_logs_count'])
            if max_logs < 100:
                return jsonify({'success': False, 'message': '最大记录数不能小于 100'}), 400
            
            Config.update(
                value=str(max_logs),
                updated_at=datetime.now()
            ).where(Config.key == 'max_logs_count').execute()

        return jsonify({
            'success': True,
            'message': '系统配置保存成功'
        })

    finally:
        db.close()


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


@app.teardown_appcontext
def close_db(error):
    """请求结束时关闭数据库连接"""
    if not db.is_closed():
        db.close()


if __name__ == '__main__':
    try:
        app.run(host='0.0.0.0', port=5000, debug=False)
    finally:
        stop_scheduler()

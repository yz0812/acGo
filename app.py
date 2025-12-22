"""Flask 主程序"""
import os
import json
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from models import Account, CheckinLog, Config, db, init_db
from auth import login_required, check_password
from scheduler import (
    start_scheduler,
    stop_scheduler,
    add_job,
    remove_job,
    execute_checkin,
    parse_curl_command
)

app = Flask(__name__)
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
                parts = data['cron_expr'].split()
                if len(parts) != 5:
                    raise ValueError('Cron 表达式格式错误，应为 5 个字段（分 时 日 月 周）')
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
        
        # 执行签到
        result = execute_checkin(account_id)
        
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
    
    db.connect(reuse_if_open=True)
    
    try:
        # 分页查询
        logs = (CheckinLog
                .select(CheckinLog, Account)
                .join(Account)
                .order_by(CheckinLog.executed_at.desc())
                .paginate(page, page_size))
        
        # 总数
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
        Config.replace(
            key='webhook_enabled',
            value='true' if data.get('enabled') else 'false',
            updated_at=datetime.now()
        ).execute()

        Config.replace(
            key='webhook_include_response',
            value='true' if data.get('include_response') else 'false',
            updated_at=datetime.now()
        ).execute()

        Config.replace(
            key='webhook_url',
            value=data.get('url', ''),
            updated_at=datetime.now()
        ).execute()

        Config.replace(
            key='webhook_method',
            value=data.get('method', 'POST'),
            updated_at=datetime.now()
        ).execute()

        Config.replace(
            key='webhook_headers',
            value=data.get('headers', ''),
            updated_at=datetime.now()
        ).execute()

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
    from scheduler import send_webhook_notification

    db.connect(reuse_if_open=True)

    try:
        # 发送测试通知
        send_webhook_notification(
            account_name='测试账号',
            status='success',
            response_code=200,
            message='这是一条测试通知',
            response_body='{"test": true, "message": "Webhook 测试成功"}'
        )

        return jsonify({
            'success': True,
            'message': '测试通知已发送'
        })

    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'测试失败: {str(e)}'
        }), 500

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

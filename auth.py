"""认证模块"""
from functools import wraps
from flask import session, redirect, url_for, request
from models import Config, db


def login_required(f):
    """登录验证装饰器"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function


def get_admin_password() -> str:
    """从数据库获取管理员密码"""
    db.connect(reuse_if_open=True)
    try:
        config = Config.get_or_none(Config.key == 'admin_password')
        return config.value if config else 'acgo123321'
    finally:
        db.close()


def check_password(password: str) -> bool:
    """验证密码"""
    return password == get_admin_password()

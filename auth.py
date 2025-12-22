"""认证模块"""
import os
from functools import wraps
from flask import session, redirect, url_for, request
from dotenv import load_dotenv

load_dotenv()

# 从环境变量读取管理员密码
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'acgo123321')


def login_required(f):
    """登录验证装饰器"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function


def check_password(password: str) -> bool:
    """验证密码"""
    return password == ADMIN_PASSWORD

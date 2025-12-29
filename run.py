"""acGo 签到管理系统 - 启动入口"""
import sys
import os

# 将项目根目录添加到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.app import app
from src.scheduler import stop_scheduler

if __name__ == '__main__':
    try:
        app.run(host='0.0.0.0', port=5000, debug=False)
    finally:
        stop_scheduler()

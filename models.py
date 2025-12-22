"""数据库模型定义"""
import os
from datetime import datetime
from peewee import (
    SqliteDatabase,
    Model,
    AutoField,
    CharField,
    TextField,
    IntegerField,
    BooleanField,
    DateTimeField,
    ForeignKeyField,
)

# 确保数据目录存在
DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
os.makedirs(DATA_DIR, exist_ok=True)

# 数据库实例
db = SqliteDatabase(os.path.join(DATA_DIR, 'acgo.db'))


class BaseModel(Model):
    """基础模型"""
    class Meta:
        database = db


class Account(BaseModel):
    """账号表"""
    id = AutoField(primary_key=True)
    name = CharField(max_length=100, verbose_name='账号名称')
    curl_command = TextField(verbose_name='Curl命令')
    cron_expr = CharField(max_length=50, default='0 8 * * *', verbose_name='Cron表达式')
    retry_count = IntegerField(default=3, verbose_name='重试次数')
    retry_interval = IntegerField(default=60, verbose_name='重试间隔(秒)')
    enabled = BooleanField(default=True, verbose_name='是否启用')
    created_at = DateTimeField(default=datetime.now, verbose_name='创建时间')

    class Meta:
        table_name = 'accounts'


class CheckinLog(BaseModel):
    """签到日志表"""
    id = AutoField(primary_key=True)
    account = ForeignKeyField(Account, backref='logs', on_delete='CASCADE')
    status = CharField(max_length=20, verbose_name='状态')  # success, failed
    response_code = IntegerField(null=True, verbose_name='响应状态码')
    response_body = TextField(null=True, verbose_name='响应内容')
    error_message = TextField(null=True, verbose_name='错误信息')
    executed_at = DateTimeField(default=datetime.now, verbose_name='执行时间')

    class Meta:
        table_name = 'checkin_logs'


class Config(BaseModel):
    """系统配置表"""
    id = AutoField(primary_key=True)
    key = CharField(max_length=50, unique=True, verbose_name='配置键')
    value = TextField(verbose_name='配置值')
    updated_at = DateTimeField(default=datetime.now, verbose_name='更新时间')

    class Meta:
        table_name = 'configs'


def init_db():
    """初始化数据库"""
    db.connect(reuse_if_open=True)
    db.create_tables([Account, CheckinLog, Config], safe=True)  # safe=True 表示表已存在时不报错
    db.close()
    print('数据库检查完成')


if __name__ == '__main__':
    init_db()
    print('数据库初始化完成')

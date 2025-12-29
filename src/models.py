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

# 确保数据目录存在（指向项目根目录的 data/）
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')
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
    # 请求参数字段（用于记录实际发送的请求）
    request_method = CharField(max_length=10, null=True, verbose_name='请求方式')
    request_url = TextField(null=True, verbose_name='请求地址')
    request_headers = TextField(null=True, verbose_name='请求头')
    request_cookies = TextField(null=True, verbose_name='请求Cookies')
    request_data = TextField(null=True, verbose_name='请求体')

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


def init_config():
    """初始化系统配置（从环境变量读取默认值）"""
    import os
    from dotenv import load_dotenv
    
    load_dotenv()
    
    db.connect(reuse_if_open=True)
    
    try:
        # 配置项及其默认值
        default_configs = {
            'admin_password': os.getenv('ADMIN_PASSWORD', 'acgo123321'),
            'auto_clean_logs': os.getenv('AUTO_CLEAN_LOGS', 'false'),
            'max_logs_count': os.getenv('MAX_LOGS_COUNT', '500')
        }
        
        # 检查并初始化配置
        for key, default_value in default_configs.items():
            existing = Config.get_or_none(Config.key == key)
            if not existing:
                Config.create(
                    key=key,
                    value=default_value,
                    updated_at=datetime.now()
                )
                print(f'初始化配置: {key} = {default_value}')
    
    finally:
        db.close()


def migrate_database():
    """数据库迁移：添加缺失的字段"""
    db.connect(reuse_if_open=True)

    try:
        # 检查 checkin_logs 表是否存在新字段
        cursor = db.execute_sql("PRAGMA table_info(checkin_logs)")
        columns = [row[1] for row in cursor.fetchall()]

        # 需要添加的新字段
        new_fields = {
            'request_method': 'VARCHAR(10)',
            'request_url': 'TEXT',
            'request_headers': 'TEXT',
            'request_cookies': 'TEXT',
            'request_data': 'TEXT'
        }

        # 检查并添加缺失的字段
        for field_name, field_type in new_fields.items():
            if field_name not in columns:
                print(f'添加字段: {field_name}')
                db.execute_sql(f'ALTER TABLE checkin_logs ADD COLUMN {field_name} {field_type}')

        print('数据库迁移完成')

    except Exception as e:
        print(f'数据库迁移失败: {e}')

    finally:
        db.close()


def init_db():
    """初始化数据库"""
    db.connect(reuse_if_open=True)
    db.create_tables([Account, CheckinLog, Config], safe=True)  # safe=True 表示表已存在时不报错
    db.close()
    print('数据库检查完成')

    # 执行数据库迁移
    migrate_database()

    # 初始化配置
    init_config()


if __name__ == '__main__':
    init_db()
    print('数据库初始化完成')

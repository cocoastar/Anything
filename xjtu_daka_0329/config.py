# xjtu_daka 项目配置：路径与运行参数
import os

# 项目根目录（本文件所在目录）
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 运行日志目录（每学号一个 .txt）
LOGS_DIR = os.path.join(BASE_DIR, 'logs')

# 打卡脚本目录（dk6.py 所在目录，默认与 xjtu_daka 同级的 jtcdk）
JTCDK_DIR = os.environ.get('JTCDK_DIR') or os.path.abspath(os.path.join(BASE_DIR, '..', 'jtcdk'))

# 授权学号列表文件（每行一个学号后四位；不存在则不做校验）
A_TXT_PATH = os.environ.get('A_TXT_PATH') or os.path.join(BASE_DIR, 'a.txt')

# ---------- 数据库 ----------
# 开发默认 SQLite；生产可用环境变量 DATABASE_URL=mysql+pymysql://user:pass@host/db
DATABASE_URL = os.environ.get('DATABASE_URL') or ('sqlite:///' + os.path.join(BASE_DIR, 'instance', 'xjtu.db'))

# ---------- 上传与文件 ----------
# 上传根目录（本地存储）
UPLOAD_DIR = os.environ.get('UPLOAD_DIR') or os.path.join(BASE_DIR, 'uploads')
# 单文件最大大小（字节），默认 10MB
MAX_FILE_SIZE = int(os.environ.get('MAX_FILE_SIZE', 10 * 1024 * 1024))
# 图片扩展名（二手交易、西交吃什么评价、帖子插图）
ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
# 附件扩展名（考试资料）
ALLOWED_ATTACHMENT_EXTENSIONS = {'pdf', 'doc', 'docx', 'zip', 'rar'}
# 上传子目录名（按板块）
UPLOAD_SUBDIRS = {
    'second_hand': 'second_hand',
    'exam': 'exam',
    'canteen': 'canteen',
    'forum': 'forum',
}

# 管理员学号列表（逗号分隔或环境变量 ADMIN_STUDENT_IDS），为空则仅依赖 User.role
ADMIN_STUDENT_IDS = [x.strip() for x in (os.environ.get('ADMIN_STUDENT_IDS') or '').split(',') if x.strip()]

# ---------- AI 工具（文生图/图生视频/TTS）对接 videogen 服务 ----------
# videogen 服务地址。注意：是「运行西交校园 main4 的那台机器」要能访问该地址；
# 若 main4 与 videogen 不在同一台机（或 main4 在 Docker 内），请设 VIDEOGEN_BASE_URL 为可访问的 URL（如 http://主机IP:20262）
VIDEOGEN_BASE_URL = os.environ.get('VIDEOGEN_BASE_URL', 'http://127.0.0.1:20262')
# 积分消耗：文生图 / TTS 各 5，图生视频 20
AI_POINTS_T2I = 5
AI_POINTS_I2V = 20
AI_POINTS_TTS = 5

# ---------- 打卡账号密码加密 ----------
# 用于 DakaUser.password_encrypted 的 Fernet 密钥（32 字节 base64）。不设则用 FLASK_SECRET_KEY 派生
import base64
import hashlib
_flask_secret = os.environ.get('FLASK_SECRET_KEY', 'main4-xjtu-daka-secret-change-in-prod')
DAKA_ENCRYPT_KEY = os.environ.get('DAKA_ENCRYPT_KEY') or base64.urlsafe_b64encode(
    hashlib.sha256(_flask_secret.encode()).digest()
).decode('ascii')

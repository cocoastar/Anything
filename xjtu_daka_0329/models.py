# 西交校园平台 - SQLAlchemy 模型
# 依赖 extensions.db，在 app 创建后 import 以完成绑定
from datetime import datetime
from extensions import db


class User(db.Model):
    """用户：昵称、头像、角色（user/admin）、积分（默认 100）"""
    __tablename__ = 'user'
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.String(20), unique=True, nullable=False, index=True)
    nickname = db.Column(db.String(64), nullable=True)
    avatar_url = db.Column(db.String(512), nullable=True)
    role = db.Column(db.String(16), default='user')  # user / admin
    points = db.Column(db.Integer, default=100)  # 积分，默认 100；上传食堂菜品/发面经/发二手各 +5
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ---------- 二手交易 ----------
class SecondHandItem(db.Model):
    """二手商品"""
    __tablename__ = 'second_hand_item'
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.String(20), nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    price = db.Column(db.Numeric(10, 2), nullable=False)
    category = db.Column(db.String(32), nullable=True)  # 书籍/数码/生活用品等
    status = db.Column(db.String(16), default='on_sale')  # on_sale / off_shelf / sold
    image_urls = db.Column(db.JSON, nullable=True)       # ["/uploads/second_hand/xx.jpg", ...]
    contact = db.Column(db.String(100), nullable=True)   # 脱敏后的联系方式
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SecondHandFavorite(db.Model):
    """二手收藏"""
    __tablename__ = 'second_hand_favorite'
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.String(20), nullable=False, index=True)
    item_id = db.Column(db.Integer, db.ForeignKey('second_hand_item.id', ondelete='CASCADE'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint('student_id', 'item_id', name='uq_second_hand_fav'),)


# ---------- 考试资料 ----------
class ExamPost(db.Model):
    """考试资料帖子"""
    __tablename__ = 'exam_post'
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.String(20), nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    body = db.Column(db.Text, nullable=True)
    course_name = db.Column(db.String(100), nullable=True)
    tags = db.Column(db.JSON, nullable=True)   # ["高数", "期末"]
    file_path = db.Column(db.String(512), nullable=True)  # 附件相对路径
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ExamPostLike(db.Model):
    """考试资料点赞"""
    __tablename__ = 'exam_post_like'
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.String(20), nullable=False, index=True)
    post_id = db.Column(db.Integer, db.ForeignKey('exam_post.id', ondelete='CASCADE'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint('student_id', 'post_id', name='uq_exam_post_like'),)


# ---------- 论坛（面试经验 + 学长有话说）----------
class InterviewCompany(db.Model):
    """面试经验-公司分类，用户可添加"""
    __tablename__ = 'interview_company'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), nullable=False, unique=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class ForumPost(db.Model):
    """论坛帖子：board=interview 面试经验，board=senior 学长有话说"""
    __tablename__ = 'forum_post'
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.String(20), nullable=False, index=True)
    board = db.Column(db.String(32), nullable=False, index=True)  # interview / senior
    title = db.Column(db.String(200), nullable=False)
    body = db.Column(db.Text, nullable=True)
    tags = db.Column(db.JSON, nullable=True)
    company_id = db.Column(db.Integer, db.ForeignKey('interview_company.id', ondelete='SET NULL'), nullable=True)
    interview_type = db.Column(db.String(32), nullable=True)  # 秋招/春招/暑期实习/日常实习
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    company = db.relationship('InterviewCompany', backref='posts', lazy='joined', foreign_keys=[company_id])
    comments = db.relationship('ForumComment', backref='post', lazy='dynamic', cascade='all, delete-orphan')


class ForumComment(db.Model):
    """论坛评论（支持楼中楼：parent_id 指向父评论）"""
    __tablename__ = 'forum_comment'
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey('forum_post.id', ondelete='CASCADE'), nullable=False)
    parent_id = db.Column(db.Integer, db.ForeignKey('forum_comment.id', ondelete='CASCADE'), nullable=True)
    student_id = db.Column(db.String(20), nullable=False, index=True)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    replies = db.relationship('ForumComment', backref=db.backref('parent', remote_side='ForumComment.id'), lazy='dynamic')


class ForumLike(db.Model):
    """论坛点赞（帖子或评论）"""
    __tablename__ = 'forum_like'
    id = db.Column(db.Integer, primary_key=True)
    target_type = db.Column(db.String(16), nullable=False)  # post / comment
    target_id = db.Column(db.Integer, nullable=False)
    student_id = db.Column(db.String(20), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint('target_type', 'target_id', 'student_id', name='uq_forum_like'),)


# ---------- 西交吃什么 ----------
class Canteen(db.Model):
    """餐厅。status: pending 待审核 / approved 已通过 / rejected 已拒绝"""
    __tablename__ = 'canteen'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), nullable=False)
    floors_count = db.Column(db.Integer, default=3)
    status = db.Column(db.String(16), default='approved')  # pending / approved / rejected
    submitted_by = db.Column(db.String(20), nullable=True, index=True)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    reviewed_by = db.Column(db.String(20), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    windows = db.relationship('CanteenWindow', backref='canteen', lazy='dynamic', cascade='all, delete-orphan')


class CanteenWindow(db.Model):
    """窗口（某餐厅某层）。image_urls 为用户上传的窗口图片"""
    __tablename__ = 'canteen_window'
    id = db.Column(db.Integer, primary_key=True)
    canteen_id = db.Column(db.Integer, db.ForeignKey('canteen.id', ondelete='CASCADE'), nullable=False)
    floor = db.Column(db.Integer, nullable=False)  # 1/2/3 或 -1 表示负一
    name = db.Column(db.String(64), nullable=False)
    image_urls = db.Column(db.JSON, nullable=True)  # ["/uploads/canteen/xx.jpg", ...]
    status = db.Column(db.String(16), default='approved')
    submitted_by = db.Column(db.String(20), nullable=True)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    reviewed_by = db.Column(db.String(20), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    dishes = db.relationship('CanteenDish', backref='window', lazy='dynamic', cascade='all, delete-orphan')
    reviews = db.relationship('CanteenReview', backref='window', lazy='dynamic', foreign_keys='CanteenReview.window_id', cascade='all, delete-orphan')


class CanteenDish(db.Model):
    """菜品"""
    __tablename__ = 'canteen_dish'
    id = db.Column(db.Integer, primary_key=True)
    window_id = db.Column(db.Integer, db.ForeignKey('canteen_window.id', ondelete='CASCADE'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    price_min = db.Column(db.Numeric(8, 2), nullable=True)
    price_max = db.Column(db.Numeric(8, 2), nullable=True)
    price = db.Column(db.Numeric(8, 2), nullable=True)
    sort_order = db.Column(db.Integer, default=0)
    status = db.Column(db.String(16), default='approved')
    submitted_by = db.Column(db.String(20), nullable=True)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    reviewed_by = db.Column(db.String(20), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    reviews = db.relationship('CanteenReview', backref='dish', lazy='dynamic', foreign_keys='CanteenReview.dish_id', cascade='all, delete-orphan')


class CanteenReview(db.Model):
    """食堂评价（可评菜品或窗口）"""
    __tablename__ = 'canteen_review'
    id = db.Column(db.Integer, primary_key=True)
    dish_id = db.Column(db.Integer, db.ForeignKey('canteen_dish.id', ondelete='CASCADE'), nullable=True)
    window_id = db.Column(db.Integer, db.ForeignKey('canteen_window.id', ondelete='CASCADE'), nullable=True)
    student_id = db.Column(db.String(20), nullable=False, index=True)
    rating = db.Column(db.SmallInteger, nullable=False)   # 1-5
    content = db.Column(db.Text, nullable=True)
    image_urls = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# ---------- 体育打卡（数据库版，替代 tmux）----------
class DakaUser(db.Model):
    """打卡账号：学号、加密密码，用于每天波动时间段自动打卡"""
    __tablename__ = 'daka_user'
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.String(20), unique=True, nullable=False, index=True)
    password_encrypted = db.Column(db.String(512), nullable=False)  # Fernet 加密
    enabled = db.Column(db.Boolean, default=True)  # 是否参与每日自动打卡
    # 每日打卡时间窗口（HH:MM），在此范围内随机签到/签退
    time_window_start = db.Column(db.String(8), default='13:00')   # 如 13:00
    time_window_end = db.Column(db.String(8), default='14:00')      # 如 14:00
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DakaRecord(db.Model):
    """单日打卡记录：签到时间、签退时间、经纬度、响应信息"""
    __tablename__ = 'daka_record'
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.String(20), nullable=False, index=True)
    record_date = db.Column(db.Date, nullable=False, index=True)  # 打卡日期
    sign_in_at = db.Column(db.DateTime, nullable=True)   # 签到时间
    sign_out_at = db.Column(db.DateTime, nullable=True) # 签退时间
    sign_in_lat = db.Column(db.String(24), nullable=True)
    sign_in_lon = db.Column(db.String(24), nullable=True)
    sign_out_lat = db.Column(db.String(24), nullable=True)
    sign_out_lon = db.Column(db.String(24), nullable=True)
    sign_in_msg = db.Column(db.String(128), nullable=True)   # 如 "签到成功！"
    sign_out_msg = db.Column(db.String(128), nullable=True) # 如 "签退成功！"
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint('student_id', 'record_date', name='uq_daka_record_student_date'),)

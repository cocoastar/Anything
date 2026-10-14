# daka_service.py - 打卡逻辑（数据库版）：加密存储、签到/签退 调用 dk6.sign_in_with_token/sign_out_with_token，记录入库
import random
from datetime import datetime, date, timedelta

from cryptography.fernet import Fernet

from config import DAKA_ENCRYPT_KEY
from get_token import get_token

# 签到/签退请求统一走 dk6 的逻辑（同一接口、同一经纬度范围）
from dk6 import sign_in_with_token, sign_out_with_token


def _fernet():
    key = DAKA_ENCRYPT_KEY.encode() if isinstance(DAKA_ENCRYPT_KEY, str) else DAKA_ENCRYPT_KEY
    return Fernet(key)


def encrypt_password(password: str) -> str:
    return _fernet().encrypt(password.encode('utf-8')).decode('ascii')


def decrypt_password(encrypted: str) -> str:
    return _fernet().decrypt(encrypted.encode('ascii')).decode('utf-8')


def run_sign_in(student_id: str, app=None):
    """为指定学号执行签到，并写入 DakaRecord。需在 Flask app_context 下调用。"""
    from extensions import db
    from models import DakaUser, DakaRecord

    if app:
        with app.app_context():
            return _run_sign_in_impl(student_id, db, DakaUser, DakaRecord)
    return _run_sign_in_impl(student_id, db, DakaUser, DakaRecord)


def _run_sign_in_impl(student_id: str, db, DakaUser, DakaRecord):
    user = DakaUser.query.filter_by(student_id=student_id, enabled=True).first()
    if not user:
        return False, "未找到打卡账号或已禁用"
    try:
        password = decrypt_password(user.password_encrypted)
    except Exception:
        return False, "密码解密失败"
    token = get_token(student_id, password)
    if not token or not isinstance(token, str):
        return False, token or "获取 token 失败"
    if "用户名或密码错误" in token or "验证码" in token:
        return False, token
    ok, msg, lat, lon, _status = sign_in_with_token(token)
    today = date.today()
    rec = DakaRecord.query.filter_by(student_id=student_id, record_date=today).first()
    now = datetime.now()
    if rec:
        rec.sign_in_at = now
        rec.sign_in_lat = lat
        rec.sign_in_lon = lon
        rec.sign_in_msg = msg[:128] if msg else None
    else:
        rec = DakaRecord(
            student_id=student_id,
            record_date=today,
            sign_in_at=now,
            sign_in_lat=lat,
            sign_in_lon=lon,
            sign_in_msg=msg[:128] if msg else None,
        )
        db.session.add(rec)
    db.session.commit()
    return ok, msg


def run_sign_out(student_id: str, app=None):
    """为指定学号执行签退，并更新当日 DakaRecord。需在 Flask app_context 下调用。"""
    from extensions import db
    from models import DakaUser, DakaRecord

    if app:
        with app.app_context():
            return _run_sign_out_impl(student_id, db, DakaUser, DakaRecord)
    return _run_sign_out_impl(student_id, db, DakaUser, DakaRecord)


def _run_sign_out_impl(student_id: str, db, DakaUser, DakaRecord):
    user = DakaUser.query.filter_by(student_id=student_id, enabled=True).first()
    if not user:
        return False, "未找到打卡账号或已禁用"
    try:
        password = decrypt_password(user.password_encrypted)
    except Exception:
        return False, "密码解密失败"
    token = get_token(student_id, password)
    if not token or not isinstance(token, str):
        return False, token or "获取 token 失败"
    ok, msg, lat, lon, _status = sign_out_with_token(token)
    today = date.today()
    rec = DakaRecord.query.filter_by(student_id=student_id, record_date=today).first()
    now = datetime.now()
    if rec:
        rec.sign_out_at = now
        rec.sign_out_lat = lat
        rec.sign_out_lon = lon
        rec.sign_out_msg = msg[:128] if msg else None
        db.session.commit()
    else:
        rec = DakaRecord(
            student_id=student_id,
            record_date=today,
            sign_out_at=now,
            sign_out_lat=lat,
            sign_out_lon=lon,
            sign_out_msg=msg[:128] if msg else None,
        )
        db.session.add(rec)
        db.session.commit()
    return ok, msg


def random_time_in_window(window_start: str, window_end: str, interval_min=31, interval_max=90):
    """
    在时间窗口内生成随机签到时间和签退时间。
    window_start/window_end: "HH:MM" 或 "HH:MM:SS"
    返回 (sign_in_time, sign_out_time) 为 datetime.time
    """
    def parse(s):
        s = s.strip()
        if len(s) <= 5:  # HH:MM
            return datetime.strptime(s, "%H:%M").time()
        return datetime.strptime(s[:8], "%H:%M:%S").time()

    start = parse(window_start)
    end = parse(window_end)
    # 转为 datetime 便于计算
    base = datetime(2000, 1, 1)
    start_dt = datetime.combine(base.date(), start)
    end_dt = datetime.combine(base.date(), end)
    if end_dt <= start_dt:
        end_dt += timedelta(hours=1)
    # 随机开始：在 [start, start+20分] 内
    delta_min = random.randint(0, 20)
    sign_in_dt = start_dt + timedelta(minutes=delta_min)
    # 签退在 [sign_in+interval_min, sign_in+interval_max] 且不超过 end+20
    latest_end = end_dt + timedelta(minutes=20)
    interval = random.randint(interval_min, min(interval_max, int((latest_end - sign_in_dt).total_seconds() // 60)))
    sign_out_dt = sign_in_dt + timedelta(minutes=interval)
    return sign_in_dt.time(), sign_out_dt.time()

# daka2.py - 打卡逻辑：校验登录后写入数据库，由调度器每日随机时间打卡（不再使用 tmux）
import random
from datetime import datetime, timedelta

from get_token import get_token, get_token_with_sms

from daka_service import encrypt_password
from daka_scheduler import set_user_plan_today

scheduled_tasks = {}
user_logs = {}
user_times = {}
user_threads = {}


def daka(username, passwd, app=None):
    """校验学号密码，将账号写入数据库并开启每日自动打卡。首次点击：当前时间+几秒开始；已在打卡中则提示先停止。"""
    if not app:
        return "系统错误：缺少 app 上下文"

    with app.app_context():
        from extensions import db
        from models import DakaUser

        u = DakaUser.query.filter_by(student_id=username).first()
        if u and u.enabled:
            return "您已在打卡中，请先点击「停止」再重新签到"

    token = user_threads.get(username) or get_token(username, passwd)

    if isinstance(token, dict) and token.get("need_sms"):
        return token
    if isinstance(token, str) and "用户名或密码错误" in token:
        return "请检查用户名和密码"
    if isinstance(token, str) and "图形验证码" in token:
        return "请检查图形验证码,在这个网站成功登录一次就可以消除验证码，请复制下面网址：https://ipahw.xjtu.edu.cn/"
    if not token or not isinstance(token, str):
        return "获取 token 失败，无法执行打卡"

    user_logs[username] = [f"欢迎 {username}，已开启每日自动打卡\n"]

    with app.app_context():
        from extensions import db
        from models import DakaUser

        u = DakaUser.query.filter_by(student_id=username).first()
        enc = encrypt_password(passwd)
        if u:
            u.password_encrypted = enc
            u.enabled = True
        else:
            u = DakaUser(student_id=username, password_encrypted=enc, enabled=True)
            db.session.add(u)
        db.session.commit()

    # 今日计划：当前时间往后推几秒开始签到，签退在 31～90 分钟后（间隔大于 30 分钟）
    now = datetime.now()
    sign_in_dt = now + timedelta(seconds=10)
    sign_out_dt = sign_in_dt + timedelta(minutes=random.randint(31, 90))
    set_user_plan_today(username, sign_in_dt, sign_out_dt)

    return "已开启每日自动打卡，今天将在 %s 签到、%s 签退，之后每天在设定时间段内随机打卡" % (
        sign_in_dt.strftime("%H:%M:%S"),
        sign_out_dt.strftime("%H:%M:%S"),
    )


def daka_continue(username, sms_code, passwd, app=None):
    """短信验证通过后，将账号写入数据库并开启每日自动打卡，并设置今日签到/签退时间。"""
    if not app:
        return "系统错误：缺少 app 上下文"

    with app.app_context():
        from models import DakaUser

        u = DakaUser.query.filter_by(student_id=username).first()
        if u and u.enabled:
            return "您已在打卡中，请先点击「停止」再重新签到"

    token = get_token_with_sms(username, sms_code)
    if "请先点击" in token or "短信验证码错误" in token or "短信验证失败" in token:
        return token
    if not token or not isinstance(token, str):
        return "验证失败，请重试"

    user_logs[username] = [f"欢迎 {username}，验证成功，已开启每日自动打卡\n"]

    with app.app_context():
        from extensions import db
        from models import DakaUser

        u = DakaUser.query.filter_by(student_id=username).first()
        enc = encrypt_password(passwd)
        if u:
            u.password_encrypted = enc
            u.enabled = True
        else:
            u = DakaUser(student_id=username, password_encrypted=enc, enabled=True)
            db.session.add(u)
        db.session.commit()

    now = datetime.now()
    sign_in_dt = now + timedelta(seconds=10)
    sign_out_dt = sign_in_dt + timedelta(minutes=random.randint(31, 90))
    set_user_plan_today(username, sign_in_dt, sign_out_dt)

    return "验证成功，今天将在 %s 签到、%s 签退" % (
        sign_in_dt.strftime("%H:%M:%S"),
        sign_out_dt.strftime("%H:%M:%S"),
    )

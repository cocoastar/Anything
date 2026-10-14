# daka_scheduler.py - 每日随机时间打卡调度（单进程，读数据库，不依赖 tmux）
import random
import threading
import time
from datetime import datetime, date, timedelta

from daka_service import random_time_in_window, run_sign_in, run_sign_out

# 每日计划：student_id -> { sign_in_time, sign_out_time, sign_in_done, sign_out_done }
_daily_plans = {}
_plans_lock = threading.Lock()
_scheduler_thread = None


def _refresh_plans(app):
    """从数据库加载所有 DakaUser，为每人生成当日随机签到/签退时间。"""
    with app.app_context():
        from models import DakaUser
        users = DakaUser.query.filter_by(enabled=True).all()
    plans = {}
    for u in users:
        t_in, t_out = random_time_in_window(
            u.time_window_start or "13:00",
            u.time_window_end or "14:00",
            interval_min=31,
            interval_max=90,
        )
        plans[u.student_id] = {
            "sign_in_time": t_in,
            "sign_out_time": t_out,
            "sign_in_done": False,
            "sign_out_done": False,
        }
    with _plans_lock:
        _daily_plans.clear()
        _daily_plans.update(plans)
    return plans


def _maybe_run_pending(app):
    """检查当前时间，若已到某用户的签到/签退时间则执行并标记。"""
    now = datetime.now().time()
    today = date.today()
    with _plans_lock:
        to_run_in = []
        to_run_out = []
        for sid, p in list(_daily_plans.items()):
            if not p["sign_in_done"] and now >= p["sign_in_time"]:
                to_run_in.append(sid)
            if not p["sign_out_done"] and now >= p["sign_out_time"]:
                to_run_out.append(sid)
    for sid in to_run_in:
        ok, msg = run_sign_in(sid, app=app)
        with _plans_lock:
            if sid in _daily_plans:
                _daily_plans[sid]["sign_in_done"] = True
    for sid in to_run_out:
        ok, msg = run_sign_out(sid, app=app)
        with _plans_lock:
            if sid in _daily_plans:
                _daily_plans[sid]["sign_out_done"] = True


def _scheduler_loop(app):
    last_date = None
    while True:
        try:
            now = datetime.now()
            today = now.date()
            # 每天 00:01 刷新计划
            if last_date != today and (now.hour == 0 and now.minute <= 2 or last_date is None):
                _refresh_plans(app)
                last_date = today
            _maybe_run_pending(app)
        except Exception:
            pass
        time.sleep(30)


def start_scheduler(app):
    """在后台线程启动打卡调度器。"""
    global _scheduler_thread
    if _scheduler_thread is not None and _scheduler_thread.is_alive():
        return
    _scheduler_thread = threading.Thread(target=_scheduler_loop, args=(app,), daemon=True)
    _scheduler_thread.start()


def set_user_plan_today(student_id: str, sign_in_dt: datetime, sign_out_dt: datetime):
    """
    为某用户设置今日签到/签退时间（点击「签到」后立即生效）。
    sign_in_dt/sign_out_dt 为 datetime，调度器到点执行。强制签退至少比签到晚 31 分钟。
    """
    min_out = sign_in_dt + timedelta(minutes=31)
    if sign_out_dt <= min_out:
        sign_out_dt = min_out
    with _plans_lock:
        _daily_plans[student_id] = {
            "sign_in_time": sign_in_dt.time(),
            "sign_out_time": sign_out_dt.time(),
            "sign_in_done": False,
            "sign_out_done": False,
        }


def get_today_plan(student_id: str):
    """返回某用户今日计划的签到/签退时间（用于展示）。"""
    with _plans_lock:
        p = _daily_plans.get(student_id)
    if not p:
        return None
    return {
        "sign_in_time": p["sign_in_time"].strftime("%H:%M:%S") if p.get("sign_in_time") else None,
        "sign_out_time": p["sign_out_time"].strftime("%H:%M:%S") if p.get("sign_out_time") else None,
        "sign_in_done": p.get("sign_in_done", False),
        "sign_out_done": p.get("sign_out_done", False),
    }

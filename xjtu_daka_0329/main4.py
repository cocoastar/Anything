# main4.py - 西交体育打卡 Web 入口
# 流程：登录（学号+密码，需短信时弹窗验证）→ 工作台 → 签到打卡
import os
import re
import uuid
from threading import Lock

import requests as _requests
from flask import Flask, request, jsonify, render_template, redirect, url_for, session, send_from_directory, Response

from get_token import get_token, get_token_with_sms
from daka2 import daka, daka_continue, user_threads, user_logs

# 确保模板目录为本项目
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__, template_folder=os.path.join(BASE_DIR, 'templates'))
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'main4-xjtu-daka-secret-change-in-prod')
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# 数据库与迁移
from config import (
    DATABASE_URL, UPLOAD_DIR, MAX_FILE_SIZE,
    ALLOWED_IMAGE_EXTENSIONS, ALLOWED_ATTACHMENT_EXTENSIONS, UPLOAD_SUBDIRS,
    VIDEOGEN_BASE_URL, AI_POINTS_T2I, AI_POINTS_I2V, AI_POINTS_TTS,
)
app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

from extensions import db
db.init_app(app)
import models  # noqa: F401,E402 - 注册模型
from models import (
    User, SecondHandItem, SecondHandFavorite,
    Canteen, CanteenWindow, CanteenDish, CanteenReview,
    InterviewCompany, ForumPost, ForumComment,
    DakaUser, DakaRecord,
)
from config import ADMIN_STUDENT_IDS
from flask_migrate import Migrate
Migrate(app, db)

# 启动打卡调度器（从数据库读账号，每日随机时间签到/签退，不再使用 tmux）
try:
    from daka_scheduler import start_scheduler
    start_scheduler(app)
except Exception:
    pass

thread_lock = Lock()

# AI 任务待扣积分：仅当轮询到「成功」时扣分，失败不扣。job_id -> (student_id, amount)
_pending_ai_jobs = {}
_pending_ai_lock = Lock()


def validate_student_id(student_id: str) -> bool:
    return re.match(r'^\d{10}$', student_id or '') is not None


def require_login(f):
    from functools import wraps
    @wraps(f)
    def wrapped(*args, **kwargs):
        if not session.get('student_id'):
            if 'application/json' in request.headers.get('Accept', ''):
                return jsonify({'code': 401, 'message': '请先登录'}), 401
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return wrapped


def require_admin(f):
    from functools import wraps
    @wraps(f)
    def wrapped(*args, **kwargs):
        if not session.get('student_id'):
            if 'application/json' in request.headers.get('Accept', ''):
                return jsonify({'code': 401, 'message': '请先登录'}), 401
            return redirect(url_for('login_page'))
        sid = session.get('student_id')
        if sid in ADMIN_STUDENT_IDS:
            return f(*args, **kwargs)
        u = User.query.filter_by(student_id=sid).first()
        if not u or getattr(u, 'role', None) != 'admin':
            if 'application/json' in request.headers.get('Accept', ''):
                return jsonify({'code': 403, 'message': '需要管理员权限'}), 403
            return redirect(url_for('home_page'))
        return f(*args, **kwargs)
    return wrapped


def _ensure_user(student_id):
    """登录成功后调用：创建或更新 User"""
    u = User.query.filter_by(student_id=student_id).first()
    if not u:
        u = User(student_id=student_id, role='admin' if student_id in ADMIN_STUDENT_IDS else 'user', points=100)
        db.session.add(u)
    elif getattr(u, 'points', None) is None:
        u.points = 100
    db.session.commit()
    return u


def _add_points(student_id, delta):
    """给用户增加积分（上传食堂菜品/发面经/发二手后调用）。"""
    u = User.query.filter_by(student_id=student_id).first()
    if not u:
        return
    cur = getattr(u, 'points', None)
    if cur is None:
        u.points = 100 + delta
    else:
        u.points = cur + delta
    db.session.commit()


def _deduct_points(student_id, amount):
    """扣减用户积分（AI 工具调用前）。积分不足返回 False，成功返回 True。"""
    if amount <= 0:
        return True
    u = User.query.filter_by(student_id=student_id).first()
    if not u:
        return False
    cur = getattr(u, 'points', None)
    if cur is None:
        u.points = 100
        cur = 100
    if cur < amount:
        return False
    u.points = cur - amount
    db.session.commit()
    return True


# ---------- 页面 ----------
@app.route('/')
def index():
    if session.get('student_id'):
        return redirect(url_for('home_page'))
    return redirect(url_for('login_page'))


@app.route('/login')
def login_page():
    if session.get('student_id'):
        return redirect(url_for('home_page'))
    return render_template('login.html')


@app.route('/home')
@require_login
def home_page():
    u = User.query.filter_by(student_id=session.get('student_id')).first()
    return render_template('home.html', student_id=session.get('student_id'), is_admin=(u and getattr(u, 'role', None) == 'admin'))


# ---------- 二手交易 页面 ----------
@app.route('/second-hand')
@require_login
def second_hand_list():
    return render_template('second_hand_list.html', student_id=session.get('student_id'))


@app.route('/second-hand/publish')
@require_login
def second_hand_publish():
    return render_template('second_hand_publish.html', student_id=session.get('student_id'))


@app.route('/second-hand/<int:item_id>')
@require_login
def second_hand_detail(item_id):
    return render_template('second_hand_detail.html', student_id=session.get('student_id'), item_id=item_id)


# ---------- 其他板块占位页（后续实现）----------
@app.route('/exam')
@require_login
def exam_redirect():
    """考试资料板块已下线，重定向到工作台"""
    return redirect(url_for('home_page'))


@app.route('/forum/<board>')
@require_login
def forum_list(board):
    if board != 'interview':
        return redirect(url_for('home_page'))
    return render_template('forum_interview.html', student_id=session.get('student_id'))


@app.route('/eat')
@require_login
def eat_index():
    return render_template('eat_index.html', student_id=session.get('student_id'))


@app.route('/eat/<int:canteen_id>')
@require_login
def eat_canteen(canteen_id):
    """旧链接重定向到单页并带上餐厅 id"""
    return redirect(url_for('eat_index', canteen_id=canteen_id))


@app.route('/ai-tools')
@require_login
def ai_tools_page():
    """AI 工具合集：文生图、图生视频、TTS。"""
    return render_template('ai_tools.html', student_id=session.get('student_id'))


# ---------- AI 工具：对接 videogen，扣积分 ----------
def _ai_proxy_url(path):
    """把 videogen 的 /files/xxx 转为本站代理 URL（供前端使用）。"""
    base = VIDEOGEN_BASE_URL.rstrip('/')
    if path.startswith(base + '/'):
        path = path[len(base):].lstrip('/')
    if path.startswith('files/'):
        path = path[6:]
    return url_for('ai_proxy_file', subpath=path, _external=False)


@app.route('/api/ai/proxy/files/<path:subpath>')
@require_login
def ai_proxy_file(subpath):
    """代理 videogen 的输出文件，便于前端展示（避免跨域）。"""
    url = f"{VIDEOGEN_BASE_URL.rstrip('/')}/files/{subpath}"
    try:
        r = _requests.get(url, timeout=60)
        r.raise_for_status()
        ct = r.headers.get('Content-Type', 'application/octet-stream')
        return Response(r.content, status=r.status_code, mimetype=ct)
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@app.route('/api/ai/t2i', methods=['POST'])
@require_login
def api_ai_t2i():
    """文生图：提交不扣积分，仅当轮询到成功时扣 5 积分。"""
    sid = session.get('student_id')
    u = User.query.filter_by(student_id=sid).first()
    cur = getattr(u, 'points', None) if u else None
    if cur is None:
        cur = 100
    if cur < AI_POINTS_T2I:
        return jsonify({'ok': False, 'error': '积分不足', 'code': 'INSUFFICIENT_POINTS'}), 400
    data = request.get_json(silent=True) or {}
    prompt = (data.get('prompt') or '').strip()
    if not prompt:
        return jsonify({'ok': False, 'error': 'prompt 不能为空'}), 400
    url = f"{VIDEOGEN_BASE_URL.rstrip('/')}/api/v1/t2i/jobs"
    try:
        r = _requests.post(url, json={
            'prompt': prompt,
            'width': data.get('width', 512),
            'height': data.get('height', 512),
            'steps': data.get('steps', 4),
            'guidance': data.get('guidance', 1.0),
            'seed': data.get('seed'),
        }, timeout=15)
        r.raise_for_status()
        out = r.json()
        if not out.get('ok') or not out.get('jobId'):
            return jsonify({'ok': False, 'error': out.get('error', '创建任务失败')}), 502
        with _pending_ai_lock:
            _pending_ai_jobs[out['jobId']] = (sid, AI_POINTS_T2I)
        return jsonify({'ok': True, 'jobId': out['jobId']})
    except Exception as e:
        err = str(e)
        if 'Connection refused' in err or 'Failed to establish' in err or 'ConnectionError' in err:
            err = (
                'AI 服务连接失败。请确认：运行西交校园的这台机器能否访问 videogen（当前 VIDEOGEN_BASE_URL=%s）。'
                '若西交校园与 videogen 不在同一台机，请设置环境变量 VIDEOGEN_BASE_URL 为可访问的地址。'
            ) % VIDEOGEN_BASE_URL
        return jsonify({'ok': False, 'error': err}), 503


@app.route('/api/ai/t2i/jobs/<job_id>')
@require_login
def api_ai_t2i_job(job_id):
    """轮询文生图任务状态；成功时扣积分并把 resultUrl 改为本站代理。"""
    url = f"{VIDEOGEN_BASE_URL.rstrip('/')}/api/v1/t2i/jobs/{job_id}"
    try:
        r = _requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        if not data.get('ok'):
            return jsonify(data), 404
        status = (data.get('status') or '').lower()
        if status in ('failed', 'error', 'canceled'):
            with _pending_ai_lock:
                _pending_ai_jobs.pop(job_id, None)
            return jsonify(data)
        if status in ('succeeded', 'success'):
            with _pending_ai_lock:
                pending = _pending_ai_jobs.pop(job_id, None)
            if pending:
                sid, amount = pending
                _deduct_points(sid, amount)
        result_url = data.get('resultUrl')
        if result_url and '/files/' in result_url:
            base = VIDEOGEN_BASE_URL.rstrip('/')
            if result_url.startswith(base + '/'):
                rel = result_url[len(base):].lstrip('/')
                if rel.startswith('files/'):
                    data['resultUrl'] = url_for('ai_proxy_file', subpath=rel[6:], _external=True)
        return jsonify(data)
    except Exception as e:
        err = str(e)
        if 'Connection refused' in err or 'Failed to establish' in err or 'ConnectionError' in err:
            err = 'AI 服务连接失败（请确认运行西交校园的机器能访问 VIDEOGEN_BASE_URL）'
        return jsonify({'ok': False, 'error': err}), 503


@app.route('/api/ai/i2v', methods=['POST'])
@require_login
def api_ai_i2v():
    """图生视频（LightX2V 单图）：提交不扣积分，仅当轮询到成功时扣 20 积分。"""
    sid = session.get('student_id')
    u = User.query.filter_by(student_id=sid).first()
    cur = getattr(u, 'points', None) if u else None
    if cur is None:
        cur = 100
    if cur < AI_POINTS_I2V:
        return jsonify({'ok': False, 'error': '积分不足', 'code': 'INSUFFICIENT_POINTS'}), 400
    img_f = request.files.get('image') or request.files.get('image1')
    if not img_f or not img_f.filename:
        return jsonify({'ok': False, 'error': '请上传一张图片'}), 400
    prompt = (request.form.get('prompt') or '').strip()
    if not prompt:
        return jsonify({'ok': False, 'error': '画面描述（prompt）不能为空'}), 400
    try:
        img_f.seek(0)
        req_files = [('image', (img_f.filename or 'image.png', img_f.read(), img_f.content_type or 'image/png'))]
        data = {
            'prompt': prompt,
            'negative_prompt': request.form.get('negative_prompt') or ' ',
            'seed': request.form.get('seed') or '',
            'target_h': request.form.get('target_h') or '320',
            'target_w': request.form.get('target_w') or '512',
        }
        r = _requests.post(f"{VIDEOGEN_BASE_URL.rstrip('/')}/api/v1/i2v/jobs", files=req_files, data=data, timeout=60)
        r.raise_for_status()
        out = r.json()
        if not out.get('ok') or not out.get('jobId'):
            return jsonify({'ok': False, 'error': out.get('error', '创建任务失败')}), 502
        with _pending_ai_lock:
            _pending_ai_jobs[out['jobId']] = (sid, AI_POINTS_I2V)
        return jsonify({'ok': True, 'jobId': out['jobId']})
    except Exception as e:
        err = str(e)
        if 'Connection refused' in err or 'Failed to establish' in err or 'ConnectionError' in err:
            err = (
                'AI 服务连接失败。请确认：运行西交校园的这台机器能否访问 videogen（当前 VIDEOGEN_BASE_URL=%s）。'
                '若西交校园与 videogen 不在同一台机，请设置环境变量 VIDEOGEN_BASE_URL 为可访问的地址。'
            ) % VIDEOGEN_BASE_URL
        return jsonify({'ok': False, 'error': err}), 503


@app.route('/api/ai/i2v/jobs/<job_id>')
@require_login
def api_ai_i2v_job(job_id):
    """轮询图生视频（LightX2V）任务状态；成功时扣积分并把 resultUrl 改为本站代理。"""
    url = f"{VIDEOGEN_BASE_URL.rstrip('/')}/api/v1/i2v/jobs/{job_id}"
    try:
        r = _requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        if not data.get('ok'):
            return jsonify(data), 404
        status = (data.get('status') or '').lower()
        if status in ('failed', 'error', 'canceled'):
            with _pending_ai_lock:
                _pending_ai_jobs.pop(job_id, None)
            return jsonify(data)
        if status in ('succeeded', 'success'):
            with _pending_ai_lock:
                pending = _pending_ai_jobs.pop(job_id, None)
            if pending:
                sid, amount = pending
                _deduct_points(sid, amount)
        result_url = data.get('resultUrl')
        if result_url and '/files/' in result_url:
            base = VIDEOGEN_BASE_URL.rstrip('/')
            if result_url.startswith(base + '/'):
                rel = result_url[len(base):].lstrip('/')
                if rel.startswith('files/'):
                    data['resultUrl'] = url_for('ai_proxy_file', subpath=rel[6:], _external=True)
        return jsonify(data)
    except Exception as e:
        err = str(e)
        if 'Connection refused' in err or 'Failed to establish' in err or 'ConnectionError' in err:
            err = 'AI 服务连接失败（请确认运行西交校园的机器能访问 VIDEOGEN_BASE_URL）'
        return jsonify({'ok': False, 'error': err}), 503


@app.route('/api/ai/tts', methods=['POST'])
@require_login
def api_ai_tts():
    """TTS 语音克隆：提交不扣积分，仅当轮询到成功时扣 5 积分。"""
    sid = session.get('student_id')
    u = User.query.filter_by(student_id=sid).first()
    cur = getattr(u, 'points', None) if u else None
    if cur is None:
        cur = 100
    if cur < AI_POINTS_TTS:
        return jsonify({'ok': False, 'error': '积分不足', 'code': 'INSUFFICIENT_POINTS'}), 400
    ref_audio = request.files.get('ref_audio') or request.files.get('audio')
    ref_text = (request.form.get('ref_text') or '').strip()
    text = (request.form.get('text') or '').strip()
    if not ref_audio:
        return jsonify({'ok': False, 'error': '请上传参考音频'}), 400
    if not ref_text:
        return jsonify({'ok': False, 'error': '参考音频对应的文字（ref_text）不能为空'}), 400
    if not text:
        return jsonify({'ok': False, 'error': '要合成的文字（text）不能为空'}), 400
    try:
        ref_audio.seek(0)
        files = [('ref_audio', (ref_audio.filename or 'ref.wav', ref_audio.read(), ref_audio.content_type or 'audio/wav'))]
        data = {'ref_text': ref_text, 'text': text, 'language': request.form.get('language') or 'Chinese'}
        r = _requests.post(f"{VIDEOGEN_BASE_URL.rstrip('/')}/api/v1/tts/clone/jobs", files=files, data=data, timeout=30)
        r.raise_for_status()
        out = r.json()
        if not out.get('ok') or not out.get('jobId'):
            return jsonify({'ok': False, 'error': out.get('error', '创建任务失败')}), 502
        with _pending_ai_lock:
            _pending_ai_jobs[out['jobId']] = (sid, AI_POINTS_TTS)
        return jsonify({'ok': True, 'jobId': out['jobId']})
    except Exception as e:
        err = str(e)
        if 'Connection refused' in err or 'Failed to establish' in err or 'ConnectionError' in err:
            err = (
                'AI 服务连接失败。请确认：运行西交校园的这台机器能否访问 videogen（当前 VIDEOGEN_BASE_URL=%s）。'
                '若西交校园与 videogen 不在同一台机，请设置环境变量 VIDEOGEN_BASE_URL 为可访问的地址。'
            ) % VIDEOGEN_BASE_URL
        return jsonify({'ok': False, 'error': err}), 503


@app.route('/api/ai/tts/jobs/<job_id>')
@require_login
def api_ai_tts_job(job_id):
    """轮询 TTS 任务状态；成功时扣积分并把 resultUrl 改为本站代理。"""
    url = f"{VIDEOGEN_BASE_URL.rstrip('/')}/api/v1/tts/clone/jobs/{job_id}"
    try:
        r = _requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        if not data.get('ok'):
            return jsonify(data), 404
        status = (data.get('status') or '').lower()
        if status in ('failed', 'error', 'canceled'):
            with _pending_ai_lock:
                _pending_ai_jobs.pop(job_id, None)
            return jsonify(data)
        if status in ('succeeded', 'success'):
            with _pending_ai_lock:
                pending = _pending_ai_jobs.pop(job_id, None)
            if pending:
                sid, amount = pending
                _deduct_points(sid, amount)
        result_url = data.get('resultUrl')
        if result_url and '/files/' in result_url:
            base = VIDEOGEN_BASE_URL.rstrip('/')
            if result_url.startswith(base + '/'):
                rel = result_url[len(base):].lstrip('/')
                if rel.startswith('files/'):
                    data['resultUrl'] = url_for('ai_proxy_file', subpath=rel[6:], _external=True)
        return jsonify(data)
    except Exception as e:
        err = str(e)
        if 'Connection refused' in err or 'Failed to establish' in err or 'ConnectionError' in err:
            err = 'AI 服务连接失败（请确认运行西交校园的机器能访问 VIDEOGEN_BASE_URL）'
        return jsonify({'ok': False, 'error': err}), 503


# ---------- 登录 API ----------
@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.json or {}
    student_id = (data.get('student_id') or '').strip()
    password = (data.get('password') or '').strip()

    if not validate_student_id(student_id):
        return jsonify({'ok': False, 'message': '学号格式不正确，应为10位数字'}), 400
    if not password:
        return jsonify({'ok': False, 'message': '请输入密码'}), 400

    try:
        with thread_lock:
            result = get_token(student_id, password)
    except RuntimeError as e:
        msg = str(e)
        if '401' in msg or 'status' in msg:
            return jsonify({'ok': False, 'message': '登录被拒绝（401），请检查学号与密码是否正确'})
        if 'MFA' in msg or 'state' in msg or 'securephone' in msg:
            return jsonify({'ok': False, 'message': '短信验证环节异常，请稍后重试'})
        return jsonify({'ok': False, 'message': msg or '登录失败'})
    except Exception as e:
        return jsonify({'ok': False, 'message': '登录异常：{}'.format(str(e))})

    if isinstance(result, dict) and result.get('need_sms'):
        return jsonify({
            'ok': False,
            'need_mfa': True,
            'message': '已向安全手机 {} 发送验证码，请输入验证码'.format(result.get('mask_phone', '')),
            'mask_phone': result.get('mask_phone', ''),
        })

    if isinstance(result, str):
        if '用户名或密码错误' in result:
            return jsonify({'ok': False, 'message': '用户名或密码错误'})
        if '图形验证码' in result:
            return jsonify({'ok': False, 'message': '请先在浏览器打开 https://ipahw.xjtu.edu.cn/ 登录一次以通过图形验证'})
        if not result or '失败' in result or '错误' in result:
            return jsonify({'ok': False, 'message': result or '登录失败'})
        session['student_id'] = student_id
        session['password'] = password
        session.permanent = True
        user_threads[student_id] = result
        with app.app_context():
            _ensure_user(student_id)
        return jsonify({'ok': True, 'message': '登录成功'})

    return jsonify({'ok': False, 'message': '登录失败'})


@app.route('/api/login_verify', methods=['POST'])
def api_login_verify():
    data = request.json or {}
    student_id = (data.get('student_id') or '').strip()
    password = (data.get('password') or '').strip()
    code = (data.get('code') or '').strip()

    if not validate_student_id(student_id):
        return jsonify({'ok': False, 'message': '学号格式不正确'}), 400
    if not code:
        return jsonify({'ok': False, 'message': '请输入短信验证码'}), 400

    try:
        with thread_lock:
            token = get_token_with_sms(student_id, code)
    except RuntimeError as e:
        return jsonify({'ok': False, 'message': str(e) or '验证失败'})
    except Exception as e:
        return jsonify({'ok': False, 'message': '验证异常：{}'.format(str(e))})

    if not isinstance(token, str) or '请先' in token or '短信验证码错误' in token or '短信验证失败' in token:
        return jsonify({'ok': False, 'message': token if isinstance(token, str) else '验证失败'})

    session['student_id'] = student_id
    session['password'] = password
    session.permanent = True
    user_threads[student_id] = token
    with app.app_context():
        _ensure_user(student_id)
    return jsonify({'ok': True, 'message': '验证成功，已登录'})


# ---------- 个人中心 ----------
@app.route('/profile')
@require_login
def profile_page():
    return render_template('profile.html', student_id=session.get('student_id'))


@app.route('/api/profile', methods=['GET'])
@require_login
def api_profile_get():
    u = User.query.filter_by(student_id=session.get('student_id')).first()
    if not u:
        return jsonify({'code': 0, 'profile': {'student_id': session.get('student_id'), 'nickname': None, 'points': 100}})
    return jsonify({'code': 0, 'profile': {'student_id': u.student_id, 'nickname': u.nickname or '', 'points': getattr(u, 'points', 100) or 100}})


@app.route('/api/profile', methods=['PATCH'])
@require_login
def api_profile_update():
    data = request.json or {}
    nickname = (data.get('nickname') or '').strip()[:64]
    u = User.query.filter_by(student_id=session.get('student_id')).first()
    if not u:
        u = User(student_id=session.get('student_id'), nickname=nickname or None)
        db.session.add(u)
    else:
        u.nickname = nickname or None
    db.session.commit()
    return jsonify({'code': 0, 'message': '已保存', 'profile': {'student_id': u.student_id, 'nickname': u.nickname or '', 'points': getattr(u, 'points', 100) or 100}})


# ---------- 工作台 API ----------
@app.route('/api/daka', methods=['POST'])
@require_login
def api_daka():
    student_id = session.get('student_id')
    password = session.get('password') or ''
    with thread_lock:
        result = daka(username=student_id, passwd=password, app=app)
    if isinstance(result, dict) and result.get('need_sms'):
        return jsonify({
            'code': 202,
            'need_mfa': True,
            'message': '已向安全手机 {} 发送验证码，请输入验证码'.format(result.get('mask_phone', '')),
            'mask_phone': result.get('mask_phone', ''),
        })
    logs = user_logs.get(student_id, [])
    return jsonify({'code': 0, 'message': result, 'logs': logs})


@app.route('/api/daka_verify', methods=['POST'])
@require_login
def api_daka_verify():
    student_id = session.get('student_id')
    password = session.get('password') or ''
    code = (request.json or {}).get('code', '').strip()
    if not code:
        return jsonify({'code': -1, 'message': '请输入短信验证码'}), 400
    with thread_lock:
        result = daka_continue(username=student_id, sms_code=code, passwd=password, app=app)
    ok = '成功' in result or '已开始' in result
    return jsonify({'code': 0 if ok else -1, 'message': result})


@app.route('/api/logs', methods=['GET'])
@require_login
def api_logs():
    from datetime import date
    student_id = session.get('student_id')
    today = date.today()
    logs = []
    # 今日计划（调度器生成的签到/签退时间）
    try:
        from daka_scheduler import get_today_plan
        plan = get_today_plan(student_id)
        if plan and (plan.get('sign_in_time') or plan.get('sign_out_time')):
            parts = []
            if plan.get('sign_in_time'):
                parts.append('签到 %s' % plan['sign_in_time'])
            if plan.get('sign_out_time'):
                parts.append('签退 %s' % plan['sign_out_time'])
            if parts:
                logs.append('新一天的打卡时间:%s\n' % '-'.join(parts))
    except Exception:
        pass
    # 打卡记录：当天按原 tmux 风格展示签到/签退详情，其余日期简要一行
    recs = DakaRecord.query.filter_by(student_id=student_id).order_by(DakaRecord.record_date.desc()).limit(30).all()
    for r in recs:
        if r.record_date == today and (r.sign_in_at or r.sign_out_at):
            # 当天且已有签到或签退：原样输出 Signing in... / 经纬度 / HTTP 状态码 / 响应内容
            if r.sign_in_at:
                logs.append('Signing in...\n')
                logs.append('%s - Signing in...\n' % r.sign_in_at.strftime('%Y-%m-%d %H:%M:%S'))
                if r.sign_in_lat and r.sign_in_lon:
                    logs.append('经纬度:%s,%s\n' % (r.sign_in_lat, r.sign_in_lon))
                logs.append('HTTP 状态码: 200\n')
                logs.append('响应内容:%s\n' % ((r.sign_in_msg or '').strip() or '签到成功！'))
            if r.sign_out_at:
                logs.append('Signing out...\n')
                logs.append('%s - Signing out...\n' % r.sign_out_at.strftime('%Y-%m-%d %H:%M:%S'))
                if r.sign_out_lat and r.sign_out_lon:
                    logs.append('经纬度:%s,%s\n' % (r.sign_out_lat, r.sign_out_lon))
                logs.append('HTTP 状态码: 200\n')
                logs.append('响应内容:%s\n' % ((r.sign_out_msg or '').strip() or '签退成功！'))
        else:
            if r.sign_in_at:
                logs.append('%s - 签到 %s\n' % (r.sign_in_at.strftime('%Y-%m-%d %H:%M:%S'), (r.sign_in_msg or '').strip() or '成功'))
            if r.sign_out_at:
                logs.append('%s - 签退 %s\n' % (r.sign_out_at.strftime('%Y-%m-%d %H:%M:%S'), (r.sign_out_msg or '').strip() or '成功'))
    logs.extend(user_logs.get(student_id, []))
    return jsonify({'code': 0, 'logs': logs})


@app.route('/api/daka/records', methods=['GET'])
@require_login
def api_daka_records():
    """查询当前用户的打卡记录：成功次数 + 每条记录的签到/签退时间。"""
    student_id = session.get('student_id')
    page = max(1, int(request.args.get('page', 1)))
    per_page = min(50, max(1, int(request.args.get('per_page', 20))))
    query = DakaRecord.query.filter_by(student_id=student_id).order_by(DakaRecord.record_date.desc())
    total = query.count()
    # 成功次数：既有签到又有签退且消息含成功的记录
    success_count = DakaRecord.query.filter_by(student_id=student_id).filter(
        DakaRecord.sign_in_at.isnot(None),
        DakaRecord.sign_out_at.isnot(None),
    ).count()
    pagination = query.paginate(page=page, per_page=per_page)
    records = []
    for r in pagination.items:
        records.append({
            'date': r.record_date.isoformat() if r.record_date else None,
            'sign_in_at': r.sign_in_at.strftime('%Y-%m-%d %H:%M:%S') if r.sign_in_at else None,
            'sign_out_at': r.sign_out_at.strftime('%Y-%m-%d %H:%M:%S') if r.sign_out_at else None,
            'sign_in_msg': r.sign_in_msg,
            'sign_out_msg': r.sign_out_msg,
        })
    return jsonify({
        'code': 0,
        'total_count': total,
        'success_count': success_count,
        'records': records,
        'page': page,
        'per_page': per_page,
    })


@app.route('/api/stop', methods=['POST'])
@require_login
def api_stop():
    student_id = session.get('student_id')
    u = DakaUser.query.filter_by(student_id=student_id).first()
    if u:
        u.enabled = False
        db.session.commit()
        msg = '已停止每日自动打卡'
    else:
        msg = '未找到打卡账号'
    return jsonify({'code': 0, 'message': msg})


@app.route('/api/logout', methods=['POST'])
def api_logout():
    session.clear()
    return jsonify({'ok': True})


# ---------- 文件上传 API ----------
def _allowed_file(filename, allowed_set):
    if not filename or '.' not in filename:
        return False
    return filename.rsplit('.', 1)[-1].lower() in allowed_set


@app.route('/api/upload', methods=['POST'])
@require_login
def api_upload():
    """上传文件。表单字段: file, bucket（second_hand/exam/canteen/forum）。
    图片 bucket 仅允许图片后缀；exam 仅允许附件后缀。返回 { code, message, url }。"""
    bucket = (request.form.get('bucket') or '').strip() or 'forum'
    if bucket not in UPLOAD_SUBDIRS:
        return jsonify({'code': -1, 'message': '无效的 bucket'}), 400
    subdir = UPLOAD_SUBDIRS[bucket]
    if 'file' not in request.files:
        return jsonify({'code': -1, 'message': '没有选择文件'}), 400
    f = request.files['file']
    if not f or not f.filename:
        return jsonify({'code': -1, 'message': '没有选择文件'}), 400
    if bucket == 'exam':
        allowed = ALLOWED_ATTACHMENT_EXTENSIONS
    else:
        allowed = ALLOWED_IMAGE_EXTENSIONS
    if not _allowed_file(f.filename, allowed):
        return jsonify({'code': -1, 'message': '不允许的文件类型'}), 400
    try:
        content = f.read()
    except Exception as e:
        return jsonify({'code': -1, 'message': '读取文件失败: {}'.format(str(e))}), 400
    if len(content) > MAX_FILE_SIZE:
        return jsonify({'code': -1, 'message': '文件过大（最大 {}MB）'.format(MAX_FILE_SIZE // (1024 * 1024))}), 400
    ext = f.filename.rsplit('.', 1)[-1].lower()
    save_name = '{}.{}'.format(uuid.uuid4().hex, ext)
    dir_path = os.path.join(UPLOAD_DIR, subdir)
    os.makedirs(dir_path, exist_ok=True)
    full_path = os.path.join(dir_path, save_name)
    try:
        with open(full_path, 'wb') as out:
            out.write(content)
    except Exception as e:
        return jsonify({'code': -1, 'message': '保存失败: {}'.format(str(e))}), 500
    url = '/uploads/{}/{}'.format(subdir, save_name)
    return jsonify({'code': 0, 'message': '上传成功', 'url': url})


@app.route('/uploads/<path:path>')
def serve_upload(path):
    """提供上传文件的访问（只读）。"""
    dir_path = os.path.dirname(os.path.join(UPLOAD_DIR, path))
    if not os.path.abspath(dir_path).startswith(os.path.abspath(UPLOAD_DIR)):
        return jsonify({'code': -1, 'message': '非法路径'}), 403
    filename = os.path.basename(path)
    return send_from_directory(dir_path, filename, as_attachment=False)


# ---------- 面试经验 API ----------
INTERVIEW_TYPES = ('秋招', '春招', '暑期实习', '日常实习')


def _forum_post_to_dict(p, mask=True):
    return {
        'id': p.id,
        'student_id': _mask_student_id(p.student_id) if mask else p.student_id,
        'title': p.title,
        'body': p.body,
        'company_id': p.company_id,
        'company_name': p.company.name if p.company else None,
        'interview_type': p.interview_type,
        'created_at': p.created_at.isoformat() if p.created_at else None,
    }


def _forum_comment_to_dict(c, mask=True):
    return {
        'id': c.id,
        'post_id': c.post_id,
        'parent_id': c.parent_id,
        'student_id': _mask_student_id(c.student_id) if mask else c.student_id,
        'content': c.content,
        'created_at': c.created_at.isoformat() if c.created_at else None,
    }


@app.route('/api/forum/interview/companies', methods=['GET'])
def api_forum_interview_companies():
    """公司分类列表"""
    companies = InterviewCompany.query.order_by(InterviewCompany.name).all()
    return jsonify({'code': 0, 'companies': [{'id': c.id, 'name': c.name} for c in companies]})


@app.route('/api/forum/interview/companies', methods=['POST'])
@require_login
def api_forum_interview_company_add():
    """用户添加公司名称"""
    data = request.json or {}
    name = (data.get('name') or '').strip()[:64]
    if not name:
        return jsonify({'code': -1, 'message': '请填写公司名称'}), 400
    if InterviewCompany.query.filter_by(name=name).first():
        return jsonify({'code': -1, 'message': '该公司已存在'}), 400
    c = InterviewCompany(name=name)
    db.session.add(c)
    db.session.commit()
    return jsonify({'code': 0, 'message': '添加成功', 'company': {'id': c.id, 'name': c.name}})


@app.route('/api/forum/interview/posts', methods=['GET'])
def api_forum_interview_posts():
    """帖子列表：?company_id=&interview_type=&page=1&size=20"""
    page = max(1, request.args.get('page', 1, type=int))
    size = min(50, max(1, request.args.get('size', 20, type=int)))
    company_id = request.args.get('company_id', type=int)
    interview_type = (request.args.get('interview_type') or '').strip()
    q = ForumPost.query.filter_by(board='interview')
    if company_id:
        q = q.filter(ForumPost.company_id == company_id)
    if interview_type and interview_type in INTERVIEW_TYPES:
        q = q.filter(ForumPost.interview_type == interview_type)
    q = q.order_by(ForumPost.created_at.desc())
    pagination = q.paginate(page=page, per_page=size)
    posts = [_forum_post_to_dict(p) for p in pagination.items]
    return jsonify({'code': 0, 'posts': posts, 'total': pagination.total, 'page': page, 'size': size})


@app.route('/api/forum/interview/posts/<int:post_id>', methods=['GET'])
def api_forum_interview_post_detail(post_id):
    """帖子详情与评论（评论按时间正序，楼中楼用 parent_id 关联）"""
    p = ForumPost.query.filter_by(id=post_id, board='interview').first_or_404()
    comments = ForumComment.query.filter_by(post_id=post_id).order_by(ForumComment.created_at).all()
    comment_list = [_forum_comment_to_dict(c) for c in comments]
    return jsonify({'code': 0, 'post': _forum_post_to_dict(p), 'comments': comment_list})


@app.route('/api/forum/interview/posts', methods=['POST'])
@require_login
def api_forum_interview_post_create():
    """发帖：title, body, company_id, interview_type"""
    data = request.json or {}
    title = (data.get('title') or '').strip()[:200]
    body = (data.get('body') or '').strip()[:50000]
    company_id = data.get('company_id')
    interview_type = (data.get('interview_type') or '').strip()
    if not title:
        return jsonify({'code': -1, 'message': '请填写标题'}), 400
    if company_id is not None and InterviewCompany.query.get(company_id) is None:
        return jsonify({'code': -1, 'message': '所选公司不存在'}), 400
    if interview_type and interview_type not in INTERVIEW_TYPES:
        return jsonify({'code': -1, 'message': '面试类型无效'}), 400
    p = ForumPost(
        student_id=session.get('student_id'),
        board='interview',
        title=title,
        body=body or None,
        company_id=company_id if company_id else None,
        interview_type=interview_type or None,
    )
    db.session.add(p)
    db.session.commit()
    _add_points(session.get('student_id'), 5)
    return jsonify({'code': 0, 'message': '发布成功', 'post': _forum_post_to_dict(p, mask=False)})


@app.route('/api/forum/interview/posts/<int:post_id>/comments', methods=['POST'])
@require_login
def api_forum_interview_comment_add(post_id):
    """发表评论：content, parent_id（可选，楼中楼）"""
    post = ForumPost.query.filter_by(id=post_id, board='interview').first_or_404()
    data = request.json or {}
    content = (data.get('content') or '').strip()[:2000]
    parent_id = data.get('parent_id')
    if not content:
        return jsonify({'code': -1, 'message': '请填写评论内容'}), 400
    if parent_id is not None:
        parent = ForumComment.query.filter_by(id=parent_id, post_id=post_id).first()
        if not parent:
            return jsonify({'code': -1, 'message': '父评论不存在'}), 400
    c = ForumComment(
        post_id=post_id,
        parent_id=parent_id if parent_id else None,
        student_id=session.get('student_id'),
        content=content,
    )
    db.session.add(c)
    db.session.commit()
    return jsonify({'code': 0, 'message': '评论成功', 'comment': _forum_comment_to_dict(c, mask=False)})


# ---------- 二手交易 API ----------
def _mask_student_id(sid):
    if not sid or len(sid) < 4:
        return '****'
    return sid[:2] + '****' + sid[-2:]


def _item_to_dict(item, mask=True):
    return {
        'id': item.id,
        'student_id': _mask_student_id(item.student_id) if mask else item.student_id,
        'title': item.title,
        'description': item.description or '',
        'price': float(item.price),
        'category': item.category or '',
        'status': item.status,
        'image_urls': item.image_urls or [],
        'contact': item.contact or '',
        'created_at': item.created_at.isoformat() if item.created_at else None,
    }


@app.route('/api/second-hand', methods=['GET'])
def api_second_hand_list():
    """列表：?page=1&size=20&category=书籍&status=on_sale&my=1(我的发布)&favorite=1(我的收藏)"""
    page = max(1, request.args.get('page', 1, type=int))
    size = min(50, max(1, request.args.get('size', 20, type=int)))
    category = (request.args.get('category') or '').strip()
    status = (request.args.get('status') or 'on_sale').strip()
    my_only = request.args.get('my', type=int) == 1
    fav_only = request.args.get('favorite', type=int) == 1
    sid = session.get('student_id')
    q = SecondHandItem.query
    if my_only and sid:
        q = q.filter(SecondHandItem.student_id == sid)
    elif fav_only and sid:
        fav_ids = [r.item_id for r in SecondHandFavorite.query.filter_by(student_id=sid).all()]
        if not fav_ids:
            return jsonify({'code': 0, 'items': [], 'total': 0, 'page': page, 'size': size})
        q = q.filter(SecondHandItem.id.in_(fav_ids))
    if category:
        q = q.filter(SecondHandItem.category == category)
    if status:
        q = q.filter(SecondHandItem.status == status)
    q = q.order_by(SecondHandItem.created_at.desc())
    pagination = q.paginate(page=page, per_page=size)
    items = []
    for i in pagination.items:
        d = _item_to_dict(i)
        if sid:
            d['is_owner'] = i.student_id == sid
            d['is_favorited'] = SecondHandFavorite.query.filter_by(student_id=sid, item_id=i.id).first() is not None
        items.append(d)
    return jsonify({'code': 0, 'items': items, 'total': pagination.total, 'page': page, 'size': size})


@app.route('/api/second-hand', methods=['POST'])
@require_login
def api_second_hand_create():
    """发布商品。JSON: title, description, price, category, image_urls[], contact"""
    data = request.json or {}
    title = (data.get('title') or '').strip()
    if not title:
        return jsonify({'code': -1, 'message': '请填写标题'}), 400
    price = data.get('price')
    try:
        price = float(price)
    except (TypeError, ValueError):
        return jsonify({'code': -1, 'message': '请填写有效价格'}), 400
    if price < 0:
        return jsonify({'code': -1, 'message': '价格不能为负'}), 400
    item = SecondHandItem(
        student_id=session.get('student_id'),
        title=title[:200],
        description=(data.get('description') or '').strip()[:5000],
        price=price,
        category=(data.get('category') or '').strip()[:32] or None,
        image_urls=data.get('image_urls') if isinstance(data.get('image_urls'), list) else [],
        contact=(data.get('contact') or '').strip()[:100] or None,
        status='on_sale',
    )
    db.session.add(item)
    db.session.commit()
    _add_points(session.get('student_id'), 5)
    return jsonify({'code': 0, 'message': '发布成功', 'id': item.id})


@app.route('/api/second-hand/<int:item_id>', methods=['GET'])
def api_second_hand_detail(item_id):
    item = SecondHandItem.query.get_or_404(item_id)
    d = _item_to_dict(item)
    sid = session.get('student_id')
    if sid:
        d['is_owner'] = item.student_id == sid
        d['is_favorited'] = SecondHandFavorite.query.filter_by(student_id=sid, item_id=item_id).first() is not None
    else:
        d['is_owner'] = False
        d['is_favorited'] = False
    return jsonify({'code': 0, 'item': d})


@app.route('/api/second-hand/<int:item_id>', methods=['PATCH'])
@require_login
def api_second_hand_update(item_id):
    """下架/已出：JSON { "status": "off_shelf"|"sold" }，仅作者可操作"""
    item = SecondHandItem.query.get_or_404(item_id)
    if item.student_id != session.get('student_id'):
        return jsonify({'code': -1, 'message': '无权限'}), 403
    data = request.json or {}
    status = (data.get('status') or '').strip()
    if status in ('off_shelf', 'sold'):
        item.status = status
        db.session.commit()
    return jsonify({'code': 0, 'message': '已更新', 'item': _item_to_dict(item, mask=False)})


@app.route('/api/second-hand/<int:item_id>', methods=['DELETE'])
@require_login
def api_second_hand_delete(item_id):
    """删除商品，仅作者可操作"""
    item = SecondHandItem.query.get_or_404(item_id)
    if item.student_id != session.get('student_id'):
        return jsonify({'code': -1, 'message': '无权限'}), 403
    db.session.delete(item)
    db.session.commit()
    return jsonify({'code': 0, 'message': '已删除'})


@app.route('/api/second-hand/<int:item_id>/favorite', methods=['POST'])
@require_login
def api_second_hand_favorite_add(item_id):
    """收藏"""
    item = SecondHandItem.query.get_or_404(item_id)
    sid = session.get('student_id')
    if SecondHandFavorite.query.filter_by(student_id=sid, item_id=item_id).first():
        return jsonify({'code': 0, 'message': '已收藏', 'favorited': True})
    fav = SecondHandFavorite(student_id=sid, item_id=item_id)
    db.session.add(fav)
    db.session.commit()
    return jsonify({'code': 0, 'message': '收藏成功', 'favorited': True})


@app.route('/api/second-hand/<int:item_id>/favorite', methods=['DELETE'])
@require_login
def api_second_hand_favorite_remove(item_id):
    """取消收藏"""
    SecondHandFavorite.query.filter_by(student_id=session.get('student_id'), item_id=item_id).delete()
    db.session.commit()
    return jsonify({'code': 0, 'message': '已取消收藏', 'favorited': False})


# ---------- 西交吃什么 API（用户端：仅展示 approved）----------
def _canteen_to_dict(c):
    return {'id': c.id, 'name': c.name, 'floors_count': c.floors_count}


def _window_to_dict(w):
    return {'id': w.id, 'canteen_id': w.canteen_id, 'floor': w.floor, 'name': w.name, 'image_urls': w.image_urls or []}


def _review_to_dict(r):
    return {
        'id': r.id, 'window_id': r.window_id, 'dish_id': r.dish_id,
        'student_id': _mask_student_id(r.student_id),
        'rating': r.rating, 'content': r.content or '', 'image_urls': r.image_urls or [],
        'created_at': r.created_at.isoformat() if r.created_at else None,
    }


def _dish_to_dict(d):
    return {
        'id': d.id, 'window_id': d.window_id, 'name': d.name,
        'price': float(d.price) if d.price is not None else None,
        'price_min': float(d.price_min) if d.price_min is not None else None,
        'price_max': float(d.price_max) if d.price_max is not None else None,
    }


@app.route('/api/eat/canteens', methods=['GET'])
def api_eat_canteens():
    """已通过的餐厅列表"""
    q = Canteen.query.filter_by(status='approved').order_by(Canteen.id)
    return jsonify({'code': 0, 'canteens': [_canteen_to_dict(c) for c in q.all()]})


@app.route('/api/eat/canteens/<int:canteen_id>', methods=['GET'])
def api_eat_canteen_detail(canteen_id):
    """餐厅详情：楼层 -> 窗口 -> 菜品（含菜品评价）、窗口图片、窗口评价（仅 approved）。
    按餐厅 floors_count 补全空楼层，使 UI 能显示三层（如惠风苑）即使某层暂无窗口。"""
    c = Canteen.query.filter_by(id=canteen_id, status='approved').first_or_404()
    floors = {}
    for w in c.windows.filter(CanteenWindow.status == 'approved').order_by(CanteenWindow.floor, CanteenWindow.id):
        fid = w.floor
        if fid not in floors:
            floors[fid] = []
        window_reviews = [ _review_to_dict(r) for r in w.reviews.filter(CanteenReview.dish_id.is_(None)).order_by(CanteenReview.created_at.desc()).limit(50) ]
        dishes_with_reviews = []
        for d in w.dishes.filter(CanteenDish.status == 'approved').order_by(CanteenDish.sort_order, CanteenDish.id):
            dish_dict = _dish_to_dict(d)
            dish_dict['reviews'] = [ _review_to_dict(r) for r in d.reviews.order_by(CanteenReview.created_at.desc()).limit(30) ]
            dishes_with_reviews.append(dish_dict)
        floors[fid].append({
            'window': _window_to_dict(w),
            'dishes': dishes_with_reviews,
            'reviews': window_reviews,
        })
    # 同层同名窗口合并为一项展示；同一窗口内同菜名只保留一项（按菜名去重，只显示一条）
    for fid in list(floors.keys()):
        by_name = {}
        for block in floors[fid]:
            name = (block['window'] or {}).get('name') or ''
            if name not in by_name:
                by_name[name] = {'window': block['window'], 'dishes': [], 'reviews': []}
            by_name[name]['dishes'].extend(block.get('dishes') or [])
            by_name[name]['reviews'].extend(block.get('reviews') or [])
        for name in by_name:
            merged = []
            seen = set()
            for d in by_name[name]['dishes']:
                key = (d.get('name') or '').strip()
                if key and key not in seen:
                    seen.add(key)
                    merged.append(d)
                elif not key:
                    merged.append(d)
            by_name[name]['dishes'] = merged
        floors[fid] = list(by_name.values())
    # 按 floors_count 补全 1..N 层，空层返回 []，保证惠风苑等显示三层
    n = max(1, c.floors_count or 1)
    for i in range(1, n + 1):
        if i not in floors:
            floors[i] = []
    return jsonify({'code': 0, 'canteen': _canteen_to_dict(c), 'floors': floors})


@app.route('/api/eat/submit', methods=['POST'])
@require_login
def api_eat_submit():
    """用户提交：可选已有餐厅(canteen_id)或新建(canteen_name)；可选已有窗口(window_id)或新建(window_name)；
    楼层(floor)；菜品为 [{dish_id}] 引用已有或 [{name, price}] 新建。创建为 pending，待管理员审核。"""
    data = request.json or {}
    canteen_id = data.get('canteen_id')
    canteen_name = (data.get('canteen_name') or '').strip()[:64]
    window_id = data.get('window_id')
    window_name = (data.get('window_name') or '').strip()[:64]
    floor = data.get('floor')
    dishes = data.get('dishes')
    if not isinstance(dishes, list):
        dishes = []

    try:
        floor = int(floor) if floor is not None else 1
    except (TypeError, ValueError):
        floor = 1

    sid = session.get('student_id')
    c = None
    w = None

    if canteen_id:
        c = Canteen.query.filter_by(id=canteen_id, status='approved').first()
        if not c:
            return jsonify({'code': -1, 'message': '所选餐厅不存在或未通过'}), 400
    if not c and canteen_name:
        c = Canteen(name=canteen_name, floors_count=max(1, floor), status='pending', submitted_by=sid)
        db.session.add(c)
        db.session.flush()
    if not c:
        return jsonify({'code': -1, 'message': '请选择已有餐厅或填写新餐厅名'}), 400

    if window_id:
        w = CanteenWindow.query.filter_by(id=window_id, canteen_id=c.id, status='approved').first()
        if not w:
            return jsonify({'code': -1, 'message': '所选窗口不存在或未通过'}), 400
        if w.floor != floor:
            return jsonify({'code': -1, 'message': '所选窗口与楼层不一致'}), 400
    if not w and window_name:
        w = CanteenWindow(canteen_id=c.id, floor=floor, name=window_name, status='pending', submitted_by=sid)
        db.session.add(w)
        db.session.flush()
    if not w:
        return jsonify({'code': -1, 'message': '请选择已有窗口或填写新窗口名'}), 400

    dish_order = 0
    new_dish_count = 0
    for row in dishes[:50]:
        if row.get('dish_id') is not None:
            try:
                did = int(row.get('dish_id'))
            except (TypeError, ValueError):
                continue
            d = CanteenDish.query.filter_by(id=did, window_id=w.id, status='approved').first()
            if d:
                dish_order += 1
                continue
        name = (row.get('name') or '').strip()[:100]
        if not name:
            continue
        try:
            p = float(row.get('price', 0)) if row.get('price') is not None else None
        except (TypeError, ValueError):
            p = None
        dish = CanteenDish(window_id=w.id, name=name, price=p, sort_order=dish_order, status='pending', submitted_by=sid)
        db.session.add(dish)
        dish_order += 1
        new_dish_count += 1

    if c.status == 'pending':
        c.floors_count = max(c.floors_count or 1, floor)
    db.session.commit()
    if new_dish_count > 0:
        _add_points(sid, 5 * new_dish_count)
    return jsonify({'code': 0, 'message': '提交成功，等待管理员审核', 'canteen_id': c.id})


@app.route('/api/eat/windows/<int:window_id>/images', methods=['POST'])
@require_login
def api_eat_window_upload_image(window_id):
    """为窗口上传图片，追加到 window.image_urls"""
    w = CanteenWindow.query.filter_by(id=window_id, status='approved').first_or_404()
    if 'file' not in request.files:
        return jsonify({'code': -1, 'message': '没有选择文件'}), 400
    f = request.files['file']
    if not f or not f.filename:
        return jsonify({'code': -1, 'message': '没有选择文件'}), 400
    if not _allowed_file(f.filename, ALLOWED_IMAGE_EXTENSIONS):
        return jsonify({'code': -1, 'message': '仅支持图片格式'}), 400
    try:
        content = f.read()
    except Exception as e:
        return jsonify({'code': -1, 'message': '读取文件失败'}), 400
    if len(content) > MAX_FILE_SIZE:
        return jsonify({'code': -1, 'message': '文件过大'}), 400
    ext = f.filename.rsplit('.', 1)[-1].lower()
    save_name = '{}.{}'.format(uuid.uuid4().hex, ext)
    subdir = UPLOAD_SUBDIRS['canteen']
    dir_path = os.path.join(UPLOAD_DIR, subdir)
    os.makedirs(dir_path, exist_ok=True)
    full_path = os.path.join(dir_path, save_name)
    try:
        with open(full_path, 'wb') as out:
            out.write(content)
    except Exception:
        return jsonify({'code': -1, 'message': '保存失败'}), 500
    url = '/uploads/{}/{}'.format(subdir, save_name)
    urls = list(w.image_urls or [])
    urls.append(url)
    w.image_urls = urls
    db.session.commit()
    return jsonify({'code': 0, 'message': '上传成功', 'url': url, 'image_urls': urls})


@app.route('/api/eat/review', methods=['POST'])
@require_login
def api_eat_review():
    """对菜品或窗口评价。JSON: dish_id 或 window_id, rating 1-5, content, image_urls[]"""
    data = request.json or {}
    dish_id = data.get('dish_id')
    window_id = data.get('window_id')
    rating = data.get('rating')
    try:
        rating = int(rating)
    except (TypeError, ValueError):
        rating = None
    if rating is None or rating < 1 or rating > 5:
        return jsonify({'code': -1, 'message': '请选择 1-5 星'}), 400
    if not dish_id and not window_id:
        return jsonify({'code': -1, 'message': '请指定菜品或窗口'}), 400
    sid = session.get('student_id')
    r = CanteenReview(
        dish_id=dish_id if dish_id else None,
        window_id=window_id if window_id else None,
        student_id=sid,
        rating=rating,
        content=(data.get('content') or '').strip()[:1000] or None,
        image_urls=data.get('image_urls') if isinstance(data.get('image_urls'), list) else None,
    )
    db.session.add(r)
    db.session.commit()
    return jsonify({'code': 0, 'message': '评价成功'})


# ---------- 管理员 ----------
@app.route('/admin')
@require_login
@require_admin
def admin_index():
    return render_template('admin_index.html', student_id=session.get('student_id'))


@app.route('/admin/eat')
@require_login
@require_admin
def admin_eat():
    return render_template('admin_eat.html', student_id=session.get('student_id'))


@app.route('/api/admin/eat/pending', methods=['GET'])
@require_login
@require_admin
def api_admin_eat_pending():
    """待审核：① 餐厅/窗口/菜品整条 pending 链；② 已通过餐厅+已通过窗口下仅菜品 pending（如用户只在已有窗口下新增猪肘饭）"""
    out = []
    # ① 整条链都是 pending：新餐厅 + 新窗口 + 新菜品
    canteens_pending = Canteen.query.filter_by(status='pending').order_by(Canteen.created_at.desc()).all()
    for c in canteens_pending:
        wins = c.windows.filter(CanteenWindow.status == 'pending').all()
        for w in wins:
            dish_list = w.dishes.filter(CanteenDish.status == 'pending').all()
            out.append({
                'canteen': _canteen_to_dict(c), 'canteen_id': c.id, 'submitted_by': c.submitted_by,
                'window': _window_to_dict(w), 'window_id': w.id,
                'dishes': [_dish_to_dict(d) for d in dish_list], 'dish_ids': [d.id for d in dish_list],
            })
    # ② 已通过餐厅、待审核窗口（如用户新增的「三楼窗口」「兰州拉面窗口」）
    canteens_approved = Canteen.query.filter_by(status='approved').order_by(Canteen.id).all()
    for c in canteens_approved:
        for w in c.windows.filter(CanteenWindow.status == 'pending').all():
            dish_list = w.dishes.filter(CanteenDish.status == 'pending').all()
            out.append({
                'canteen': _canteen_to_dict(c), 'canteen_id': c.id, 'submitted_by': w.submitted_by,
                'window': _window_to_dict(w), 'window_id': w.id,
                'dishes': [_dish_to_dict(d) for d in dish_list], 'dish_ids': [d.id for d in dish_list],
            })
    # ③ 已通过餐厅、已通过窗口下，仅菜品为 pending（如猪肘饭）
    for c in canteens_approved:
        for w in c.windows.filter(CanteenWindow.status == 'approved').all():
            dish_list = w.dishes.filter(CanteenDish.status == 'pending').all()
            if not dish_list:
                continue
            out.append({
                'canteen': _canteen_to_dict(c), 'canteen_id': c.id, 'submitted_by': getattr(dish_list[0], 'submitted_by', None),
                'window': _window_to_dict(w), 'window_id': w.id,
                'dishes': [_dish_to_dict(d) for d in dish_list], 'dish_ids': [d.id for d in dish_list],
            })
    return jsonify({'code': 0, 'pending': out})


@app.route('/api/admin/eat/approve', methods=['POST'])
@require_login
@require_admin
def api_admin_eat_approve():
    """审核通过：JSON { canteen_id, window_id, dish_ids[] }，将对应项 status 改为 approved"""
    data = request.json or {}
    canteen_id = data.get('canteen_id')
    window_id = data.get('window_id')
    dish_ids = data.get('dish_ids') or []
    if not canteen_id or not window_id:
        return jsonify({'code': -1, 'message': '缺少 canteen_id 或 window_id'}), 400
    from datetime import datetime
    now = datetime.utcnow()
    sid = session.get('student_id')
    c = Canteen.query.get(canteen_id)
    w = CanteenWindow.query.filter_by(id=window_id, canteen_id=canteen_id).first()
    if not c or not w:
        return jsonify({'code': -1, 'message': '记录不存在'}), 404
    c.status = 'approved'
    c.reviewed_at = now
    c.reviewed_by = sid
    w.status = 'approved'
    w.reviewed_at = now
    w.reviewed_by = sid
    for did in dish_ids:
        d = CanteenDish.query.filter_by(id=did, window_id=window_id).first()
        if d:
            d.status = 'approved'
            d.reviewed_at = now
            d.reviewed_by = sid
    db.session.commit()
    return jsonify({'code': 0, 'message': '已通过'})


@app.route('/api/admin/eat/reject', methods=['POST'])
@require_login
@require_admin
def api_admin_eat_reject():
    """审核拒绝。若传 dish_ids：仅拒绝这些菜品，不动餐厅和窗口；否则拒绝整条窗口（仅当窗口/餐厅为 pending 时才把餐厅设为 rejected）。"""
    data = request.json or {}
    canteen_id = data.get('canteen_id')
    window_id = data.get('window_id')
    dish_ids = data.get('dish_ids') or []
    if not canteen_id or not window_id:
        return jsonify({'code': -1, 'message': '缺少 canteen_id 或 window_id'}), 400
    c = Canteen.query.get(canteen_id)
    w = CanteenWindow.query.filter_by(id=window_id, canteen_id=canteen_id).first()
    if not c or not w:
        return jsonify({'code': -1, 'message': '记录不存在'}), 404
    if dish_ids:
        # 仅拒绝勾选的菜品，餐厅和窗口保持原状（惠风苑等已通过餐厅不会消失）
        for did in dish_ids:
            d = CanteenDish.query.filter_by(id=did, window_id=window_id).first()
            if d:
                d.status = 'rejected'
        db.session.commit()
        return jsonify({'code': 0, 'message': '已拒绝所选菜品'})
    # 拒绝整条窗口（新餐厅/新窗口场景）
    w.status = 'rejected'
    for d in w.dishes:
        d.status = 'rejected'
    if c.status == 'pending':
        c.status = 'rejected'
    db.session.commit()
    return jsonify({'code': 0, 'message': '已拒绝'})


@app.route('/api/admin/eat/create', methods=['POST'])
@require_login
@require_admin
def api_admin_eat_create():
    """管理员直接创建（approved）：可选已有餐厅(canteen_id)或新建(canteen_name)；
    可选已有窗口(window_id)或新建(window_name)；楼层(floor)；菜品为 [{dish_id}] 或 [{name, price}]"""
    data = request.json or {}
    canteen_id = data.get('canteen_id')
    canteen_name = (data.get('canteen_name') or '').strip()[:64]
    window_id = data.get('window_id')
    window_name = (data.get('window_name') or '').strip()[:64]
    floor = data.get('floor')
    dishes = data.get('dishes') or []
    try:
        floor = int(floor) if floor is not None else 1
    except (TypeError, ValueError):
        floor = 1

    c = None
    w = None
    if canteen_id:
        c = Canteen.query.filter_by(id=canteen_id, status='approved').first()
        if not c:
            return jsonify({'code': -1, 'message': '所选餐厅不存在'}), 400
    if not c and canteen_name:
        c = Canteen(name=canteen_name, floors_count=max(1, floor), status='approved')
        db.session.add(c)
        db.session.flush()
    if not c:
        return jsonify({'code': -1, 'message': '请选择已有餐厅或填写新餐厅名'}), 400

    if window_id:
        w = CanteenWindow.query.filter_by(id=window_id, canteen_id=c.id, status='approved').first()
        if not w:
            return jsonify({'code': -1, 'message': '所选窗口不存在'}), 400
        if w.floor != floor:
            return jsonify({'code': -1, 'message': '所选窗口与楼层不一致'}), 400
    if not w and window_name:
        w = CanteenWindow(canteen_id=c.id, floor=floor, name=window_name, status='approved')
        db.session.add(w)
        db.session.flush()
    if not w:
        return jsonify({'code': -1, 'message': '请选择已有窗口或填写新窗口名'}), 400

    dish_order = 0
    for row in dishes[:50]:
        if row.get('dish_id') is not None:
            try:
                did = int(row.get('dish_id'))
            except (TypeError, ValueError):
                continue
            d = CanteenDish.query.filter_by(id=did, window_id=w.id, status='approved').first()
            if d:
                dish_order += 1
                continue
        name = (row.get('name') or '').strip()[:100]
        if not name:
            continue
        try:
            p = float(row.get('price', 0)) if row.get('price') is not None else None
        except (TypeError, ValueError):
            p = None
        db.session.add(CanteenDish(window_id=w.id, name=name, price=p, sort_order=dish_order, status='approved'))
        dish_order += 1

    c.floors_count = max(c.floors_count or 1, floor)
    db.session.commit()
    return jsonify({'code': 0, 'message': '创建成功', 'canteen_id': c.id})


def seed_eat_canteens():
    """预置五个餐厅及每层默认窗口：惠风苑(3层)、朗清苑(3层)、同和苑(3层)、和鸣苑(3层)、涵英楼负一(1层)。"""
    defaults = [
        ('惠风苑', 3, [1, 2, 3]),
        ('朗清苑', 3, [1, 2, 3]),
        ('同和苑', 3, [1, 2, 3]),
        ('和鸣苑', 3, [1, 2, 3]),
        ('涵英楼负一', 1, [-1]),
    ]
    floor_labels = {-1: '负一层', 1: '一楼', 2: '二楼', 3: '三楼'}
    with app.app_context():
        for item in defaults:
            name = item[0]
            floors_count = item[1]
            floors = item[2]
            c = Canteen.query.filter_by(name=name).first()
            if not c:
                c = Canteen(name=name, floors_count=floors_count, status='approved')
                db.session.add(c)
                db.session.flush()
            elif name == '惠风苑' and c.floors_count != 3:
                c.floors_count = 3
                db.session.flush()
            if c.status == 'rejected' and name in ('惠风苑', '朗清苑', '同和苑', '和鸣苑', '涵英楼负一'):
                c.status = 'approved'
                for w in c.windows:
                    w.status = 'approved'
                    for d in w.dishes:
                        d.status = 'approved'
                db.session.flush()
            if c.windows.count() > 0:
                continue
            for f in floors:
                label = floor_labels.get(f, str(f) + ' 层')
                w = CanteenWindow(
                    canteen_id=c.id,
                    floor=f,
                    name=label + '窗口',
                    status='approved',
                )
                db.session.add(w)
        db.session.commit()


def seed_interview_companies_and_example_post():
    """预置面试经验公司分类及一条示例面经"""
    default_companies = ['阿里巴巴', '腾讯', '字节跳动', '美团', '京东', '百度', '国家电网']
    example_body = '''## 一面
**时间线**：2025年9月初投递 → 9月中旬笔试 → 9月底一面

**面试内容**：
- 自我介绍 + 项目深挖（大模型训练与推理优化）
- 手写：实现 multi-head attention 的 forward
- 问：Transformer 与 CNN 在长序列上的优劣；如何做长文本压缩
- 算法题：LeetCode 中等难度一道（序列相关）
- 反问环节

---

## 二面
**时间线**：一面后约一周

**面试内容**：
- 项目中的多卡训练、混合精度、梯度累积
- 大模型推理加速：KV Cache、量化、批处理
- 手写：简单 beam search 或 top-p sampling
- 场景题：如何设计一个面向 C 端的对话系统
- 反问

---

## 三面（HR/技术主管）
**时间线**：二面后约 5 天

**面试内容**：
- 职业规划、为什么选百度、为什么做算法
- 对部门业务的了解
- 期望薪资与到岗时间
- 少量技术广度问题（模型演进、业界动态）

整体体验：流程规范，面试官友好，建议提前准备好项目细节和常见大模型/算法题。'''

    with app.app_context():
        for name in default_companies:
            if InterviewCompany.query.filter_by(name=name).first():
                continue
            db.session.add(InterviewCompany(name=name))
        db.session.commit()

        if ForumPost.query.filter_by(board='interview').first():
            return
        baidu = InterviewCompany.query.filter_by(name='百度').first()
        post = ForumPost(
            student_id='0000000000',
            board='interview',
            title='25秋招面试百度大模型算法工程师面经',
            body=example_body,
            company_id=baidu.id if baidu else None,
            interview_type='秋招',
        )
        db.session.add(post)
        db.session.commit()


if __name__ == '__main__':
    os.makedirs(os.path.join(BASE_DIR, 'logs'), exist_ok=True)
    os.makedirs(os.path.join(BASE_DIR, 'instance'), exist_ok=True)
    for sub in UPLOAD_SUBDIRS.values():
        os.makedirs(os.path.join(UPLOAD_DIR, sub), exist_ok=True)
    # 启动前自动执行数据库迁移，确保 interview_company 等表存在
    with app.app_context():
        from flask_migrate import upgrade as migrate_upgrade
        migrate_upgrade()
    seed_eat_canteens()
    seed_interview_companies_and_example_post()
    app.run(host='0.0.0.0', port=8894, debug=True, use_reloader=False)

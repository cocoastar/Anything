#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re
import json
import base64
import traceback
import threading
import urllib.parse as urlparse
from urllib.parse import urlencode, urljoin
import requests
from bs4 import BeautifulSoup

# 如需 RSA 加密（视 CAS 实际要求决定）
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_v1_5

from http.cookies import SimpleCookie

# ===================== 配置区 =====================

# 账号信息（按需替换）
# USERNAME = "3124336006"
# PASSWORD = "ljy031021"  # 替换为真实密码


# USERNAME = "3124158013"
# PASSWORD = "2205110932wc"  # 替换为真实密码


CLIENT_ID = "1740"
STATE = "1234"

# 抓包中的 service（完整保留）
SERVICE_URL = (
    "https://login.xjtu.edu.cn/cas/oauth2.0/callbackAuthorize?"
    "client_id=1740&"
    "redirect_uri=https%3A%2F%2Forg.xjtu.edu.cn%2Fopenplatform%2Foauth%2Fauthorizesw%3Fredirect_uri%3Dhttps%3A%2F%2Fipahw.xjtu.edu.cn%2Fsso%2Fcallback&"
    "response_type=code&"
    "client_name=CasOAuthClient"
)

LOGIN_URL_WITH_SERVICE = "https://login.xjtu.edu.cn/cas/login?service=" + urlparse.quote(SERVICE_URL, safe="")
LOGIN_PAGE_BASE = "https://login.xjtu.edu.cn/cas/login"

# 若系统需要 RSA 加密密码，请填入 CAS 公钥（如不需要可将 encrypt_password=False）
PUBLIC_KEY_PEM = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA2u2v/bjSIVsaxCBBxkjWf7LpmsjuhFJUJE7MYTn9hBcDXlK4smgtNoMqmGz4ztg5t1h+h0fqrJT3WkdoLV/FKC8OwElTe+p+YLqA6/PgmGtsffcQmAW0eye5NygiWM+B0tO69ML6jNLpAWAvXwod5kr/k7qsM1DGTux+e7bjdFz/IA8vOZx3IlGHnX+RE/uBJUwPXHnLPw5pQSwkWwfpPwxMrgzwik6htqRHF2c7Z+pJToXbrIJWD5nmRiU6jzgu8ncLqbMb3WNOKSodcEnlUpTH/ApH56IOJHWpq3mxJL9DaUaWzjziR93wjlyvR1K4VM7TLqD35CVZQaoE5FWgZwIDAQAB
-----END PUBLIC KEY-----"""

# 可选：MFA 相关接口（以实际为准）
MFA_DETECT_URL = "https://login.xjtu.edu.cn/cas/mfa/detect"
MFA_PHONE_SEND_URL = "https://login.xjtu.edu.cn/cas/mfa/securePhone/send"
MFA_PHONE_VALIDATE_URL = "https://login.xjtu.edu.cn/cas/mfa/securePhone/validate"

# 业务换 token（如需）
IPAHW_CODE_LOGIN = "https://ipahw.xjtu.edu.cn/szjy-boot/sso/codeLogin"

# 如需代理（可选）
PROXIES = {
    # "http": "http://127.0.0.1:7890",
    # "https": "http://127.0.0.1:7890",
}

# 抓包对齐的公共请求头（不包含 Cookie，交给 session 管理）
COMMON_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Upgrade-Insecure-Requests": "1",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36",
    "sec-ch-ua": '"Chromium";v="142", "Google Chrome";v="142", "Not_A Brand";v="99"',
    "sec-ch-ua-platform": '"Windows"',
    "sec-ch-ua-mobile": "?0",
}

def debug_print(title: str, content: str = ""):
    print(f"[DEBUG] {title}")
    if content is not None and content != "":
        print(content)
    print("-" * 80)

def save_text(filename: str, text: str):
    try:
        with open(filename, "w", encoding="utf-8") as f:
            f.write(text)
        # # debug_print("Saved", filename)
    except Exception as e:
        debug_print("Save failed", f"{filename} :: {e}")

def dump_cookies(session: requests.Session) -> str:
    try:
        cookies_list = []
        for c in session.cookies:
            cookies_list.append(f"{c.name}={c.value}; domain={c.domain}; path={c.path}")
        return "; ".join(cookies_list)
    except Exception:
        return "(cookie dump failed)"

def merge_set_cookie_to_session(session: requests.Session, set_cookie_header, domain_fallback: str):
    if not set_cookie_header:
        return
    raw_list = []
    if isinstance(set_cookie_header, (list, tuple)):
        raw_list = list(set_cookie_header)
    else:
        raw_list = [set_cookie_header]
    for raw in raw_list:
        sc = SimpleCookie()
        try:
            sc.load(raw)
        except Exception:
            continue
        for key, morsel in sc.items():
            value = morsel.value
            c_domain = morsel["domain"] or domain_fallback
            c_path = morsel["path"] or "/"
            secure_flag = bool(morsel["secure"])
            rest_kwargs = {}
            if morsel["httponly"]:
                rest_kwargs["HttpOnly"] = True
            if morsel["samesite"]:
                rest_kwargs["SameSite"] = morsel["samesite"]
            session.cookies.set(
                name=key,
                value=value,
                domain=c_domain,
                path=c_path,
                secure=secure_flag,
                rest=rest_kwargs
            )

def add_tracking_cookies(session: requests.Session):
    # 仅添加统计类 Cookie，避免覆盖/干扰 SESSION/TGC
    session.cookies.set("Hm_lvt_d605d8df6bf5ca8a54fe078683196518", "1763134324", domain="login.xjtu.edu.cn", path="/")
    session.cookies.set("Hm_lpvt_d605d8df6bf5ca8a54fe078683196518", "1763134324", domain="login.xjtu.edu.cn", path="/")
    session.cookies.set("HMACCOUNT", "1BD43D45C66D6726", domain="login.xjtu.edu.cn", path="/")

def ensure_session_cookie(session: requests.Session, service_url_quoted: str):
    # 通过预热 GET 从服务端获取 SESSION（首选方案）
    url = "https://login.xjtu.edu.cn/cas/login?service=" + service_url_quoted
    r = session.get(
        url,
        headers={**COMMON_HEADERS, "Referer": "https://ipahw.xjtu.edu.cn/"},
        allow_redirects=False,
        timeout=15
    )
    merge_set_cookie_to_session(session, r.headers.get("Set-Cookie"), "login.xjtu.edu.cn")
    # # debug_print("ensure_session_cookie GET login", f"status={r.status_code} has_SESSION={any(c.name=='SESSION' for c in session.cookies if c.domain.endswith('xjtu.edu.cn'))}")
    # 不自造 SESSION，保持与服务端一致性
    return r

def rsa_encrypt_with_public_key(pem_public_key: str, plaintext: str) -> str:
    key = RSA.import_key(pem_public_key)
    cipher = PKCS1_v1_5.new(key)
    ct = cipher.encrypt(plaintext.encode("utf-8"))
    b64 = base64.b64encode(ct).decode("utf-8")
    return "__RSA__" + b64  # 如现场不需要前缀，可去掉

def extract_execution(html: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    inp = soup.find("input", attrs={"name": "execution"})
    if inp and inp.has_attr("value"):
        return inp["value"]
    m = re.search(
        r'<input[^>]*name=["\']execution["\'][^>]*value=["\']([^"\']+)["\']',
        html or "", flags=re.I | re.S
    )
    if m:
        return m.group(1)
    raise RuntimeError("execution not found in login page")

def get_fp_visitor_id() -> str:
    # 可按站点真实计算逻辑获取，这里使用固定占位
    return "935f952ba19740e122874dc56ab50641"
    return "935f952ba19740e122874dc56ab50645"

def page_indicates_mfa(html: str) -> bool:
    return any(k in (html or "") for k in [
        "当前登录环境异常，需通过安全验证",
        "安全手机", "安全邮箱", "OTP", "超级APP", "二维码", "人脸识别",
    ])

def mfa_detect(session: requests.Session, username: str, enc_pwd: str, fp_id: str) -> dict:
    data = {
        "username": username,
        "password": enc_pwd,
        "fpVisitorId": fp_id
    }
    r = session.post(
        MFA_DETECT_URL,
        headers={
            **COMMON_HEADERS,
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": LOGIN_PAGE_BASE,
        },
        data=urlencode(data),
        timeout=15
    )
    # # debug_print("MFA detect", f"{r.status_code} {r.text[:400] if r.text else ''}")
    try:
        j = r.json()
        d = j.get("data") or {}
        return {"need": d.get("need", False), "state": d.get("state", "")}
    except Exception:
        return {"need": False, "state": ""}

def mfa_phone_send(session: requests.Session, mfa_state: str) -> bool:
    r = session.post(
        MFA_PHONE_SEND_URL,
        headers={
            **COMMON_HEADERS,
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": LOGIN_PAGE_BASE,
        },
        data=urlencode({"mfaState": mfa_state}),
        timeout=15
    )
    # # debug_print("MFA send", f"{r.status_code} {r.text[:200] if r.text else ''}")
    try:
        j = r.json()
        return r.status_code == 200 and (j.get("code") in (0, None))
    except Exception:
        return r.status_code == 200

def mfa_phone_validate(session: requests.Session, mfa_state: str, sms_code: str):
    r = session.post(
        MFA_PHONE_VALIDATE_URL,
        headers={
            **COMMON_HEADERS,
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": LOGIN_PAGE_BASE,
        },
        data=urlencode({"mfaState": mfa_state, "code": sms_code.strip()}),
        timeout=20,
        allow_redirects=False
    )
    # # debug_print("MFA validate", f"{r.status_code} headers={dict(r.headers)}")
    if r.status_code in (302, 303) and r.headers.get("Location"):
        return True, urljoin(LOGIN_PAGE_BASE, r.headers["Location"])
    try:
        j = r.json()
        if j.get("code") == 0:
            return True, (j.get("data") or {}).get("redirectUrl")
    except Exception:
        pass
    return False, None


# === 新增/替换的关键函数 ===

# 用于「开始」后需短信验证时暂存状态，供 submit_verify 继续完成登录
_pending_get_token = {}
_pending_lock = threading.Lock()


def _mfa_secure_phone_init_send(session: requests.Session, mfa_state: str):
    """initByType + send，返回 (gid, secure_phone_mask, attest_base)。不读 stdin、不调 valid。"""
    if not mfa_state:
        raise RuntimeError("缺少 mfa_state，无法进行 securephone 初始化")
    init_url = f"https://login.xjtu.edu.cn/cas/mfa/initByType/securephone?state={urlparse.quote(mfa_state, safe='')}"
    headers = {
        **COMMON_HEADERS,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": LOGIN_URL_WITH_SERVICE,
    }
    r_init = session.get(init_url, headers=headers, timeout=15)
    if r_init.status_code != 200:
        raise RuntimeError("securephone initByType 非 200")
    j = r_init.json()
    if (j.get("code") != 0) or (not j.get("data")):
        raise RuntimeError(f"securephone initByType 返回异常：{j}")
    gid = j["data"].get("gid") or ""
    secure_phone_mask = j["data"].get("securePhone") or ""
    attest_base = j["data"].get("attestServerUrl") or "https://login.xjtu.edu.cn/attest"
    if not gid:
        raise RuntimeError("initByType 未返回 gid")
    send_url = urljoin(attest_base.rstrip("/") + "/", "api/guard/securephone/send")
    send_headers = {
        **COMMON_HEADERS,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/json; charset=UTF-8",
        "Origin": "https://login.xjtu.edu.cn",
        "Referer": LOGIN_URL_WITH_SERVICE,
        "X-Requested-With": "XMLHttpRequest",
    }
    r_send = session.post(send_url, headers=send_headers, json={"gid": gid}, timeout=15)
    if r_send.status_code != 200:
        raise RuntimeError("securephone send 非 200")
    try:
        js = r_send.json()
        if js.get("code") not in (0, None):
            raise RuntimeError(f"securephone send 失败：{js}")
    except Exception:
        pass
    return gid, secure_phone_mask, attest_base


def _mfa_secure_phone_validate(session: requests.Session, gid: str, sms_code: str, attest_base: str) -> bool:
    """仅执行 valid 步骤，返回是否成功。"""
    valid_url = urljoin(attest_base.rstrip("/") + "/", "api/guard/securephone/valid")
    send_headers = {
        **COMMON_HEADERS,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/json; charset=UTF-8",
        "Origin": "https://login.xjtu.edu.cn",
        "Referer": LOGIN_URL_WITH_SERVICE,
        "X-Requested-With": "XMLHttpRequest",
    }
    r_valid = session.post(valid_url, headers=send_headers, json={"gid": gid, "code": sms_code.strip()}, timeout=20)
    if r_valid.status_code != 200:
        return False
    try:
        jv = r_valid.json()
        if jv.get("code") not in (0, None):
            return False
    except Exception:
        pass
    return True


def mfa_secure_phone_flow(session: requests.Session, mfa_state: str) -> bool:
    """
    使用“securephone + attest”三步法完成短信验证（CLI 交互，会 input 读验证码）：
      1) initByType 获取 gid、securePhone、attestServerUrl
      2) send 发送验证码
      3) valid 输入验证码完成校验
    要求：session 已在 CAS 域完成登录表单提交，且需要进行 MFA；mfa_state 为 detect 返回的 state。
    返回 True 表示验证完成。
    """
    gid, secure_phone_mask, attest_base = _mfa_secure_phone_init_send(session, mfa_state)
    print(f"已向安全手机 {secure_phone_mask} 发送短信验证码。")
    sms_code = input("请输入短信验证码：").strip()
    if not sms_code:
        raise RuntimeError("未输入短信验证码")
    if not _mfa_secure_phone_validate(session, gid, sms_code, attest_base):
        raise RuntimeError("securephone valid 失败")
    print("短信验证已完成。")
    return True


def extract_code_from_url(url: str) -> dict:
    parsed = urlparse.urlparse(url)
    qs = urlparse.parse_qs(parsed.query)
    return {
        "code": qs.get("code", [""])[0],
        "state": qs.get("state", [""])[0],
        "userType": qs.get("userType", [""])[0],
        "employeeNo": qs.get("employeeNo", [""])[0],
        "host": parsed.netloc,
        "path": parsed.path,
    }


def _get_token_after_mfa(session: requests.Session, form: dict) -> str:
    """MFA 验证通过后：再次 POST 登录、authorize 链、codeLogin，返回 token 或错误字符串。"""
    r1_mfa = post(
        session,
        LOGIN_URL_WITH_SERVICE,
        data=form,
        referer=LOGIN_URL_WITH_SERVICE,
        origin="https://login.xjtu.edu.cn",
        allow_redirects=False
    )
    auth_url = (
        "https://login.xjtu.edu.cn/cas/oauth2.0/authorize?"
        f"response_type=code&client_id={CLIENT_ID}&"
        "redirect_uri=https%3A%2F%2Forg.xjtu.edu.cn%2Fopenplatform%2Foauth%2Fauthorizesw%3Fredirect_uri%3Dhttps%3A%2F%2Fipahw.xjtu.edu.cn%2Fsso%2Fcallback&"
        f"state={STATE}"
    )
    r_auth_mfa = get(session, auth_url, referer=LOGIN_URL_WITH_SERVICE, allow_redirects=False)
    if r_auth_mfa.status_code not in (302, 303):
        return "MFA 后 authorize 非 302"
    loc2_mfa = r_auth_mfa.headers.get("Location", "")
    url3_mfa = loc2_mfa if loc2_mfa.startswith("http") else urljoin(auth_url, loc2_mfa)
    r3_mfa = get(session, url3_mfa, referer=LOGIN_URL_WITH_SERVICE, allow_redirects=False)
    loc3_mfa = r3_mfa.headers.get("Location", "")
    url4_mfa = loc3_mfa if loc3_mfa.startswith("http") else urljoin(url3_mfa, loc3_mfa)
    r4_mfa = get(session, url4_mfa, referer="https://login.xjtu.edu.cn/", allow_redirects=False)
    loc4_mfa = r4_mfa.headers.get("Location", "")
    url5_mfa = loc4_mfa if loc4_mfa.startswith("http") else urljoin(url4_mfa, loc4_mfa)
    r5_mfa = get(session, url5_mfa, referer="https://login.xjtu.edu.cn/", allow_redirects=False)
    save_text("ipahw_callback.html", r5_mfa.text if (r5_mfa.text is not None) else f"(no body) status={r5_mfa.status_code}")
    params_final = extract_code_from_url(url5_mfa)
    code_final = params_final.get("code") or ""
    user_type = params_final.get("userType") or "1"
    username = form.get("username") or ""
    employee_no = params_final.get("employeeNo") or username
    if not code_final:
        return "短信验证失败"
    r_token_mfa = session.get(
        IPAHW_CODE_LOGIN,
        headers={**COMMON_HEADERS},
        params={"userType": user_type, "code": code_final, "employeeNo": employee_no},
        timeout=20
    )
    save_text("codeLogin.json", (r_token_mfa.text or "")[:4000])
    try:
        jd = r_token_mfa.json()
        token = (jd.get("data") or {}).get("token")
        if token:
            return token
    except Exception:
        pass
    return "短信验证失败"


def get_token_with_sms(username: str, sms_code: str) -> str:
    """
    在「开始」已发送验证码后，用户输入验证码并提交时调用。
    从 _pending_get_token 取出该用户的 session/gid/form，校验验证码后完成登录并返回 token；
    若失败或无暂存状态则返回错误字符串。
    """
    with _pending_lock:
        state = _pending_get_token.pop(username, None)
    if not state:
        return "请先点击「开始」并等待发送验证码后再输入验证码"
    session = state["session"]
    gid = state["gid"]
    attest_base = state["attest_base"]
    form = state["form"]
    if not _mfa_secure_phone_validate(session, gid, sms_code, attest_base):
        with _pending_lock:
            _pending_get_token[username] = state
        return "短信验证码错误，请重试"
    return _get_token_after_mfa(session, form)


def get(session: requests.Session, url: str, referer: str = None, origin: str = None, allow_redirects: bool = False, timeout: int = 20):
    headers = {**COMMON_HEADERS}
    if referer:
        headers["Referer"] = referer
    if origin:
        headers["Origin"] = origin
    r = session.get(url, headers=headers, allow_redirects=allow_redirects, timeout=timeout)
    merge_set_cookie_to_session(session, r.headers.get("Set-Cookie"), urlparse.urlparse(url).hostname or "")
    return r

def post(session: requests.Session, url: str, data: dict | str, referer: str = None, origin: str = None, allow_redirects: bool = False, json_mode: bool = False, timeout: int = 30):
    headers = {**COMMON_HEADERS}
    if referer:
        headers["Referer"] = referer
    if origin:
        headers["Origin"] = origin
    if json_mode:
        headers["Content-Type"] = "application/json; charset=UTF-8"
        body_kwargs = {"json": data, "data": None}
    else:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        if isinstance(data, dict):
            data = urlencode(data)
        body_kwargs = {"data": data, "json": None}

    r = session.post(url, headers=headers, allow_redirects=allow_redirects, timeout=timeout, **body_kwargs)
    merge_set_cookie_to_session(session, r.headers.get("Set-Cookie"), urlparse.urlparse(url).hostname or "")
    return r

def get_token(username: str, password: str) -> str:
    USERNAME = username
    PASSWORD = password
    session = requests.Session()
    if PROXIES:
        session.proxies.update(PROXIES)

    # 先从服务端获取 SESSION（预热），再添加统计类 Cookie，避免覆盖 SESSION
    r_preheat = ensure_session_cookie(session, service_url_quoted=urlparse.quote(SERVICE_URL, safe=""))
    # debug_print("cookies after preheat", dump_cookies(session))

    # 加入你指定的 Hm_* 与 HMACCOUNT（不覆盖 SESSION）
    add_tracking_cookies(session)
    # debug_print("cookies after add tracking", dump_cookies(session))

    # 如果预热不是 200/302，尝试再拿一次登录页（可选）
    if r_preheat.status_code not in (200, 302, 303):
        _ = get(session, LOGIN_URL_WITH_SERVICE, referer="https://ipahw.xjtu.edu.cn/", allow_redirects=False)
        # debug_print("cookies after fallback GET login", dump_cookies(session))

    # ========== Step 0：GET 登录页以获取 execution ==========
    r0 = get(session, LOGIN_URL_WITH_SERVICE, referer="https://ipahw.xjtu.edu.cn/", allow_redirects=False)
    # debug_print("GET login page", f"status={r0.status_code} url={r0.url}")
    # debug_print("cookies after GET login", dump_cookies(session))
    save_text("login_page.html", r0.text or "")
    try:
        execution = extract_execution(r0.text or "")
        # debug_print("execution", execution)
    except Exception as e:
        # debug_print("execution parse failed", str(e))
        # 再尝试刷新一次
        r0b = get(session, LOGIN_URL_WITH_SERVICE, referer="https://ipahw.xjtu.edu.cn/", allow_redirects=False)
        save_text("login_page_retry.html", r0b.text or "")
        try:
            execution = extract_execution(r0b.text or "")
            # debug_print("execution(retry)", execution)
        except Exception as e2:
            raise RuntimeError(f"无法从登录页解析 execution: {e2}")

    # ========== Step 1：加密密码、MFA 探测 ==========
    encrypt_password = True
    enc_password = rsa_encrypt_with_public_key(PUBLIC_KEY_PEM, PASSWORD) if encrypt_password else PASSWORD
    fp_visitor_id = get_fp_visitor_id()
    need_mfa = False
    mfa_state = ""

    try:
        info = mfa_detect(session, USERNAME, enc_password, fp_visitor_id)
        need_mfa = info.get("need", False)
        mfa_state = info.get("state", "")
        # debug_print("MFA need/state", f"{need_mfa} / {mfa_state}")
    except Exception as e:
        debug_print("MFA detect error", str(e))

    print(f"enc:{enc_password}")

    # ========== Step 2：POST /cas/login?service=... ==========
    form = {
        "username": USERNAME,
        "password": enc_password,
        "captcha": "",
        "rememberMe": "on",
        "currentMenu": "1",
        "failN": "0",
        "mfaState": mfa_state,
        "execution": execution,
        "_eventId": "submit",
        "geolocation": "",
        "fpVisitorId": fp_visitor_id,
        "trustAgent": "false",
        "submit1": "Login1",
    }
    r1 = post(
        session,
        LOGIN_URL_WITH_SERVICE,
        data=form,
        referer=LOGIN_URL_WITH_SERVICE,
        origin="https://login.xjtu.edu.cn",
        allow_redirects=False
    )
    # debug_print("POST /cas/login", f"status={r1.status_code}")
    # debug_print("Resp headers", json.dumps(dict(r1.headers), ensure_ascii=False, indent=2))
    # debug_print("cookies after login POST", dump_cookies(session))

    if r1.status_code == 200:
        html = r1.text or ""
        save_text("post_login_200.html", html)
        if page_indicates_mfa(html) or need_mfa:
            if not mfa_state:
                info = mfa_detect(session, USERNAME, enc_password, fp_visitor_id)
                mfa_state = info.get("state", "")
                if not mfa_state:
                    raise RuntimeError("MFA state 缺失，无法继续短信验证")

            # 仅执行 init+send，不阻塞读 stdin；状态暂存供 Web 端输入验证码后 get_token_with_sms 继续
            gid, secure_phone_mask, attest_base = _mfa_secure_phone_init_send(session, mfa_state)
            print(f"已向安全手机 {secure_phone_mask} 发送短信验证码。")
            with _pending_lock:
                _pending_get_token[USERNAME] = {
                    "session": session,
                    "gid": gid,
                    "attest_base": attest_base,
                    "form": form,
                }
            return {"need_sms": True, "mask_phone": secure_phone_mask}
        else:
            raise RuntimeError("登录返回 200 但非 MFA 场景，可能是表单或账号错误，请查看 post_login_200.html")
    elif r1.status_code in (302, 303):
        loc1 = r1.headers.get("Location", "")
        if not loc1:
            raise RuntimeError("登录 302 但无 Location")
    else:
        body_preview = ""
        try:
            body_preview = (r1.text or "")[:800]
        except Exception:
            pass
        save_text("post_login_error.html", body_preview)
        raise RuntimeError(f"登录失败 status={r1.status_code}")

    # ========== Step 3：按五段跳转 ==========
    url2 = loc1 if loc1.startswith("http") else urljoin(LOGIN_PAGE_BASE, loc1)
    print("url2 =", url2)

    # 回调前确认 Cookie（尤其 TGC）
    # debug_print("cookies before callbackAuthorize", dump_cookies(session))
    r2 = get(session, url2, referer=LOGIN_URL_WITH_SERVICE, origin="https://login.xjtu.edu.cn", allow_redirects=False)
    # debug_print("GET callbackAuthorize", f"status={r2.status_code}")
    print("r2.headers =", r2.headers)

    if r2.status_code not in (302, 303):
        # 兜底保存响应体以排查
        save_text("callbackAuthorize_body.html", r2.text or "")
        raise RuntimeError("callbackAuthorize 非 302")

    loc2 = r2.headers.get("Location", "")
    print("loc2 =", loc2)

    # 如果 Location 被送回根域，且我们确认已有 TGC，则兜底尝试直接 authorize
    if loc2.strip("/") == "https://login.xjtu.edu.cn".strip("/"):
        has_tgc = any(c.name == "TGC" for c in session.cookies if c.domain.endswith("xjtu.edu.cn"))
        # debug_print("callbackAuthorize returned root, has_tgc", str(has_tgc))
        if has_tgc:
            auth_url = (
                "https://login.xjtu.edu.cn/cas/oauth2.0/authorize?"
                f"response_type=code&client_id={CLIENT_ID}&"
                "redirect_uri=https%3A%2F%2Forg.xjtu.edu.cn%2Fopenplatform%2Foauth%2Fauthorizesw%3Fredirect_uri%3Dhttps%3A%2F%2Fipahw.xjtu.edu.cn%2Fsso%2Fcallback&"
                f"state={STATE}"
            )
            r3_try = get(session, auth_url, referer=LOGIN_URL_WITH_SERVICE, allow_redirects=False)
            # debug_print("authorize fallback", f"status={r3_try.status_code} headers={dict(r3_try.headers)}")
            if r3_try.status_code in (302, 303):
                loc2 = r3_try.headers.get("Location", loc2)
            else:
                # 仍不行，继续后续逻辑用 loc2 处理
                pass

    url3 = loc2 if loc2.startswith("http") else urljoin(url2, loc2)
    print("url3 =", url3)
    # debug_print("cookies before authorize", dump_cookies(session))
    r3 = get(session, url3, referer=LOGIN_URL_WITH_SERVICE, allow_redirects=False)
    print("r3.headers =", r3.headers)
    # debug_print("GET authorize", f"status={r3.status_code} url={url3}")
    # if r3.status_code not in (302, 303):
    #     # 保存以排查（常见：缺少 TGC 或 ST 失效）
    #     save_text("authorize_body.html", r3.text or "")
    #     raise RuntimeError("authorize 非 302")
    loc3 = r3.headers.get("Location", "")

    print("r3.headers", r3.headers)
    print("loc3 =", loc3)
    

    if "code=" in loc3 and "org.xjtu.edu.cn" in loc3:
        m = re.search(r"[?&]code=([^&]+)", loc3)
        if m:
            oc_code = urlparse.unquote(m.group(1))
            # debug_print("OC code", oc_code)

    url4 = loc3 if loc3.startswith("http") else urljoin(url3, loc3)
    r4 = get(session, url4, referer="https://login.xjtu.edu.cn/", allow_redirects=False)
    # debug_print("GET org authorizesw", f"status={r4.status_code}")
    print("r4.headers =", r4.headers)
    # if r4.status_code not in (302, 303):
    #     save_text("org_authorizesw_body.html", r4.text or "")
    #     raise RuntimeError("org authorizesw 非 302")
    loc4 = r4.headers.get("Location", "")
    print("loc4 =", loc4)
    if "code=" in loc4 and "ipahw.xjtu.edu.cn" in loc4:
        m = re.search(r"[?&]code=([^&]+)", loc4)
        if m:
            sw_oc_code = urlparse.unquote(m.group(1))
            # debug_print("SW-OC code", sw_oc_code)

    url5 = loc4 if loc4.startswith("http") else urljoin(url4, loc4)
    r5 = get(session, url5, referer="https://login.xjtu.edu.cn/", allow_redirects=False)
    print("r5.headers =", r5.headers)
    print("url5 =", url5)
    # debug_print("GET ipahw callback", f"status={r5.status_code}")
    save_text("ipahw_callback.html", r5.text if (r5.text is not None) else f"(no body) status={r5.status_code}")
    # debug_print("ipahw headers", json.dumps(dict(r5.headers), ensure_ascii=False, indent=2))

    params_final = extract_code_from_url(url5)
    # debug_print("final params", json.dumps(params_final, ensure_ascii=False, indent=2))

    code_final = params_final.get("code") or ""
    user_type = params_final.get("userType") or "1"
    employee_no = params_final.get("employeeNo") or USERNAME

    if code_final:
        r_token = session.get(
            IPAHW_CODE_LOGIN,
            headers={**COMMON_HEADERS},
            params={"userType": user_type, "code": code_final, "employeeNo": employee_no},
            timeout=20
        )
        # debug_print("codeLogin", f"status={r_token.status_code}")
        save_text("codeLogin.json", (r_token.text or "")[:4000])
        try:
            jd = r_token.json()
            token = (jd.get("data") or {}).get("token")
            if token:
                print(f"\n===== 成功获取 token =====\n{token}\n")
                return token
            else:
                print("\n未在 codeLogin 响应中找到 token，请查看 codeLogin.json\n")
        except Exception:
            print("\ncodeLogin 返回非 JSON 或解析失败，详见 codeLogin.json\n")
    else:
        print("\n未提取到最终 code，检查前述跳转链与响应头。\n")

# if __name__ == "__main__":
#     try:
#         main()
#     except Exception as e:
#         print("\n[ERROR] 运行出错：", e)
#         traceback.print_exc()
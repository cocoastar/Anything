import argparse
import json
import random
import time
from datetime import datetime, timedelta

import requests
import schedule

# 经纬度范围（与 daka_service 一致）
latitude_range = (34.25356291315148, 34.25360308684852)
longitude_range = (108.65873469809705, 108.65878330190296)

# 命令行/主流程用的全局变量，仅 __main__ 时赋值
headers = {}
token = None
username = None
password = None
day_cnt = 0
starttime = "00:00:00"
#
# latitude_range = (34.252689927927925, 34.25271007207207)
# longitude_range = (108.99388781451708, 108.99391218548291)


def update_random_coordinates(data, latitude_range, longitude_range):
    """
    在给定的经纬度范围内为data字典生成随机经纬度，并更新data字典。

    参数:
    data (dict): 原始数据字典。
    latitude_range (tuple): 纬度的最小和最大值。
    longitude_range (tuple): 经度的最小和最大值。
    """
    # 在指定范围内生成随机经纬度
    random_latitude = random.uniform(*latitude_range)
    random_longitude = random.uniform(*longitude_range)

    # 更新字典中的经纬度数据，保持与原始数据相同的小数点精度
    data["latitude"] = f"{random_latitude:.6f}"
    data["longitude"] = f"{random_longitude:.6f}"

    return data


def _headers_with_token(tk):
    """根据 token 生成请求头，供 sign_in_with_token / sign_out_with_token 及主流程使用。"""
    return {
        "Accept": "*/*",
        "Accept-Encoding": "gzip, deflate, br",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
        "Host": "ipahw.xjtu.edu.cn",
        "Origin": "https://ipahw.xjtu.edu.cn",
        "Referer": "https://ipahw.xjtu.edu.cn/pages/index/hdgl/hdgl_run?courseType=7&signType=1&activityAddress=null&courseInfoId=null",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
        "Content-Type": "application/json",
        "token": tk,
    }


def sign_in_with_token(tk):
    """
    可被外部调用：传入 token，执行签到（与 sign_in 同一接口与逻辑）。
    返回 (成功, msg, lat, lon, status_code)。
    """
    url_qiandao = "https://ipahw.xjtu.edu.cn/szjy-boot/api/v1/sportActa/signRun"
    data = {"sportType": 2, "longitude": "108.658759", "latitude": "34.253583", "courseInfoId": "null"}
    data = update_random_coordinates(data, latitude_range, longitude_range)
    h = _headers_with_token(tk)
    r = requests.post(url_qiandao, headers=h, data=json.dumps(data), timeout=15)
    try:
        res = r.json()
        msg = res.get("msg", r.text or "")
    except Exception:
        msg = r.text or ""
    return (r.status_code == 200 and "成功" in msg, msg, data["latitude"], data["longitude"], r.status_code)


def sign_out_with_token(tk):
    """
    可被外部调用：传入 token，执行签退（与 sign_out 同一接口与逻辑）。
    返回 (成功, msg, lat, lon, status_code)。
    """
    url_qiantui = "https://ipahw.xjtu.edu.cn/szjy-boot/api/v1/sportActa/signOutTrain"
    data = {"longitude": "108.658759", "latitude": "34.253583", "courseInfoId": "1828599707263381506"}
    data = update_random_coordinates(data, latitude_range, longitude_range)
    h = _headers_with_token(tk)
    r = requests.post(url_qiantui, headers=h, data=json.dumps(data), timeout=15)
    try:
        res = r.json()
        msg = res.get("msg", r.text or "")
    except Exception:
        msg = r.text or ""
    return (r.status_code == 200 and "成功" in msg, msg, data["latitude"], data["longitude"], r.status_code)


def sign_in():
    """主流程用：使用全局 headers 签到并 print 日志。"""
    global token, headers
    print("Signing in...")
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{current_time} - Signing in...")
    url_qiandao = "https://ipahw.xjtu.edu.cn/szjy-boot/api/v1/sportActa/signRun"
    data = {"sportType": 2, "longitude": "108.658759", "latitude": "34.253583", "courseInfoId": "null"}
    data = update_random_coordinates(data, latitude_range, longitude_range)
    print(f"经纬度:{data['latitude']},{data['longitude']}")
    response_qiandao = requests.post(url_qiandao, headers=headers, data=json.dumps(data))
    print("HTTP 状态码:", response_qiandao.status_code)
    res = json.loads(response_qiandao.text)
    print(f"响应内容:{res['msg']}")
    msg = res["msg"]
    if "Token" in msg and token is not None and username is not None and password is not None:
        from get_token import get_token
        token = get_token(username, password)
        headers["token"] = token
        response_qiandao = requests.post(url_qiandao, headers=headers, data=json.dumps(data))
        print("HTTP 状态码:", response_qiandao.status_code)
        res = json.loads(response_qiandao.text)
        print(f"响应内容:{res['msg']}")


def sign_out():
    """主流程用：使用全局 headers 签退并 print 日志。"""
    print("Signing out...")
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{current_time} - Signing out...")
    url_qiantui = "https://ipahw.xjtu.edu.cn/szjy-boot/api/v1/sportActa/signOutTrain"
    data = {"longitude": "108.658759", "latitude": "34.253583", "courseInfoId": "1828599707263381506"}
    data = update_random_coordinates(data, latitude_range, longitude_range)
    print(f"经纬度:{data['latitude']},{data['longitude']}")
    response_qiantui = requests.post(url_qiantui, headers=headers, data=json.dumps(data))
    print("HTTP 状态码:", response_qiantui.status_code)
    res = json.loads(response_qiantui.text)
    print(f"响应内容:{res['msg']}")




def random_time(start_time, end_time, interval_min=31, interval_max=90):
    """
    生成随机的开始和结束时间。
    start_time: 每天运动的预期开始时间 (格式: 'HH:MM')。
    end_time: 每天运动的预期结束时间 (格式: 'HH:MM')。
    interval_min: 最小时间间隔（分钟）。
    interval_max: 最大时间间隔（分钟）。
    """
    format_str = '%H:%M'  # 时间格式
    start = datetime.strptime(start_time, format_str)
    end = datetime.strptime(end_time, format_str)

    # 计算最早和最晚开始时间
    earliest_start = start - timedelta(minutes=20)
    latest_end = end + timedelta(minutes=20)

    # 随机选择一个开始时间
    start_delta = random.randint(0, 40)  # 随机波动0到20分钟
    random_start = earliest_start + timedelta(minutes=start_delta)

    # 确保结束时间在开始时间之后的interval_min到interval_max分钟内
    min_end = random_start + timedelta(minutes=interval_min)
    max_end = random_start + timedelta(minutes=min(interval_max, (latest_end - random_start).seconds // 60))

    # 随机选择一个结束时间
    if max_end > min_end:
        end_delta = random.randint(0, (max_end - min_end).seconds // 60)
        random_end = min_end + timedelta(minutes=end_delta)
    else:
        random_end = min_end

    return random_start.strftime(format_str), random_end.strftime(format_str)

def random_time_with_seconds(start_time, end_time, interval_min=31, interval_max=90):
    """
    生成随机的开始和结束时间，并在最终时间中加入随机的秒。
    start_time: 每天运动的预期开始时间 (格式: 'HH:MM')。
    end_time: 每天运动的预期结束时间 (格式: 'HH:MM')。
    interval_min: 最小时间间隔（分钟）。
    interval_max: 最大时间间隔（分钟）。
    """
    format_str = '%H:%M:%S'  # 更新时间格式以包含秒
    start = datetime.strptime(start_time, format_str)  # 将起始时间转换为datetime对象
    end = datetime.strptime(end_time + ":00", format_str)  # 将结束时间转换为datetime对象

    # 计算最早和最晚开始时间
    earliest_start = start - timedelta(minutes=0)
    latest_end = end + timedelta(minutes=20)

    # 随机选择一个开始时间
    start_delta = random.randint(0, 20)  # 随机波动0到40分钟
    random_start = earliest_start + timedelta(minutes=start_delta)

    # 确保结束时间在开始时间之后的interval_min到interval_max分钟内
    min_end = random_start + timedelta(minutes=interval_min)
    max_end = random_start + timedelta(minutes=min(interval_max, (latest_end - random_start).seconds // 60))

    # 随机选择一个结束时间
    if max_end > min_end:
        end_delta = random.randint(0, (max_end - min_end).seconds // 60)
        random_end = min_end + timedelta(minutes=end_delta)
    else:
        random_end = min_end

    # 随机加入秒
    random_start_seconds = random.randint(0, 59)
    random_end_seconds = random.randint(0, 59)

    # 在最终时间中加入随机的秒
    random_start = random_start.replace(second=random_start_seconds)
    random_end = random_end.replace(second=random_end_seconds)

    return random_start.strftime(format_str), random_end.strftime(format_str)


def setup_daily_tasks():
    # 先取消之前的任务
    schedule.clear('daily-sign-in')
    schedule.clear('daily-sign-out')

    global day_cnt
    global starttime
    if day_cnt == 0:
        # 使用用户输入的时间计算新的随机时间（间隔大于 30 分钟）
        random_start, random_end = random_time_with_seconds(starttime, "23:59", 31, 90)

        # 如果随机结束时间与开始时间的间隔不够，将结束时间增加20分钟
        start_hour, start_minute, start_second = map(int, starttime.split(":"))
        end_hour, end_minute, end_second = map(int, random_end.split(":"))
        start_time_seconds = start_hour * 3600 + start_minute * 60 + start_second
        end_time_seconds = end_hour * 3600 + end_minute * 60 + end_second

        if end_time_seconds - start_time_seconds < 1800:
            end_time_seconds += 10 * 60
            end_hour, end_minute = divmod(end_time_seconds, 3600)
            end_minute, end_second = divmod(end_minute, 60)
            random_end = f"{end_hour:02d}:{end_minute:02d}:{end_second:02d}"
        if end_time_seconds - start_time_seconds > 2400:
            end_time_seconds -= 8 * 60
            end_hour, end_minute = divmod(end_time_seconds, 3600)
            end_minute, end_second = divmod(end_minute, 60)
            random_end = f"{end_hour:02d}:{end_minute:02d}:{end_second:02d}"
        print(f"新一天的打卡时间:{starttime}-{random_end}")
        day_cnt = 1
        # 安排新的任务
        schedule.every().day.at(starttime).do(sign_in).tag('daily-sign-in')
        schedule.every().day.at(random_end).do(sign_out).tag('daily-sign-out')
    else:
        # 使用用户输入的时间计算新的随机时间（间隔大于 30 分钟）
        random_start, random_end = random_time_with_seconds(starttime, "23:59", 31, 90)

        print(f"新一天的打卡时间:{random_start}-{random_end}")
        # 安排新的任务
        schedule.every().day.at(random_start).do(sign_in).tag('daily-sign-in')
        schedule.every().day.at(random_end).do(sign_out).tag('daily-sign-out')
        # day_cnt = 1




if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="体育打卡脚本，可独立运行或由 Web 调度调用。")
    parser.add_argument("--username", "-u", type=str, default="3123358165", help="学号")
    parser.add_argument("--password", "-p", type=str, default="", help="密码")
    args = parser.parse_args()
    username = args.username
    password = args.password

    from get_token import get_token
    token = get_token(username, password)
    headers.update(_headers_with_token(token))

    schedule.every().day.at("00:01").do(setup_daily_tasks)
    starttime = (datetime.now() + timedelta(seconds=10)).strftime("%H:%M:%S")
    setup_daily_tasks()

    while True:
        schedule.run_pending()
        time.sleep(1)


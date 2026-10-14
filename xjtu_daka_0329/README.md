# 西交校园 Web（xjtu_daka）

基于统一身份认证的登录 + 工作台（体育打卡、二手交易、考试资料、面试经验、学长有话说、西交吃什么），入口为 `main4.py`。

## 目录结构

```
xjtu_daka/
├── main4.py          # Web 入口（Flask）
├── config.py         # 路径与数据库、上传配置
├── extensions.py     # SQLAlchemy 实例
├── models.py         # 数据库模型（二手/考试/论坛/食堂）
├── get_token.py      # 统一身份认证 + 短信验证（CAS）
├── daka2.py          # 打卡逻辑
├── templates/        # 页面模板
├── migrations/       # Flask-Migrate 数据库迁移
├── instance/         # SQLite 数据库文件（默认）
├── uploads/          # 上传文件（二手图片、资料附件等）
├── logs/             # 每学号运行日志 .txt
├── requirements.txt
└── README.md
```

## 依赖

- Python 3.8+
- `jtcdk` 目录：与 `xjtu_daka` 同级，内含 `dk6.py`（即 `Pro2/jtcdk/dk6.py`）
- tmux（用于后台跑打卡任务）

## 运行

```bash
cd /root/Pro2/xjtu_daka
pip install -r requirements.txt
python main4.py
```

浏览器访问：`http://<IP>:8894/`

## 配置

- **端口**：`main4.py` 末尾 `port=8894`（8893 可保留作其他用途）
- **打卡脚本目录**：默认 `../jtcdk`，可通过环境变量 `JTCDK_DIR` 覆盖
- **日志目录**：`config.LOGS_DIR`，默认 `./logs`
- **授权名单**：`config.A_TXT_PATH`，默认 `./a.txt`；文件不存在则不做学号校验
- **管理员**：设置环境变量 `ADMIN_STUDENT_IDS=学号1,学号2`，或登录后在 Flask 中把对应用户的 `User.role` 设为 `admin`（如 `flask shell` 里执行 `User.query.filter_by(student_id='你的学号').update({'role': 'admin'})` 并 `db.session.commit()`）

## 数据库迁移（首次或模型变更后）

```bash
cd /root/Pro2/xjtu_daka
export FLASK_APP=main4.py
flask db upgrade   # 应用迁移（若尚未执行过，需先 flask db migrate -m "说明"）
```

## 流程

1. 打开页面 → 登录（学号 + 密码）
2. 若需短信验证 → 弹窗输入验证码 → 验证并继续
3. 进入工作台 → 可进入「体育打卡」「二手交易」「西交吃什么」等板块；顶部可进「个人中心」；管理员可见「管理后台」
4. **体育打卡**：点击「签到打卡」→ 后台 tmux 执行 `jtcdk/dk6.py`，可查看日志、停止、退出
5. **二手交易**：列表（全部 / 我的发布 / 我的收藏）/ 发布（标题、价格、分类、多图、联系方式）/ 详情（作者可下架、标已出、删除；他人可收藏）
6. **个人中心**：修改昵称，快捷进入「我的发布」「我的收藏」
7. **西交吃什么**：浏览已审核餐厅 → 楼层 → 窗口 → 菜品与价格；用户可「提交食堂信息」（餐厅名、楼层、窗口、菜品与价格），提交后为待审核，仅管理员通过后展示
8. **管理后台**（仅管理员）：西交吃什么 · 审核与创建 — 待审核列表通过/拒绝，或直接创建餐厅/窗口/菜品（立即展示）

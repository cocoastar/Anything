# 扩展实例，避免与 app 循环引用
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

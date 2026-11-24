"""业务路由汇总：归档回查固定路径须先于通用资源路由注册。"""
from __future__ import annotations

from app.routers import recall, resource

ROUTERS = [recall, resource]

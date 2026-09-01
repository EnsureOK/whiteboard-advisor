"""工作台演示数据 seed(薄壳)。

用法: cd backend && .venv/bin/python scripts/seed_workbench.py
会清空工作台相关表后重建(不影响白板 sessions/leads 与用户/订单/积分)。
"""

import sys

sys.path.insert(0, ".")

from app.db import init_db
from app.services.demo_seed import seed

if __name__ == "__main__":
    init_db()
    seed()

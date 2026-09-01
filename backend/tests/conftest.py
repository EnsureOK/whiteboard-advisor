import os

# 测试环境不启动内建定时循环(它使用真实 SessionLocal,会写开发库)
os.environ.setdefault("WB_DISABLE_SCHEDULER", "1")
# 强制无 LLM:环境变量优先于 .env,清掉开发机的真实 key,
# 让全部用例走确定性的 mock/模板路径(与 CI 一致;否则 chat 走 agent
# 分支后用生产 SessionLocal 查不到测试库数据,泄漏 ExceptionGroup)
os.environ["QIANFAN_API_KEY"] = ""

import pytest

from app.models.schemas import Language, Session, SessionState
from app.services import zone_engine


@pytest.fixture
def fresh_session() -> Session:
    s = Session(language=Language.zh, state=SessionState.template_loaded)
    zone_engine.init_zones(s)
    return s

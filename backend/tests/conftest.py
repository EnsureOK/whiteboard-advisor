import os

# 测试环境不启动内建定时循环(它使用真实 SessionLocal,会写开发库)
os.environ.setdefault("WB_DISABLE_SCHEDULER", "1")

import pytest

from app.models.schemas import Language, Session, SessionState
from app.services import zone_engine


@pytest.fixture
def fresh_session() -> Session:
    s = Session(language=Language.zh, state=SessionState.template_loaded)
    zone_engine.init_zones(s)
    return s

"""安全加固测试:XXE 防护解析器 + AUTH_REQUIRED 门禁。"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.wecom_app import _parse_xml
from app.main import app


# ---------- P0: wecom 回调 XML 解析防护 ----------

def test_parse_xml_rejects_doctype():
    malicious = b"<?xml version='1.0'?><!DOCTYPE bomb [<!ENTITY a \"x\">]><xml><Encrypt>&a;</Encrypt></xml>"
    with pytest.raises(HTTPException) as ei:
        _parse_xml(malicious)
    assert ei.value.status_code == 400


def test_parse_xml_rejects_entity_decl():
    malicious = b"<xml><!ENTITY a 'x'><Encrypt>1</Encrypt></xml>"
    with pytest.raises(HTTPException) as ei:
        _parse_xml(malicious)
    assert ei.value.status_code == 400


def test_parse_xml_rejects_oversize():
    with pytest.raises(HTTPException) as ei:
        _parse_xml(b"<xml>" + b"a" * (1024 * 1024 + 1) + b"</xml>")
    assert ei.value.status_code == 413


def test_parse_xml_accepts_normal():
    root = _parse_xml(b"<xml><Encrypt>abc</Encrypt><MsgType>text</MsgType></xml>")
    assert root.findtext("Encrypt") == "abc"


# ---------- P1: AUTH_REQUIRED 门禁 ----------

def test_gate_off_by_default():
    # 默认演示模式:未登录可用
    with TestClient(app) as c:
        assert c.get("/api/workbench/bootstrap").status_code == 200
        assert c.get("/api/kb/documents").status_code == 200


def test_gate_on_requires_token(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "auth_required", True)
    with TestClient(app) as c:
        assert c.get("/api/workbench/bootstrap").status_code == 401
        assert c.get("/api/kb/documents").status_code == 401

        # 登录后放行
        tok = c.post(
            "/api/auth/login", json={"username": "demo", "password": "demo123456"}
        ).json()["token"]
        H = {"Authorization": f"Bearer {tok}"}
        assert c.get("/api/workbench/bootstrap", headers=H).status_code == 200
        assert c.get("/api/kb/documents", headers=H).status_code == 200

"""企微通道测试:加解密回环 / 签名 / 未配置 404。"""

from __future__ import annotations

import base64
import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def wecom_cfg(monkeypatch):
    from app.config import settings

    aes_key = base64.b64encode(os.urandom(32)).decode().rstrip("=")
    monkeypatch.setattr(settings, "wecom_corp_id", "ww_test_corp")
    monkeypatch.setattr(settings, "wecom_app_secret", "sec")
    monkeypatch.setattr(settings, "wecom_app_token", "tok")
    monkeypatch.setattr(settings, "wecom_app_aes_key", aes_key)
    monkeypatch.setattr(settings, "wecom_app_agentid", "1000001")
    return settings


def test_encrypt_decrypt_roundtrip(wecom_cfg):
    from app.api import wecom_app

    xml = "<xml><Content><![CDATA[你好 助理]]></Content></xml>"
    enc = wecom_app.encrypt_msg(xml)
    assert wecom_app.decrypt_msg(enc) == xml


def test_decrypt_rejects_wrong_corp(wecom_cfg, monkeypatch):
    from app.api import wecom_app

    enc = wecom_app.encrypt_msg("<xml>x</xml>")
    monkeypatch.setattr(wecom_cfg, "wecom_corp_id", "another_corp")
    with pytest.raises(ValueError):
        wecom_app.decrypt_msg(enc)


def test_signature(wecom_cfg):
    from app.api import wecom_app

    sig = wecom_app._sign("tok", "123", "abc", "payload")
    assert len(sig) == 40  # sha1 hex
    assert wecom_app._sign("tok", "123", "abc", "payload") == sig


def test_callback_404_when_not_configured():
    from app.main import app

    with TestClient(app) as client:
        r = client.get(
            "/api/wecom/callback",
            params={"msg_signature": "x", "timestamp": "1", "nonce": "n", "echostr": "e"},
        )
        assert r.status_code == 404


def test_url_verify_roundtrip(wecom_cfg):
    from app.api import wecom_app
    from app.main import app

    echo_plain = "echo-12345"
    echostr = wecom_app.encrypt_msg(echo_plain)
    ts, nonce = "111", "nn"
    sig = wecom_app._sign("tok", ts, nonce, echostr)
    with TestClient(app) as client:
        r = client.get(
            "/api/wecom/callback",
            params={"msg_signature": sig, "timestamp": ts, "nonce": nonce, "echostr": echostr},
        )
        assert r.status_code == 200
        assert r.text == echo_plain

import os

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.paths import DATA_DIR

# 源码运行: backend/.env;打包运行/显式数据目录: 追加 DATA_DIR/.env(后者优先覆盖)
_ENV_FILES = tuple(
    p for p in (
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"),
        os.path.join(DATA_DIR, ".env"),
    )
    if os.path.isfile(p)
) or ".env"


class Settings(BaseSettings):
    """运行时配置。两套千帆/百度凭证分别覆盖 LLM 与语音服务。"""

    model_config = SettingsConfigDict(env_file=_ENV_FILES, env_file_encoding="utf-8", extra="ignore")

    # 千帆大模型平台 (LLM, v2 OpenAI 兼容, Bearer 鉴权)
    qianfan_api_key: str = ""
    qianfan_base_url: str = "https://qianfan.baidubce.com/v2"
    # 交互轮(意图+zone+解说)用快模型,复杂规划用 deep 模型
    qianfan_model_fast: str = "glm-5.3-flash"
    qianfan_model_deep: str = "glm-5.3-flash"
    # 兼容旧配置:QIANFAN_MODEL 若设置则作为 deep 模型
    qianfan_model: str = ""

    # 知识库向量化的 embedding 模型(千帆 v2 OpenAI 兼容 /embeddings)
    qianfan_embedding_model: str = "bge-large-zh"

    # 登录态 JWT 密钥(生产务必在 .env 里设置 JWT_SECRET)
    jwt_secret: str = ""

    # Stripe(支付宝/微信;沙盒 key 开发,生产换正式 key + Dashboard 开通支付方式)
    stripe_api_key: str = ""
    stripe_webhook_secret: str = ""

    # 百度智能云语音 (ASR/TTS, API_KEY + SECRET_KEY -> access_token)
    baidu_speech_api_key: str = ""
    baidu_speech_secret_key: str = ""

    # 服务端
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    @property
    def model_deep(self) -> str:
        # 旧 QIANFAN_MODEL 优先作为 deep 模型
        return self.qianfan_model or self.qianfan_model_deep

    @property
    def model_fast(self) -> str:
        return self.qianfan_model_fast

    @property
    def has_llm(self) -> bool:
        return bool(self.qianfan_api_key)

    @property
    def has_speech(self) -> bool:
        return bool(self.baidu_speech_api_key and self.baidu_speech_secret_key)


settings = Settings()

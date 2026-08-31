from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """运行时配置。两套千帆/百度凭证分别覆盖 LLM 与语音服务。"""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # 千帆大模型平台 (LLM, v2 OpenAI 兼容, Bearer 鉴权)
    qianfan_api_key: str = ""
    qianfan_base_url: str = "https://qianfan.baidubce.com/v2"
    # 交互轮(意图+zone+解说)用快模型,复杂规划用 deep 模型
    qianfan_model_fast: str = "ernie-4.5-turbo-128k"
    qianfan_model_deep: str = "ernie-4.5-turbo-128k"
    # 兼容旧配置:QIANFAN_MODEL 若设置则作为 deep 模型
    qianfan_model: str = ""

    # 知识库向量化的 embedding 模型(千帆 v2 OpenAI 兼容 /embeddings)
    qianfan_embedding_model: str = "bge-large-zh"

    # 登录态 JWT 密钥(生产务必在 .env 里设置 JWT_SECRET)
    jwt_secret: str = ""

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

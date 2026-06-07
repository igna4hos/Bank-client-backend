from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    postgres_host: str = "bank-postgres"
    postgres_port: int = 5432
    postgres_db: str = "bank_users"
    postgres_user: str = "bank"
    postgres_password: str = "bank_pass"

    clickhouse_host: str = "mart-clickhouse"
    clickhouse_port: int = 8123
    clickhouse_user: str = "bank"
    clickhouse_password: str = "bank_pass"
    clickhouse_db: str = "bank_marts"

    model_config = {"env_file": ".env"}

    @property
    def postgres_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()

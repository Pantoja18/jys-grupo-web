"""Configuración del CRM, leída de variables de entorno (en Docker las pasa docker-compose.yml).

La URL de la base de datos se arma con `CRM_DB_*` (así la contraseña no se repite en el .env y puede
tener cualquier carácter). `DATABASE_URL` / `MIGRATIONS_DATABASE_URL`, si se definen, tienen prioridad
(útil fuera de Docker).
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL, make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    # Conexión de la aplicación: rol jys_app, sin permisos de UPDATE/DELETE sobre audit_log.
    database_url: str | None = Field(default=None, alias="DATABASE_URL")
    db_host: str = Field(default="postgres", alias="CRM_DB_HOST")
    db_port: int = Field(default=5432, alias="CRM_DB_PORT")
    db_name: str = Field(default="jys", alias="CRM_DB_NAME")
    db_user: str = Field(default="jys_app", alias="CRM_DB_USER")
    db_password: str | None = Field(default=None, alias="CRM_DB_PASSWORD", repr=False)

    # Conexión para migraciones (dueño de las tablas). Solo la tiene el servicio crm-tools.
    migrations_database_url: str | None = Field(default=None, alias="MIGRATIONS_DATABASE_URL")
    db_admin_user: str | None = Field(default=None, alias="CRM_DB_ADMIN_USER")
    db_admin_password: str | None = Field(default=None, alias="CRM_DB_ADMIN_PASSWORD", repr=False)

    policy_version: str = Field(default="sin-version", alias="POLICY_VERSION")
    retention_leads_months: int = Field(default=12, alias="RETENTION_LEADS_MONTHS")
    business_timezone: str = Field(default="America/Lima", alias="BUSINESS_TIMEZONE")
    # "dev" habilita /docs; en cualquier otro valor la documentación interactiva queda apagada.
    env: str = Field(default="prod", alias="CRM_ENV")

    def _url(self, explicit: str | None, user: str | None, password: str | None) -> URL:
        if explicit:
            return make_url(explicit)
        if not user or not password:
            raise RuntimeError(
                "Falta la conexión a la base de datos: defina CRM_DB_USER/CRM_DB_PASSWORD "
                "(en Docker los pasa docker-compose.yml desde POSTGRES_*) o DATABASE_URL."
            )
        return URL.create("postgresql+psycopg", username=user, password=password,
                          host=self.db_host, port=self.db_port, database=self.db_name)

    def app_database_url(self) -> URL:
        return self._url(self.database_url, self.db_user, self.db_password)

    def migrations_url(self) -> URL:
        return self._url(self.migrations_database_url, self.db_admin_user, self.db_admin_password)


@lru_cache
def get_settings() -> Settings:
    return Settings()

from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Maps Authentik's OAuth2 provider "Subject mode" to the field on the
# /api/v3/core/users/ object that produces the same `sub` value.
SUB_MODE_FIELDS = {
    "hashed_user_id": "uid",
    "user_id": "pk",
    "user_uuid": "uuid",
    "user_username": "username",
    "user_email": "email",
    "user_upn": "attributes.upn",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Game Night"
    base_url: str = "http://localhost:8000"
    secret_key: str = ""
    database_url: str = "sqlite:////data/boardgames.db"
    timezone: str = "America/New_York"

    # OIDC login (Authentik OAuth2/OpenID provider)
    oidc_issuer: str = ""  # e.g. https://auth.example.com/application/o/boardgames/
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_scopes: str = "openid profile email"
    oidc_admin_group: str = ""  # members get admin in the app
    oidc_allowed_group: str = ""  # optional extra gate; prefer an Authentik policy binding

    # Authentik API user sync
    authentik_url: str = ""  # e.g. https://auth.example.com
    authentik_token: str = ""
    authentik_group: str = ""  # only sync members of this group; empty = all internal users
    authentik_sub_mode: str = "hashed_user_id"
    authentik_sync_interval_minutes: int = 60
    authentik_verify_tls: bool = True

    # Files: photos, game covers and backups live under here
    data_dir: str = "/data"
    max_photo_mb: int = 25

    # Plays before this local hour count toward the previous evening's game night
    night_cutoff_hour: int = 5

    # Notifications when a play is logged (all optional)
    ntfy_url: str = ""  # full topic URL, e.g. https://ntfy.example.com/gamenight
    ntfy_token: str = ""
    discord_webhook_url: str = ""
    notify_webhook_url: str = ""  # generic JSON POST, e.g. a Home Assistant webhook

    # BoardGameGeek lookup (register an app at https://boardgamegeek.com/applications)
    bgg_token: str = ""

    # Backups (SQLite only): daily at this local hour, keep this many
    backup_hour: int = 3
    backup_keep: int = 14

    # Local development only: bypasses SSO with a user picker.
    dev_mode: bool = False

    @field_validator("night_cutoff_hour", "backup_hour")
    @classmethod
    def check_hour(cls, v: int) -> int:
        if not 0 <= v <= 23:
            raise ValueError("hour settings must be 0-23")
        return v

    @field_validator("base_url", "authentik_url")
    @classmethod
    def strip_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @field_validator("timezone")
    @classmethod
    def check_tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise ValueError(f"TIMEZONE '{v}' is not a valid IANA zone name") from e
        return v

    @field_validator("authentik_sub_mode")
    @classmethod
    def check_sub_mode(cls, v: str) -> str:
        if v not in SUB_MODE_FIELDS:
            raise ValueError(f"AUTHENTIK_SUB_MODE must be one of {sorted(SUB_MODE_FIELDS)}")
        return v

    @model_validator(mode="after")
    def check_required(self):
        if not self.secret_key or len(self.secret_key) < 32:
            raise ValueError("SECRET_KEY must be set and at least 32 characters (openssl rand -hex 32)")
        if not self.dev_mode:
            missing = [
                n
                for n in ("oidc_issuer", "oidc_client_id", "oidc_client_secret")
                if not getattr(self, n)
            ]
            if missing:
                raise ValueError(
                    "Missing OIDC settings: " + ", ".join(m.upper() for m in missing)
                    + " (or set DEV_MODE=true for local testing)"
                )
        return self

    @property
    def oidc_metadata_url(self) -> str:
        return self.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration"

    @property
    def secure_cookies(self) -> bool:
        return self.base_url.startswith("https://")

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def notify_enabled(self) -> bool:
        return bool(self.ntfy_url or self.discord_webhook_url or self.notify_webhook_url)

    @property
    def sync_enabled(self) -> bool:
        return bool(self.authentik_url and self.authentik_token)


@lru_cache
def get_settings() -> Settings:
    return Settings()

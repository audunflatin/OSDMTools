from sqlalchemy import Column, Integer, String, Boolean, DateTime, create_engine, text, inspect as sa_inspect
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime, timezone
import os

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./users.db")

connect_args = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, connect_args=connect_args)

SessionLocal = sessionmaker(bind=engine)

Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    is_admin = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    must_change_password = Column(Boolean, default=True)
    first_login_at = Column(DateTime, nullable=True)
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)


class LoginLog(Base):
    __tablename__ = "login_log"

    id = Column(Integer, primary_key=True)
    email = Column(String, nullable=False, index=True)
    logged_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    ip_address = Column(String, nullable=True)
    success = Column(Boolean, default=True, nullable=False)


class EventLog(Base):
    __tablename__ = "event_log"

    id = Column(Integer, primary_key=True)
    logged_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    user_email = Column(String, nullable=True, index=True)
    event_type = Column(String, nullable=False, index=True)
    status = Column(String, nullable=False, default="ok")  # "ok" | "error"
    detail = Column(String, nullable=True)  # JSON-streng


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id = Column(Integer, primary_key=True)
    token = Column(String, unique=True, index=True, nullable=False)
    email = Column(String, nullable=False)
    expires_at = Column(DateTime, nullable=False)


class AccessRequest(Base):
    __tablename__ = "access_requests"

    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=False)
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    org = Column(String, nullable=False)
    requested_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    status = Column(String, default="pending")  # "pending" | "approved" | "rejected"


def _migrate():
    insp = sa_inspect(engine)
    user_cols = {c["name"] for c in insp.get_columns("users")}
    log_cols  = {c["name"] for c in insp.get_columns("login_log")}
    ar_cols = {c["name"] for c in insp.get_columns("access_requests")}
    with engine.begin() as conn:
        if "must_change_password" not in user_cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN must_change_password BOOLEAN DEFAULT 0"))
        if "first_login_at" not in user_cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN first_login_at DATETIME"))
        if "first_name" not in user_cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN first_name VARCHAR"))
        if "last_name" not in user_cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN last_name VARCHAR"))
        if "success" not in log_cols:
            conn.execute(text("ALTER TABLE login_log ADD COLUMN success BOOLEAN DEFAULT 1"))
        if "first_name" not in ar_cols:
            conn.execute(text("ALTER TABLE access_requests ADD COLUMN first_name VARCHAR"))
        if "last_name" not in ar_cols:
            conn.execute(text("ALTER TABLE access_requests ADD COLUMN last_name VARCHAR"))

        # Normaliser eksisterende e-poster til lowercase (idempotent)
        conn.execute(text("UPDATE users SET email = LOWER(email) WHERE email != LOWER(email)"))
        conn.execute(text("UPDATE login_log SET email = LOWER(email) WHERE email != LOWER(email)"))


def init_db():
    Base.metadata.create_all(bind=engine)
    _migrate()
"""Test setup: a real PostgreSQL test database, migrated with Alembic, emptied before each test."""
import os

os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://portal:portal@localhost:5432/portal_test"
)
os.environ["SETUP_KEY"] = "test-setup-key"
from cryptography.fernet import Fernet  # noqa: E402

os.environ["ENCRYPTION_KEY"] = Fernet.generate_key().decode()  # throwaway key for the test run
os.environ["FILES_DIR"] = __import__("tempfile").mkdtemp(prefix="portal-test-files-")
os.environ["PUBLIC_BASE_URL"] = "https://portal.test"

from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db import get_engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base, User, Worker  # noqa: E402
from app.security import hash_password  # noqa: E402
from app.services import create_organisation  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]
PASSWORD = "Correct-Horse-9"
_HASH = hash_password(PASSWORD)


@pytest.fixture(scope="session", autouse=True)
def _migrate():
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    command.upgrade(cfg, "head")
    yield


@pytest.fixture(autouse=True)
def _clean():
    tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    with get_engine().begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture
def db():
    with Session(get_engine(), expire_on_commit=False) as session:
        yield session


@pytest.fixture
def client():
    return TestClient(app)


class World:
    """Two organisations whose people share display names, to prove tenant isolation."""

    def __init__(self, db: Session, client: TestClient):
        self.db = db
        self.client = client
        self.org1 = create_organisation(db, "Org One")
        self.org2 = create_organisation(db, "Org Two")
        self.users: dict[str, User] = {}
        for key, org, role, name in [
            ("admin1", self.org1, "admin", "Alex Rowe"),
            ("owner1", self.org1, "admin", "Olive Owner"),
            ("hseq1", self.org1, "hseq_lead", "Sam Keating"),
            ("super1", self.org1, "supervisor", "Jess Marlow"),
            ("worker1", self.org1, "worker", "Tom Okafor"),
            ("auditor1", self.org1, "auditor", "Lee Fontaine"),
            ("admin2", self.org2, "admin", "Alex Rowe"),
            ("worker2", self.org2, "worker", "Tom Okafor"),
        ]:
            email = "owner@one.example" if key == "owner1" else f"{key}@{'one' if org is self.org1 else 'two'}.example"
            user = User(org_id=org.id, email=email, name=name, role_id=role, status="active", password_hash=_HASH)
            db.add(user)
            self.users[key] = user
        db.flush()
        # Olive Owner is the account holder and payroll owner of Org One.
        self.org1.owner_user_id = self.users["owner1"].id
        self.org1.payroll_owner_user_id = self.users["owner1"].id
        db.commit()
        self._tokens: dict[str, str] = {}

    def login(self, key: str, **extra) -> str:
        response = self.client.post(
            "/api/auth/login", json={"email": self.users[key].email, "password": PASSWORD, **extra}
        )
        assert response.status_code == 200, response.text
        return response.json()["access_token"]

    def auth(self, key: str) -> dict[str, str]:
        if key not in self._tokens:
            self._tokens[key] = self.login(key)
        return {"Authorization": f"Bearer {self._tokens[key]}"}

    def id(self, key: str) -> str:
        return self.users[key].id

    def add_worker(self, first: str, last: str, *, org=None, link: str | None = None, **fields) -> Worker:
        """A worker record, optionally linked to one of the logins above."""
        org = org or self.org1
        worker = Worker(org_id=org.id, first_name=first, last_name=last, **fields)
        self.db.add(worker)
        self.db.flush()
        if link:
            self.users[link].worker_id = worker.id
        self.db.commit()
        return worker


@pytest.fixture
def world(db, client) -> World:
    return World(db, client)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}

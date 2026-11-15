"""PV2-03a end-to-end tests use one disposable PostgreSQL database."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import json
from types import SimpleNamespace
import unittest
import uuid

import psycopg
from psycopg import sql

from backend.auth.passwords import Argon2PasswordService
from backend.auth.service import LocalAuthService
from backend.auth.settings import LocalAuthSettings
from backend.auth.store import PostgresAuthStore
from backend.local_auth_app import create_local_auth_app
from scripts.migrate import apply_migrations, connect_kwargs


PASSWORD = "synthetic-password-123"
ORIGIN = "http://127.0.0.1:8775"


class MutableClock:
    def __init__(self):
        self.value = datetime(2026, 11, 15, 10, 0, tzinfo=UTC)

    def __call__(self):
        return self.value


class AsgiClient:
    def __init__(self, app, *, client_host="127.0.0.1"):
        self.app = app
        self.client_host = client_host
        self.cookie = None

    def request(self, method, path, *, payload=None, headers=None, include_origin=True):
        async def run():
            output = []
            request_headers = {"host":"127.0.0.1:8775", "accept":"application/json"}
            if payload is not None:
                request_headers["content-type"] = "application/json"
            if method not in {"GET", "HEAD"} and include_origin:
                request_headers.update({"origin":ORIGIN,"sec-fetch-site":"same-origin"})
            if self.cookie:
                request_headers["cookie"] = self.cookie
            request_headers.update({key.lower():value for key,value in (headers or {}).items()})
            raw_headers = [(key.encode(),value.encode()) for key,value in request_headers.items()]
            body = b"" if payload is None else json.dumps(payload).encode()
            sent = False

            async def receive():
                nonlocal sent
                if sent:
                    return {"type":"http.disconnect"}
                sent = True
                return {"type":"http.request","body":body,"more_body":False}

            async def send(message):
                output.append(message)

            await self.app(
                {"type":"http","asgi":{"version":"3.0"},"http_version":"1.1","method":method,
                 "scheme":"http","path":path,"raw_path":path.encode(),"query_string":b"",
                 "headers":raw_headers,"server":("127.0.0.1",8775),
                 "client":(self.client_host,12345)},
                receive,send,
            )
            response_headers = {}
            for key,value in output[0].get("headers",[]):
                response_headers.setdefault(key.decode().lower(),[]).append(value.decode())
            for value in response_headers.get("set-cookie",[]):
                if value.startswith("eac_local_session="):
                    self.cookie = value.split(";",1)[0] if value.split("=",1)[1].split(";",1)[0] else None
            raw = b"".join(item.get("body",b"") for item in output)
            return SimpleNamespace(
                status_code=output[0]["status"], text=raw.decode(), headers=response_headers,
                json=lambda:json.loads(raw) if raw else None,
            )
        return asyncio.run(run())

    def get(self,path,*,headers=None):
        return self.request("GET",path,headers=headers)

    def post(self,path,*,payload=None,headers=None,include_origin=True):
        return self.request("POST",path,payload=payload,headers=headers,include_origin=include_origin)


class LocalAuthDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin = connect_kwargs()
        cls.database_name = "eac_test_local_auth_" + uuid.uuid4().hex
        with psycopg.connect(**cls.admin, autocommit=True) as connection:
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(cls.database_name)))
        cls.addClassCleanup(cls.cleanup_database)
        cls.database = {**cls.admin,"dbname":cls.database_name}
        with psycopg.connect(**cls.database,autocommit=True) as connection:
            apply_migrations(connection)

    @classmethod
    def cleanup_database(cls):
        with psycopg.connect(**cls.admin,autocommit=True) as connection:
            connection.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(cls.database_name)))

    def setUp(self):
        with psycopg.connect(**self.database,autocommit=True) as connection:
            connection.execute("TRUNCATE product_auth.users CASCADE")
            connection.execute("TRUNCATE product_auth.login_rate_limits")
        self.clock = MutableClock()
        self.settings = LocalAuthSettings(
            enabled=True,
            allowed_origin=ORIGIN,
            rate_limit_secret="synthetic-rate-limit-secret-32-characters",
            session_ttl_seconds=300,
            failed_login_limit=3,
            failed_login_window_seconds=60,
            login_block_seconds=60,
        )
        self.store = PostgresAuthStore(self.connections)
        self.service = LocalAuthService(
            self.store,
            settings=self.settings,
            passwords=Argon2PasswordService(time_cost=1,memory_cost=8_192,parallelism=1),
            clock=self.clock,
        )
        self.app = create_local_auth_app(settings=self.settings,service=self.service)
        self.client = AsgiClient(self.app)

    @contextmanager
    def connections(self):
        connection = psycopg.connect(**self.database)
        try:
            yield connection
        finally:
            connection.close()

    @staticmethod
    def registration(account="analyst@example.com"):
        return {"account":account,"display_name":"Synthetic Analyst","password":PASSWORD}

    def register(self,client=None,account="analyst@example.com"):
        return (client or self.client).post("/auth-api/register",payload=self.registration(account))

    def test_migration_contract_and_public_access_revoked(self):
        with self.connections() as connection:
            applied = connection.execute(
                "SELECT count(*) FROM app_migrations.applied WHERE name='009_local_auth_sessions.sql'"
            ).fetchone()[0]
            tables = connection.execute(
                """SELECT count(*) FROM information_schema.tables WHERE table_schema='product_auth'
                   AND table_name IN ('users','credentials','sessions','login_rate_limits','audit_events')"""
            ).fetchone()[0]
            privileges = connection.execute(
                "SELECT has_schema_privilege('public','product_auth','USAGE'),"
                "has_table_privilege('public','product_auth.users','SELECT')"
            ).fetchone()
        self.assertEqual((applied,tables,privileges),(1,5,(False,False)))

    def test_register_refresh_restart_protected_workspace_and_logout(self):
        response = self.register()
        self.assertEqual(response.status_code,201)
        self.assertEqual(response.json()["next_path"],"/#overview")
        cookie = response.headers["set-cookie"][0]
        self.assertIn("HttpOnly",cookie)
        self.assertIn("SameSite=strict",cookie)
        self.assertNotIn("Secure",cookie)
        self.assertNotIn(PASSWORD,response.text)
        first_csrf = response.json()["csrf_token"]

        with self.connections() as connection:
            password_hash,token_hash = connection.execute(
                """SELECT c.password_hash,s.token_hash FROM product_auth.credentials c
                   JOIN product_auth.sessions s ON s.user_id=c.user_id"""
            ).fetchone()
        self.assertTrue(password_hash.startswith("$argon2id$"))
        self.assertNotIn(PASSWORD,password_hash)
        self.assertNotIn(self.client.cookie.split("=",1)[1],token_hash)

        refreshed = self.client.get("/auth-api/session")
        self.assertEqual(refreshed.status_code,200)
        self.assertNotEqual(refreshed.json()["csrf_token"],first_csrf)
        self.assertEqual(self.client.get("/auth-api/my-workspace").json()["destination"],"my-workspace")

        restarted = AsgiClient(create_local_auth_app(settings=self.settings,service=self.service))
        restarted.cookie = self.client.cookie
        self.assertEqual(restarted.get("/auth-api/session").status_code,200)
        csrf = restarted.get("/auth-api/session").json()["csrf_token"]
        self.assertEqual(restarted.post("/auth-api/logout",headers={"X-CSRF-Token":"wrong"}).status_code,403)
        self.assertEqual(restarted.post("/auth-api/logout",headers={"X-CSRF-Token":csrf}).status_code,204)
        self.assertEqual(restarted.get("/auth-api/session").status_code,401)

    def test_login_errors_do_not_enumerate_and_rate_limit(self):
        self.register()
        existing = AsgiClient(self.app)
        missing = AsgiClient(self.app)
        bad_existing = existing.post("/auth-api/login",payload={"account":"analyst@example.com","password":"wrong-password"})
        bad_missing = missing.post("/auth-api/login",payload={"account":"missing@example.com","password":"wrong-password"})
        self.assertEqual((bad_existing.status_code,bad_missing.status_code),(401,401))
        self.assertEqual(bad_existing.json()["detail"]["message"],bad_missing.json()["detail"]["message"])
        for _ in range(2):
            last = existing.post("/auth-api/login",payload={"account":"analyst@example.com","password":"wrong-password"})
        self.assertEqual(last.status_code,429)
        blocked_correct = existing.post("/auth-api/login",payload={"account":"analyst@example.com","password":PASSWORD})
        self.assertEqual(blocked_correct.status_code,429)
        self.clock.value += timedelta(seconds=61)
        self.assertEqual(existing.post("/auth-api/login",payload={"account":"analyst@example.com","password":PASSWORD}).status_code,200)

    def test_duplicate_registration_concurrent_sessions_and_expiry(self):
        first = self.register()
        duplicate = AsgiClient(self.app).post("/auth-api/register",payload=self.registration())
        self.assertEqual(duplicate.status_code,409)
        self.assertNotIn("analyst@example.com",duplicate.text)
        a,b = AsgiClient(self.app),AsgiClient(self.app)
        self.assertEqual(a.post("/auth-api/login",payload={"account":"analyst@example.com","password":PASSWORD}).status_code,200)
        self.assertEqual(b.post("/auth-api/login",payload={"account":"analyst@example.com","password":PASSWORD}).status_code,200)
        self.assertNotEqual(a.cookie,b.cookie)
        self.clock.value += timedelta(seconds=301)
        self.assertEqual(a.get("/auth-api/session").status_code,401)
        self.assertEqual(b.get("/auth-api/session").status_code,401)

    def test_csrf_origin_loopback_validation_and_demo_bypass(self):
        self.assertEqual(self.client.get("/auth-api/my-workspace").status_code,401)
        no_origin = self.client.post("/auth-api/register",payload=self.registration(),include_origin=False)
        self.assertEqual(no_origin.status_code,403)
        cross_site = self.client.post(
            "/auth-api/register",payload=self.registration(),headers={"Origin":"https://evil.example"}
        )
        self.assertEqual(cross_site.status_code,403)
        remote = AsgiClient(self.app,client_host="10.0.0.8")
        self.assertEqual(remote.get("/auth-api/config").status_code,403)
        self.assertEqual(self.register().status_code,201)
        self.assertEqual(self.client.post("/auth-api/logout",headers={}).status_code,403)

    def test_validation_response_never_echoes_password_or_extra_input(self):
        response = self.client.post(
            "/auth-api/register",
            payload={"account":"bad","display_name":"A","password":"SECRET-VALUE","role":"owner"},
        )
        self.assertEqual(response.status_code,422)
        self.assertNotIn("SECRET-VALUE",response.text)
        self.assertNotIn("owner",response.text)
        self.assertEqual(response.json()["detail"]["code"],"auth_request_invalid")


if __name__ == "__main__":
    unittest.main()

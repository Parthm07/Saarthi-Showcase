"""Endpoint regressions without cloud/Tally. Run with unittest discovery in tests/."""
import os
import pathlib
import sys
import unittest
import asyncio
import json
from contextlib import contextmanager
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
os.environ["SESSION_SECRET"] = "isolated-order-tests"
os.environ["WEB_DIR"] = str(pathlib.Path(__file__).resolve().parents[1] / "web")
import api


class Cursor:
    """A transactional cursor double for the endpoint's business decisions.

    Does not claim to test Postgres locking; that needs an isolated Postgres.
    """
    def __init__(self):
        self.orders = {}
        self.lines = {}
        self.one = None
        self.many = []
        self.locks = 0
        self.removed = False
        self.collide = False
        self.receipts = {}

    def execute(self, sql, args=()):
        self.one, self.many = None, []
        if "pg_advisory_xact_lock" in sql:
            self.locks += int('hashtextextended(%s, 0)' in sql)
        elif sql.startswith('SELECT request_fingerprint,response FROM order_receipt'):
            self.one = self.receipts.get(tuple(args))
        elif sql.startswith('SELECT 1 FROM order_receipt'):
            self.one = None
        elif "o.client_request_id=%s" in sql:
            for no, row in self.orders.items():
                if row[1] == args[0] and row[-2] == args[1]:
                    self.one = (no, row[7], row[3], row[5], row[-1], len(self.lines[no]))
        elif sql.startswith("SELECT guid,name,route FROM customer"):
            self.one = None if self.removed else (args[0], "Test shop", "Route")
        elif sql.startswith("SELECT guid,name,unit FROM item"):
            self.many = [] if self.removed else [(g, g, "PCS") for g in args[0]]
        elif sql.startswith("SELECT stock_asof"):
            self.one = (None,)
        elif sql.startswith("SELECT 1 FROM cloud_order"):
            self.one = (1,) if args[0] in self.orders else None
        elif sql.startswith("INSERT INTO cloud_order("):
            if self.collide:
                self.collide = False
                return
            self.orders[args[0]] = args
            self.one = (args[0],)
        else:
            raise AssertionError(f"unexpected SQL: {sql}")

    def executemany(self, sql, rows):
        for row in rows:
            self.lines.setdefault(row[0], []).append(row)

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.many


class OrderRetryTests(unittest.TestCase):
    def test_customer_scope_allows_every_sales_party_even_without_assigned_routes(self):
        for routes in [[],['Dewas'],None]:
            self.assertEqual(api._scope({'routes':routes}),('',[]))

    def setUp(self):
        self.cur = Cursor()
        self.user = {"email":"rep@example.com", "role":"salesperson", "routes":["Route"]}
        self.request_id = str(uuid4())

    def body(self, **changes):
        data = {"request_id":self.request_id, "customer_guid":"shop",
                "lines":[{"item_guid":f"item-{i}", "qty":i+1, "rate":10.5+i}
                         for i in range(10)]}
        data.update(changes)
        return api.OrderIn(**data)

    def test_lost_response_retry_returns_saved_order_without_duplicate_lines(self):
        first = api.place_order(self.body(), self.user, self.cur)
        second = api.place_order(self.body(), self.user, self.cur)
        self.assertEqual(first, second)
        self.assertEqual(len(self.cur.orders), 1)
        self.assertEqual(len(self.cur.lines[first["order_no"]]), 10)
        self.assertEqual(self.cur.locks, 2)

    def test_same_id_with_changed_order_is_rejected(self):
        api.place_order(self.body(), self.user, self.cur)
        with self.assertRaises(api.HTTPException) as caught:
            api.place_order(self.body(note="different order"), self.user, self.cur)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(len(self.cur.orders), 1)

    def test_saved_retry_resolves_after_catalogue_or_customer_disappears(self):
        first = api.place_order(self.body(), self.user, self.cur)
        self.cur.removed = True
        self.assertEqual(api.place_order(self.body(), self.user, self.cur), first)

    def test_different_submission_is_a_new_order(self):
        first = api.place_order(self.body(), self.user, self.cur)
        second = api.place_order(self.body(request_id=str(uuid4())), self.user, self.cur)
        self.assertNotEqual(first["order_no"], second["order_no"])

    def test_order_number_conflict_retries_insert(self):
        self.cur.collide = True
        response = api.place_order(self.body(), self.user, self.cur)
        self.assertIn(response["order_no"], self.cur.orders)

    def test_archived_receipt_resolves_retry_without_recreating_detail(self):
        first=api.place_order(self.body(),self.user,self.cur)
        self.cur.receipts[(self.user['email'],self.request_id)] = (api._order_fingerprint(self.body()),first)
        self.cur.orders.clear();self.cur.lines.clear();self.cur.removed=True
        self.assertEqual(api.place_order(self.body(),self.user,self.cur),first)
        self.assertEqual(self.cur.orders,{})
        with self.assertRaises(api.HTTPException) as caught:
            api.place_order(self.body(note='different'),self.user,self.cur)
        self.assertEqual(caught.exception.status_code,409)

    def http_order(self, fail_commit=False):
        events = []
        cursor = self.cur

        class Pool:
            @contextmanager
            def connection(self):
                class Connection:
                    @contextmanager
                    def cursor(self):
                        yield cursor
                try:
                    yield Connection()
                except BaseException:
                    cursor.orders.clear()
                    cursor.lines.clear()
                    events.append("rollback")
                    raise
                else:
                    if fail_commit:
                        cursor.orders.clear()
                        cursor.lines.clear()
                        raise RuntimeError("injected commit failure")
                    events.append("commit")

        async def request():
            body = json.dumps(self.body().model_dump(mode="json")).encode()
            scope = {"type":"http", "asgi":{"version":"3.0", "spec_version":"2.3"}, "http_version":"1.1",
                     "method":"POST", "scheme":"http", "path":"/api/orders", "raw_path":b"/api/orders",
                     "query_string":b"", "headers":[(b"content-type",b"application/json")],
                     "client":("127.0.0.1",1234), "server":("test",80)}
            received = False
            async def receive():
                nonlocal received
                if received:
                    await asyncio.Event().wait()
                received = True
                return {"type":"http.request", "body":body, "more_body":False}
            async def send(message):
                if message["type"] == "http.response.start":
                    events.append(message["status"])
            try:
                await asyncio.wait_for(api.app(scope, receive, send), timeout=5)
            except RuntimeError:
                if not fail_commit:
                    raise

        api.app.dependency_overrides[api.me] = lambda:self.user
        try:
            with patch.object(api, "_get_pool", return_value=Pool()):
                asyncio.run(request())
        finally:
            api.app.dependency_overrides.clear()
        return events

    def test_http_success_is_sent_only_after_commit(self):
        self.assertEqual(self.http_order(), ["commit", 200])

    def test_commit_failure_does_not_send_false_success(self):
        self.assertNotIn(200, self.http_order(fail_commit=True))
        self.assertEqual(self.cur.orders, {})

    def test_submission_ids_are_scoped_to_the_salesperson(self):
        first = api.place_order(self.body(), self.user, self.cur)
        other = dict(self.user, email="other@example.com")
        second = api.place_order(self.body(), other, self.cur)
        self.assertNotEqual(first["order_no"], second["order_no"])

    def test_line_order_does_not_change_the_request_identity(self):
        original = self.body()
        first = api.place_order(original, self.user, self.cur)
        reversed_body = self.body(lines=list(reversed(original.model_dump()["lines"])))
        self.assertEqual(api.place_order(reversed_body, self.user, self.cur), first)

    def test_salesperson_cannot_create_a_party_even_by_calling_the_api(self):
        class NoSQL:
            def execute(self, *args):
                raise AssertionError("Forbidden creation must stop before accessing party data")
        with self.assertRaises(api.HTTPException) as caught:
            api.new_prospect(api.ProspectIn(name="Novel Outlet", route="Route"), self.user, NoSQL())
        self.assertEqual(caught.exception.status_code, 403)

    def test_admin_can_create_a_party(self):
        class PartyCursor:
            inserted = False
            def execute(self, sql, args=()):
                if sql.startswith("INSERT INTO prospect"):
                    self.inserted = True
            def fetchone(self):
                return (1,)
            def fetchall(self):
                return []
        cur = PartyCursor()
        admin = {"email":"office@example.com", "role":"admin", "routes":None}
        result = api.new_prospect(api.ProspectIn(name="Novel Outlet", route="Route"), admin, cur)
        self.assertTrue(cur.inserted)
        self.assertEqual(result["status"], "pending")


if __name__ == "__main__":
    unittest.main()

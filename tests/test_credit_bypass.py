import unittest

from app.routers.credits import is_levita_credit_bypass_user


class _FakeScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


class _FakeDb:
    def __init__(self, scalar_value=None):
        self.scalar_value = scalar_value
        self.calls = []

    async def execute(self, query, params):
        self.calls.append((str(query), params))
        return _FakeScalarResult(self.scalar_value)


class TestCreditBypass(unittest.IsolatedAsyncioTestCase):
    async def test_uses_user_source_without_db_lookup(self):
        db = _FakeDb("local")

        result = await is_levita_credit_bypass_user(db, user={"id": 7, "source": "levita"})

        self.assertTrue(result)
        self.assertEqual(db.calls, [])

    async def test_falls_back_to_auth_source_lookup_by_user_id(self):
        db = _FakeDb("levita")

        result = await is_levita_credit_bypass_user(db, user_id=11)

        self.assertTrue(result)
        self.assertEqual(len(db.calls), 1)
        self.assertEqual(db.calls[0][1], {"uid": 11})

    async def test_returns_false_for_non_levita_user(self):
        db = _FakeDb("local")

        result = await is_levita_credit_bypass_user(db, user={"id": 9, "source": "local"})

        self.assertFalse(result)
        self.assertEqual(db.calls, [])


if __name__ == "__main__":
    unittest.main()
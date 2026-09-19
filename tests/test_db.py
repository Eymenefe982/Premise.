"""SQLite -> Postgres SQL çevirisinin testleri.

Gerçek bir Postgres bağlantısı olmadan koşar: yalnızca `db._to_postgres` ve
`db._split_statements`'ın çıktısını sınar. Asıl risk burada — regex tabanlı bir
çeviri, iki şemadaki (`store.SCHEMA`, `accounts.SCHEMA`) her ifadeyi doğru
dönüştürmezse Render'da sessizce yanlış bir tablo kurulur.
"""
from __future__ import annotations

import pytest

from litrag import db
from litrag.accounts import SCHEMA as ACCOUNTS_SCHEMA
from litrag.store import SCHEMA as STORE_SCHEMA


def test_autoincrement_becomes_serial():
    assert db._to_postgres("id INTEGER PRIMARY KEY AUTOINCREMENT,") == "id SERIAL PRIMARY KEY,"


def test_bare_integer_primary_key_becomes_serial():
    """SQLite'ta AUTOINCREMENT'siz "INTEGER PRIMARY KEY" da ROWID gibi otomatik artar
    (searches tablosu buna güveniyordu); Postgres'te aynı davranış için SERIAL şart,
    yoksa id'siz INSERT NOT NULL ihlaliyle patlar."""
    assert db._to_postgres("id INTEGER PRIMARY KEY, date TEXT") == "id SERIAL PRIMARY KEY, date TEXT"


def test_placeholder_conversion_skips_string_literals():
    """Dize sabitlerinin içindeki '?' bir yer tutucu değildir; '%s'e çevrilmemeli."""
    out = db._to_postgres("SELECT * FROM t WHERE x = ? AND y = 'a?b'")
    assert out == "SELECT * FROM t WHERE x = %s AND y = 'a?b'"


def test_no_schema_statement_keeps_sqlite_only_syntax():
    """Her iki şemadaki her ifade çevrildikten sonra AUTOINCREMENT kalıntısı taşımamalı."""
    for schema in (STORE_SCHEMA, ACCOUNTS_SCHEMA):
        for statement in db._split_statements(schema):
            assert "AUTOINCREMENT" not in db._to_postgres(statement)


def test_split_statements_respects_string_literals():
    """Bir dize sabitinin içindeki ';' ifadeyi bölmemeli."""
    script = "CREATE TABLE t (a TEXT DEFAULT 'x;y'); CREATE TABLE u (b INT);"
    stmts = db._split_statements(script)
    assert len(stmts) == 2
    assert "x;y" in stmts[0]


def test_split_statements_ignores_trailing_whitespace_and_empties():
    stmts = db._split_statements("SELECT 1;  \n\n  SELECT 2;   ")
    assert stmts == ["SELECT 1", "SELECT 2"]


def test_split_statements_drops_comments_with_semicolons_and_quotes():
    """Yorumdaki ';' ifadeyi ortasından kesiyor, Render açılışı "syntax error at end of
    input" ile düşüyordu (deleted_accounts tablosu). Yorumdaki kesme işareti de sonraki
    her şeyi dizge sanılır hale getirirdi."""
    script = ("CREATE TABLE t (\n"
              "    a TEXT NOT NULL,   -- ilk; ikinci\n"
              "    b TEXT             -- kullanicinin 'adi\n"
              ");\n"
              "CREATE TABLE u (c INT);")
    stmts = db._split_statements(script)
    assert len(stmts) == 2
    assert "--" not in stmts[0] and stmts[0].endswith(")")
    assert stmts[1] == "CREATE TABLE u (c INT)"


def test_split_statements_keeps_double_dash_inside_strings():
    stmts = db._split_statements("INSERT INTO t VALUES ('a--b'); SELECT 1;")
    assert stmts == ["INSERT INTO t VALUES ('a--b')", "SELECT 1"]


def test_every_schema_statement_parses_as_postgres():
    """Testler SQLite'ta koşar, Render ise Postgres'te açılır: SQLite'ın kabul ettiği bir
    şema Postgres açılışını düşürebilir. Her ifade Postgres'in kendi ayrıştırıcısından
    (libpg_query) geçmeli. pglast yalnız CI'da kurulur (GPL), uygulamaya girmez."""
    pglast = pytest.importorskip("pglast")
    for schema in (STORE_SCHEMA, ACCOUNTS_SCHEMA):
        for statement in db._split_statements(schema):
            pglast.parse_sql(db._to_postgres(statement))


def test_every_schema_statement_is_complete_after_splitting():
    """Postgres'e giden her parça kendi başına tam bir ifade olmalı. pglast kurulu
    olmayan yerel koşularda da çalışan güvence bu (SQLite'ın ayrıştırıcısıyla)."""
    import sqlite3
    for schema in (STORE_SCHEMA, ACCOUNTS_SCHEMA):
        stmts = db._split_statements(schema)
        assert len(stmts) == schema.count("CREATE ")
        for statement in stmts:
            assert sqlite3.complete_statement(statement + ";"), statement
            assert statement.count("(") == statement.count(")"), statement

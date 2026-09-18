"""Veritabanı katmanı: yerelde SQLite dosyası, canlıda Postgres.

`DATABASE_URL` verilmişse Postgres'e, verilmemişse SQLite'a bağlanır. Kalıcılık
sorunu tam olarak buradan doğuyordu: Render'ın kapsayıcı diski geçicidir ve
SQLite dosyası her yeniden başlatmada silinerek hesapları, geçmişi, kredi
defterini ve önbelleği götürüyordu.

Çağıran kod iki arka uçta da aynı kalır: `execute` hâlâ `?` yer tutucusu alır,
satırlar hâlâ `row["sütun"]` ile okunur, `lastrowid` hâlâ çalışır. Böylece 60'ı
aşkın sorgunun tek tek elden geçirilmesi gerekmedi ve testler hızlı olsun diye
yerelde SQLite'ta koşmaya devam ediyor.
"""
from __future__ import annotations

import re
import sqlite3
import threading
from pathlib import Path

from .config import DATABASE_URL, DB_PATH

# SQLite'ta "INTEGER PRIMARY KEY" tek başına bile ROWID takma adıdır ve otomatik
# artar (AUTOINCREMENT eklense de eklenmese de). Postgres'te böyle bir davranış
# yok: eşleniği SERIAL. "searches" tablosu AUTOINCREMENT yazmadan bu davranışa
# güveniyordu; regex ikisini de yakalar.
_AUTOINC_RE = re.compile(r"INTEGER\s+PRIMARY\s+KEY(\s+AUTOINCREMENT)?", re.IGNORECASE)
# INSERT'ten sonra lastrowid'i okuyabilmek için id'yi geri istememiz gerekir.
_INSERT_RE = re.compile(r"^\s*INSERT\s+INTO", re.IGNORECASE)
_RETURNING_RE = re.compile(r"\bRETURNING\b", re.IGNORECASE)
_TABLE_RE = re.compile(r"^\s*INSERT\s+INTO\s+([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)


def _table_of(sql: str) -> str:
    match = _TABLE_RE.match(sql)
    return match.group(1).lower() if match else ""


def _to_postgres(sql: str) -> str:
    """SQLite lehçesini Postgres'e çevirir."""
    sql = _AUTOINC_RE.sub("SERIAL PRIMARY KEY", sql)
    # Yer tutucular: ? -> %s. Dizge sabitlerinin içindeki soru işaretleri korunur.
    out, in_string = [], False
    for ch in sql:
        if ch == "'":
            in_string = not in_string
        out.append("%s" if (ch == "?" and not in_string) else ch)
    return "".join(out)


class _Cursor:
    """fetchone/fetchall/lastrowid arayüzünü iki arka uçta da aynı tutar."""

    def __init__(self, cursor, lastrowid=None):
        self._cursor = cursor
        self._lastrowid = lastrowid

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def __iter__(self):
        return iter(self._cursor)

    @property
    def lastrowid(self):
        if self._lastrowid is not None:
            return self._lastrowid
        return getattr(self._cursor, "lastrowid", None)

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount


class Database:
    def __init__(self) -> None:
        self.backend = "postgres" if DATABASE_URL else "sqlite"
        self._lock = threading.Lock()
        self._conn = None
        self._no_id_tables: set[str] = set()
        self._connect()

    # ------------------------------------------------------------- bağlantı
    def _connect(self) -> None:
        if self.backend == "postgres":
            import psycopg
            from psycopg.rows import dict_row
            # autocommit: Neon boştayken işlemciyi uyutur ve bağlantıyı düşürür;
            # açık kalan bir işlem uyandıktan sonra geçersiz olurdu. Yazma
            # işlemleri zaten tek tek ve `_lock` altında yapılıyor.
            self._conn = psycopg.connect(DATABASE_URL, row_factory=dict_row,
                                         autocommit=True)
        else:
            Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=20)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=20000")

    def _is_connection_error(self, exc: Exception) -> bool:
        if self.backend != "postgres":
            return False
        import psycopg
        return isinstance(exc, (psycopg.OperationalError, psycopg.InterfaceError))

    # -------------------------------------------------------------- sorgular
    def execute(self, sql: str, params: tuple | list = ()) -> _Cursor:
        if self.backend == "sqlite":
            return _Cursor(self._conn.execute(sql, params))
        # Tek bağlantı bütün istek thread'leri tarafından paylaşılıyor. Yazmalar
        # zaten store._lock altında ama okumalar değil; imleç oluşturma ve çalıştırma
        # burada serileştirilir.
        with self._lock:
            return self._execute_postgres(sql, params)

    def _execute_postgres(self, sql: str, params: tuple | list = ()) -> _Cursor:
        base = _to_postgres(sql)
        # Postgres'te lastrowid yoktur; id'yi INSERT'ten geri isteriz. Anahtarı
        # metin olan tablolarda (search_cache, app_settings, auth_tokens) id sütunu
        # yoktur ve bu ek patlar; o tabloları bir kez öğrenip bir daha denemeyiz.
        wants_id = (bool(_INSERT_RE.match(sql)) and not _RETURNING_RE.search(sql)
                    and _table_of(sql) not in self._no_id_tables)
        reconnected = False

        while True:
            statement = (base.rstrip().rstrip(";") + " RETURNING id") if wants_id else base
            try:
                cursor = self._conn.cursor()
                cursor.execute(statement, tuple(params))
                break
            except Exception as exc:
                # Bağlantı hatası önce gelir: yoksa kopmuş bağlantıyı "id sütunu yok"
                # sanıp yeniden bağlanma hakkını harcardık.
                if self._is_connection_error(exc) and not reconnected:
                    print("[db] connection lost, reconnecting")
                    reconnected = True
                    self._connect()
                    continue
                if wants_id:
                    self._no_id_tables.add(_table_of(sql))
                    wants_id = False
                    continue
                raise

        last = None
        if wants_id:
            try:
                row = cursor.fetchone()
                last = row["id"] if row else None
            except Exception:
                last = None
        return _Cursor(cursor, last)

    def executescript(self, script: str) -> None:
        if self.backend == "sqlite":
            self._conn.executescript(script)
            return
        for statement in _split_statements(script):
            self.execute(statement)

    def commit(self) -> None:
        if self.backend == "sqlite":
            self._conn.commit()

    def fix_serial(self, table: str, column: str = "id") -> None:
        """Bir SERIAL sütununa elle değer yazıldıktan sonra diziyi ileri alır.

        Postgres'te elle verilen bir id, arka plandaki diziyi (sequence) ilerletmez;
        bir sonraki otomatik ekleme aynı id'yi tekrar üretip PRIMARY KEY çakışmasıyla
        patlar. `users` tablosuna yönetici id=1 olarak elle yazıldığı için gerekli.
        SQLite'ta ROWID zaten tablodaki gerçek en büyük değere göre ilerlediği için
        no-op'tur.
        """
        if self.backend != "postgres":
            return
        self.execute(
            f"SELECT setval(pg_get_serial_sequence('{table}', '{column}'),"
            f" COALESCE((SELECT MAX({column}) FROM {table}), 1))")

    def column_names(self, table: str) -> set[str]:
        """Şema göçleri için mevcut sütun adları."""
        if self.backend == "sqlite":
            return {r["name"] for r in
                    self.execute(f"PRAGMA table_info({table})").fetchall()}
        rows = self.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
            (table,)).fetchall()
        return {r["column_name"] for r in rows}


_db: Database | None = None
_db_lock = threading.Lock()


def get() -> Database:
    """Süreç genelinde tek bağlantı. `Database()` her çağrıda yeniden bağlanmasın diye."""
    global _db
    if _db is None:
        with _db_lock:
            if _db is None:
                _db = Database()
    return _db


def is_integrity_error(exc: Exception) -> bool:
    """UNIQUE/tekillik ihlali mi? İki motorda da farklı istisna sınıfı fırlatılır."""
    if isinstance(exc, sqlite3.IntegrityError):
        return True
    if get().backend == "postgres":
        import psycopg
        return isinstance(exc, psycopg.errors.UniqueViolation)
    return False


def _split_statements(script: str) -> list[str]:
    """Şema betiğini tek tek ifadelere böler (dizge içindeki ';' korunur)."""
    statements, current, in_string = [], [], False
    for ch in script:
        if ch == "'":
            in_string = not in_string
        if ch == ";" and not in_string:
            if current and "".join(current).strip():
                statements.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    if current and "".join(current).strip():
        statements.append("".join(current).strip())
    return statements

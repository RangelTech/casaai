"""Banco do Casa AI: SQLite num volume (/data). Hora em epoch (s)."""
from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager

ESQUEMA = """
CREATE TABLE IF NOT EXISTS usuarios (id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, criado REAL NOT NULL,
  plano TEXT NOT NULL DEFAULT 'teste', ativo_ate REAL NOT NULL DEFAULT 0, pedido TEXT);
CREATE TABLE IF NOT EXISTS codigos (email TEXT PRIMARY KEY, hash TEXT NOT NULL, expira REAL NOT NULL, tentativas INTEGER NOT NULL DEFAULT 0, enviado REAL NOT NULL);
CREATE TABLE IF NOT EXISTS sessoes (hash TEXT PRIMARY KEY, usuario INTEGER NOT NULL, expira REAL NOT NULL);
CREATE TABLE IF NOT EXISTS eventos_cakto (id TEXT PRIMARY KEY, recebido REAL NOT NULL, corpo TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS assinaturas (usuario INTEGER PRIMARY KEY, plano TEXT NOT NULL, ativo_ate REAL NOT NULL, pedido TEXT);

-- um imóvel = um site cinematográfico público em /c/<slug>
CREATE TABLE IF NOT EXISTS imoveis (
  id INTEGER PRIMARY KEY, usuario INTEGER NOT NULL, slug TEXT UNIQUE NOT NULL, titulo TEXT NOT NULL, tipo TEXT NOT NULL DEFAULT 'casa',
  negocio TEXT NOT NULL DEFAULT 'venda', preco REAL, bairro TEXT NOT NULL DEFAULT '', cidade TEXT NOT NULL DEFAULT '',
  quartos INTEGER, suites INTEGER, banheiros INTEGER, vagas INTEGER, area REAL, descricao TEXT NOT NULL DEFAULT '',
  whatsapp TEXT NOT NULL DEFAULT '', corretor TEXT NOT NULL DEFAULT '', cor TEXT NOT NULL DEFAULT '#ffb547',
  estado TEXT NOT NULL DEFAULT 'rascunho',   -- rascunho | processando | pronto | erro
  etapa TEXT NOT NULL DEFAULT '', erro TEXT, textos TEXT, quadros INTEGER NOT NULL DEFAULT 0, visitas INTEGER NOT NULL DEFAULT 0,
  cliques INTEGER NOT NULL DEFAULT 0, criado REAL NOT NULL, pronto_em REAL
);
CREATE INDEX IF NOT EXISTS imoveis_usuario ON imoveis(usuario, criado);
"""


class Banco:
    def __init__(self, caminho: str):
        self._trava = threading.Lock()
        self.con = sqlite3.connect(caminho, check_same_thread=False, isolation_level=None)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.executescript(ESQUEMA)

    @contextmanager
    def tx(self):
        with self._trava:
            self.con.execute("BEGIN IMMEDIATE")
            try:
                yield self.con
                self.con.execute("COMMIT")
            except BaseException:
                self.con.execute("ROLLBACK")
                raise

    def exec(self, sql: str, args: tuple = ()):
        with self._trava:
            return self.con.execute(sql, args)

    def um(self, sql: str, args: tuple = ()) -> dict | None:
        with self._trava:
            r = self.con.execute(sql, args).fetchone()
        return dict(r) if r else None

    def todos(self, sql: str, args: tuple = ()) -> list[dict]:
        with self._trava:
            return [dict(r) for r in self.con.execute(sql, args).fetchall()]


def agora() -> float:
    return time.time()

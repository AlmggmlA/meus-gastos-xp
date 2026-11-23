"""Painel e API de faturas, disponíveis apenas na máquina local."""

import argparse
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
from urllib.parse import parse_qs

from faturas import (
    FaturaExistente, categorizar_lancamento, conectar, importar, inicializar, lancamentos,
    listar_faturas, resumo,
)
from analises import analises_cartao

PASTA = Path(__file__).resolve().parent
LIMITE_CSV = 8 * 1024 * 1024
ESTATICOS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/analises.js": ("analises.js", "text/javascript; charset=utf-8"),
    "/estilo.css": ("estilo.css", "text/css; charset=utf-8"),
}


class Servidor(ThreadingHTTPServer):
    def __init__(self, porta, banco):
        self.banco = banco
        super().__init__(("127.0.0.1", porta), Requisicao)
        self.hosts = {
            f"127.0.0.1:{self.server_port}", f"localhost:{self.server_port}"
        }


class Requisicao(BaseHTTPRequestHandler):
    def setup(self):
        self.request.settimeout(15)
        super().setup()

    def log_message(self, formato, *args):
        pass

    def resposta(self, codigo, tipo, corpo):
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
        )
        self.end_headers()
        self.wfile.write(corpo)

    def json(self, codigo, dados):
        corpo = json.dumps(dados, ensure_ascii=False).encode("utf-8")
        self.resposta(codigo, "application/json; charset=utf-8", corpo)

    def verificar_origem(self, api=True):
        host = self.headers.get("Host")
        origin = self.headers.get("Origin")
        if (host in self.server.hosts and origin in (None, f"http://{host}")
                and (not api or self.headers.get("Sec-Fetch-Site") != "cross-site")):
            return True
        self.json(403, {"erro": "acesse o painel pelo endereço local"})
        return False

    def do_GET(self):
        rota, _, consulta = self.path.partition("?")
        if not self.verificar_origem(api=rota not in ESTATICOS):
            return
        if rota in ESTATICOS:
            nome, tipo = ESTATICOS[rota]
            self.resposta(200, tipo, (PASTA / "static" / nome).read_bytes())
            return
        try:
            with closing(conectar(self.server.banco)) as conn:
                parametros = parse_qs(consulta, keep_blank_values=True)
                if rota == "/api/faturas":
                    self.json(200, listar_faturas(conn))
                elif rota in ("/api/resumo", "/api/lancamentos", "/api/analises"):
                    fatura = self.inteiro(parametros, "fatura_id", 0, 1, 2**63 - 1)
                    if rota == "/api/analises":
                        conn.execute("BEGIN")
                    selecionada = conn.execute(
                        "SELECT cartao FROM fatura WHERE id = ?", (fatura,)
                    ).fetchone()
                    if selecionada is None:
                        self.json(404, {"erro": "fatura não encontrada"})
                    elif rota == "/api/resumo":
                        self.json(200, resumo(conn, fatura))
                    elif rota == "/api/analises":
                        self.json(200, analises_cartao(conn, selecionada["cartao"], fatura))
                    else:
                        limite = self.inteiro(parametros, "limite", 50, 1, 200)
                        inicio = self.inteiro(parametros, "inicio", 0, 0, 1_000_000)
                        self.json(200, lancamentos(
                            conn, fatura, parametros.get("busca", [""])[0][:120],
                            parametros.get("natureza", [""])[0], parametros.get("categoria", [""])[0],
                            limite, inicio,
                        ))
                else:
                    self.json(404, {"erro": "endereço não encontrado"})
        except ValueError as exc:
            self.json(400, {"erro": str(exc)})
        except sqlite3.Error:
            self.json(503, {"erro": "não foi possível consultar o banco local"})

    @staticmethod
    def inteiro(parametros, nome, padrao, minimo, maximo):
        try:
            valor = int(parametros.get(nome, [padrao])[0])
            if minimo <= valor <= maximo:
                return valor
        except ValueError:
            pass
        raise ValueError(f"parâmetro inválido: {nome}")

    def do_POST(self):
        if not self.verificar_origem():
            return
        rota, _, consulta = self.path.partition("?")
        if rota == "/api/categorias":
            self.categorias()
            return
        if rota != "/api/importar":
            self.json(404, {"erro": "endereço não encontrado"})
            return
        if self.headers.get_content_type() != "application/octet-stream":
            self.json(415, {"erro": "envie o CSV com Content-Type application/octet-stream"})
            return
        try:
            quantidade = self.inteiro(
                {"Content-Length": [self.headers.get("Content-Length", "0")]},
                "Content-Length", 0, 1, LIMITE_CSV,
            )
            conteudo = self.rfile.read(quantidade)
            if len(conteudo) != quantidade:
                raise ValueError("o arquivo foi recebido incompleto")
            parametros = parse_qs(consulta, keep_blank_values=True)
            with closing(conectar(self.server.banco)) as conn:
                resultado = importar(
                    conn, conteudo, parametros.get("cartao", ["Cartão XP"])[0],
                    parametros.get("periodo", [""])[0], parametros.get("sinal", ["positivo"])[0],
                    substituir=parametros.get("substituir") == ["1"],
                )
            self.json(201 if resultado["status"] == "importada" else 200, resultado)
        except FaturaExistente as exc:
            self.json(409, {"erro": str(exc)})
        except ValueError as exc:
            self.json(400, {"erro": str(exc)})
        except sqlite3.Error:
            self.json(503, {"erro": "não foi possível gravar a fatura no banco local"})

    def categorias(self):
        if self.headers.get_content_type() != "application/json":
            self.json(415, {"erro": "envie a categoria em JSON"})
            return
        try:
            quantidade = self.inteiro(
                {"Content-Length": [self.headers.get("Content-Length", "0")]},
                "Content-Length", 0, 1, 10_000,
            )
            dados = json.loads(self.rfile.read(quantidade).decode("utf-8"))
            if not isinstance(dados, dict):
                raise ValueError("dados de categoria inválidos")
            with closing(conectar(self.server.banco)) as conn:
                resultado = categorizar_lancamento(
                    conn, int(dados.get("lancamento_id")), dados.get("categoria", ""),
                    dados.get("aplicar_estabelecimento") is True,
                )
            self.json(200, resultado)
        except (TypeError, json.JSONDecodeError, ValueError) as exc:
            self.json(400, {"erro": str(exc) or "dados de categoria inválidos"})
        except sqlite3.Error:
            self.json(503, {"erro": "não foi possível salvar a categoria"})


def main():
    argumentos = argparse.ArgumentParser(description=__doc__)
    argumentos.add_argument("--porta", type=int, default=8787)
    argumentos.add_argument("--banco", type=Path, default=PASTA / "dados" / "gastos.sqlite3")
    args = argumentos.parse_args()
    if not 1024 <= args.porta <= 65535:
        argumentos.error("use uma porta entre 1024 e 65535")
    try:
        args.banco.parent.mkdir(parents=True, exist_ok=True)
        with closing(conectar(args.banco)) as conn:
            inicializar(conn)
        with Servidor(args.porta, args.banco) as servidor:
            print(f"Abra http://127.0.0.1:{servidor.server_port} no navegador.", flush=True)
            print("Pressione Ctrl+C para encerrar.", flush=True)
            servidor.serve_forever()
    except KeyboardInterrupt:
        pass
    except (OSError, sqlite3.Error) as exc:
        argumentos.exit(1, f"Não foi possível iniciar o painel. {exc}\n")


if __name__ == "__main__":
    main()

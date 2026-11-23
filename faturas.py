"""Leitura de faturas CSV e persistência em SQLite."""

import csv
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
from io import StringIO
from pathlib import Path
import re
import sqlite3
import unicodedata


def normalizar(texto):
    texto = unicodedata.normalize("NFKD", texto.casefold())
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    return " ".join(re.findall(r"[a-z0-9]+", texto))


COLUNAS = {
    "data": {"data", "data da compra", "data compra", "data da transacao",
             "data transacao", "data do lancamento", "data lancamento"},
    "descricao": {"descricao", "descricao da compra", "descricao da transacao",
                  "descricao do lancamento", "lancamento", "estabelecimento",
                  "nome do estabelecimento", "historico", "transacao", "compra"},
    "estabelecimento": {"estabelecimento", "nome do estabelecimento", "merchant"},
    "valor": {"valor", "valor brl", "valor em brl", "valor em reais", "valor r",
              "valor da compra", "valor da transacao", "valor do lancamento",
              "valor original em reais"},
    "portador": {"portador", "nome do portador", "titular", "cartao"},
    "tipo": {"tipo", "tipo de lancamento", "tipo de transacao"},
    "moeda": {"moeda", "moeda do lancamento"},
    "parcela": {"parcela", "parcela atual", "numero da parcela", "n da parcela",
                "parcelamento"},
    "total_parcelas": {"total de parcelas", "total parcelas", "numero de parcelas",
                       "quantidade de parcelas", "qtd parcelas"},
}
OBRIGATORIAS = {"data", "descricao", "valor"}
PAGAMENTOS = re.compile(
    r"^(pagamento (?:da |de )?fatura|pagamento recebido|pgto (?:da )?fatura|"
    r"pagto (?:da )?fatura|pagamento via (?:pix|boleto)|pagamento efetuado)(?: |$)"
)
CATEGORIAS = (
    "Supermercado", "Restaurantes e delivery", "Transporte", "Saúde e farmácia", "Casa",
    "Compras pessoais", "Assinaturas", "Lazer", "Tarifas e encargos", "Outros",
)
SEM_CATEGORIA = "Sem categoria"


@dataclass(frozen=True)
class Lancamento:
    data: str
    descricao: str
    valor_centavos: int
    portador: str
    natureza: str
    parcela: str
    estabelecimento: str


class FaturaExistente(ValueError):
    pass


def chave_lancamento(item):
    campos = ("data", "descricao", "valor_centavos", "portador", "natureza", "parcela", "estabelecimento")
    try:
        return tuple(item[campo] for campo in campos)
    except TypeError:
        return tuple(getattr(item, campo) for campo in campos)


def converter_data(campo):
    for formato in ("%d/%m/%Y", "%Y-%m-%d", "%d/%m/%y"):
        try:
            return datetime.strptime(campo, formato).date().isoformat()
        except ValueError:
            continue
    raise ValueError("data inválida ou sem ano")


def converter_valor(campo):
    valor = re.sub(r"\s+", "", campo.upper().replace("R$", "").replace("BRL", ""))
    if valor.startswith("(") and valor.endswith(")"):
        valor = "-" + valor[1:-1]
    if valor.endswith("-"):
        valor = "-" + valor[:-1]
    if re.fullmatch(r"[+-]?(?:\d{1,3}(?:\.\d{3})+|\d+)(?:,\d{1,2})?", valor):
        valor = valor.replace(".", "").replace(",", ".")
    elif not re.fullmatch(r"[+-]?\d+\.\d{1,2}", valor):
        raise ValueError("valor em reais inválido")
    centavos = int(Decimal(valor) * 100)
    if abs(centavos) > 10**12:
        raise ValueError("valor acima do limite aceito")
    return centavos


def abrir_csv(conteudo):
    if conteudo.startswith((b"\xff\xfe", b"\xfe\xff")):
        return conteudo.decode("utf-16")
    if b"\x00" in conteudo:
        raise ValueError("arquivo inválido. Selecione uma fatura em CSV")
    try:
        return conteudo.decode("utf-8-sig")
    except UnicodeDecodeError:
        return conteudo.decode("cp1252")


def procurar_cabecalho(texto):
    for separador in (";", ",", "\t"):
        leitor = csv.reader(StringIO(texto, newline=""), delimiter=separador, strict=True)
        try:
            for _ in range(30):
                linha = next(leitor, None)
                if linha is None:
                    break
                colunas = {}
                for indice, titulo in enumerate(linha):
                    nome = normalizar(titulo)
                    for campo, alternativas in COLUNAS.items():
                        if nome in alternativas and campo not in colunas:
                            colunas[campo] = indice
                if OBRIGATORIAS <= colunas.keys():
                    return leitor, colunas
        except csv.Error:
            continue
    raise ValueError("cabeçalho não identificado. O CSV precisa de Data, Descrição e Valor")


def ler_fatura(conteudo, sinal):
    if sinal not in ("positivo", "negativo"):
        raise ValueError("selecione como as compras aparecem no CSV")
    texto = abrir_csv(conteudo)
    leitor, colunas = procurar_cabecalho(texto)
    registros = []
    try:
        for linha in leitor:
            if not any(campo.strip() for campo in linha):
                continue
            try:
                campos = {
                    nome: linha[indice].strip() if indice < len(linha) else ""
                    for nome, indice in colunas.items()
                }
                if not campos["descricao"]:
                    raise ValueError("descrição ausente")
                moeda = campos.get("moeda", "").upper()
                if moeda not in ("", "BRL", "R$", "REAL", "REAIS"):
                    raise ValueError("a coluna de valor deve estar em reais")
                data = converter_data(campos["data"])
                valor = converter_valor(campos["valor"])
                if sinal == "negativo":
                    valor = -valor
                tipo = normalizar(campos.get("tipo", ""))
                descricao = normalizar(campos["descricao"])
                if tipo == "pagamento" or PAGAMENTOS.match(tipo) or PAGAMENTOS.match(descricao):
                    natureza = "pagamento"
                else:
                    natureza = "credito" if valor < 0 else "debito"
                parcela = campos.get("parcela", "")
                total_parcelas = campos.get("total_parcelas", "")
                if parcela.isdecimal() and total_parcelas.isdecimal():
                    parcela = f"{parcela}/{total_parcelas}"
                registros.append(Lancamento(
                    data, campos["descricao"], valor, campos.get("portador", ""),
                    natureza, parcela, campos.get("estabelecimento") or campos["descricao"],
                ))
            except ValueError as exc:
                raise ValueError(f"linha {leitor.line_num}: {exc}") from exc
    except csv.Error as exc:
        raise ValueError(f"CSV inválido perto da linha {leitor.line_num}") from exc
    if not registros:
        raise ValueError("o CSV não contém lançamentos")
    return registros


def validar_periodo(periodo):
    if not re.fullmatch(r"\d{4}-\d{2}", periodo):
        raise ValueError("informe o mês da fatura no formato AAAA-MM")
    try:
        date.fromisoformat(periodo + "-01")
    except ValueError as exc:
        raise ValueError("mês da fatura inválido") from exc
    return periodo


def conectar(banco: Path):
    conn = sqlite3.connect(banco, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.create_function("normalizar", 1, normalizar, deterministic=True)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def inicializar(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS fatura (
            id INTEGER PRIMARY KEY,
            cartao TEXT NOT NULL,
            periodo TEXT NOT NULL,
            hash_csv TEXT NOT NULL,
            sinal TEXT NOT NULL,
            importada_em TEXT NOT NULL,
            UNIQUE(cartao, periodo)
        );
        CREATE TABLE IF NOT EXISTS lancamento (
            id INTEGER PRIMARY KEY,
            fatura_id INTEGER NOT NULL REFERENCES fatura(id) ON DELETE CASCADE,
            data TEXT NOT NULL,
            descricao TEXT NOT NULL,
            valor_centavos INTEGER NOT NULL,
            portador TEXT NOT NULL,
            natureza TEXT NOT NULL CHECK(natureza IN ('debito', 'credito', 'pagamento')),
            parcela TEXT,
            estabelecimento TEXT NOT NULL DEFAULT '',
            categoria_manual TEXT
        );
        CREATE TABLE IF NOT EXISTS regra_categoria_estabelecimento (
            estabelecimento_chave TEXT PRIMARY KEY,
            categoria TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS lancamento_fatura_data
        ON lancamento(fatura_id, data);
    """)
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        colunas = {row["name"] for row in conn.execute("PRAGMA table_info(lancamento)")}
        if "parcela" not in colunas:
            conn.execute("ALTER TABLE lancamento ADD COLUMN parcela TEXT")
        if "estabelecimento" not in colunas:
            conn.execute("ALTER TABLE lancamento ADD COLUMN estabelecimento TEXT NOT NULL DEFAULT ''")
        if "categoria_manual" not in colunas:
            conn.execute("ALTER TABLE lancamento ADD COLUMN categoria_manual TEXT")
        conn.execute("UPDATE lancamento SET estabelecimento = descricao WHERE estabelecimento = ''")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS orcamento_categoria (
                cartao TEXT NOT NULL,
                categoria TEXT NOT NULL,
                limite_centavos INTEGER NOT NULL CHECK(limite_centavos >= 0),
                atualizado_em TEXT NOT NULL,
                PRIMARY KEY(cartao, categoria)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS orcamento_categoria_cartao
            ON orcamento_categoria(cartao)
        """)


def recuperar_coluna_parcela(conn, fatura_id, registros):
    salvos = conn.execute(
        "SELECT id, parcela FROM lancamento WHERE fatura_id = ? ORDER BY id", (fatura_id,)
    ).fetchall()
    if len(salvos) != len(registros):
        return
    conn.executemany(
        "UPDATE lancamento SET parcela = ? WHERE id = ?",
        [(item.parcela, salvo["id"]) for salvo, item in zip(salvos, registros)
         if salvo["parcela"] is None],
    )


def importar(conn, conteudo, cartao, periodo, sinal, substituir=False):
    periodo = validar_periodo(periodo)
    cartao = " ".join(cartao.split())
    if not cartao or len(cartao) > 60:
        raise ValueError("informe um apelido do cartão com até 60 caracteres")
    registros = ler_fatura(conteudo, sinal)
    hash_csv = sha256(conteudo).hexdigest()
    agora = datetime.now(timezone.utc).isoformat(timespec="seconds")

    with conn:
        conn.execute("BEGIN IMMEDIATE")
        existente = conn.execute(
            "SELECT id, hash_csv, sinal FROM fatura WHERE cartao = ? AND periodo = ?",
            (cartao, periodo),
        ).fetchone()
        if existente:
            fatura_id = existente["id"]
            if existente["hash_csv"] == hash_csv and existente["sinal"] == sinal:
                recuperar_coluna_parcela(conn, fatura_id, registros)
                return {"id": fatura_id, "linhas": len(registros), "status": "ja_importada"}
            if not substituir:
                raise FaturaExistente("já existe uma fatura com esse cartão e mês")
            anteriores = conn.execute("""
                SELECT data, descricao, valor_centavos, portador, natureza, parcela,
                       estabelecimento, categoria_manual
                FROM lancamento WHERE fatura_id = ? AND categoria_manual IS NOT NULL
            """, (fatura_id,)).fetchall()
            manuais = {}
            repetidos = set()
            for anterior in anteriores:
                chave = chave_lancamento(anterior)
                if chave in manuais:
                    repetidos.add(chave)
                else:
                    manuais[chave] = anterior["categoria_manual"]
            conn.execute("DELETE FROM lancamento WHERE fatura_id = ?", (fatura_id,))
            conn.execute(
                "UPDATE fatura SET hash_csv = ?, sinal = ?, importada_em = ? WHERE id = ?",
                (hash_csv, sinal, agora, fatura_id),
            )
        else:
            manuais = {}
            repetidos = set()
            fatura_id = conn.execute(
                "INSERT INTO fatura(cartao, periodo, hash_csv, sinal, importada_em) "
                "VALUES (?, ?, ?, ?, ?)", (cartao, periodo, hash_csv, sinal, agora),
            ).lastrowid

        repetidos.update(chave for chave, quantidade in Counter(
            chave_lancamento(item) for item in registros
        ).items() if quantidade > 1)

        conn.executemany(
            "INSERT INTO lancamento"
            "(fatura_id, data, descricao, valor_centavos, portador, natureza, parcela, estabelecimento, categoria_manual) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(fatura_id, item.data, item.descricao, item.valor_centavos, item.portador,
              item.natureza, item.parcela, item.estabelecimento,
              manuais.get(chave_lancamento(item)) if chave_lancamento(item) not in repetidos else None)
             for item in registros],
        )
    return {
        "id": fatura_id, "linhas": len(registros),
        "status": "substituida" if existente else "importada",
    }


def listar_faturas(conn):
    cursor = conn.execute("""
        SELECT f.id, f.cartao, f.periodo, f.importada_em, COUNT(l.id) AS linhas
        FROM fatura f LEFT JOIN lancamento l ON l.fatura_id = f.id
        GROUP BY f.id ORDER BY f.periodo DESC, f.cartao
    """)
    return [dict(row) for row in cursor]


def lancamentos(conn, fatura_id, busca, natureza, categoria, limite, inicio):
    if natureza not in ("", "debito", "credito", "pagamento"):
        raise ValueError("tipo de lançamento inválido")
    cursor = conn.execute("""
        SELECT l.id, l.data, l.descricao, l.valor_centavos, l.portador, l.natureza,
               l.estabelecimento,
               CASE WHEN l.categoria_manual IS NOT NULL THEN l.categoria_manual
                    ELSE COALESCE(r.categoria, '') END AS categoria
        FROM lancamento l LEFT JOIN regra_categoria_estabelecimento r
          ON r.estabelecimento_chave = normalizar(l.estabelecimento)
        WHERE l.fatura_id = ? ORDER BY l.data DESC, l.id
    """, (fatura_id,))
    busca = normalizar(busca)
    registros = [
        row for row in cursor
        if (not natureza or row["natureza"] == natureza)
        and (categoria != "__sem_categoria__" or not row["categoria"])
        and (categoria in ("", "__sem_categoria__") or row["categoria"] == categoria)
        and (not busca or busca in normalizar(f'{row["descricao"]} {row["portador"]}'))
    ]
    return {"total": len(registros), "itens": [dict(row) for row in registros[inicio:inicio + limite]]}


def categorizar_lancamento(conn, lancamento_id, categoria, aplicar_estabelecimento):
    if categoria not in (*CATEGORIAS, ""):
        raise ValueError("categoria inválida")
    linha = conn.execute(
        "SELECT estabelecimento FROM lancamento WHERE id = ?", (lancamento_id,)
    ).fetchone()
    if linha is None:
        raise ValueError("lançamento não encontrado")
    chave = normalizar(linha["estabelecimento"])
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE lancamento SET categoria_manual = ? WHERE id = ?", (categoria, lancamento_id))
        if aplicar_estabelecimento:
            if categoria:
                conn.execute("""
                    INSERT INTO regra_categoria_estabelecimento(estabelecimento_chave, categoria)
                    VALUES (?, ?) ON CONFLICT(estabelecimento_chave) DO UPDATE SET categoria = excluded.categoria
                """, (chave, categoria))
            else:
                conn.execute("DELETE FROM regra_categoria_estabelecimento WHERE estabelecimento_chave = ?", (chave,))
    return {"categoria": categoria, "estabelecimento": linha["estabelecimento"]}


def salvar_orcamento(conn, cartao, categoria, limite):
    cartao = " ".join(cartao.split())
    if not cartao or len(cartao) > 60:
        raise ValueError("informe um apelido do cartão com até 60 caracteres")
    if categoria not in (*CATEGORIAS, SEM_CATEGORIA):
        raise ValueError("categoria inválida")
    limite_centavos = converter_valor(str(limite))
    if limite_centavos < 0:
        raise ValueError("o orçamento não pode ser negativo")
    agora = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with conn:
        conn.execute("""
            INSERT INTO orcamento_categoria(cartao, categoria, limite_centavos, atualizado_em)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(cartao, categoria) DO UPDATE SET
              limite_centavos = excluded.limite_centavos,
              atualizado_em = excluded.atualizado_em
        """, (cartao, categoria, limite_centavos, agora))
    return {"cartao": cartao, "categoria": categoria, "limite_centavos": limite_centavos}


def resumo(conn, fatura_id):
    row = conn.execute("""
        SELECT COUNT(*) AS linhas,
          COALESCE(SUM(CASE WHEN natureza = 'debito' THEN valor_centavos END), 0)
            AS debitos_centavos,
          COALESCE(SUM(CASE WHEN natureza = 'credito' THEN -valor_centavos END), 0)
            AS creditos_centavos
        FROM lancamento WHERE fatura_id = ?
    """, (fatura_id,)).fetchone()
    return dict(row)

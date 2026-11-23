"""Análise de gastos e acompanhamento de parcelamentos nas faturas locais."""

from collections import Counter, defaultdict
import re

from faturas import normalizar

MIN_CICLO = 12          # Janeiro do ano 1.
MAX_CICLO = 9999 * 12 + 11
NUMERACAO = r"(?<![\d/])(?P<numero>[0-9]{1,3})\s*(?:/|\bde\b)\s*(?P<total>[0-9]{1,3})(?![\d/])"
COLUNA = re.compile(rf"(?:parc(?:ela)?\.?\s*)?{NUMERACAO}", re.IGNORECASE)
MARCADOR = re.compile(rf"\bparc(?:ela)?\.?\s*[:#-]?\s*{NUMERACAO}", re.IGNORECASE)
SUFIXO = re.compile(rf"(?:^|\s|\(|\[){NUMERACAO}\s*[)\]]?\s*$", re.IGNORECASE)


def identificar_parcela(descricao, coluna):
    na_coluna = COLUNA.fullmatch(coluna or "")
    na_descricao = MARCADOR.search(descricao) or SUFIXO.search(descricao)
    encontrada = na_coluna or na_descricao
    if encontrada is None:
        return None
    numero, total = (int(encontrada[key]) for key in ("numero", "total"))
    if not 1 <= numero <= total <= 120 or total == 1:
        return None
    nome = descricao
    referencia_alternativa = None
    if na_descricao:
        referencia = tuple(int(na_descricao[key]) for key in ("numero", "total"))
        if referencia != (numero, total):
            referencia_alternativa = referencia
        nome = descricao[:na_descricao.start()] + " " + descricao[na_descricao.end():]
        nome = re.sub(r"\(\s*\)|\[\s*\]", " ", nome)
        nome = " ".join(nome.split()).strip(" —–-·:()[]")
    return nome or descricao, numero, total, referencia_alternativa


def ciclo(periodo):
    ano, mes = map(int, periodo.split("-"))
    return ano * 12 + mes - 1


def periodo(ciclo_mensal):
    ano, indice_mes = divmod(ciclo_mensal, 12)
    return f"{ano:04d}-{indice_mes + 1:02d}"


def historico_mensal(conn, cartao):
    return [dict(row) for row in conn.execute("""
        SELECT f.periodo,
               SUM(CASE l.natureza
                       WHEN 'debito' THEN l.valor_centavos
                       WHEN 'credito' THEN l.valor_centavos
                       ELSE 0 END) AS liquido_centavos
        FROM fatura f
        LEFT JOIN lancamento l ON l.fatura_id = f.id
        WHERE f.cartao = ?
        GROUP BY f.id ORDER BY f.periodo
    """, (cartao,))]


def descrever_compra(lancamentos, ultimo_ciclo, projecao):
    lancamentos.sort(key=lambda item: (item["numero"], item["data"], item["fatura_id"]))
    primeira = lancamentos[0]
    ultima = lancamentos[-1]
    total = primeira["total"]
    ciclo_primeira = ciclo(primeira["periodo"]) - primeira["numero"] + 1
    contagem = Counter(item["numero"] for item in lancamentos)
    ambiguo = any(qtd > 1 for qtd in contagem.values())
    conflito = any(item["conflito"] for item in lancamentos)
    valores = {item["valor_centavos"] for item in lancamentos}
    valor_variavel = max(valores) - min(valores) > 1
    motivo = ""
    if ambiguo:
        motivo = "Há mais de um lançamento com o mesmo número de parcela."
    elif conflito:
        motivo = "A coluna de parcelas diverge da descrição do lançamento."
    elif valor_variavel and ultima["numero"] < total:
        motivo = "As parcelas têm valores diferentes."
    elif ciclo_primeira < MIN_CICLO or ciclo_primeira + total - 1 > MAX_CICLO:
        motivo = "O período da compra está fora do intervalo de datas."

    realizadas = []
    for item in lancamentos:
        realizadas.append({
            "numero": item["numero"], "data": item["data"], "periodo": item["periodo"],
            "fatura_id": item["fatura_id"], "valor_centavos": item["valor_centavos"],
        })

    previstas = []
    faltantes = []
    if not motivo:
        for numero in range(1, total + 1):
            if numero in contagem:
                continue
            ciclo_parcela = ciclo_primeira + numero - 1
            mes = periodo(ciclo_parcela)
            if ciclo_parcela > ultimo_ciclo:
                previsto = ultima["valor_centavos"]
                previstas.append({"numero": numero, "periodo": mes, "valor_centavos": previsto})
                projecao[mes] += previsto
            else:
                faltantes.append({"numero": numero, "periodo": mes})

    if motivo:
        status = "conferir"
    elif previstas:
        status = "em_andamento"
    elif faltantes:
        status = "incompleto"
    else:
        status = "completo"

    return {
        "descricao": primeira["descricao"], "portador": primeira["portador"],
        "total": total, "numeros_observados": list(contagem), "status": status, "motivo": motivo,
        "realizadas": realizadas, "previstas": previstas, "faltantes": faltantes,
        "saldo_previsto_centavos": sum(item["valor_centavos"] for item in previstas),
        "ultimo_mes_observado": ultima["periodo"],
    }


def acompanhar_parcelas(conn, cartao, ultimo_ciclo):
    grupos = defaultdict(list)
    vinculos_conflitantes = set()
    registros = conn.execute("""
        SELECT l.data, l.descricao, l.valor_centavos, l.portador, l.parcela,
               f.id AS fatura_id, f.periodo
        FROM lancamento l JOIN fatura f ON f.id = l.fatura_id
        WHERE f.cartao = ? AND l.natureza = 'debito' AND l.valor_centavos > 0
    """, (cartao,))
    for linha in registros:
        parcela = identificar_parcela(linha["descricao"], linha["parcela"])
        if parcela is None:
            continue
        descricao, numero, total, referencia_alternativa = parcela
        fatura = ciclo(linha["periodo"])
        identidade = (normalizar(descricao), normalizar(linha["portador"]))
        chave = (*identidade, total, fatura - numero + 1)
        if referencia_alternativa:
            numero_alternativo, total_alternativo = referencia_alternativa
            vinculos_conflitantes.add(
                (*identidade, total_alternativo, fatura - numero_alternativo + 1)
            )
        grupos[chave].append({
            "data": linha["data"], "descricao": descricao, "numero": numero, "total": total,
            "conflito": bool(referencia_alternativa), "portador": linha["portador"],
            "fatura_id": linha["fatura_id"],
            "periodo": linha["periodo"], "valor_centavos": linha["valor_centavos"],
        })

    projecao = defaultdict(int)
    for chave in vinculos_conflitantes.intersection(grupos):
        grupos[chave][0]["conflito"] = True
    compras = [
        descrever_compra(lancamentos, ultimo_ciclo, projecao) for lancamentos in grupos.values()
    ]
    ordem = {"conferir": 0, "em_andamento": 1, "incompleto": 2, "completo": 3}
    compras.sort(key=lambda item: item["ultimo_mes_observado"], reverse=True)
    compras.sort(key=lambda item: ordem[item["status"]])
    meses_previstos = [
        {"periodo": mes, "previsto_centavos": valor} for mes, valor in sorted(projecao.items())
    ]
    return compras, meses_previstos


def gastos_por_categoria(conn, fatura_id):
    return [dict(linha) for linha in conn.execute("""
        SELECT CASE WHEN l.categoria_manual IS NOT NULL THEN l.categoria_manual
                    ELSE COALESCE(r.categoria, '') END AS categoria,
               SUM(l.valor_centavos) AS total_centavos
        FROM lancamento l LEFT JOIN regra_categoria_estabelecimento r
          ON r.estabelecimento_chave = normalizar(l.estabelecimento)
        WHERE l.fatura_id = ? AND l.natureza = 'debito' AND l.valor_centavos > 0
        GROUP BY categoria
        ORDER BY total_centavos DESC, categoria
    """, (fatura_id,))]


def analises_cartao(conn, cartao, fatura_id):
    historico = historico_mensal(conn, cartao)
    compras, previstos = acompanhar_parcelas(conn, cartao, ciclo(historico[-1]["periodo"]))
    return {
        "cartao": cartao, "historico": historico, "previstos": previstos, "compras": compras,
        "categorias": [
            {**item, "categoria": item["categoria"] or "Sem categoria"}
            for item in gastos_por_categoria(conn, fatura_id)
        ],
    }

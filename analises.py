"""Análise de gastos e acompanhamento de parcelamentos nas faturas locais."""

from collections import Counter, defaultdict
import re

from faturas import CATEGORIAS, SEM_CATEGORIA, normalizar

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
        "categoria": next((item["categoria"] for item in lancamentos if item["categoria"] != SEM_CATEGORIA), SEM_CATEGORIA),
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
               CASE WHEN l.categoria_manual IS NOT NULL THEN l.categoria_manual
                    ELSE COALESCE(r.categoria, '') END AS categoria,
               f.id AS fatura_id, f.periodo
        FROM lancamento l JOIN fatura f ON f.id = l.fatura_id
        LEFT JOIN regra_categoria_estabelecimento r
          ON r.estabelecimento_chave = normalizar(l.estabelecimento)
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
            "categoria": linha["categoria"] or SEM_CATEGORIA,
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


def _linhas_categorizadas(conn, cartao):
    return [dict(linha) for linha in conn.execute("""
        SELECT f.periodo, l.estabelecimento, l.valor_centavos, l.natureza,
               CASE WHEN l.categoria_manual IS NOT NULL THEN l.categoria_manual
                    ELSE COALESCE(r.categoria, '') END AS categoria
        FROM lancamento l JOIN fatura f ON f.id = l.fatura_id
        LEFT JOIN regra_categoria_estabelecimento r
          ON r.estabelecimento_chave = normalizar(l.estabelecimento)
        WHERE f.cartao = ?
    """, (cartao,))]


def _compromissos_futuros(conn, cartao):
    historico = historico_mensal(conn, cartao)
    if not historico:
        return []
    compras, _ = acompanhar_parcelas(conn, cartao, ciclo(historico[-1]["periodo"]))
    totais = defaultdict(lambda: [0, 0])
    for compra in compras:
        for parcela in compra["previstas"]:
            totais[(parcela["periodo"], compra["categoria"])][0] += parcela["valor_centavos"]
            totais[(parcela["periodo"], compra["categoria"])][1] += 1
    return [
        {"periodo": periodo, "categoria": categoria, "total_centavos": valores[0], "quantidade": valores[1]}
        for (periodo, categoria), valores in sorted(totais.items())
    ]


def analise_gastos(conn, cartao, fatura_id, periodo_filtro="", categoria_filtro=""):
    selecionada = conn.execute(
        "SELECT periodo FROM fatura WHERE id = ? AND cartao = ?", (fatura_id, cartao)
    ).fetchone()
    if selecionada is None:
        raise ValueError("fatura não encontrada para o cartão")
    periodo_referencia = periodo_filtro or selecionada["periodo"]
    periodos = [linha["periodo"] for linha in conn.execute(
        "SELECT periodo FROM fatura WHERE cartao = ? ORDER BY periodo", (cartao,)
    )]
    if periodo_filtro and periodo_filtro not in periodos:
        raise ValueError("período não encontrado para o cartão")
    linhas = _linhas_categorizadas(conn, cartao)

    def categoria(item):
        return item["categoria"] or SEM_CATEGORIA

    def despesa(item, periodo):
        return (item["periodo"] == periodo and item["natureza"] == "debito"
                and item["valor_centavos"] > 0
                and (not categoria_filtro or categoria(item) == categoria_filtro))

    despesas = [item for item in linhas if despesa(item, periodo_referencia)]
    anterior = next((mes for mes in reversed(periodos) if mes < periodo_referencia), "")
    despesas_anteriores = [item for item in linhas if anterior and despesa(item, anterior)]
    totais_categoria = defaultdict(int)
    estabelecimentos = defaultdict(lambda: [0, 0])
    evolucao = defaultdict(int)
    for item in despesas:
        totais_categoria[categoria(item)] += item["valor_centavos"]
        estabelecimento = item["estabelecimento"] or "Estabelecimento não informado"
        estabelecimentos[estabelecimento][0] += item["valor_centavos"]
        estabelecimentos[estabelecimento][1] += 1
    for item in linhas:
        if item["natureza"] == "debito" and item["valor_centavos"] > 0 and (not categoria_filtro or categoria(item) == categoria_filtro):
            evolucao[(item["periodo"], categoria(item))] += item["valor_centavos"]
    creditos = sum(-item["valor_centavos"] for item in linhas
                   if item["periodo"] == periodo_referencia and item["natureza"] == "credito")
    compromissos = _compromissos_futuros(conn, cartao)
    compromissos_categoria = defaultdict(int)
    parcelas_previstas_categoria = defaultdict(int)
    for item in compromissos:
        compromissos_categoria[item["categoria"]] += item["total_centavos"]
        parcelas_previstas_categoria[item["categoria"]] += item["quantidade"]
    limites = {linha["categoria"]: linha["limite_centavos"] for linha in conn.execute(
        "SELECT categoria, limite_centavos FROM orcamento_categoria WHERE cartao = ?", (cartao,)
    )}
    categorias = set(CATEGORIAS) | {SEM_CATEGORIA} | set(totais_categoria) | set(limites) | set(compromissos_categoria)
    orcamentos = []
    for item_categoria in sorted(categorias):
        gasto, limite = totais_categoria[item_categoria], limites.get(item_categoria, 0)
        orcamentos.append({
            "categoria": item_categoria, "gasto_centavos": gasto, "limite_centavos": limite,
            "percentual_base_pontos": gasto * 10000 // limite if limite else None,
            "saldo_centavos": limite - gasto,
            "compromissos_futuros_centavos": compromissos_categoria[item_categoria],
            "parcelas_previstas": parcelas_previstas_categoria[item_categoria],
        })
    return {
        "cartao": cartao, "periodo": periodo_referencia, "periodos": periodos,
        "despesas_elegiveis_centavos": sum(totais_categoria.values()),
        "creditos_estornos_centavos": creditos,
        "por_categoria": [{"categoria": nome, "total_centavos": total} for nome, total in sorted(totais_categoria.items(), key=lambda item: (-item[1], item[0]))],
        "evolucao": [{"periodo": mes, "categoria": nome, "total_centavos": total} for (mes, nome), total in sorted(evolucao.items())],
        "comparacao": {"periodo_anterior": anterior, "atual_centavos": sum(totais_categoria.values()), "anterior_centavos": sum(item["valor_centavos"] for item in despesas_anteriores)},
        "estabelecimentos": [{"estabelecimento": nome, "total_centavos": valores[0], "frequencia": valores[1]} for nome, valores in sorted(estabelecimentos.items(), key=lambda item: (-item[1][0], item[0]))],
        "sem_categoria": {"quantidade": sum(1 for item in despesas if categoria(item) == SEM_CATEGORIA), "total_centavos": totais_categoria[SEM_CATEGORIA]},
        "orcamentos": orcamentos, "compromissos_futuros": compromissos,
    }

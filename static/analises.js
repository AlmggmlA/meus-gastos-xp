"use strict";

const situacoesCompra = {
  em_andamento: "Em andamento",
  completo: "Todas lançadas",
  incompleto: "Histórico incompleto",
  conferir: "Conferir vínculo",
};
let analiseAtual = 0;
let comprasAtuais = [];

function criarElemento(tag, texto, classe) {
  const node = document.createElement(tag);
  if (texto !== undefined) node.textContent = texto;
  if (classe) node.className = classe;
  return node;
}

function formatarMes(periodo) {
  const [ano, mes] = periodo.split("-");
  return `${mes}/${ano}`;
}

function desenharBarras(destino, serie, campo, rotulo = (item) => formatarMes(item.periodo)) {
  let maximo = 1;
  let temNegativo = false;
  for (const item of serie) {
    maximo = Math.max(maximo, Math.abs(item[campo]));
    temNegativo ||= item[campo] < 0;
  }
  destino.replaceChildren(...serie.map((item) => {
    const valor = item[campo];
    const linha = criarElemento("li", undefined, "barra-linha");
    const faixa = criarElemento("div", undefined, "faixa");
    faixa.setAttribute("aria-hidden", "true");
    if (temNegativo) {
      const esquerda = criarElemento("progress", undefined, "barra-negativa");
      esquerda.max = maximo;
      esquerda.value = Math.max(0, -valor);
      faixa.append(esquerda);
    }
    const direita = criarElemento("progress");
    direita.max = maximo;
    direita.value = Math.max(0, valor);
    faixa.append(direita);
    linha.append(criarElemento("span", rotulo(item)), faixa,
                 criarElemento("strong", moeda.format(valor / 100)));
    return linha;
  }));
}

function abrirFatura(id) {
  navegarPara("faturas");
  const seletor = elemento("faturas");
  if (seletor.value !== String(id)) {
    seletor.value = id;
    seletor.dispatchEvent(new Event("change"));
  }
  elemento("lancamentosTitulo").focus();
}

function detalharCompra(compra) {
  const detalhes = criarElemento("details", undefined, "compra");
  const cabecalho = criarElemento("summary");
  const titulo = criarElemento("span", undefined, "titulo-compra");
  titulo.append(criarElemento("strong", compra.descricao));
  if (compra.portador) titulo.append(criarElemento("small", compra.portador));

  const progresso = criarElemento("span", undefined, "progresso-compra");
  progresso.append(
    criarElemento("span", `${compra.numeros_observados.length} de ${compra.total} lançadas`),
    criarElemento("span", situacoesCompra[compra.status], `situacao ${compra.status}`),
  );
  cabecalho.append(titulo, progresso);
  detalhes.append(cabecalho);
  if (compra.motivo) detalhes.append(criarElemento("p", compra.motivo, "nota motivo-compra"));

  const caixa = criarElemento("div", undefined, "tabela");
  const tabela = criarElemento("table");
  tabela.setAttribute("aria-label", `Parcelas de ${compra.descricao}`);
  const thead = tabela.createTHead().insertRow();
  for (const texto of ["Parcela", "Fatura", "Data no CSV", "Situação", "Valor"]) {
    const th = criarElemento("th", texto);
    th.scope = "col";
    thead.append(th);
  }
  const tbody = tabela.createTBody();
  const parcelas = [
    ...compra.realizadas.map((item) => ({ ...item, situacao: "Lançada" })),
    ...compra.previstas.map((item) => ({ ...item, situacao: "Prevista" })),
    ...compra.faltantes.map((item) => ({ ...item, situacao: "Sem lançamento importado" })),
  ].sort((esquerda, direita) => esquerda.numero - direita.numero);
  for (const parcela of parcelas) {
    const linha = tbody.insertRow();
    linha.insertCell().textContent = `${parcela.numero}/${compra.total}`;
    const mes = linha.insertCell();
    if (parcela.fatura_id) {
      const link = criarElemento("a", formatarMes(parcela.periodo));
      link.href = "#faturas";
      link.addEventListener("click", () => abrirFatura(parcela.fatura_id));
      mes.append(link);
    } else {
      mes.textContent = formatarMes(parcela.periodo);
    }
    linha.insertCell().textContent = parcela.data
      ? parcela.data.split("-").reverse().join("/") : "—";
    linha.insertCell().textContent = parcela.situacao;
    linha.insertCell().textContent = parcela.valor_centavos === undefined
      ? "—" : moeda.format(parcela.valor_centavos / 100);
  }
  caixa.append(tabela);
  detalhes.append(caixa);
  return detalhes;
}

function listarCompras() {
  const filtro = elemento("filtroCompra").value;
  const visiveis = comprasAtuais.filter((compra) => !filtro || compra.status === filtro);
  elemento("compras").replaceChildren(...visiveis.map(detalharCompra));
  const mensagem = elemento("semCompras");
  mensagem.textContent = comprasAtuais.length
    ? "Nenhuma compra nesta situação."
    : "Nenhuma parcela identificada nas faturas deste cartão.";
  mensagem.hidden = Boolean(visiveis.length);
}

async function carregarAnalises(id) {
  const numero = ++analiseAtual;
  elemento("dadosVisao").hidden = true;
  elemento("dadosParceladas").hidden = true;
  elemento("cartaoAnalisado").textContent = "";
  elemento("cartaoParcelas").textContent = "";
  const mensagem = elemento("statusAnalises");
  mensagem.classList.remove("erro");
  mensagem.textContent = "";
  if (!id) return;

  try {
    mensagem.textContent = "Consultando as faturas do cartão…";
    const dados = await api(`/api/analises?fatura_id=${id}`);
    if (numero !== analiseAtual) return;

    elemento("cartaoAnalisado").textContent = dados.cartao;
    elemento("cartaoParcelas").textContent = dados.cartao;
    desenharBarras(elemento("graficoHistorico"), dados.historico, "liquido_centavos");
    desenharBarras(elemento("graficoPrevisao"), dados.previstos, "previsto_centavos");
    desenharBarras(elemento("graficoCategorias"), dados.categorias, "total_centavos", (item) => item.categoria);
    elemento("semPrevisao").hidden = Boolean(dados.previstos.length);
    elemento("semCategorias").hidden = Boolean(dados.categorias.length);
    const saldo = dados.previstos.reduce((total, item) => total + item.previsto_centavos, 0);
    elemento("saldoPrevisto").textContent = moeda.format(saldo / 100);
    comprasAtuais = dados.compras;
    elemento("comprasAbertas").textContent = comprasAtuais.filter(
      (compra) => compra.status === "em_andamento",
    ).length;
    elemento("comprasRevisao").textContent = comprasAtuais.filter(
      (compra) => compra.status === "conferir",
    ).length;
    listarCompras();
    elemento("dadosVisao").hidden = false;
    elemento("dadosParceladas").hidden = false;
    mensagem.textContent = "";
  } catch (erro) {
    if (numero === analiseAtual) {
      mensagem.textContent = erro.message;
      mensagem.classList.add("erro");
    }
  }
}

document.getElementById("filtroCompra").addEventListener("change", listarCompras);

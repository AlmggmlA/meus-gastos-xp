"use strict";

let analiseGastosAtual = 0;
let sincronizandoAnalise = false;

function formatarPeriodoAnalise(periodo) {
  const [ano, mes] = periodo.split("-");
  return `${mes}/${ano}`;
}

function preencherSelect(select, opcoes, valor) {
  select.replaceChildren(...opcoes.map((opcao) => new Option(opcao.texto, opcao.valor)));
  if (opcoes.some((opcao) => String(opcao.valor) === String(valor))) select.value = valor;
}

function sincronizarFiltrosAnalise() {
  if (!faturasAtuais.length || sincronizandoAnalise) return;
  sincronizandoAnalise = true;
  const selecionada = faturasAtuais.find((item) => String(item.id) === elemento("faturas").value) || faturasAtuais[0];
  const cartoes = [...new Set(faturasAtuais.map((item) => item.cartao))];
  preencherSelect(elemento("analiseCartao"), cartoes.map((cartao) => ({ texto: cartao, valor: cartao })), selecionada.cartao);
  const doCartao = faturasAtuais.filter((item) => item.cartao === selecionada.cartao);
  preencherSelect(elemento("analiseFatura"), doCartao.map((item) => ({ texto: `${formatarPeriodoAnalise(item.periodo)} · ${item.cartao}`, valor: item.id })), selecionada.id);
  elemento("analiseCartaoAtual").textContent = selecionada.cartao;
  sincronizandoAnalise = false;
}

function barraAnalise(destino, itens, rotulo, campo = "total_centavos") {
  const maximo = Math.max(1, ...itens.map((item) => item[campo]));
  destino.replaceChildren(...itens.map((item) => {
    const linha = document.createElement("li"); linha.className = "barra-linha";
    const faixa = document.createElement("div"); faixa.className = "faixa";
    const progresso = document.createElement("progress"); progresso.max = maximo; progresso.value = item[campo]; faixa.append(progresso);
    const texto = document.createElement("span"); texto.textContent = rotulo(item);
    const valor = document.createElement("strong"); valor.textContent = moeda.format(item[campo] / 100);
    linha.append(texto, faixa, valor); return linha;
  }));
}

function tabelaVazia(destino, colunas, texto) {
  const celula = destino.insertRow().insertCell(); celula.colSpan = colunas; celula.textContent = texto;
}

function mostrarOrcamentos(itens, cartao) {
  const corpo = elemento("analiseOrcamentos"); corpo.replaceChildren();
  for (const item of itens) {
    const linha = corpo.insertRow();
    linha.className = item.percentual_base_pontos >= 10000 ? "orcamento-estourado" : item.percentual_base_pontos >= 9000 ? "orcamento-critico" : item.percentual_base_pontos >= 8000 ? "orcamento-atencao" : "";
    linha.insertCell().textContent = item.categoria;
    linha.insertCell().textContent = moeda.format(item.gasto_centavos / 100);
    const limite = document.createElement("input"); limite.type = "text"; limite.inputMode = "decimal"; limite.value = item.limite_centavos ? (item.limite_centavos / 100).toFixed(2).replace(".", ",") : ""; limite.placeholder = "0,00"; limite.setAttribute("aria-label", `Orçamento de ${item.categoria}`); linha.insertCell().append(limite);
    linha.insertCell().textContent = item.percentual_base_pontos === null ? "Sem limite" : `${(item.percentual_base_pontos / 100).toFixed(2).replace(".", ",")}%`;
    linha.insertCell().textContent = item.limite_centavos ? moeda.format(item.saldo_centavos / 100) : "—";
    linha.insertCell().textContent = moeda.format(item.compromissos_futuros_centavos / 100);
    linha.insertCell().textContent = item.parcelas_previstas;
    const salvar = document.createElement("button"); salvar.type = "button"; salvar.textContent = "Salvar";
    salvar.addEventListener("click", async () => {
      salvar.disabled = true;
      try {
        await api("/api/orcamentos", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ cartao, categoria: item.categoria, limite: limite.value || "0" }) });
        await carregarAnaliseGastos();
      } catch (erro) { elemento("erroAnalise").textContent = erro.message; } finally { salvar.disabled = false; }
    });
    linha.insertCell().append(salvar);
  }
}

function mostrarAnalise(dados) {
  elemento("analiseDespesas").textContent = moeda.format(dados.despesas_elegiveis_centavos / 100);
  elemento("analiseCreditos").textContent = moeda.format(dados.creditos_estornos_centavos / 100);
  elemento("analiseSemCategoria").textContent = `${dados.sem_categoria.quantidade} · ${moeda.format(dados.sem_categoria.total_centavos / 100)}`;
  barraAnalise(elemento("analiseCategorias"), dados.por_categoria, (item) => item.categoria);
  barraAnalise(elemento("analiseEvolucao"), dados.evolucao, (item) => `${formatarPeriodoAnalise(item.periodo)} · ${item.categoria}`);
  const comparacao = dados.comparacao;
  elemento("analiseComparacao").textContent = comparacao.periodo_anterior
    ? `${formatarPeriodoAnalise(dados.periodo)}: ${moeda.format(comparacao.atual_centavos / 100)}. ${formatarPeriodoAnalise(comparacao.periodo_anterior)}: ${moeda.format(comparacao.anterior_centavos / 100)}.`
    : "Não há fatura anterior para comparação.";
  mostrarOrcamentos(dados.orcamentos, dados.cartao);
  const estabelecimentos = elemento("analiseEstabelecimentos"); estabelecimentos.replaceChildren();
  if (!dados.estabelecimentos.length) tabelaVazia(estabelecimentos, 3, "Nenhuma despesa elegível neste período.");
  for (const item of dados.estabelecimentos) { const linha = estabelecimentos.insertRow(); linha.insertCell().textContent = item.estabelecimento; linha.insertCell().textContent = item.frequencia; linha.insertCell().textContent = moeda.format(item.total_centavos / 100); }
  const compromissos = elemento("analiseCompromissos"); compromissos.replaceChildren();
  if (!dados.compromissos_futuros.length) tabelaVazia(compromissos, 4, "Sem compromissos futuros estimados.");
  for (const item of dados.compromissos_futuros) { const linha = compromissos.insertRow(); linha.insertCell().textContent = formatarPeriodoAnalise(item.periodo); linha.insertCell().textContent = item.categoria; linha.insertCell().textContent = item.quantidade; linha.insertCell().textContent = moeda.format(item.total_centavos / 100); }
}

async function carregarAnaliseGastos() {
  const id = elemento("analiseFatura").value || elemento("faturas").value;
  if (!id) { elemento("dadosAnalise").hidden = true; return; }
  const numero = ++analiseGastosAtual; elemento("erroAnalise").textContent = "";
  try {
    const parametros = new URLSearchParams({ fatura_id: id, periodo: elemento("analisePeriodo").value, categoria: elemento("analiseCategoria").value });
    const dados = await api(`/api/analise-gastos?${parametros}`);
    if (numero !== analiseGastosAtual) return;
    preencherSelect(elemento("analisePeriodo"), dados.periodos.map((periodo) => ({ texto: formatarPeriodoAnalise(periodo), valor: periodo })), dados.periodo);
    mostrarAnalise(dados); elemento("dadosAnalise").hidden = false;
  } catch (erro) { if (numero === analiseGastosAtual) { elemento("erroAnalise").textContent = erro.message; elemento("dadosAnalise").hidden = true; } }
}

document.getElementById("analiseCartao").addEventListener("change", () => {
  const fatura = faturasAtuais.find((item) => item.cartao === elemento("analiseCartao").value);
  if (fatura) { elemento("faturas").value = fatura.id; elemento("faturas").dispatchEvent(new Event("change")); }
});
document.getElementById("analiseFatura").addEventListener("change", () => { elemento("faturas").value = elemento("analiseFatura").value; elemento("faturas").dispatchEvent(new Event("change")); });
document.getElementById("analisePeriodo").addEventListener("change", carregarAnaliseGastos);
document.getElementById("analiseCategoria").addEventListener("change", carregarAnaliseGastos);

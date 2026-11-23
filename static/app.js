"use strict";

const elemento = (id) => document.getElementById(id);
const moeda = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
const natureza = { debito: "Débito", credito: "Crédito", pagamento: "Pagamento" };
const categorias = ["", "Supermercado", "Restaurantes e delivery", "Transporte", "Saúde e farmácia", "Casa", "Compras pessoais", "Assinaturas", "Lazer", "Tarifas e encargos", "Outros"];
const limite = 50;
let inicio = 0, consulta = 0, observarBusca, arquivos = [], importando = false, faturasAtuais = [];

function navegarPara(tela) {
  if (location.hash !== `#${tela}`) location.hash = tela;
  atualizarTela();
}
function atualizarTela() {
  const tela = location.hash.slice(1);
  const atual = ["faturas", "visao", "parceladas", "analise"].includes(tela) ? tela : "faturas";
  if (tela !== atual) history.replaceState(null, "", `#${atual}`);
  for (const [nome, destino] of Object.entries({ faturas: "telaFaturas", visao: "telaVisao", parceladas: "telaParceladas", analise: "telaAnalise" })) {
    elemento(destino).hidden = nome !== atual;
  }
  document.querySelectorAll("[data-tela]").forEach((link) => {
    const ativa = link.dataset.tela === atual;
    link.classList.toggle("ativo", ativa);
    if (ativa) link.setAttribute("aria-current", "page"); else link.removeAttribute("aria-current");
  });
}

async function api(caminho, opcoes) {
  const resposta = await fetch(caminho, opcoes);
  const dados = await resposta.json();
  if (!resposta.ok) { const erro = new Error(dados.erro || "Falha na consulta local."); erro.status = resposta.status; throw erro; }
  return dados;
}
function mensagem(texto, erro = false) { const destino = elemento("mensagem"); destino.textContent = texto; destino.classList.toggle("erro", erro); }

async function carregarFaturas(faturaId) {
  const lista = await api("/api/faturas"), seletor = elemento("faturas"), atual = seletor.value;
  faturasAtuais = lista;
  seletor.replaceChildren(...lista.map((fatura) => { const [ano, mes] = fatura.periodo.split("-"); return new Option(`${mes}/${ano} · ${fatura.cartao}`, fatura.id); }));
  if (!lista.length) seletor.add(new Option("Sem faturas importadas", ""));
  else { const escolhida = String(faturaId ?? atual); if (lista.some((fatura) => String(fatura.id) === escolhida)) seletor.value = escolhida; }
  seletor.disabled = !lista.length;
  if (typeof sincronizarFiltrosAnalise === "function") sincronizarFiltrosAnalise();
  carregarAnalises(seletor.value);
  if (typeof carregarAnaliseGastos === "function") carregarAnaliseGastos();
  await mostrarFatura();
}
function mostrarLinhas(dados) {
  const linhas = dados.itens.map((item) => {
    const tr = document.createElement("tr");
    for (const campo of [item.data.split("-").reverse().join("/"), item.descricao, item.portador || "—", natureza[item.natureza]]) tr.insertCell().textContent = campo;
    tr.insertCell().append(editorCategoria(item));
    tr.insertCell().textContent = moeda.format(item.valor_centavos / 100);
    return tr;
  });
  const tabela = elemento("linhas"); tabela.replaceChildren(...linhas);
  if (!linhas.length) { const celula = tabela.insertRow().insertCell(); celula.colSpan = 6; celula.textContent = "Nenhum lançamento encontrado."; }
  elemento("contagem").textContent = dados.total ? `${inicio + 1}–${inicio + dados.itens.length} de ${dados.total} lançamentos` : "0 lançamentos";
  elemento("anterior").disabled = inicio === 0; elemento("proximo").disabled = inicio + limite >= dados.total;
}
function editorCategoria(item) {
  const caixa = document.createElement("div");
  caixa.className = "categoria-editor";
  const seletor = document.createElement("select");
  seletor.setAttribute("aria-label", `Categoria de ${item.descricao}`);
  for (const categoria of categorias) seletor.add(new Option(categoria || "Sem categoria", categoria));
  seletor.value = item.categoria;
  const regra = document.createElement("label"), marcar = document.createElement("input");
  marcar.type = "checkbox"; regra.append(marcar, "Aplicar também a futuras compras deste estabelecimento");
  const salvar = document.createElement("button"); salvar.type = "button"; salvar.textContent = "Salvar";
  salvar.addEventListener("click", async () => {
    salvar.disabled = true;
    try {
      await api("/api/categorias", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ lancamento_id: item.id, categoria: seletor.value, aplicar_estabelecimento: marcar.checked }) });
      await Promise.all([
        mostrarFatura(), carregarAnalises(elemento("faturas").value),
        typeof carregarAnaliseGastos === "function" ? carregarAnaliseGastos() : Promise.resolve(),
      ]);
    } catch (erro) { elemento("erroConsulta").textContent = erro.message; } finally { salvar.disabled = false; }
  });
  caixa.append(seletor, regra, salvar);
  return caixa;
}
async function mostrarFatura() {
  const numero = ++consulta, id = elemento("faturas").value;
  elemento("erroConsulta").textContent = ""; elemento("estadoInicial").hidden = Boolean(id);
  if (!id) { elemento("conteudo").hidden = true; return; }
  try {
    const parametros = new URLSearchParams({ fatura_id: id, inicio, limite, busca: elemento("busca").value, natureza: elemento("natureza").value, categoria: elemento("categoria").value });
    const [resumo, dados] = await Promise.all([api(`/api/resumo?fatura_id=${id}`), api(`/api/lancamentos?${parametros}`)]);
    if (numero !== consulta) return;
    elemento("debitos").textContent = moeda.format(resumo.debitos_centavos / 100); elemento("creditos").textContent = moeda.format(resumo.creditos_centavos / 100);
    elemento("liquido").textContent = moeda.format((resumo.debitos_centavos - resumo.creditos_centavos) / 100); mostrarLinhas(dados); elemento("conteudo").hidden = false;
  } catch (erro) { if (numero === consulta) { elemento("erroConsulta").textContent = erro.message; elemento("conteudo").hidden = true; } }
}

function periodoDoNome(nome) {
  const encontrado = /^Fatura(\d{4})-(\d{2})-(\d{2})\.csv$/i.exec(nome);
  if (!encontrado) return "";
  const [, ano, mes, dia] = encontrado, data = new Date(`${ano}-${mes}-${dia}T12:00:00`);
  return data.getFullYear() === Number(ano) && data.getMonth() + 1 === Number(mes) && data.getDate() === Number(dia) ? `${ano}-${mes}` : "";
}
function textoResultado(item) {
  return { pendente: "Pronto para importar", importando: "Importando…", importada: `Importada · ${item.linhas} lançamentos`, substituida: `Substituída · ${item.linhas} lançamentos`, ja_importada: `Já importado · ${item.linhas} lançamentos`, conflito: "Já existe uma fatura diferente para este cartão e mês.", erro: item.erro, ignorada: "Importação não realizada" }[item.status];
}
function renderizarArquivos() {
  const repetidos = new Map();
  for (const item of arquivos) if (item.periodo) repetidos.set(item.periodo, (repetidos.get(item.periodo) || 0) + 1);
  const linhas = arquivos.map((item) => {
    const tr = document.createElement("tr"), nome = tr.insertCell(); nome.textContent = item.arquivo.name;
    if (item.periodo && repetidos.get(item.periodo) > 1) { const aviso = document.createElement("span"); aviso.className = "aviso-duplicado"; aviso.textContent = "Mesmo cartão e mês em outro arquivo deste lote."; nome.append(aviso); }
    const mes = tr.insertCell(), entrada = document.createElement("input"); entrada.type = "month"; entrada.value = item.periodo; entrada.setAttribute("aria-label", `Mês da fatura ${item.arquivo.name}`);
    entrada.disabled = importando || ["importada", "substituida", "ja_importada"].includes(item.status); entrada.addEventListener("change", () => { item.periodo = entrada.value; renderizarArquivos(); }); mes.append(entrada);
    const resultado = tr.insertCell(); resultado.className = `resultado-importacao ${item.status === "erro" ? "erro" : item.status === "conflito" ? "conflito" : ""}`; resultado.textContent = textoResultado(item);
    const acoes = tr.insertCell(); acoes.className = "acoes-importacao";
    if (item.status === "conflito") {
      const substituir = document.createElement("button"); substituir.type = "button"; substituir.textContent = "Substituir"; substituir.disabled = importando; substituir.addEventListener("click", () => confirmarSubstituicao(item));
      const cancelar = document.createElement("button"); cancelar.type = "button"; cancelar.className = "claro"; cancelar.textContent = "Não substituir"; cancelar.disabled = importando; cancelar.addEventListener("click", () => { item.status = "ignorada"; renderizarArquivos(); }); acoes.append(substituir, cancelar);
    } else if (!["importada", "substituida", "ja_importada", "importando"].includes(item.status)) {
      const remover = document.createElement("button"); remover.type = "button"; remover.className = "claro"; remover.textContent = "Remover"; remover.disabled = importando; remover.addEventListener("click", () => { arquivos = arquivos.filter((outro) => outro !== item); atualizarRevisao(); }); acoes.append(remover);
    }
    return tr;
  });
  elemento("arquivosSelecionados").replaceChildren(...linhas);
}
function atualizarRevisao() { elemento("revisao").hidden = !arquivos.length; renderizarArquivos(); elemento("enviar").disabled = importando || !arquivos.length; }
async function importarUm(item, substituir = false) {
  item.status = "importando"; renderizarArquivos();
  try {
    const parametros = new URLSearchParams({ cartao: elemento("cartao").value, periodo: item.periodo, sinal: elemento("sinal").value }); if (substituir) parametros.set("substituir", "1");
    const resultado = await api(`/api/importar?${parametros}`, { method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: item.arquivo });
    item.status = resultado.status; item.linhas = resultado.linhas; item.erro = ""; return resultado;
  } catch (erro) { item.status = erro.status === 409 ? "conflito" : "erro"; item.erro = erro.message; return null; } finally { renderizarArquivos(); }
}
async function atualizarDepoisDaImportacao(id) {
  if (!id) return;
  inicio = 0; elemento("busca").value = ""; elemento("natureza").value = ""; await carregarFaturas(id);
}
async function confirmarSubstituicao(item) {
  if (importando) return;
  importando = true; atualizarRevisao(); const resultado = await importarUm(item, true); importando = false; atualizarRevisao();
  mensagem(resultado ? `${item.arquivo.name}: ${textoResultado(item)}.` : `${item.arquivo.name}: ${item.erro}`, !resultado); await atualizarDepoisDaImportacao(resultado?.id);
}
async function importarPendentes() {
  if (importando) return;
  const pendentes = arquivos.filter((item) => ["pendente", "erro"].includes(item.status));
  if (!pendentes.length) { mensagem("Não há arquivos pendentes para importar."); return; }
  importando = true; atualizarRevisao(); mensagem(`Importando ${pendentes.length} arquivo(s)…`); let ultimaFatura;
  for (const item of pendentes) { const resultado = await importarUm(item); if (resultado?.id) ultimaFatura = resultado.id; }
  importando = false; atualizarRevisao();
  const concluidos = arquivos.filter((item) => ["importada", "substituida", "ja_importada"].includes(item.status)).length, aguardando = arquivos.filter((item) => item.status === "conflito").length, falharam = arquivos.filter((item) => item.status === "erro").length;
  mensagem(`${concluidos} concluída(s)${aguardando ? `, ${aguardando} aguardando confirmação` : ""}${falharam ? ` e ${falharam} com erro` : ""}.`, Boolean(falharam)); await atualizarDepoisDaImportacao(ultimaFatura);
}

elemento("arquivo").addEventListener("change", () => {
  arquivos = [...elemento("arquivo").files].map((arquivo) => ({ arquivo, periodo: periodoDoNome(arquivo.name), status: "pendente", erro: "" }));
  const grande = arquivos.find((item) => item.arquivo.size > 8 * 1024 * 1024); mensagem(grande ? `${grande.arquivo.name} tem mais de 8 MB.` : arquivos.length ? `${arquivos.length} arquivo(s) selecionado(s).` : "", Boolean(grande)); atualizarRevisao();
});
elemento("importar").addEventListener("submit", (evento) => { evento.preventDefault(); importarPendentes(); });
elemento("faturas").addEventListener("change", () => {
  inicio = 0; mostrarFatura(); carregarAnalises(elemento("faturas").value);
  if (typeof sincronizarFiltrosAnalise === "function") sincronizarFiltrosAnalise();
  if (typeof carregarAnaliseGastos === "function") carregarAnaliseGastos();
});
elemento("natureza").addEventListener("change", () => { inicio = 0; mostrarFatura(); });
elemento("categoria").addEventListener("change", () => { inicio = 0; mostrarFatura(); });
elemento("busca").addEventListener("input", () => { clearTimeout(observarBusca); observarBusca = setTimeout(() => { inicio = 0; mostrarFatura(); }, 180); });
elemento("anterior").addEventListener("click", () => { inicio = Math.max(0, inicio - limite); mostrarFatura(); });
elemento("proximo").addEventListener("click", () => { inicio += limite; mostrarFatura(); });
window.addEventListener("hashchange", atualizarTela);
if (!location.hash) history.replaceState(null, "", "#faturas");
atualizarTela();
carregarFaturas().catch((erro) => { elemento("erroConsulta").textContent = erro.message; });

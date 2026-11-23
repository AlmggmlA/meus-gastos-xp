# Meus gastos XP

Painel de faturas do cartão para uso pessoal. O importador, o banco SQLite
e a API rodam no seu computador. O programa não precisa de conta na Pluggy
nem de credenciais bancárias. A versão atual recebe arquivos CSV exportados
pelo app da XP. O download da fatura é manual.

## Rodar no Windows

1. Extraia o ZIP. Abra o PowerShell dentro da pasta extraída.
2. Execute `py -3 app.py` com Python 3.10 ou posterior. Se o Windows não
   tiver o lançador `py`, tente `python app.py`.
3. Abra [127.0.0.1:8787](http://127.0.0.1:8787). O servidor atende apenas no
   endereço local. Pressione Ctrl+C para encerrá-lo.

O programa usa a biblioteca padrão do Python. No Linux ou macOS, use
`python3 app.py`. Se a porta estiver ocupada, execute
`py -3 app.py --porta 8790` e abra a porta indicada.

O painel abre em **Faturas e lançamentos**. A navegação no topo permite abrir
diretamente `#faturas`, `#visao` e `#parceladas`; assim, os botões Voltar e
Avançar do navegador preservam a fatura selecionada.

## Importar

1. No app da XP, abra os detalhes da fatura e procure a opção de exportar
   em CSV. Para experimentar, use `exemplos/fatura-exemplo.csv`.
2. No painel, escolha um ou mais arquivos. Para nomes no formato
   `Fatura2026-11-05.csv`, o mês é sugerido automaticamente; revise ou informe
   o mês da **fatura** de cada linha. Mantenha o mesmo apelido do cartão nos
   próximos meses.
3. Informe o sinal das compras conforme o CSV e clique em **Importar faturas**.
   Cada arquivo é processado separadamente: erros não interrompem o lote e
   conflitos pedem confirmação de substituição apenas para a fatura envolvida.

O importador aceita os campos Data, Descrição e Valor em reais, com variações
de nome. Portador e Tipo são opcionais. Aceita ponto e vírgula, vírgula e
tabulação como separador. Datas de compras podem cair fora do mês da fatura.
O gasto líquido soma débitos e desconta créditos. A identificação de pagamento
usa o tipo do lançamento ou descrições conhecidas. Compare a primeira
importação com os lançamentos do app da XP.

Cada combinação de apelido de cartão e mês representa um retrato completo
da fatura. Importar o mesmo arquivo com o mesmo sinal é idempotente.
Se o CSV mudou, o painel pede confirmação antes de substituir a fatura.
Compras idênticas em linhas distintas são preservadas. Use o mês indicado
no app da XP para evitar lançar uma mesma fatura em dois meses.

## Categorias

Use a coluna **Categoria** nos lançamentos para classificar compras. A escolha
manual prevalece sobre a sugestão e pode ser aplicada às futuras compras do
mesmo estabelecimento. As regras ficam no banco local e também são usadas nas
faturas seguintes. O gráfico **Gastos por categoria** soma somente os débitos
da fatura selecionada, incluindo **Sem categoria**.

## Gráficos e parcelamentos

Selecione qualquer fatura do cartão. A seção **Visão do cartão** usa todas as
faturas importadas para o mesmo apelido. O gráfico de gasto líquido mostra os
meses com fatura importada. O gráfico de previsão mostra o que pode vir depois
da fatura mais recente disponível para aquele cartão.

Em **Compras parceladas**, expanda uma compra para ver os números das parcelas,
os meses de referência, os valores e os links para as faturas de origem.
`Lançada` indica que a parcela aparece em um CSV importado. `Prevista` usa o
valor da última parcela observada. Se um mês anterior estiver faltando no
histórico, a parcela aparece como **Sem lançamento importado**.

O programa reconhece marcadores como `parcela 1/3`, `(1/3)` e `1 de 3` no
final da descrição ou na coluna de parcela. O vínculo entre faturas é estimado
pelo estabelecimento, portador, total de parcelas e mês inferido da primeira
parcela. Números repetidos, marcadores conflitantes e diferenças relevantes de
valor com parcelas futuras são encaminhados para conferência. Antecipações,
cancelamentos e mudanças de valor podem alterar a previsão. Confira a próxima
fatura da XP antes de usar o saldo estimado como compromisso definitivo.

Para simular a evolução, importe `exemplos/fatura-exemplo.csv` em 2026-11 e
`exemplos/fatura-exemplo-2026-12.csv` em 2026-12 com o mesmo apelido. Na
parcela da Livraria, a previsão de dezembro passa a ser lançada.

## Atualizar uma instalação anterior

Encerre o programa, extraia a nova versão e copie o arquivo anterior
`dados/gastos.sqlite3` para o mesmo caminho na nova pasta. O esquema é
atualizado na primeira inicialização. Se uma fatura antiga trazia a numeração
somente em uma coluna separada do CSV, reimporte o mesmo arquivo para recuperar
essa informação sem duplicar os lançamentos.

## Banco local

Os dados ficam em `dados/gastos.sqlite3`. Para mover ou fazer backup do banco,
encerre o programa e copie esse arquivo. O banco guarda lançamentos e eventuais
nomes de portadores. Proteja esse arquivo e os CSVs como documentos financeiros.

### Criação do esquema

Para criar um banco novo manualmente, execute a consulta abaixo no SQLite. O
aplicativo também cria e migra esse esquema automaticamente ao iniciar.

```sql
PRAGMA foreign_keys = ON;

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
```

`categoria_manual` aceita `NULL` quando não há escolha manual e uma string vazia
para uma escolha explícita de **Sem categoria**. As regras usam a versão
normalizada do estabelecimento em `estabelecimento_chave`.

## API local

| Método e caminho | Dados |
| --- | --- |
| `GET /api/faturas` | Faturas importadas e respectivos IDs |
| `GET /api/resumo?fatura_id=1` | Débitos e créditos em centavos, além da contagem |
| `GET /api/lancamentos?fatura_id=1&limite=50&inicio=0` | Lançamentos paginados. Filtros opcionais `busca` e `natureza` |
| `GET /api/analises?fatura_id=1` | Histórico, compras parceladas e previsão do cartão vinculado à fatura |
| `POST /api/categorias` | Atualiza a categoria manual de um lançamento; pode salvar a regra para o estabelecimento |
| `POST /api/importar?periodo=2026-11&sinal=positivo` | CSV no corpo com `Content-Type: application/octet-stream`. `cartao` define o apelido. `substituir=1` substitui a fatura do mesmo cartão e mês |

Exemplo de consulta no PowerShell:

```powershell
Invoke-RestMethod 'http://127.0.0.1:8787/api/faturas'
Invoke-RestMethod 'http://127.0.0.1:8787/api/resumo?fatura_id=1'
```

## Sincronização com o banco

A sincronização direta pela API transacional da XP exige uma integração
com uma instituição participante do Open Finance e o consentimento do titular.
O [portal da XP](https://developer.xpinc.com/open-finance) documenta a API
de cartão de crédito. A versão atual funciona com CSV.

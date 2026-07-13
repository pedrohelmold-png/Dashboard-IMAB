# Dashboard IMA-B e FI-Infra

Aplicativo Streamlit para acompanhar o carrego do IMA-B 5 e do IMA-B completo e
para registrar snapshots semanais da Regua de Ciclo FI-Infra.

## Executar

```powershell
pip install -r requirements.txt
streamlit run app.py
```

Para usar o mesmo banco em app e ETLs (por exemplo, em um volume persistente),
defina `IMAB_DB_PATH` antes de iniciar os processos. Use `python backup_db.py`
para gerar uma copia consistente do SQLite.

`app.py` e o unico ponto de entrada. Selecione **IMA-B 5**, **IMA-B** ou
**Regua FI-Infra** no menu lateral.

O menu **Tela** alterna entre o dashboard de carrego e a Regua FI-Infra. Na
regua, o botao **Atualizar dados oficiais** busca NTN-B e dados macro via
ANBIMA/BCB, fechamentos dos fundos na B3 e cotas patrimoniais no Informe
Diario da CVM. Confira os tres sinais e salve o snapshot semanal. Limiares,
snapshots e tranches ficam no SQLite configurado em
`config.py`.

O contrato de decisao e os limites metodologicos da regua estao em
[`docs/DECISION_CONTRACT.md`](docs/DECISION_CONTRACT.md). A zona se refere a
exposicao agregada da classe FI-Infra; ela nao escolhe automaticamente um
ticker.

Nos dashboards de carrego, o **IPCA Focus 12m** e comum ao IMA-B 5 e ao IMA-B
na mesma data e e a premissa principal do carrego nominal. A **inflacao
implicita** e exibida separadamente e pode diferir entre os indices porque e
ponderada pelos titulos de cada universo. Na falta do Focus, a implicita e
usada como fallback explicitamente identificado.

Quando a data solicitada ainda nao foi publicada, a coleta recua ate cinco
dias uteis e marca o valor como defasado. A NTN-B de referencia e o titulo com
duration mais proxima da duration mediana dos fundos; vencimento, duration,
data-base e status aparecem na tela e sao gravados no snapshot.

Na Regua FI-Infra, o CDI liquido real e deflacionado pelo **IPCA Focus 12m**.
A inflacao implicita da NTN-B de referencia permanece separada como breakeven
de mercado e funciona apenas como fallback explicitamente identificado se o
Focus estiver indisponivel.

A metodologia atual da regua e `v2`. O yield real estimado do fundo soma NTN-B,
spread e o excesso de desconto anualizado pela duration; excesso negativo reduz
o yield, em vez de ser zerado. Para alternativas tributadas cotadas como taxa
real, como IMA-B, a regua reconstrui o retorno nominal implicito pela inflacao
usada, aplica imposto sobre esse retorno nominal e deflaciona de volta.

Cada atualizacao forma um lote vinculado a data solicitada. Mudar a data
invalida o lote anterior, e o botao forca nova leitura das fontes mesmo com o
servidor aberto ha varios dias. B3 e CVM sao tratadas independentemente para
preservar resultados parciais. A coleta tenta janelas anteriores quando a data
solicitada cai em virada de mes/ano ou quando um arquivo ainda nao foi
publicado: B3 pode recorrer ao COTAHIST do ano anterior, e CVM pode recorrer ao
Informe Diario do mes anterior. A recomendacao operacional exige ao menos tres
dos quatro fundos com dados completos.

Toda coleta tambem e guardada como observacao bruta, mesmo sem salvar um
snapshot de decisao. O comando `python fiinfra_etl.py` executa essa coleta sem
aplicar premissas manuais ou emitir recomendacao; ele e acionado no workflow
diario junto com IMA-B 5 e IMA-B.

Use `python fiinfra_etl.py --backfill --days 20` para iniciar uma serie recente
de observacoes. O modo `--strict`, usado no workflow diario, faz a execucao
falhar quando uma fonte reporta erro, preservando os dados parciais para
auditoria e tornando a falha visivel no GitHub Actions.

O snapshot da Regua FI-Infra guarda a proveniencia dos principais campos: lote
de coleta, data solicitada, fontes macro, valor original coletado, status de
SLA e indicacao de override manual. Os limiares usados na classificacao tambem
ficam congelados no snapshot para auditoria historica. Quando um snapshot da
mesma data e salvo novamente, a versao anterior e arquivada como revisao com
seu JSON completo e os fundos daquela foto; a tela mostra as revisoes
arquivadas da data selecionada e permite restaurar uma revisao mediante
confirmacao. No grafico historico, as linhas de limiar usam os valores
congelados em cada snapshot; snapshots antigos sem esses campos usam os
limiares atuais como fallback.

Nos fundos, a regua preserva CNPJ, fonte, data-base, status e valor original
das cotas de mercado e patrimonial. Um fundo deixa de entrar na mediana quando
tem dados incompletos ou quando B3 e CVM estao desalinhadas por mais de um dia
util. Alteracoes manuais nas cotas sao marcadas como override.

O painel **Qualidade dos dados** consolida lote de coleta, cobertura, status
das fontes, overrides, estimativas e pontos que exigem revisao antes do
salvamento. Ele usa as mesmas regras que habilitam a confirmacao do snapshot.
Erros de coleta e pontos confirmados na revisao ficam gravados no snapshot em
JSON para auditoria posterior.
O lote tambem registra as fontes candidatas tentadas na coleta B3/CVM, o que
ajuda a explicar recuos para ano ou mes anterior em viradas de calendario.
O SQLite tambem registra metadados de schema da regua (`schema_name` e
`schema_version`), facilitando diagnostico quando um banco antigo for reaberto.

O spread de credito, a taxa total e a duration de cada fundo continuam
editaveis: essas informacoes ainda nao possuem, no fluxo atual, uma fonte
estruturada com cobertura e periodicidade uniformes. O spread fica auditado no
snapshot com fonte, status, valor original e override, e aparece no painel de
qualidade como premissa sem coleta oficial automatizada. O ultimo valor salvo e
reutilizado como fallback. A taxa total e a duration tambem podem ser
atualizadas em lote por CSV/clipboard na tela da regua; valores aplicados por
esse caminho ficam com status `IMPORTADO_LOTE`. Quando taxa ou duration ainda
sao estimativas, o salvamento exige confirmacao; depois de confirmado, o status
fica gravado como manual confirmado.

As taxas e descontos usam pontos percentuais (por exemplo, `6.5` para 6,5%), o
spread usa pontos-base e a aliquota e armazenada em decimal.

Taxa e duration deixam de receber valores numericos iniciais implicitos quando
nao existe historico. Nessa situacao, a regua exige premissas explicitas antes
de liberar uma decisao operacional. O score de desconto e uma heuristica de
pesquisa, nao um calculo de valor justo ou yield contratual do fundo.

A secao **Carteira e proposta de alocacao** consolida as tranches registradas,
compara a exposicao atual a limites informados pelo usuario e dimensiona uma
proposta em reais. Ela e apenas orientativa e nunca envia ordens.

## Testes

```powershell
python -m unittest discover -v
```

Os testes cobrem as regras de classificacao, zonas, filtro de carrego e as
operacoes de persistencia da Regua FI-Infra em banco temporario.

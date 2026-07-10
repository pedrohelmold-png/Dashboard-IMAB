# Dashboard IMA-B e FI-Infra

Aplicativo Streamlit para acompanhar o carrego do IMA-B 5 e do IMA-B completo e
para registrar snapshots semanais da Regua de Ciclo FI-Infra.

## Executar

```powershell
pip install -r requirements.txt
streamlit run app.py
```

O menu **Tela** alterna entre o dashboard de carrego e a Regua FI-Infra. Na
regua, o botao **Atualizar dados oficiais** busca NTN-B e dados macro via
ANBIMA/BCB, fechamentos dos fundos na B3 e cotas patrimoniais no Informe
Diario da CVM. Confira os tres sinais e salve o snapshot semanal. Limiares,
snapshots e tranches ficam no SQLite configurado em
`config.py`.

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
preservar resultados parciais. A recomendacao operacional exige ao menos tres
dos quatro fundos com dados completos.

O snapshot da Regua FI-Infra guarda a proveniencia dos principais campos: lote
de coleta, data solicitada, fontes macro, valor original coletado, status de
SLA e indicacao de override manual. Os limiares usados na classificacao tambem
ficam congelados no snapshot para auditoria historica.

Nos fundos, a regua preserva CNPJ, fonte, data-base, status e valor original
das cotas de mercado e patrimonial. Um fundo deixa de entrar na mediana quando
tem dados incompletos ou quando B3 e CVM estao desalinhadas por mais de um dia
util. Alteracoes manuais nas cotas sao marcadas como override.

O spread IDA-Infra, a taxa total e a duration de cada fundo continuam
editaveis: essas informacoes ainda nao possuem, no fluxo atual, uma fonte
estruturada com cobertura e periodicidade uniformes. O ultimo valor salvo e
reutilizado como fallback. Quando taxa ou duration ainda sao estimativas, o
salvamento exige confirmacao; depois de confirmado, o status fica gravado como
manual confirmado.

As taxas e descontos usam pontos percentuais (por exemplo, `6.5` para 6,5%), o
spread usa pontos-base e a aliquota e armazenada em decimal.

## Testes

```powershell
python -m unittest discover -v
```

Os testes cobrem as regras de classificacao, zonas, filtro de carrego e as
operacoes de persistencia da Regua FI-Infra em banco temporario.

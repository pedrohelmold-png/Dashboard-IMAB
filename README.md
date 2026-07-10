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

Quando a data solicitada ainda nao foi publicada, a coleta recua ate cinco
dias uteis e marca o valor como defasado. A NTN-B de referencia e o titulo com
duration mais proxima da duration mediana dos fundos; vencimento, duration,
data-base e status aparecem na tela e sao gravados no snapshot.

O spread IDA-Infra, a taxa total e a duration de cada fundo continuam
editaveis: essas informacoes ainda nao possuem, no fluxo atual, uma fonte
estruturada com cobertura e periodicidade uniformes. O ultimo valor salvo e
reutilizado como fallback.

As taxas e descontos usam pontos percentuais (por exemplo, `6.5` para 6,5%), o
spread usa pontos-base e a aliquota e armazenada em decimal.

## Testes

```powershell
python -m unittest discover -v
```

Os testes cobrem as regras de classificacao, zonas, filtro de carrego e as
operacoes de persistencia da Regua FI-Infra em banco temporario.

# Dashboard IMA-B e FI-Infra

Aplicativo Streamlit para acompanhar o carrego do IMA-B 5 e do IMA-B completo e
para registrar snapshots semanais da Regua de Ciclo FI-Infra.

## Executar

```powershell
pip install -r requirements.txt
streamlit run app.py
```

O menu **Tela** alterna entre o dashboard de carrego e a Regua FI-Infra. Na
regua, preencha os dados dos fundos, confira os tres sinais e salve o snapshot
semanal. Limiares, snapshots e tranches ficam no SQLite configurado em
`config.py`.

As taxas e descontos usam pontos percentuais (por exemplo, `6.5` para 6,5%), o
spread usa pontos-base e a aliquota e armazenada em decimal.

## Testes

```powershell
python -m unittest discover -v
```

Os testes cobrem as regras de classificacao, zonas, filtro de carrego e as
operacoes de persistencia da Regua FI-Infra em banco temporario.

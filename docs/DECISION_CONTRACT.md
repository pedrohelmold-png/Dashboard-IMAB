# Contrato de decisão — FI-Infra

## Finalidade

O aplicativo é um instrumento de pesquisa e disciplina para alocação tática da
**classe FI-Infra listada**, comparada a alternativas de renda fixa real. Ele
não é um sistema de execução, suitability, recomendação individual de ativos
nem cálculo de valor patrimonial justo.

## Decisão que a régua suporta

Em revisão semanal, a régua responde se a exposição agregada a FI-Infra deve:

| Zona | Orientação |
| --- | --- |
| `COMPRAR` | Planejar aumento gradual da exposição agregada, em 3–4 tranches. |
| `CARREGAR` | Manter a exposição e colher o carrego; não é uma indicação de compra. |
| `REDUZIR` | Avaliar redução parcial, sujeita ao mandato e ao filtro de carrego. |

O resultado não escolhe, por si só, qual fundo comprar. A seleção de um ticker
exige análise própria de liquidez, carteira, custo, risco de crédito,
concentração e adequação à carteira.

## Sinais e limites da metodologia

A zona usa três sinais da cesta monitorada: juro real da NTN-B de referência,
spread de crédito informado e excesso de desconto aproximado. O último é uma
heurística de pesquisa: compara desconto de mercado com uma aproximação
linear baseada em taxa e duration. Portanto, deve ser apresentado como
**score de desconto relativo**, e não como valuation completo ou yield
contratual do fundo.

Os limiares são hipóteses de investimento versionadas no snapshot. Eles não
devem ser tratados como calibrados até existir histórico FI-Infra suficiente
para avaliar resultados subsequentes fora da amostra.

## Regras de qualidade

- A recomendação exige cobertura de ao menos 3 dos 4 fundos monitorados.
- Dados oficiais e premissas manuais são identificados separadamente.
- Premissas de spread, taxa e duration precisam de fonte, data-base e
  confirmação humana antes de sustentar um snapshot operacional.
- Dados defasados, falhas de coleta e overrides reduzem a confiança e devem
  permanecer auditáveis.
- O aplicativo nunca envia ordens nem altera posições.

## Cadência e responsabilidade

- **IMA-B:** monitoramento diário de carrego.
- **FI-Infra:** coleta de observações diária quando disponível; decisão e
  snapshot em cadência semanal.
- **Responsável pela premissa:** o usuário que a confirma no snapshot.
- **Responsável pelos limiares:** definido pelo processo de investimento, não
  pelo coletor automático.

## Métrica de sucesso futura

Depois de formada uma série histórica FI-Infra, a régua será avaliada por
retornos subsequentes de 1, 3, 6 e 12 meses, drawdown, turnover e comparação
contra manter FI-Infra, IMA-B e CDI. A avaliação deve reservar um período fora
da amostra; não se devem ajustar limiares exclusivamente ao histórico inteiro.

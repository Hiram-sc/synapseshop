Estudo sobre Engenharia de Prompts
1. Introdução

A Engenharia de Prompts consiste em criar instruções claras para que modelos de Inteligência Artificial (LLMs) gerem respostas mais precisas e adequadas ao objetivo desejado. No SynapseShop, essas técnicas podem ajudar o assistente de suporte e a análise de logs a fornecer informações mais confiáveis e organizadas.

2. Impacto dos Parâmetros Globais da LLM
2.1. Temperatura (Temperature)

A temperatura controla o nível de variação das respostas geradas pela IA. Esse parâmetro influencia a escolha das palavras e a previsibilidade do resultado.

2.1.1. Temperatura Baixa (0.0 a 0.2)

Com uma temperatura baixa, as respostas tendem a ser mais consistentes e previsíveis. É uma boa opção para tarefas como classificação de erros, análise de logs e extração de informações.

2.1.2. Temperatura Alta (0.7 a 1.0)

Com uma temperatura alta, as respostas podem apresentar mais variedade e criatividade. Isso pode ser útil para gerar ideias e sugestões, mas também pode aumentar a inconsistência dos resultados. Uma temperatura alta não significa necessariamente que a resposta será incorreta.

2.2. Limite de Tokens (max_tokens)

Tokens são unidades utilizadas pelo modelo para processar e gerar textos. O parâmetro max_tokens define o limite de tokens que podem ser gerados na resposta, dependendo da API utilizada.

2.2.1. Janela de Contexto

A janela de contexto representa a quantidade de tokens que o modelo consegue considerar durante uma interação, incluindo as instruções, as mensagens anteriores e a resposta gerada. Quando esse limite é ultrapassado, pode ser necessário reduzir o conteúdo enviado.

2.2.2. Custos e Truncamento de Saídas

Em muitas APIs, o consumo de tokens influencia o custo da utilização do modelo. Além disso, se o limite de saída for muito baixo, a resposta poderá terminar antes de apresentar todas as informações necessárias. Por isso, é importante configurar esse limite de acordo com a tarefa.

3. Técnicas de Prompting
3.1. Zero-shot Prompting
3.1.1. Conceito

Zero-shot é uma técnica em que a IA recebe uma instrução direta, sem exemplos prévios de como deve responder. O resultado depende da clareza do prompt e do conhecimento do modelo.

3.1.2. Exemplo Prático: Categorização de Erros de Log
prompt = """
Classifique o erro de log em uma das categorias:
[Autenticação, Banco de Dados, Rede ou Outro].

Retorne apenas a categoria correspondente.

Erro: "Connection timeout while connecting to database"
"""

# Saída esperada: Banco de Dados

3.1.3. Análise do Exemplo

Nesse exemplo, a IA recebe uma instrução e um erro de log, mas não recebe exemplos anteriores. A tarefa é identificar a categoria mais adequada. Essa técnica pode ser utilizada para classificar erros registrados no backend do SynapseShop.

3.2. Few-shot Prompting
3.2.1. Conceito

Few-shot é uma técnica em que são fornecidos alguns exemplos de entradas e saídas para orientar o modelo. Dessa forma, a IA consegue compreender melhor o formato e o padrão esperado para a resposta.

3.2.2. Exemplo Prático: Extração de Dados de Pedidos em JSON
prompt = """
Extraia o número do pedido e o valor total.
Retorne os dados em formato JSON.

Exemplo 1:
Entrada: "Pedido 101, total de R$ 50,00"
Saída: {"pedido": 101, "total": 50.00}

Exemplo 2:
Entrada: "Pedido 205, total de R$ 89,90"
Saída: {"pedido": 205, "total": 89.90}

Agora processe:
Entrada: "Pedido 310, total de R$ 120,00"
Saída:
"""

# Saída esperada:
# {"pedido": 310, "total": 120.00}

3.2.3. Análise do Exemplo

Os exemplos mostram à IA como os dados devem ser extraídos e organizados. Isso ajuda a manter um padrão nas respostas, facilitando o processamento das informações pelo backend. Em uma aplicação real, o JSON também deve ser validado antes de ser utilizado.

3.3. Chain of Thought (Cadeia de Pensamento)
3.3.1. Conceito

Chain of Thought é uma técnica que incentiva a IA a analisar um problema por etapas antes de apresentar uma conclusão. Isso pode ajudar em tarefas que exigem a avaliação de várias condições ou regras de negócio.

3.3.2. Exemplo Prático: Resolução de Regras de Negócio
prompt = """
Analise a seguinte regra de negócio:

Um pedido só pode ser enviado quando o pagamento
estiver aprovado e o produto estiver disponível
em estoque.

Pedido:
- Pagamento: aprovado
- Estoque: indisponível

Verifique cada condição e informe:
1. Se o pedido pode ser enviado.
2. Qual condição impediu o envio.
3. Qual ação deve ser realizada.

Apresente uma justificativa objetiva.
"""

# Saída esperada:
# O pedido não pode ser enviado.
# O produto está indisponível em estoque.
# É necessário aguardar a reposição antes do envio.

3.3.3. Análise do Exemplo

Nesse caso, a IA precisa considerar duas condições para determinar se o pedido pode ser enviado. A análise estruturada ajuda a identificar o motivo do bloqueio e a sugerir uma ação adequada. Para aplicações reais, a decisão final deve respeitar as regras implementadas no sistema, sem depender exclusivamente da IA.

4. Análise de Falhas e Mitigação
4.1. O que são Alucinações em LLMs?

Alucinações acontecem quando a IA apresenta informações incorretas, inventadas ou sem suporte nos dados disponíveis, mas as apresenta como se fossem verdadeiras. Isso pode ocorrer, por exemplo, quando o modelo inventa um status de pedido ou uma informação que não existe no banco de dados.

4.2. Quando as Alucinações Ocorrem?

As alucinações podem ocorrer quando o prompt é pouco claro, faltam informações importantes ou a pergunta exige dados que o modelo não possui. Elas também podem aparecer quando a IA tenta responder mesmo sem ter certeza sobre o assunto.

4.3. Estratégias para Prevenção e Mitigação
4.3.1. Uso de Delimitadores

Delimitadores, como aspas triplas ou marcadores específicos, ajudam a separar as instruções dos dados que serão analisados. Isso torna o prompt mais organizado e reduz a possibilidade de confusão entre o conteúdo de entrada e as instruções.

Exemplo:

Analise o log abaixo e identifique o erro.

<log>
Erro ao conectar ao banco de dados.
</log>

Não invente informações que não estejam presentes no log.

4.3.2. Restrições Explícitas nos Prompts

As restrições explícitas definem o que a IA pode ou não fazer. É possível especificar o formato da resposta, limitar as categorias disponíveis e instruir o modelo a informar quando não
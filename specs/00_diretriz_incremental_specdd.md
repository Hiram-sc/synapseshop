# META-SPEC: Diretriz de Desenvolvimento Incremental (Restrição de Escopo)

## 1. Regra de Ouro do Projeto (Contexto Restrito)
Este repositório hospeda o desenvolvimento do SynapseShop, o MVP de um backend de pedidos com IA integrada. O planejamento completo deste software abrange um curso de 100 horas dividido em 25 aulas[cite: 1]. 

**ATENÇÃO ABSOLUTA PARA AGENTES DE IA E DESENVOLVEDORES:** 
Embora o escopo total do projeto (que inclui mensageria, RAG, NL-to-SQL, FastAPI e Django[cite: 1]) já esteja mapeado, **o desenvolvimento seguirá estritamente a metodologia SpecDD (Specification-Driven Development) de forma iterativa e incremental.**

## 2. Proibição de Antecipação (Anti-Hallucination Rule)
* **Escopo Fechado:** Cada bloco de aulas entrega apenas uma parte funcional do sistema[cite: 1]. É terminantemente proibido tentar implementar, sugerir, importar bibliotecas ou gerar código para funcionalidades que não estejam explicitamente declaradas na `spec` da aula atual.
* **Sem Vazamento de Contexto:** Se a spec ativa for da Aula 2 (Conteinerização), não crie modelos de banco de dados (Aula 6), não configure o Django REST Framework (Aula 4) e não insira lógicas de inteligência artificial (Aulas 14+)[cite: 1].
* **Resolução Estrita:** Qualquer ferramenta de IA generativa lendo este repositório deve limitar sua resposta e geração de código única e exclusivamente aos "Critérios de Entrega (Definition of Done)" do documento de especificação (Spec) fornecido no momento.

## 3. Fluxo de Trabalho
1. O desenvolvedor fornecerá a spec atual (ex: `specs_aula_03.md`).
2. A implementação deve satisfazer **apenas** os requisitos daquela spec específica.
3. O código não deve conter *placeholders* ansiosos ou configurações de infraestrutura que só serão utilizadas em etapas futuras.
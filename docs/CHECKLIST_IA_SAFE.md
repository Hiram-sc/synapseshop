# Checklist "IA-Safe" — Revisão de Código Gerado por IA

> Guia para revisar código, configuração, documentação ou soluções sugeridas por assistentes virtuais antes de sua integração ao projeto. As revisões devem considerar os requisitos funcionais, técnicos e de segurança, registrando os prompts relevantes no `PROMPTS.md`.

## 1. Escopo e requisitos

- [ ] A solução atende aos requisitos definidos para a funcionalidade.
- [ ] O código está limitado ao escopo solicitado.
- [ ] A alteração está alinhada à arquitetura existente do projeto.

## 2. Código e dependências

- [ ] O código sugerido foi compreendido e revisado antes da integração.
- [ ] Todos os imports e dependências adicionados são necessários e utilizados.
- [ ] As novas dependências estão registradas no arquivo apropriado do projeto.

## 3. Modelagem e dados

- [ ] Os tipos de dados e campos são adequados ao domínio.
- [ ] As regras de integridade e relacionamentos foram preservadas.
- [ ] Alterações na estrutura de dados possuem as migrações ou mecanismos necessários.

## 4. APIs e validação

- [ ] As rotas e métodos HTTP estão de acordo com o padrão definido pelo projeto.
- [ ] Entradas inválidas e recursos inexistentes possuem tratamento adequado.
- [ ] Os status HTTP utilizados são apropriados para cada operação.

## 5. Segurança

- [ ] Nenhuma credencial, senha, token ou informação sensível foi inserida no código.
- [ ] Dados recebidos externamente são devidamente validados.
- [ ] Erros e respostas não expõem informações internas desnecessárias.

## 6. Testes e integração

- [ ] O código foi executado e validado localmente.
- [ ] Foram testados cenários de sucesso e de erro.
- [ ] As funcionalidades existentes continuam funcionando corretamente após a alteração.

## 7. Revisão e rastreabilidade

- [ ] As sugestões da IA foram verificadas antes de serem incorporadas ao projeto.
- [ ] Prompts relevantes e alterações manuais foram registrados no `PROMPTS.md`.
- [ ] A alteração final foi revisada e está pronta para integração ao projeto.

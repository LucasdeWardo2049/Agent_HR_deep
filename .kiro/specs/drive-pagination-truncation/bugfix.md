# Bugfix Requirements Document

## Introduction

`GoogleWorkspaceClient.list_resume_files` lista os currículos da pasta de talentos com uma única chamada a `GOOGLEDRIVE_FIND_FILE`, passando apenas a query `q`. Não há parâmetro de paginação nem laço de continuação, então quando a pasta tem mais arquivos suportados do que o tamanho de página padrão do provedor, a função devolve uma lista incompleta de `ResumeFile`.

A truncagem é silenciosa e se propaga: `sync_profiles` monta `stats.drive_file_ids` a partir da lista truncada e `search()` usa esse conjunto para filtrar `store.list_profiles`, de modo que candidatos ausentes da primeira página ficam fora do relatório mesmo quando já existe um `CandidateProfile` em cache no Postgres. Nenhuma exceção é levantada e nenhum aviso entra em `SyncStats.warnings`; o relatório informa `candidates_analyzed` e "Busca concluída. N perfis foram preparados para revisão humana." como se a pasta tivesse apenas N candidatos.

Isso viola a garantia de produto reforçada no `AGENTS.md` de apresentar todos os candidatos da pasta atual, em ordem alfabética. Portanto é uma correção de defeito, não uma otimização. Observação de escopo: os nomes exatos dos parâmetros de paginação e a localização do token de continuação no envelope do Composio ainda não foram confirmados contra o schema da versão fixada `20260815_00` — essa verificação pertence à fase de design.

## Bug Analysis

### Current Behavior (Defect)

O que acontece hoje quando a pasta de talentos excede uma página de resultados.

1.1 WHEN a pasta de talentos contém mais arquivos suportados do que o tamanho de página padrão do provedor THEN the system retorna apenas os registros da primeira página e descarta silenciosamente os demais.

1.2 WHEN a resposta do Composio inclui um token de continuação THEN the system ignora o token e não emite nenhuma requisição adicional.

1.3 WHEN `list_resume_files` retorna uma lista truncada THEN the system monta `stats.drive_file_ids` apenas com os IDs da página retornada, e `search()` filtra `store.list_profiles` por esse conjunto, excluindo do relatório perfis já em cache no Postgres.

1.4 WHEN ocorre truncagem THEN the system não levanta exceção e não adiciona aviso a `SyncStats.warnings`, reportando `candidates_analyzed` e a mensagem de conclusão como se a pasta tivesse apenas os candidatos da primeira página.

1.5 WHEN a pasta cresce além de uma página THEN the system produz um relatório cuja composição depende da ordem de paginação do provedor, sem qualquer sinal observável de que candidatos foram omitidos.

### Expected Behavior (Correct)

O que deve acontecer nas mesmas condições.

2.1 WHEN a pasta de talentos contém mais arquivos suportados do que o tamanho de página do provedor THEN the system SHALL percorrer todas as páginas e retornar a lista completa de `ResumeFile` correspondentes à query.

2.2 WHEN a resposta do Composio inclui um token de continuação THEN the system SHALL solicitar a página seguinte usando esse token, repetindo até que nenhuma página adicional seja indicada.

2.3 WHEN a listagem completa é obtida THEN the system SHALL popular `stats.drive_file_ids` com os IDs de todos os arquivos da pasta, de modo que `search()` inclua no escopo do relatório todos os perfis em cache correspondentes.

2.4 WHEN a paginação não pode ser concluída — página seguinte falha, ou um limite defensivo de páginas é atingido com token ainda presente — THEN the system SHALL adicionar um aviso explícito de listagem possivelmente incompleta a `SyncStats.warnings` em vez de retornar silenciosamente uma lista truncada.

2.5 WHEN páginas distintas retornam o mesmo `drive_file_id` THEN the system SHALL desduplicar por `drive_file_id`, preservando um único `ResumeFile` por arquivo.

2.6 WHEN uma resposta paginada em múltiplas páginas é simulada offline THEN the system SHALL produzir a lista completa de `ResumeFile`, comprovada por teste na suíte pytest padrão sem acesso à rede.

### Unchanged Behavior (Regression Prevention)

Comportamento existente que precisa continuar valendo.

3.1 WHEN a pasta de talentos cabe em uma única página e a resposta não traz token de continuação THEN the system SHALL CONTINUE TO executar exatamente uma chamada a `GOOGLEDRIVE_FIND_FILE` e retornar os mesmos `ResumeFile` de hoje.

3.2 WHEN um registro tem `mimeType` fora de `SUPPORTED_MIME_TYPES` (PDF, DOCX, Google Docs) THEN the system SHALL CONTINUE TO descartar esse registro.

3.3 WHEN um registro não tem `id`/`file_id`, `name`/`file_name` ou `mimeType`/`mime_type` válidos THEN the system SHALL CONTINUE TO descartar esse registro sem levantar exceção.

3.4 WHEN um registro não traz `webViewLink`/`web_view_link` ou `modifiedTime`/`modified_time` THEN the system SHALL CONTINUE TO usar a URL de fallback `https://drive.google.com/open?id={id}` e `modified_time = None`.

3.5 WHEN o envelope de resposta não contém nenhuma lista de registros reconhecível THEN the system SHALL CONTINUE TO retornar uma lista vazia sem erro.

3.6 WHEN a chamada ao Composio falha THEN the system SHALL CONTINUE TO levantar `GoogleWorkspaceError` com a mesma semântica de mensagem e de `log_id`/`request_id`.

3.7 WHEN a integração com o Drive é usada THEN the system SHALL CONTINUE TO usar a versão datada fixada `composio_googledrive_version` e a query atual de pasta, lixeira e MIME types, sem alterar o slug da ferramenta.

3.8 WHEN um relatório é gerado THEN the system SHALL CONTINUE TO ordenar candidatos alfabeticamente, sem ranking, aprovação, rejeição ou recomendação, e sem introduzir campos pessoais proibidos.

3.9 WHEN eventos de sincronização são registrados THEN the system SHALL CONTINUE TO logar apenas IDs, provider, model, duração, fallback e tipo de erro, nunca corpo de currículo ou segredos.

3.10 WHEN a suíte pytest padrão é executada THEN the system SHALL CONTINUE TO rodar offline e rápido, sem novas tabelas de domínio, migrações Alembic ou workflows do Agno.

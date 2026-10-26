# O Jornal da Manhã

Todo dia às 5h30 (Brasília) o GitHub lê CNN Brasil, ge e Fox News, o Gemini escolhe as notícias mais importantes, traduz e resume em português, e uma primeira página de jornal com fotos é publicada num link fixo.

## Instalação (uma vez só, uns 15 minutos)

1. **Chave do Gemini**: entre em https://aistudio.google.com/apikey e crie uma chave gratuita.
2. **Repositório**: no GitHub, crie um repositório **público** novo (por exemplo `meu-jornal`).
3. **Subir os arquivos**: use "Add file > Upload files" e arraste o conteúdo desta pasta, incluindo a pasta `.github`. Se o navegador não mostrar pastas ocultas, crie o arquivo `.github/workflows/jornal.yml` pelo botão "Create new file" digitando o caminho completo e colando o conteúdo.
4. **Guardar a chave**: em *Settings > Secrets and variables > Actions > New repository secret*, crie `GEMINI_API_KEY` e cole a chave.
5. **Ligar o site**: em *Settings > Pages*, escolha *Deploy from a branch*, branch `main`, pasta `/docs`.
6. **Testar agora**: aba *Actions > Jornal diário > Run workflow*. Em cerca de 1 minuto o arquivo `docs/index.html` aparece.
7. **Seu link**: `https://SEU-USUARIO.github.io/meu-jornal/`. Salve nos favoritos ou na tela inicial do celular.

## Mudar fontes e tamanho

Edite `fontes.json` direto no site do GitHub:

- `total_itens`: quantas notícias entram na página (padrão 24).
- `horas_recentes`: só entram notícias das últimas X horas.
- `fontes`: para cada uma, `nome` e `url` do feed RSS. O campo `plano_b` é uma página usada se o RSS falhar.

## Se algo der errado

- **A edição não atualizou**: o script mantém a edição anterior no ar se o Gemini ou todas as fontes falharem. Veja o motivo na aba *Actions*, no último run.
- **Erro 429 ou limite**: o plano gratuito do Gemini tem limite por minuto e por dia. Uma execução por dia costuma ficar bem abaixo.
- **Modelo do Gemini fora do ar ou trocado**: crie a variável de ambiente `GEMINI_MODEL` no workflow, ou troque o nome padrão em `gerar_jornal.py` (hoje `gemini-2.5-flash`).
- **Feed do ge sem fotos ou vazio**: é a fonte mais frágil. O script tenta o RSS e cai para a leitura da página.
- **Horário**: o agendador do GitHub pode atrasar alguns minutos nos horários de pico.

As edições antigas ficam guardadas em `docs/edicoes/AAAA-MM-DD.html`.

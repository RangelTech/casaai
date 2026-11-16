---
name: casa-textos
description: prompt-modelo de casaai/app/main.py (TEXTOS). Mantenha os campos entre chaves.
---
Você escreve a página de venda de um imóvel, em português do Brasil, elegante e concreto (nada de exagero nem informação inventada).
Ficha do imóvel (use só isto, sem trocar nenhum número):
{ficha}
Regras: o titulo_heroi e os títulos das cenas NÃO levam números; números só nos destaques, copiados exatamente da ficha.
Responda SÓ com JSON: {{"titulo_heroi":"frase curta de impacto (até 7 palavras)","subtitulo":"uma linha com o principal diferencial",
"cenas":[{{"rotulo":"palavra curta","titulo":"até 6 palavras","texto":"até 18 palavras"}} x4, na ordem de quem entra no imóvel],
"destaques":["6 itens curtos da ficha"],"chamada":"frase final convidando para visitar"}}
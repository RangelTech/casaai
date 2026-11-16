"""Casa AI: do vídeo do imóvel a um site cinematográfico pronto para vender. API + páginas no mesmo serviço.

O corretor cadastra o imóvel e sobe um vídeo (celular serve). O Casa AI tira os quadros (o vídeo anda conforme o visitante rola),
a IA (Hermes) escreve os textos de venda a partir da ficha, e a página pública /c/<endereço> fica no ar com botão de WhatsApp.
Fase 2 (spec 44): tour 3D navegável (three.js) reconstruído na GPU a partir do mesmo vídeo.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import shutil
import threading
import unicodedata
import urllib.parse
import uuid
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

from app import auth, mailer, senha_rotas, video
from app.config import Config, carregar
from app.db import Banco, agora
from app import admin  # prompts editáveis no /admin

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("casaai")
COOKIE = "casaai_s"
ESTATICO = Path(__file__).resolve().parent.parent / "static"
MAX_VIDEO = 900 * 1024 * 1024
LIMITES = {None: 1, "corretor": 6, "imobiliaria": 40}  # sites por mês (grátis = 1 de teste, com a marca Casa AI)

PLANOS = {
    "corretor": {"nome": "Corretor", "preco": 59.90, "depois": 89.90,
                 "itens": ["6 sites de imóvel por mês", "Rolagem cinematográfica com o seu vídeo", "Textos de venda escritos pela IA",
                           "Botão de WhatsApp e contador de visitas", "Sem a marca Casa AI"]},
    "imobiliaria": {"nome": "Imobiliária", "preco": 149.90, "depois": 219.90,
                    "itens": ["40 sites por mês", "Tudo do Corretor", "Cor da sua marca em cada site", "Vários corretores no mesmo plano",
                              "Tour 3D navegável assim que lançar"]},
}

TEXTOS = """Você escreve a página de venda de um imóvel, em português do Brasil, elegante e concreto (nada de exagero nem informação inventada).
Ficha do imóvel (use só isto, sem trocar nenhum número):
{ficha}
Regras: o titulo_heroi e os títulos das cenas NÃO levam números; números só nos destaques, copiados exatamente da ficha.
Responda SÓ com JSON: {{"titulo_heroi":"frase curta de impacto (até 7 palavras)","subtitulo":"uma linha com o principal diferencial",
"cenas":[{{"rotulo":"palavra curta","titulo":"até 6 palavras","texto":"até 18 palavras"}} x4, na ordem de quem entra no imóvel],
"destaques":["6 itens curtos da ficha"],"chamada":"frase final convidando para visitar"}}"""


def _slug(t: str) -> str:
    s = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:48] or "imovel"


def criar_app(cfg: Config | None = None, llm=None, sincrono: bool = False) -> FastAPI:
    cfg = cfg or carregar()
    os.makedirs(cfg.dados, exist_ok=True)
    db = Banco(os.path.join(cfg.dados, "casaai.db"))
    admin.preparar(db)
    PASTA = Path(cfg.dados) / "imoveis"
    PASTA.mkdir(exist_ok=True)
    app = FastAPI(title="Casa AI", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.cfg, app.state.db = cfg, db

    def chamar_llm(mensagens: list[dict]) -> str:
        if llm:
            return llm(mensagens)
        r = httpx.post(cfg.llm_base + "/chat/completions", headers={"Authorization": f"Bearer {cfg.llm_key}"}, timeout=240,
                       json={"model": cfg.llm_modelo, "messages": mensagens, "temperature": 0.7, "max_tokens": 1400})
        r.raise_for_status()
        return (r.json()["choices"][0]["message"]["content"] or "").strip()

    if cfg.demo_email:
        u = auth.usuario_por_email(db, cfg.demo_email.lower())
        db.exec("INSERT INTO assinaturas(usuario,plano,ativo_ate) VALUES(?,?,?) ON CONFLICT(usuario) DO UPDATE SET plano=excluded.plano, ativo_ate=excluded.ativo_ate",
                (u["id"], "imobiliaria", agora() + 3650 * 86400))

    def usuario(request: Request, obrigatorio: bool = True) -> dict | None:
        u = auth.usuario_da_sessao(db, cfg.segredo_sessao, request.cookies.get(COOKIE))
        if not u and obrigatorio:
            raise HTTPException(401, "entre na sua conta")
        return u

    def plano_de(u: dict) -> str | None:
        a = db.um("SELECT * FROM assinaturas WHERE usuario=?", (u["id"],))
        return a["plano"] if a and a["ativo_ate"] > agora() else None

    def usados_mes(u: dict) -> int:
        import datetime as dt
        h = dt.datetime.now(dt.timezone.utc)
        ini = h.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
        return db.um("SELECT COUNT(*) n FROM imoveis WHERE usuario=? AND estado IN ('processando','pronto') AND criado>=?", (u["id"], ini))["n"]

    def meu_imovel(u: dict, iid: int) -> dict:
        i = db.um("SELECT * FROM imoveis WHERE id=? AND usuario=?", (iid, u["id"]))
        if not i:
            raise HTTPException(404, "imóvel não encontrado")
        return i

    def vista(i: dict, publico: bool = False) -> dict:
        out = {k: i[k] for k in ("slug", "titulo", "tipo", "negocio", "preco", "bairro", "cidade", "quartos", "suites", "banheiros", "vagas",
                                 "area", "descricao", "corretor", "cor", "quadros")}
        out["textos"] = json.loads(i["textos"]) if i.get("textos") else None
        out["url"] = f"{cfg.base_url}/c/{i['slug']}"
        out["whatsapp"] = re.sub(r"\D", "", i["whatsapp"] or "")
        if not publico:
            out.update({"id": i["id"], "estado": i["estado"], "etapa": i["etapa"], "erro": i["erro"], "visitas": i["visitas"], "cliques": i["cliques"]})
        else:
            dono = db.um("SELECT * FROM usuarios WHERE id=?", (i["usuario"],))
            out["marca_casa_ai"] = not (dono and plano_de(dono))
        return out

    def ficha_texto(i: dict) -> str:
        """Ficha em português legível (com JSON cru o modelo trocava quartos por banheiros)."""
        partes = [f"Título: {i['titulo']}", f"Tipo: {i['tipo']} para {i['negocio']}"]
        if i["preco"]:
            partes.append(f"Preço: R$ {i['preco']:,.0f}".replace(",", "."))
        if i["bairro"] or i["cidade"]:
            partes.append("Local: " + ", ".join(x for x in (i["bairro"], i["cidade"]) if x))
        for k, n in (("quartos", "quartos"), ("suites", "suítes (já contadas entre os quartos)"), ("banheiros", "banheiros"),
                     ("vagas", "vagas de garagem"), ("area", "m² de área")):
            if i[k]:
                partes.append(f"{i[k]:g} {n}")
        if i["descricao"]:
            partes.append("Descrição do corretor: " + i["descricao"])
        return "\n".join("- " + x for x in partes)

    def escrever(i: dict) -> dict:
        erro = None
        for _ in range(2):  # a IA às vezes demora ou corta o JSON: uma nova tentativa antes dos textos padrão
            try:
                txt = chamar_llm([{"role": "user", "content": admin.formatar("casa-textos", TEXTOS, ficha=ficha_texto(i))}])
                m = re.search(r"\{.*\}", txt, re.S)
                textos = json.loads(re.sub(r",\s*([}\]])", r"\1", m.group(0)), strict=False)
                assert len(textos.get("cenas") or []) >= 3
                return textos
            except Exception as e:  # noqa: BLE001
                erro = e
        if True:  # a página sai mesmo sem a IA, com textos da ficha
            log.warning("textos padrão para %s: %s", i["id"], erro)
            return {"titulo_heroi": i["titulo"], "subtitulo": f"{i['bairro']}, {i['cidade']}".strip(", "),
                    "cenas": [{"rotulo": "Chegada", "titulo": "Bem-vindo", "texto": i["descricao"][:120]},
                              {"rotulo": "Espaço", "titulo": "Feito para viver", "texto": ""},
                              {"rotulo": "Detalhes", "titulo": "Cada canto pensado", "texto": ""},
                              {"rotulo": "Agora", "titulo": "Venha conhecer", "texto": ""}],
                    "destaques": [], "chamada": "Agende a sua visita"}

    # ------------------------------------------------ processamento (quadros + textos)
    def processar(iid: int) -> None:
        i = db.um("SELECT * FROM imoveis WHERE id=?", (iid,))
        base = PASTA / str(iid)
        try:
            db.exec("UPDATE imoveis SET etapa=? WHERE id=?", ("Tirando os quadros do vídeo", iid))
            info = video.quadros(base / "video.mp4", base / "q")
            db.exec("UPDATE imoveis SET etapa=?, quadros=? WHERE id=?", ("A IA está escrevendo os textos", info["total"], iid))
            textos = escrever(i)
            (base / "video.mp4").unlink(missing_ok=True)  # o vídeo original não fica guardado: só os quadros
            db.exec("UPDATE imoveis SET estado='pronto', etapa='', textos=?, pronto_em=? WHERE id=?", (json.dumps(textos, ensure_ascii=False), agora(), iid))
        except Exception as e:  # noqa: BLE001
            log.exception("imóvel %s", iid)
            db.exec("UPDATE imoveis SET estado='erro', erro=? WHERE id=?", (str(e)[:300], iid))

    @app.exception_handler(json.JSONDecodeError)
    async def corpo_invalido(request: Request, exc: Exception):
        return JSONResponse({"detail": "corpo inválido (envie JSON)"}, status_code=400)

    @app.get("/health")
    def health():
        return {"ok": True}

    # ------------------------------------------------ login
    def _login(u: dict) -> JSONResponse:
        token = auth.criar_sessao(db, cfg.segredo_sessao, u["id"])
        resp = JSONResponse({"ok": True})
        resp.set_cookie(COOKIE, token, max_age=auth.SESSAO_S, httponly=True, secure=not cfg.dev, samesite="lax", path="/")
        return resp

    @app.post("/api/entrar/codigo")
    async def pedir_codigo(request: Request):
        b = await request.json()
        email = auth.normalizar_email(b.get("email"))
        if not email:
            raise HTTPException(422, "e-mail inválido")
        cod, motivo = auth.novo_codigo(db, cfg.segredo_sessao, email)
        if motivo == "espere":
            raise HTTPException(429, "acabamos de mandar um código; espere alguns segundos")
        out = {"ok": True, "enviado": mailer.codigo(cfg, email, cod)}
        if cfg.dev:
            out["codigo_dev"] = cod
        return out

    @app.post("/api/entrar")
    async def entrar(request: Request):
        b = await request.json()
        email = auth.normalizar_email(b.get("email"))
        if not email:
            raise HTTPException(422, "e-mail inválido")
        r = auth.conferir_codigo(db, cfg.segredo_sessao, email, str(b.get("codigo") or ""))
        if r != "ok":
            raise HTTPException(400, {"invalido": "código errado", "expirado": "código vencido; peça outro", "tentativas": "muitas tentativas; peça outro código"}[r])
        return _login(auth.usuario_por_email(db, email))

    senha_rotas.registrar(app, db, cfg, mailer, _login, usuario)

    @app.post("/api/sair")
    def sair(request: Request):
        auth.encerrar_sessao(db, cfg.segredo_sessao, request.cookies.get(COOKIE))
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE, path="/")
        return resp

    @app.get("/api/planos")
    def planos():
        return {"planos": [{"codigo": k, **v, "checkout": cfg.checkouts.get(k) or "", "lancamento_ate": cfg.lancamento_ate} for k, v in PLANOS.items()]}

    @app.get("/api/conta")
    def conta(request: Request):
        u = usuario(request)
        p = plano_de(u)
        return {"email": u["email"], "plano": p, "usados": usados_mes(u), "limite": LIMITES.get(p, 1), "tem_senha": bool(u.get("senha_hash")),
                "planos": planos()["planos"]}

    # ------------------------------------------------ imóveis
    @app.get("/api/imoveis")
    def imoveis(request: Request):
        u = usuario(request)
        return {"imoveis": [vista(i) for i in db.todos("SELECT * FROM imoveis WHERE usuario=? ORDER BY criado DESC", (u["id"],))]}

    @app.post("/api/imoveis")
    async def novo_imovel(request: Request):
        u = usuario(request)
        b = await request.json()
        titulo = str(b.get("titulo") or "").strip()[:90]
        if len(titulo) < 4:
            raise HTTPException(422, "dê um título ao imóvel")
        if usados_mes(u) >= LIMITES.get(plano_de(u), 1):  # barra antes de o corretor subir o vídeo
            raise HTTPException(402, "você usou os sites do seu plano neste mês")
        num = lambda k, t=float: t(b[k]) if b.get(k) not in (None, "") else None  # noqa: E731
        slug = _slug(f"{titulo} {b.get('bairro') or ''}")
        while db.um("SELECT 1 FROM imoveis WHERE slug=?", (slug,)):
            slug = f"{slug[:40]}-{uuid.uuid4().hex[:4]}"
        cor = b.get("cor") if re.fullmatch(r"#[0-9a-fA-F]{6}", str(b.get("cor") or "")) else "#ffb547"
        iid = db.exec("""INSERT INTO imoveis(usuario,slug,titulo,tipo,negocio,preco,bairro,cidade,quartos,suites,banheiros,vagas,area,descricao,whatsapp,corretor,cor,criado)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (u["id"], slug, titulo, str(b.get("tipo") or "casa")[:20], "aluguel" if b.get("negocio") == "aluguel" else "venda", num("preco"),
                       str(b.get("bairro") or "")[:60], str(b.get("cidade") or "")[:60], num("quartos", int), num("suites", int), num("banheiros", int),
                       num("vagas", int), num("area"), str(b.get("descricao") or "")[:1500], re.sub(r"\D", "", str(b.get("whatsapp") or ""))[:15],
                       str(b.get("corretor") or "")[:60], cor, agora())).lastrowid
        return vista(db.um("SELECT * FROM imoveis WHERE id=?", (iid,)))

    @app.post("/api/imoveis/{iid}/video")
    async def subir_video(iid: int, request: Request):
        u = usuario(request)
        i = meu_imovel(u, iid)
        if i["estado"] == "processando":
            raise HTTPException(409, "esse imóvel já está sendo processado")
        if i["estado"] == "rascunho" and usados_mes(u) >= LIMITES.get(plano_de(u), 1):
            raise HTTPException(402, "você usou os sites do seu plano neste mês")
        base = PASTA / str(iid)
        base.mkdir(exist_ok=True)
        destino, total = base / "video.mp4", 0
        with open(destino, "wb") as f:
            async for pedaco in request.stream():
                total += len(pedaco)
                if total > MAX_VIDEO:
                    f.close()
                    destino.unlink(missing_ok=True)
                    raise HTTPException(413, "vídeo maior que 900 MB; exporte em 1080p")
                f.write(pedaco)
        if total < 10_000:
            destino.unlink(missing_ok=True)
            raise HTTPException(422, "vídeo vazio")
        db.exec("UPDATE imoveis SET estado='processando', etapa='Na fila', erro=NULL WHERE id=?", (iid,))
        if sincrono:
            processar(iid)
        else:
            threading.Thread(target=processar, args=(iid,), daemon=True).start()
        return vista(db.um("SELECT * FROM imoveis WHERE id=?", (iid,)))

    @app.get("/api/imoveis/{iid}")
    def ver_imovel(iid: int, request: Request):
        return vista(meu_imovel(usuario(request), iid))

    @app.post("/api/imoveis/{iid}/textos")
    def refazer_textos(iid: int, request: Request):
        """O corretor não gostou dos textos: a IA escreve de novo a partir da ficha (o vídeo não precisa subir de novo)."""
        u = usuario(request)
        i = meu_imovel(u, iid)
        if i["estado"] != "pronto":
            raise HTTPException(409, "o site ainda não está pronto")
        db.exec("UPDATE imoveis SET textos=? WHERE id=?", (json.dumps(escrever(i), ensure_ascii=False), iid))
        return vista(db.um("SELECT * FROM imoveis WHERE id=?", (iid,)))

    @app.delete("/api/imoveis/{iid}")
    def apagar_imovel(iid: int, request: Request):
        u = usuario(request)
        meu_imovel(u, iid)
        shutil.rmtree(PASTA / str(iid), ignore_errors=True)
        db.exec("DELETE FROM imoveis WHERE id=?", (iid,))
        return {"ok": True}

    # ------------------------------------------------ página pública do imóvel
    @app.get("/api/publico/{slug}")
    def publico(slug: str):
        i = db.um("SELECT * FROM imoveis WHERE slug=? AND estado='pronto'", (slug,))
        if not i:
            raise HTTPException(404, "imóvel não encontrado")
        db.exec("UPDATE imoveis SET visitas=visitas+1 WHERE id=?", (i["id"],))
        return vista(i, publico=True)

    @app.get("/c/{slug}/whatsapp")
    def clique_whatsapp(slug: str):
        i = db.um("SELECT * FROM imoveis WHERE slug=? AND estado='pronto'", (slug,))
        if not i or not i["whatsapp"]:
            raise HTTPException(404, "sem WhatsApp")
        db.exec("UPDATE imoveis SET cliques=cliques+1 WHERE id=?", (i["id"],))
        msg = f"Olá! Vi o imóvel \"{i['titulo']}\" e quero agendar uma visita: {cfg.base_url}/c/{slug}"
        return RedirectResponse("https://wa.me/" + re.sub(r"\D", "", i["whatsapp"]) + "?text=" + urllib.parse.quote(msg))

    @app.get("/c/{slug}/q/{arq}")
    def quadro(slug: str, arq: str):
        i = db.um("SELECT id FROM imoveis WHERE slug=? AND estado='pronto'", (slug,))
        if not i or not re.fullmatch(r"\d{4}\.webp|quadros\.json", arq):
            raise HTTPException(404, "não encontrado")
        p = PASTA / str(i["id"]) / "q" / arq
        if not p.exists():
            raise HTTPException(404, "não encontrado")
        return FileResponse(p, headers={"Cache-Control": "public, max-age=604800, immutable"})

    @app.get("/c/{slug}/capa.webp")
    def capa(slug: str):
        i = db.um("SELECT id FROM imoveis WHERE slug=?", (slug,))
        p = PASTA / str(i["id"]) / "capa.webp" if i else None
        if not p or not p.exists():
            raise HTTPException(404, "não encontrado")
        return FileResponse(p)

    @app.get("/c/{slug}", response_class=HTMLResponse)
    def pagina_imovel(slug: str):
        return FileResponse(ESTATICO / "imovel.html")

    # ------------------------------------------------ Cakto
    @app.post("/api/cakto")
    async def cakto(request: Request):
        cru = await request.body()
        try:
            b = json.loads(cru)
        except ValueError:
            raise HTTPException(400, "json")
        if not cfg.cakto_segredo or not hmac.compare_digest(str(b.get("secret") or ""), cfg.cakto_segredo):
            raise HTTPException(401, "segredo")
        evento = str(b.get("event") or "")
        dados = b.get("data") or {}
        if isinstance(dados, list):
            dados = dados[0] if dados else {}
        eid = str(dados.get("id") or hashlib.sha256(cru).hexdigest()[:24]) + ":" + evento
        if db.um("SELECT 1 FROM eventos_cakto WHERE id=?", (eid,)):
            return {"ok": True, "repetido": True}
        db.exec("INSERT INTO eventos_cakto(id,recebido,corpo) VALUES(?,?,?)", (eid, agora(), cru.decode("utf-8", "replace")[:20000]))
        email = auth.normalizar_email((dados.get("customer") or {}).get("email"))
        plano = cfg.planos_por_produto.get(str((dados.get("product") or {}).get("id") or ""))
        if not email or plano not in PLANOS:
            return {"ok": True, "ignorado": "sem email ou produto desconhecido"}
        u = auth.usuario_por_email(db, email)
        a = db.um("SELECT * FROM assinaturas WHERE usuario=?", (u["id"],))
        if evento in ("purchase_approved", "subscription_renewed", "subscription_created"):
            base = max(agora(), a["ativo_ate"] if a else 0)
            db.exec("INSERT INTO assinaturas(usuario,plano,ativo_ate,pedido) VALUES(?,?,?,?) ON CONFLICT(usuario) DO UPDATE SET plano=excluded.plano, ativo_ate=excluded.ativo_ate, pedido=excluded.pedido",
                    (u["id"], plano, base + 32 * 86400, str(dados.get("id") or "")))
            return {"ok": True, "liberado": email}
        if evento in ("refund", "chargeback", "subscription_canceled", "subscription_cancelled"):
            db.exec("UPDATE assinaturas SET ativo_ate=? WHERE usuario=?", (agora(), u["id"]))
            return {"ok": True, "revogado": email}
        return {"ok": True, "ignorado": evento}

    from fastapi.staticfiles import StaticFiles
    app.include_router(admin.rotas(db, cfg.segredo_sessao, """SELECT email, criado, plano, ativo_ate, (ativo_ate > strftime('%s','now')) ativo
        FROM usuarios ORDER BY criado DESC""", seguro=not cfg.dev))

    @app.get("/admin", response_class=HTMLResponse)
    def pagina_admin():
        return FileResponse(ESTATICO / "admin.html")

    app.mount("/s", StaticFiles(directory=ESTATICO), name="estatico")

    @app.get("/", response_class=HTMLResponse)
    def inicio():
        return FileResponse(ESTATICO / "index.html")

    @app.get("/painel", response_class=HTMLResponse)
    def painel():
        return FileResponse(ESTATICO / "painel.html")

    return app


app = criar_app() if os.environ.get("CASAAI_SEM_APP") != "1" else None

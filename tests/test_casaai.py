from produtos_core.teste import entrar
import json
import os
import subprocess

os.environ["CASAAI_SEM_APP"] = "1"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import Config  # noqa: E402
from app.main import criar_app  # noqa: E402


def llm_falso(msgs):
    return json.dumps({"titulo_heroi": "Luz, jardim e silêncio", "subtitulo": "Casa térrea no Jardim Goiás",
                       "cenas": [{"rotulo": "Chegada", "titulo": "Fachada que recebe", "texto": "Jardim na entrada"},
                                 {"rotulo": "Sala", "titulo": "Pé-direito duplo", "texto": "Luz o dia inteiro"},
                                 {"rotulo": "Cozinha", "titulo": "Gourmet integrada", "texto": "Ilha e coifa"},
                                 {"rotulo": "Área", "titulo": "Piscina aquecida", "texto": "Churrasqueira e deck"}],
                       "destaques": ["3 suítes", "320 m²"], "chamada": "Agende a sua visita"})


@pytest.fixture(scope="module")
def video(tmp_path_factory):
    p = tmp_path_factory.mktemp("v") / "casa.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=24", "-t", "5", "-c:v", "mpeg4", "-q:v", "5", str(p)], check=True)
    return p.read_bytes()


@pytest.fixture
def amb(tmp_path):
    cfg = Config(dados=str(tmp_path), segredo_sessao="s", dev=True, cakto_segredo="ck", planos_por_produto={"p-corretor": "corretor"}, base_url="http://t")
    c = TestClient(criar_app(cfg, llm=llm_falso, sincrono=True))
    entrar(c, "cor@x.com")
    return c


def test_video_vira_site_publico(amb, video):
    c = amb
    i = c.post("/api/imoveis", json={"titulo": "Casa no Jardim Goiás", "bairro": "Jardim Goiás", "cidade": "Goiânia", "preco": 1850000,
                                     "quartos": 4, "suites": 3, "area": 320, "whatsapp": "(62) 99999-0000", "descricao": "Térrea, piscina aquecida"}).json()
    assert i["estado"] == "rascunho" and i["slug"].startswith("casa-no-jardim-goias")
    r = c.post(f"/api/imoveis/{i['id']}/video", content=video).json()
    assert r["estado"] == "pronto" and r["quadros"] >= 60 and r["textos"]["titulo_heroi"]
    pub = TestClient(c.app)
    j = pub.get(f"/api/publico/{i['slug']}").json()
    assert j["marca_casa_ai"] is True and j["whatsapp"] == "62999990000" and "id" not in j
    assert pub.get(f"/c/{i['slug']}/q/quadros.json").json()["total"] == r["quadros"]
    assert pub.get(f"/c/{i['slug']}/q/0001.webp").status_code == 200
    assert pub.get(f"/c/{i['slug']}").status_code == 200
    w = pub.get(f"/c/{i['slug']}/whatsapp", follow_redirects=False)
    assert w.status_code in (302, 307) and w.headers["location"].startswith("https://wa.me/62999990000?text=")
    k = c.get(f"/api/imoveis/{i['id']}").json()
    assert k["visitas"] == 1 and k["cliques"] == 1


def test_limite_gratis_e_plano(amb, video):
    c = amb
    a = c.post("/api/imoveis", json={"titulo": "Apartamento 1"}).json()
    c.post(f"/api/imoveis/{a['id']}/video", content=video)
    assert c.post("/api/imoveis", json={"titulo": "Apartamento 2"}).status_code == 402  # grátis: 1 site, barrado antes do vídeo
    c.post("/api/cakto", json={"secret": "ck", "event": "purchase_approved", "data": {"id": "1", "customer": {"email": "cor@x.com"}, "product": {"id": "p-corretor"}}})
    b = c.post("/api/imoveis", json={"titulo": "Apartamento 2"}).json()
    assert c.post(f"/api/imoveis/{b['id']}/video", content=video).json()["estado"] == "pronto"
    assert TestClient(c.app).get(f"/api/publico/{b['slug']}").json()["marca_casa_ai"] is False

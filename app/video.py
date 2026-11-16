"""Vídeo do imóvel -> sequência de quadros para a ROLAGEM QUADRO A QUADRO (skill sites-cinematograficos).
O vídeo anda conforme o visitante rola a página. 100 a 140 quadros WEBP bastam para ficar liso e leve."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


def duracao(video: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(video)],
                       capture_output=True, text=True, timeout=120)
    return float(r.stdout.strip() or 0)


def quadros(video: Path, pasta: Path, total: int = 120, largura: int = 1280, qualidade: int = 62) -> dict:
    pasta.mkdir(parents=True, exist_ok=True)
    d = duracao(video)
    if d < 3:
        raise ValueError("o vídeo precisa ter pelo menos 3 segundos")
    total = max(60, min(total, 160))
    fps = total / d
    for f in pasta.glob("*.webp"):
        f.unlink()
    r = subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(video), "-an",
                        "-vf", f"fps={fps:.5f},scale='min({largura},iw)':-2:flags=lanczos", "-c:v", "libwebp", "-quality", str(qualidade),
                        "-compression_level", "4", str(pasta / "%04d.webp")], capture_output=True, text=True, timeout=1800)
    gerados = sorted(pasta.glob("*.webp"))
    if r.returncode != 0 or not gerados:
        raise RuntimeError("não consegui ler o vídeo (" + (r.stderr or "")[-200:] + ")")
    info = {"total": len(gerados), "duracao": round(d, 1)}
    (pasta / "quadros.json").write_text(json.dumps(info))
    meio = gerados[len(gerados) // 3]
    shutil.copy(meio, pasta.parent / "capa.webp")
    return info

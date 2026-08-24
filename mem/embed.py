"""Embeddings locales para la búsqueda semántica (ONNX en CPU, sin torch).

El modelo se descarga UNA sola vez al cache estándar de Hugging Face; nada en
el flujo de escritura dispara la descarga (`disponible()` solo mira el disco).
Sin modelo, el índice guarda vec=NULL y la búsqueda degrada a BM25.

MEM_EMBED_FAKE=1 (tests): vectores deterministas por hash del texto, dim 32,
cero red. El prefijo query/passage se ignora en fake para que el mismo texto
dé el mismo vector (cos=1), que es lo que los tests necesitan controlar.
"""
import hashlib
import os
import re

import numpy as np

from . import config

# int8 primero: ~120 MB y portable; model.onnx (fp32, ~450 MB) como último recurso
_ARCHIVOS = ("onnx/model_int8.onnx", "onnx/model_qint8_avx512_vnni.onnx", "onnx/model.onnx")
_MAX_TOKENS = 512
_LOTE = 16
_ses = None   # (modelo, InferenceSession, Tokenizer, nombres_de_inputs)


def _fake() -> bool:
    return os.environ.get("MEM_EMBED_FAKE") == "1"


def modelo() -> str:
    return str(config.cargar().get("embed_modelo") or "")


def _archivos_locales(nombre: str) -> tuple[str, str] | None:
    """(model.onnx, tokenizer.json) si YA están en el cache HF local; sin red."""
    from huggingface_hub import hf_hub_download
    from huggingface_hub.errors import EntryNotFoundError, LocalEntryNotFoundError
    try:
        tok = hf_hub_download(nombre, "tokenizer.json", local_files_only=True)
    except (EntryNotFoundError, LocalEntryNotFoundError, OSError):
        return None
    for f in _ARCHIVOS:
        try:
            return hf_hub_download(nombre, f, local_files_only=True), tok
        except (EntryNotFoundError, LocalEntryNotFoundError, OSError):
            continue
    return None


def _descargar(nombre: str) -> tuple[str, str]:
    from huggingface_hub import hf_hub_download
    from huggingface_hub.errors import EntryNotFoundError
    tok = hf_hub_download(nombre, "tokenizer.json")
    for f in _ARCHIVOS:
        try:
            return hf_hub_download(nombre, f), tok
        except EntryNotFoundError:
            continue
    raise FileNotFoundError(f"{nombre}: ningún ONNX conocido ({', '.join(_ARCHIVOS)})")


def disponible() -> bool:
    """True si se puede embeber sin descargar nada. Nunca dispara red."""
    if _fake():
        return True
    try:
        return _archivos_locales(modelo()) is not None
    except Exception:
        return False


def _sesion(descargar: bool):
    global _ses
    nombre = modelo()
    if _ses and _ses[0] == nombre:
        return _ses
    rutas = _archivos_locales(nombre) or (_descargar(nombre) if descargar else None)
    if not rutas:
        return None
    import onnxruntime
    from tokenizers import Tokenizer
    tok = Tokenizer.from_file(rutas[1])
    tok.enable_truncation(max_length=_MAX_TOKENS)
    tok.enable_padding()
    ses = onnxruntime.InferenceSession(rutas[0], providers=["CPUExecutionProvider"])
    _ses = (nombre, ses, tok, [i.name for i in ses.get_inputs()])
    return _ses


def _encode_fake(textos: list[str]) -> np.ndarray:
    """Bolsa de palabras hasheadas: textos con palabras en común ⇒ coseno alto.
    Determinista y graduado — alcanza para testear ranking, vecinas y duplicados."""
    filas = []
    for t in textos:
        v = np.zeros(32, dtype=np.float32)
        for palabra in re.findall(r"\w+", t.lower()) or [""]:
            h = hashlib.sha256(palabra.encode("utf-8")).digest()
            v += np.frombuffer(h, dtype=np.uint8).astype(np.float32) - 127.5
        filas.append(v / max(float(np.linalg.norm(v)), 1e-9))
    return np.stack(filas)


def encode(textos: list[str], tipo: str = "passage", descargar: bool = False) -> np.ndarray | None:
    """(n, dim) float32 L2-normalizado, o None si el modelo no está y descargar=False.

    `tipo` = "query" | "passage": los modelos e5 esperan ese prefijo en el texto.
    La sesión ONNX se carga una vez por proceso y se reusa.
    """
    if not textos:
        return np.zeros((0, 1), dtype=np.float32)
    if _fake():
        return _encode_fake(textos)
    s = _sesion(descargar)
    if s is None:
        return None
    _, ses, tok, inputs = s
    filas = []
    for i in range(0, len(textos), _LOTE):
        lote = tok.encode_batch([f"{tipo}: {t}" for t in textos[i:i + _LOTE]])
        ids = np.array([e.ids for e in lote], dtype=np.int64)
        mask = np.array([e.attention_mask for e in lote], dtype=np.int64)
        feed = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in inputs:
            feed["token_type_ids"] = np.zeros_like(ids)
        hidden = ses.run(None, feed)[0]   # primer output = last_hidden_state en estos exports
        m = mask[:, :, None].astype(np.float32)
        pooled = (hidden * m).sum(axis=1) / np.clip(m.sum(axis=1), 1e-9, None)
        filas.append(pooled)
    v = np.concatenate(filas).astype(np.float32)
    return v / np.clip(np.linalg.norm(v, axis=1, keepdims=True), 1e-9, None)

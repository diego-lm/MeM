import os

# Toda la suite usa el embedder fake (embed.py): determinista, sin red y sin
# cargar el modelo ONNX real aunque ya esté descargado en el cache de HF.
os.environ.setdefault("MEM_EMBED_FAKE", "1")

# Sin debounce del barrido de mtimes (indice.SYNC_CADA): los tests editan .md
# directo y esperan que la próxima consulta lo absorba ya mismo.
os.environ.setdefault("MEM_SYNC_CADA", "0")

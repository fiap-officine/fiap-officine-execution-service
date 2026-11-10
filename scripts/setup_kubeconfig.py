import base64
import json
import os

import yaml


def setup_kubeconfig():
    raw = os.environ.get("KUBE_CONFIG_RAW", "").strip()
    if not raw:
        print("Warning: KUBE_CONFIG_RAW is empty.")
        return

    # 1. Limpeza preliminar de aspas externas (JSON/Shell)
    for _ in range(3):
        raw = raw.strip()
        if (raw.startswith('"') and raw.endswith('"')) or (
            raw.startswith("'") and raw.endswith("'")
        ):
            try:
                raw = json.loads(raw)
            except Exception:
                raw = raw[1:-1].strip()

    # 2. Se quebras de linha vieram escapadas como literal \n
    if "\\n" in raw:
        raw = raw.replace("\\r\\n", "\n").replace("\\n", "\n")

    # 3. Tenta decodificar de Base64
    candidates = [
        raw,
        raw.replace("\r", "").replace("\n", "").replace(" ", "").replace("\t", ""),
    ]
    for c in candidates:
        try:
            padding = len(c) % 4
            if padding:
                c += "=" * (4 - padding)
            dec = base64.b64decode(c).decode("utf-8", errors="ignore")
            if "apiVersion" in dec and "clusters" in dec:
                raw = dec.strip()
                print("Base64 decodificado com sucesso.")
                break
        except Exception:
            pass

    # 4. Validar e normalizar via PyYAML
    config_data = None
    try:
        loaded = yaml.safe_load(raw)
        if isinstance(loaded, dict):
            config_data = loaded
        elif isinstance(loaded, str):
            loaded_inner = yaml.safe_load(loaded)
            if isinstance(loaded_inner, dict):
                config_data = loaded_inner
    except Exception as e:
        print(f"Aviso ao analisar YAML: {e}")

    target = os.path.expanduser("~/.kube/config")
    os.makedirs(os.path.dirname(target), exist_ok=True)

    with open(target, "w", encoding="utf-8") as f:
        if config_data and isinstance(config_data, dict):
            yaml.safe_dump(config_data, f, default_flow_style=False)
            print(f"Kubeconfig normalizado e salvo via PyYAML em {target}.")
        else:
            f.write(raw.strip() + "\n")
            print(f"Kubeconfig salvo como texto bruto em {target} ({len(raw)} bytes).")

    # Diagnostico das primeiras linhas
    try:
        with open(target, encoding="utf-8") as f:
            first_lines = [line.strip() for line in f.readlines()[:6]]
        print("Primeiras linhas do kubeconfig gerado:")
        for linha in first_lines:
            if "token" in linha.lower() or "certificate" in linha.lower():
                print("  " + linha.split(":")[0] + ": [MASKED]")
            else:
                print("  " + linha)
    except Exception:
        pass


if __name__ == "__main__":
    setup_kubeconfig()

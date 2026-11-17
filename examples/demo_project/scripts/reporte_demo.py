"""Herramienta FICTICIA 3/3: informe Markdown. Uso: python reporte_demo.py conf_reporte_demo.json TITULO"""
import json
import sys
from pathlib import Path


def main(conf_path: str, titulo: str) -> None:
    conf = json.loads(Path(conf_path).read_text(encoding="utf-8-sig"))
    root = Path(conf_path).resolve().parent.parent
    data = json.loads((root / conf["resumen"]).read_text(encoding="utf-8"))
    dst = root / conf["salida"]
    lines = [f"# {titulo}", "", "| Grupo | N | Suma | Media |", "|---|---|---|---|"]
    lines += [f"| {g} | {d['n']} | {d['suma']} | {d['media']} |" for g, d in data.items()]
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"reporte: {dst}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])

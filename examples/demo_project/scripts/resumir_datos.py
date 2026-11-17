"""Herramienta FICTICIA 2/3: resume el CSV limpio por grupo. Uso: python resumir_datos.py conf_resumir.json"""
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path


def main(conf_path: str) -> None:
    conf = json.loads(Path(conf_path).read_text(encoding="utf-8-sig"))
    root = Path(conf_path).resolve().parent.parent
    src, dst = root / conf["entrada"], root / conf["salida"]
    grp, val = conf["columna_grupo"], conf["columna_valor"]
    acc: dict[str, list[float]] = defaultdict(list)
    with open(src, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            acc[row[grp]].append(float(row[val]))
    resumen = {g: {"n": len(v), "suma": round(sum(v), 2), "media": round(sum(v) / len(v), 2)} for g, v in sorted(acc.items())}
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(resumen, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"resumir: {len(resumen)} grupos -> {dst}")


if __name__ == "__main__":
    main(sys.argv[1])

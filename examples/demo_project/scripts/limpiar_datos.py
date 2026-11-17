"""Herramienta FICTICIA 1/3: limpia un CSV de ventas. Uso: python limpiar_datos.py conf_limpiar.json"""
import csv
import json
import sys
from pathlib import Path


def main(conf_path: str) -> None:
    conf = json.loads(Path(conf_path).read_text(encoding="utf-8-sig"))
    root = Path(conf_path).resolve().parent.parent
    src, dst = root / conf["entrada"], root / conf["salida"]
    col, minimo = conf["columna_valor"], float(conf["umbral_minimo"])
    kept = dropped = 0
    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(src, newline="", encoding="utf-8-sig") as f, open(dst, "w", newline="", encoding="utf-8") as g:
        rd = csv.DictReader(f)
        if col not in (rd.fieldnames or []):
            raise KeyError(col)
        wr = csv.DictWriter(g, fieldnames=rd.fieldnames)
        wr.writeheader()
        for row in rd:
            try:
                ok = float(row[col]) >= minimo
            except ValueError:
                ok = False
            if ok:
                wr.writerow(row)
                kept += 1
            else:
                dropped += 1
    print(f"limpiar: {kept} filas conservadas, {dropped} descartadas -> {dst}")


if __name__ == "__main__":
    main(sys.argv[1])

"""
Nombre : DetectorRotacion.py
Propósito: Detecta automáticamente la rotación de un modelo de bloques a partir
           de las coordenadas de centroide y las dimensiones de cada bloque,
           sin necesidad de metadatos (bearing/dip/plunge). Soporta modelos
           regulares y sub-bloqueados de Vulcan.
Creacion : 23-Abril-2026
Autor : Eloy Paredes Quispe
Lugar : Buenaventura - Lima
Dependencia: scipy
"""

import numpy as np
from scipy.spatial import cKDTree


class DetectorRotacion:
    """
    Infere la matriz de rotación R (3x3) que mapea coordenadas del frame local
    del modelo de bloques al frame mundo (UTM). El algoritmo:

    1. Para cada par de bloques cercanos, computa el vector de desplazamiento
       entre centroides.
    2. Acepta el par como "alineado con un eje local" si su distancia coincide
       con la distancia esperada para bloques adyacentes que comparten cara
       (= promedio de las dimensiones correspondientes).
    3. Sobre los vectores unitarios resultantes (con simetría antipodal) extrae
       iterativamente 3 modas direccionales (mean-shift con folding).
    4. Ortonormaliza vía Procrustes (SVD) y canonicaliza la asignación de
       columnas para que R sea lo más cercana posible a la identidad.
    5. Si ||R - I||_F < umbral, retorna la identidad (modelo axis-aligned).

    Funciona con modelos sub-bloqueados porque la prueba de "distancia esperada"
    es local al par (usa las dimensiones propias de cada bloque), no un tamaño
    global del modelo.
    """

    def __init__(self, umbral_identidad=0.01, tolerancia_distancia=0.01,
                 min_votos=30, max_muestras=5000, semilla=42,
                 solo_bearing=True, dip_max_grados=10.0):
        self.umbral_id = umbral_identidad
        self.tol_dist = tolerancia_distancia
        self.min_votos = min_votos
        self.max_muestras = max_muestras
        self.semilla = semilla
        self.solo_bearing = solo_bearing
        # Umbral para considerar un voto como "horizontal" en modo solo-bearing.
        # Un voto con |vz| > sin(dip_max) se descarta como posible mezcla vertical.
        self.sen_dip_max = float(np.sin(np.radians(dip_max_grados)))

    def detectar(self, centroides, dimensiones):
        """
        centroides : (N, 3) array de coordenadas de centroide (mundo).
        dimensiones: (N, 3) array de X/Y/Z Length por bloque.

        Retorna (R, info) con:
          R    : matriz 3x3 ortogonal, det=+1, que mapea local → mundo.
          info : dict con status, n_votos y ángulos aproximados.
        """
        centroides = np.asarray(centroides, dtype=np.float64)
        dimensiones = np.asarray(dimensiones, dtype=np.float64)

        vectores, ejes_voto = self._recolectar_vectores(centroides, dimensiones)
        n_votos = len(vectores)

        if n_votos < self.min_votos:
            return np.eye(3), self._info("insuficiente", n_votos, np.eye(3))

        if self.solo_bearing:
            # Caso típico Vulcan: modelos rotan solo en bearing (eje Z vertical).
            # Usamos votos ETIQUETADOS por eje local (0=X, 1=Y) y aplicamos
            # folding 2x por eje separado. Esto preserva la distinción X↔Y
            # que es crítica cuando dim_x != dim_y (sub-bloques no cúbicos).
            R, etiquetado = self._estimar_R_bearing(vectores, ejes_voto)
            # Si el estimador trabajó con votos etiquetados, las columnas de R
            # ya están en su posición correcta (col 0=X local, col 1=Y local).
            # Canonicalizar permutaría columnas y rompería esta correspondencia,
            # así que sólo aseguramos mano derecha (det=+1).
            if etiquetado:
                if np.linalg.det(R) < 0:
                    R[:, -1] = -R[:, -1]
            else:
                # Fallback sin etiquetas (bloques cúbicos) — canonicalizar OK.
                R = self._canonicalizar(R)
        else:
            # Peeling: extrae 3 ejes por mean-shift antipodal (una moda, remove,
            # repite) y ortonormaliza con Procrustes. En la práctica da el mejor
            # theta_total aunque la descomposición bearing/dip puede ser
            # subóptima (se corrige al canonicalizar).
            ejes = self._extraer_ejes(vectores)
            if len(ejes) < 2:
                return np.eye(3), self._info("sin_ejes", n_votos, np.eye(3))
            R = self._construir_rotacion(ejes)
            R = self._canonicalizar(R)

        if np.linalg.norm(R - np.eye(3), ord='fro') < self.umbral_id:
            return np.eye(3), self._info("identidad", n_votos, np.eye(3))

        return R, self._info("rotacion_detectada", n_votos, R)

    def _estimar_R_bearing(self, vectores, ejes_voto):
        """Estima R asumiendo rotación pura en bearing.

        Retorna (R, etiquetado) donde etiquetado=True indica que se usaron
        votos etiquetados X/Y (columnas de R corresponden a X-local y Y-local
        exactamente — no canonicalizar) y etiquetado=False indica que se cayó
        al folding 4x tradicional (bloques cúbicos, columnas intercambiables).

        Con etiquetas, hacemos folding 2x por eje separado: cada voto X local
        tiene ángulo {theta, theta+pi}; al multiplicar por 2 se colapsa a
        2*theta (mod 2pi) sin mezclar con ±Y local.
        """
        # Filtrar horizontales
        vz = np.abs(vectores[:, 2])
        mask_h = vz <= self.sen_dip_max
        horizontales = vectores[mask_h]
        ejes_h = ejes_voto[mask_h]

        if len(horizontales) < 3:
            return self._fallback_peeling(vectores), False

        # Intento 1 — votos etiquetados X (0) y/o Y (1)
        mask_x = ejes_h == 0
        mask_y = ejes_h == 1

        thetas, pesos = [], []
        if int(mask_x.sum()) >= 3:
            thetas.append(self._theta_folding2(horizontales[mask_x]))
            pesos.append(int(mask_x.sum()))
        if int(mask_y.sum()) >= 3:
            # El eje Y local está a 90° del X local: restamos pi/2 y llevamos
            # al rango canonico (-pi/2, pi/2].
            t = self._theta_folding2(horizontales[mask_y]) - np.pi / 2.0
            if t <= -np.pi / 2.0:
                t += np.pi
            thetas.append(t)
            pesos.append(int(mask_y.sum()))

        if thetas:
            # Combinar (promedio circular ponderado con simetría pi, folding 2x)
            thetas = np.asarray(thetas, dtype=np.float64)
            pesos = np.asarray(pesos, dtype=np.float64)
            c2 = np.sum(pesos * np.cos(2.0 * thetas)) / pesos.sum()
            s2 = np.sum(pesos * np.sin(2.0 * thetas)) / pesos.sum()
            theta = float(np.arctan2(s2, c2)) / 2.0
            return self._matriz_Rz(theta), True

        # Intento 2 — sin etiquetas X/Y: folding 4x sobre todos los horizontales
        # (sólo válido bajo simetría cúbica dim_x == dim_y)
        R = self._estimar_R_bearing_4x(horizontales)
        return R, False

    def _theta_folding2(self, votos_horiz):
        """Ángulo promedio de un conjunto de votos co-lineales (mismo eje local),
        con simetría antipodal (folding 2x). Retorna theta en (-pi/2, pi/2]."""
        xy = votos_horiz[:, :2]
        normas = np.linalg.norm(xy, axis=1)
        ok = normas > 1e-9
        xy = xy[ok] / normas[ok, None]
        alfa = np.arctan2(xy[:, 1], xy[:, 0])
        c2 = float(np.mean(np.cos(2.0 * alfa)))
        s2 = float(np.mean(np.sin(2.0 * alfa)))
        return float(np.arctan2(s2, c2)) / 2.0

    def _estimar_R_bearing_4x(self, horizontales):
        """Folding 4x clásico. Correcto sólo para bloques cúbicos (dim_x == dim_y),
        pero útil como fallback cuando no hay votos etiquetados X/Y."""
        xy = horizontales[:, :2]
        normas = np.linalg.norm(xy, axis=1)
        ok = normas > 1e-9
        xy = xy[ok] / normas[ok, None]
        if len(xy) < 3:
            return np.eye(3)
        alfa = np.arctan2(xy[:, 1], xy[:, 0])
        c4 = float(np.mean(np.cos(4.0 * alfa)))
        s4 = float(np.mean(np.sin(4.0 * alfa)))
        theta = float(np.arctan2(s4, c4)) / 4.0
        return self._matriz_Rz(theta)

    @staticmethod
    def _matriz_Rz(theta):
        c, s = float(np.cos(theta)), float(np.sin(theta))
        return np.array([
            [c, -s, 0.0],
            [s,  c, 0.0],
            [0.0, 0.0, 1.0],
        ], dtype=np.float64)

    def _fallback_peeling(self, vectores):
        """Fallback para casos degenerados en solo_bearing: peeling 3D."""
        ejes = self._extraer_ejes(vectores)
        if len(ejes) < 2:
            return np.eye(3)
        return self._construir_rotacion(ejes)

    def _recolectar_vectores(self, centroides, dimensiones):
        """Recolecta votos direccionales etiquetados por eje local.

        Retorna (vectores, ejes) donde ejes[i] es:
          0 → voto alineado con X-local (distancia = (di_x + dj_x)/2)
          1 → voto alineado con Y-local
          2 → voto alineado con Z-local
         -1 → voto con múltiples candidatos horizontales (X/Y indistinguibles,
              típico de bloques cúbicos donde dim_x = dim_y)
        """
        n = len(centroides)
        if n < 2:
            return np.empty((0, 3)), np.empty(0, dtype=int)

        max_dim = float(dimensiones.max())
        if max_dim <= 0.0:
            return np.empty((0, 3)), np.empty(0, dtype=int)

        tree = cKDTree(centroides)

        if n > self.max_muestras:
            rng = np.random.default_rng(self.semilla)
            idx_muestra = rng.choice(n, self.max_muestras, replace=False)
        else:
            idx_muestra = np.arange(n)

        radio = max_dim * 1.1
        tol = self.tol_dist

        vectores = []
        ejes = []
        for i in idx_muestra:
            pi = centroides[i]
            di = dimensiones[i]
            idx_vecinos = tree.query_ball_point(pi, r=radio)
            for j in idx_vecinos:
                if j <= i:
                    continue
                disp = centroides[j] - pi
                dist = float(np.linalg.norm(disp))
                if dist < 1e-9:
                    continue

                esperadas = (di + dimensiones[j]) * 0.5  # (3,)
                matches = [k for k in range(3)
                           if abs(dist - esperadas[k]) <= tol * esperadas[k]]
                if not matches:
                    continue

                u = disp / dist
                if len(matches) == 1:
                    k = matches[0]
                else:
                    # Desempate por dirección del voto.
                    # Vertical (|u_z| > 0.7) → asumimos Z-local.
                    # Horizontal con sólo {X} o sólo {Y} → ese.
                    # Horizontal con {X, Y} ambos candidatos → ambiguo (-1).
                    if abs(u[2]) > 0.7 and 2 in matches:
                        k = 2
                    else:
                        horiz = [m for m in matches if m != 2]
                        if len(horiz) == 1:
                            k = horiz[0]
                        elif len(horiz) == 2:
                            k = -1  # ambiguo X/Y (bloque con dim_x == dim_y)
                        else:
                            k = matches[0]

                vectores.append(u)
                ejes.append(k)

        if not vectores:
            return np.empty((0, 3)), np.empty(0, dtype=int)
        return np.asarray(vectores), np.asarray(ejes, dtype=int)

    def _extraer_ejes(self, vectores):
        restantes = vectores.copy()
        ejes = []
        for _ in range(3):
            if len(restantes) < 3:
                break
            eje, idx_alineados = self._moda_direccional(restantes)
            if eje is None:
                break
            ejes.append(eje)
            mantener = np.ones(len(restantes), dtype=bool)
            mantener[idx_alineados] = False
            restantes = restantes[mantener]
        return ejes

    def _moda_direccional(self, vectores, iteraciones=30, cos_tol_grados=30.0):
        """Mean-shift antipodal: encuentra el vector unitario hacia el que
        convergen (por mayoría) los datos, tratando v y -v como equivalentes."""
        if len(vectores) == 0:
            return None, np.array([], dtype=int)

        # Referencia inicial: el vector con mayor concentración local.
        # Heurística barata: probar el primer vector (los siguientes iteraciones
        # lo refinan por mean-shift).
        ref = vectores[0].copy()

        for _ in range(iteraciones):
            dots = vectores @ ref
            signs = np.where(dots >= 0, 1.0, -1.0)
            folded = vectores * signs[:, None]
            nueva = folded.mean(axis=0)
            norma = float(np.linalg.norm(nueva))
            if norma < 1e-12:
                return None, np.array([], dtype=int)
            nueva /= norma
            if abs(float(nueva @ ref)) > 1.0 - 1e-9:
                ref = nueva
                break
            ref = nueva

        cos_sim = np.abs(vectores @ ref)
        umbral_cos = np.cos(np.radians(cos_tol_grados))
        alineados = np.flatnonzero(cos_sim > umbral_cos)
        return ref, alineados

    def _construir_rotacion(self, ejes):
        """Dados 2 o 3 vectores aproximadamente ortogonales, retorna una matriz
        3x3 ortonormal con det=+1 (vía Procrustes/SVD). Si solo hay 2, completa
        el tercero con producto cruz."""
        ejes = [np.asarray(e, dtype=np.float64) for e in ejes]

        if len(ejes) == 2:
            tercero = np.cross(ejes[0], ejes[1])
            n = float(np.linalg.norm(tercero))
            if n < 1e-9:
                # Ejes paralelos: no hay rotación fiable
                return np.eye(3)
            ejes.append(tercero / n)

        M = np.column_stack(ejes)
        U, _, Vt = np.linalg.svd(M)
        R = U @ Vt

        if np.linalg.det(R) < 0:
            R[:, -1] = -R[:, -1]

        return R

    def _canonicalizar(self, R):
        """Reordena columnas de R y ajusta signos para que:
           - la columna k quede asociada al eje mundo k (asignación greedy)
           - la diagonal sea positiva (R[k,k] > 0)
           - el sistema sea mano derecha (det = +1)
        Así un modelo axis-aligned produce R = I sin ambigüedad."""
        abs_R = np.abs(R)

        usados = set()
        asignacion = [0, 0, 0]
        for mundo in range(3):
            mejor_col, mejor_val = -1, -1.0
            for col in range(3):
                if col in usados:
                    continue
                if abs_R[mundo, col] > mejor_val:
                    mejor_val = float(abs_R[mundo, col])
                    mejor_col = col
            asignacion[mundo] = mejor_col
            usados.add(mejor_col)

        R_reord = R[:, asignacion]

        for k in range(3):
            if R_reord[k, k] < 0:
                R_reord[:, k] = -R_reord[:, k]

        if np.linalg.det(R_reord) < 0:
            R_reord[:, 2] = -R_reord[:, 2]

        return R_reord

    def _info(self, status, n_votos, R):
        angulos = self._angulos_aproximados(R)
        return {
            "status": status,
            "n_votos": int(n_votos),
            "R": R,
            "bearing": angulos["bearing"],
            "dip": angulos["dip"],
            "theta_total": angulos["theta_total"],
        }

    @staticmethod
    def _angulos_aproximados(R):
        """Ángulos referenciales en grados: bearing (rotación del eje Y local
        respecto al norte, sentido horario mirando desde arriba), dip (inclinación
        de Y local respecto al plano horizontal), y theta_total (ángulo total
        de rotación de R respecto a la identidad)."""
        cos_theta = (np.trace(R) - 1.0) * 0.5
        cos_theta = float(np.clip(cos_theta, -1.0, 1.0))
        theta_total = float(np.degrees(np.arccos(cos_theta)))

        y_local_mundo = R[:, 1]
        bearing = float(np.degrees(np.arctan2(y_local_mundo[0], y_local_mundo[1])))
        if bearing < 0:
            bearing += 360.0

        dip = float(np.degrees(np.arcsin(-float(np.clip(y_local_mundo[2], -1.0, 1.0)))))

        return {"bearing": bearing, "dip": dip, "theta_total": theta_total}

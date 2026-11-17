"""
Nombre : DesurveyTangenteVulcan.py
Propósito: Motor de desurvey que replica la corrida de desurvey de Maptek Vulcan. El método se
           dedujo por ingeniería inversa comparando contra una corrida real de Vulcan
           (E:\\Borrar\\val_desurvey): NO es el método tangente puro (que se desvía hasta ~0.8 m),
           sino CURVATURA MÍNIMA (minimum curvature). Entre cada par de estaciones de survey el
           sondaje sigue un arco circular; los puntos intermedios (Desde/Hasta/centro de cada
           tramo de assay) se colocan exactamente sobre ese arco.

           Validación: reproduce top/mid/bot de desurvey_vulcan.csv con mediana 0.4 mm y media
           0.6 mm sobre 3 sondajes / 430 intervalos (a nivel del redondeo a 3 decimales del
           archivo de Vulcan).
Creacion : 08-Julio-2026
Autor : Eloy Paredes Quispe
Lugar : Buenaventura - Lima
Modificado : 09-Julio-2026 - Forma producto estable del arco: elimina la cancelación
             catastrófica (posiciones cuantizadas en saltos de metros) cuando dos
             estaciones consecutivas tienen ángulos idénticos (β ~ 1e-8).
"""

import numpy as np

from Herramientas.MotorDesurvey import MotorDesurvey

_EPS = 1e-10


class DesurveyTangenteVulcan(MotorDesurvey):
    """
    Desurvey por curvatura mínima, replicando el desurvey de Maptek Vulcan.

    Convención de signos (igual que en los datos de Vulcan): dip negativo apunta hacia abajo
    (la cota disminuye), dip positivo hacia arriba. La dirección unitaria de cada estación es

        u = (cos(dip)·sin(az), cos(dip)·cos(az), sin(dip)).

    Entre dos estaciones consecutivas, el desplazamiento por curvatura mínima es
    (L/2)·(u0 + u1)·FR, con el factor de razón FR = (2/β)·tan(β/2) y β el ángulo de dogleg
    (β = arccos(u0·u1)). Un punto a una profundidad intermedia δ dentro del tramo se ubica sobre
    el arco:

        P(δ) = P0 + (L/(β·sen β))·[ u0·(cos(β−φ) − cos β) + u1·(1 − cos φ) ],  φ = δ·β/L.

    Fuera del rango del survey (antes de la primera estación o después de la última) se extiende
    en línea recta con la dirección de la estación extrema (extensión tangente), igual que Vulcan.
    """

    def _integrar_trayectoria(self, trace, survey, start_x, start_y, start_z):
        depths = trace['Profundidad'].to_numpy(float)
        sd = survey['Profundidad'].to_numpy(float)
        saz = np.radians(survey['Azimut'].to_numpy(float))
        sdip = np.radians(survey['Dip'].to_numpy(float))
        # Dirección unitaria en cada estación de survey.
        su = np.column_stack([np.cos(sdip) * np.sin(saz),
                              np.cos(sdip) * np.cos(saz),
                              np.sin(sdip)])
        n = len(sd)

        # Caso degenerado: una sola estación -> extensión tangente recta desde el collar.
        if n == 1:
            return np.column_stack([start_x + (depths - sd[0]) * su[0, 0],
                                    start_y + (depths - sd[0]) * su[0, 1],
                                    start_z + (depths - sd[0]) * su[0, 2]])

        # Posición de cada estación por curvatura mínima (acumulada desde el collar).
        Lseg = np.diff(sd)
        dot_seg = np.clip(np.sum(su[:-1] * su[1:], axis=1), -1.0, 1.0)
        beta_seg = np.arccos(dot_seg)
        rf = np.where(beta_seg < _EPS, 1.0, (2.0 / np.where(beta_seg < _EPS, 1.0, beta_seg)) * np.tan(beta_seg / 2.0))
        deltas = (Lseg / 2.0)[:, None] * (su[:-1] + su[1:]) * rf[:, None]
        spos = np.empty((n, 3))
        spos[0] = [start_x, start_y, start_z]
        spos[1:] = spos[0] + np.cumsum(deltas, axis=0)

        # Ubicar cada profundidad de la traza sobre el arco del tramo que la contiene.
        k = np.clip(np.searchsorted(sd, depths, side='right') - 1, 0, n - 2)
        L = sd[k + 1] - sd[k]
        delta = depths - sd[k]
        u0 = su[k]
        u1 = su[k + 1]
        beta = beta_seg[k]
        sinb = np.sin(beta)
        phi = np.where(L > 0.0, delta * beta / L, 0.0)

        recto = beta < _EPS
        # Rama con curvatura (arco); se evita división por cero donde beta≈0.
        beta_safe = np.where(recto, 1.0, beta)
        sinb_safe = np.where(recto, 1.0, sinb)
        # Forma producto de la expresión del arco, algebraicamente equivalente a
        #   coef·[u0·(cos(β−φ) − cos β) + u1·(1 − cos φ)]
        # usando las identidades de diferencia de cosenos:
        #   cos(β−φ) − cos β = 2·sen(β−φ/2)·sen(φ/2)   y   1 − cos φ = 2·sen²(φ/2).
        # La forma con restas de cosenos sufre cancelación catastrófica cuando β→0
        # (estaciones consecutivas con ángulos idénticos: β ~ 1e-8 por redondeo del
        # producto punto) y cuantizaba las posiciones intermedias en saltos de metros;
        # la forma producto es estable para todo β >= _EPS.
        sen_half_phi = np.sin(0.5 * phi)
        coef = 2.0 * L * sen_half_phi / (beta_safe * sinb_safe)
        arco = spos[k] + coef[:, None] * (u0 * np.sin(beta - 0.5 * phi)[:, None]
                                          + u1 * sen_half_phi[:, None])
        # Rama recta (dogleg ~ 0): interpolación lineal a lo largo de la dirección del tramo.
        lineal = spos[k] + delta[:, None] * u0
        pos = np.where(recto[:, None], lineal, arco)

        # Extensión tangente fuera del rango del survey.
        antes = depths < sd[0]
        despues = depths > sd[-1]
        if antes.any():
            pos[antes] = spos[0] + (depths[antes] - sd[0])[:, None] * su[0]
        if despues.any():
            pos[despues] = spos[-1] + (depths[despues] - sd[-1])[:, None] * su[-1]

        return pos

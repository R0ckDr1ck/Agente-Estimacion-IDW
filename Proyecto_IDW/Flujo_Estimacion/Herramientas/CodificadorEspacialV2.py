"""
Nombre : CodificadorEspacialV2.py
Propósito: Versión optimizada de CodificadorEspacial usando Fast Winding Number (libigl)
           Reemplaza ray-casting por Fast Winding Number: O(n·log m) vs O(n·m),
           ~10-100x más rápido, sin necesidad de batching, tolerante a mallas abiertas.
Creacion : 09-Abril-2026
Autor : Eloy Paredes Quispe
Lugar : Buenaventura - Lima
Dependencia: pip install libigl
"""

import time
from pathlib import Path

import numpy as np
import polars as pl
import trimesh
import igl
import manifold3d as m3d

from Util.DetectorRotacion import DetectorRotacion


class CodificadorEspacialV2:
    def __init__(self, df, solidos, coordenadas, dimensiones=None):
        self.df = df
        self.solidos = solidos
        self.x, self.y, self.z = coordenadas
        self.dx, self.dy, self.dz = dimensiones if dimensiones else (None, None, None)

    @staticmethod
    def load_solid(obj_path: Path) -> trimesh.Trimesh:
        """Carga un .OBJ, lo repara si es necesario y devuelve un Trimesh."""
        print(f"Cargando sólido: {obj_path.name}")
        loaded = trimesh.load(str(obj_path), force="mesh", process=True)

        if isinstance(loaded, trimesh.Scene):
            meshes = [g for g in loaded.geometry.values() if isinstance(g, trimesh.Trimesh)]
            loaded = trimesh.util.concatenate(meshes)

        mesh: trimesh.Trimesh = loaded
        mesh.fix_normals()

        if not mesh.is_watertight:
            print(f"  ⚠ '{obj_path.name}' NO es estanco (watertight). Intentando reparar…")
            trimesh.repair.fill_holes(mesh)
            trimesh.repair.fix_winding(mesh)
            trimesh.repair.fix_normals(mesh)
            if mesh.is_watertight:
                print("  ✓ Reparado exitosamente.")
            else:
                print("  ✗ No se pudo reparar (Fast Winding Number tolera mallas abiertas).")

        print(f"  Vértices: {mesh.vertices.shape[0]:,} | Triángulos: {mesh.faces.shape[0]:,}")
        return mesh

    @staticmethod
    def aabb_filter(points: np.ndarray, mesh: trimesh.Trimesh, tol: float,
                    half_dims: np.ndarray = None) -> np.ndarray:
        """Filtro AABB optimizado eje por eje para minimizar memoria temporal.

        Si se proporciona half_dims (N,3), cada punto se considera como un AABB
        centrado en el punto con extensión ±half_dims, y pasa el filtro si su AABB
        intersecta la AABB del mesh (expandida por tol). Esto es necesario en modo
        proporcional para no descartar bloques fronterizos cuyo centroide cae fuera
        del sólido pero parte de su volumen está dentro.
        """
        lo = mesh.bounds[0] - tol
        hi = mesh.bounds[1] + tol
        if half_dims is None:
            mask = (points[:, 0] >= lo[0]) & (points[:, 0] <= hi[0])
            mask &= (points[:, 1] >= lo[1]) & (points[:, 1] <= hi[1])
            mask &= (points[:, 2] >= lo[2]) & (points[:, 2] <= hi[2])
        else:
            mask = (points[:, 0] + half_dims[:, 0] >= lo[0]) & (points[:, 0] - half_dims[:, 0] <= hi[0])
            mask &= (points[:, 1] + half_dims[:, 1] >= lo[1]) & (points[:, 1] - half_dims[:, 1] <= hi[1])
            mask &= (points[:, 2] + half_dims[:, 2] >= lo[2]) & (points[:, 2] - half_dims[:, 2] <= hi[2])
        return mask

    def codificar(self, solid_col="solido", prop_col="proporcion", aabb_tolerance=0.01, winding_threshold=0.5,
                  sub_blocks=3, tolerancia_pared=0.0, modo="centroide"):
        if modo not in ("centroide", "proporcional", "exacto"):
            raise ValueError(f"modo debe ser 'centroide', 'proporcional' o 'exacto' (recibido: {modo!r})")

        print("=" * 70)
        print(f"  Modo de codificación: {modo}")
        t0 = time.perf_counter()
        df = self.df
        print(f"  {df.height:,} filas × {df.width} columnas [{time.perf_counter() - t0:.2f}s]")

        points = df.select(self.x, self.y, self.z).to_numpy().astype(np.float64)
        t_total = time.perf_counter()

        has_dims = self.dx and self.dy and self.dz
        necesita_dims = modo in ("proporcional", "exacto")

        if necesita_dims and not has_dims:
            raise ValueError(f"El modo '{modo}' requiere dimensiones (dx, dy, dz) en el dataframe.")

        # Rotación del modelo (local → mundo). Por defecto identidad.
        # Solo la detectamos cuando el modo usa la geometría de los bloques
        # (proporcional / exacto); en modo centroide la orientación de la caja
        # es irrelevante porque solo se evalúa el punto del centroide.
        R = np.eye(3)
        R_is_identity = True
        world_half_dims_all = None

        if necesita_dims:
            dims_all = df.select(self.dx, self.dy, self.dz).to_numpy().astype(np.float64)
            half_dims_all = dims_all / 2.0

            # Autodetección de rotación a partir de la disposición de centroides.
            # Funciona en modelos sub-bloqueados porque compara distancias
            # esperadas por par (usa las dimensiones propias de cada pareja).
            t_rot = time.perf_counter()
            detector = DetectorRotacion()
            R, info_rot = detector.detectar(points, dims_all)
            R_is_identity = (info_rot["status"] in ("identidad", "insuficiente",
                                                     "sin_rotacion_detectable"))
            if R_is_identity:
                print(f"  Rotación: modelo axis-aligned [{info_rot['status']}, "
                      f"n_votos={info_rot['n_votos']}] "
                      f"[{time.perf_counter() - t_rot:.2f}s]")
            else:
                print(f"  Rotación detectada: bearing={info_rot['bearing']:.2f}° | "
                      f"dip={info_rot['dip']:.2f}° | θ_total={info_rot['theta_total']:.2f}° "
                      f"(n_votos={info_rot['n_votos']}) [{time.perf_counter() - t_rot:.2f}s]")

            # AABB mundo de cada bloque (orientado): para un OBB con semiejes
            # h en el frame local, los semiejes de su AABB mundo son |R| @ h.
            if R_is_identity:
                world_half_dims_all = half_dims_all
            else:
                abs_R = np.abs(R)
                world_half_dims_all = half_dims_all @ abs_R.T

        if modo == "proporcional":
            step = 1.0 / sub_blocks
            steps = np.linspace(-0.5 + step / 2, 0.5 - step / 2, sub_blocks)
            grid_x, grid_y, grid_z = np.meshgrid(steps, steps, steps, indexing='ij')
            unit_grid = np.column_stack([grid_x.ravel(), grid_y.ravel(), grid_z.ravel()])

        resultados_solidos = []

        # Candado global: solo aplica al modo centroide (asignación exclusiva).
        # Proporcional y exacto permiten que un bloque aporte a varios sólidos
        # con su fracción volumétrica correspondiente.
        bloques_asignados = np.zeros(df.height, dtype=bool)

        for path_str in self.solidos:
            obj_path = Path(path_str)
            solid_name = obj_path.stem
            print("-" * 50)

            mesh = self.load_solid(obj_path)
            V = np.ascontiguousarray(mesh.vertices, dtype=np.float64)
            F = np.ascontiguousarray(mesh.faces, dtype=np.int64)

            t_aabb = time.perf_counter()
            if necesita_dims:
                aabb_mask = self.aabb_filter(points, mesh, aabb_tolerance, half_dims=world_half_dims_all)
            else:
                aabb_mask = self.aabb_filter(points, mesh, aabb_tolerance)
            idx_cand = np.flatnonzero(aabb_mask)
            n_cand = idx_cand.shape[0]
            print(f"  AABB: {n_cand:,} / {points.shape[0]:,} candidatos [{time.perf_counter() - t_aabb:.2f}s]")

            if n_cand == 0:
                continue

            t_fwn = time.perf_counter()

            if modo == "proporcional":
                # --- MODO PROPORCIONAL ---
                centers = points[idx_cand]
                dims_cand = dims_all[idx_cand]

                # Offsets de sub-bloque en frame local del bloque.
                sub_offsets_local = dims_cand[:, None, :] * unit_grid[None, :, :]
                if R_is_identity:
                    sub_points = centers[:, None, :] + sub_offsets_local
                else:
                    # Rotar cada offset local al frame mundo: p_mundo = R · p_local.
                    # Con vectores-fila: offset_mundo = offset_local @ R.T.
                    sub_offsets_world = sub_offsets_local @ R.T
                    sub_points = centers[:, None, :] + sub_offsets_world
                sub_points_flat = sub_points.reshape(-1, 3)

                winding = igl.fast_winding_number(V, F, sub_points_flat)
                inside_flat = winding > winding_threshold

                inside_grid = inside_flat.reshape(n_cand, sub_blocks ** 3)
                proportions = inside_grid.mean(axis=1)

                inside_cand_mask = proportions > 0
                final_proportions = proportions[inside_cand_mask]
                tipo_calc = f"Proporcional ({sub_blocks}³)"

            elif modo == "exacto":
                # --- MODO EXACTO (intersección booleana de mallas) ---
                # Estrategia híbrida para minimizar costo:
                #  1) Clasificar cada candidato con FWN + distancia al mesh.
                #  2) Interior puro (winding dentro AND distancia > semi-diagonal): proporción = 1.0.
                #  3) Exterior puro (winding fuera AND distancia > semi-diagonal): descartar.
                #  4) Frontera (distancia ≤ semi-diagonal): booleano exacto con manifold3d.
                # Solo los bloques frontera pagan el costo del booleano.
                centers = points[idx_cand]
                dims_cand = dims_all[idx_cand]
                vols_cand = dims_cand.prod(axis=1)
                half_diag = 0.5 * np.linalg.norm(dims_cand, axis=1)

                winding = igl.fast_winding_number(V, F, centers)
                inside_centroid = winding > winding_threshold

                sq_d, _, _ = igl.point_mesh_squared_distance(centers, V, F)
                dist_to_mesh = np.sqrt(sq_d)

                is_far = dist_to_mesh > half_diag
                pure_interior = inside_centroid & is_far
                boundary_mask = ~is_far

                n_int = int(pure_interior.sum())
                n_bnd = int(boundary_mask.sum())
                n_ext = n_cand - n_int - n_bnd
                print(f"  Clasificación: {n_int:,} interior puro | {n_bnd:,} frontera | {n_ext:,} exterior puro")

                # Manifold del sólido: se construye una sola vez por sólido.
                # Usamos Mesh64 (float64) porque en coordenadas UTM grandes (p.ej. Norte ~8.5M)
                # float32 tiene precisión ≥ 0.5 m por vértice, lo que desplaza el mesh y
                # produce errores acumulados del orden del 1-2% en el tonelaje vs Vulcan.
                # Además, recentramos los vértices al origen del bounding box: la tolerancia
                # interna de manifold3d escala con la magnitud de las coordenadas y en UTM
                # produce "slivers" espurios (fracciones ~1e-3) en bloques que no intersectan.
                mesh_center = (mesh.bounds[0] + mesh.bounds[1]) * 0.5
                V_local = V - mesh_center
                try:
                    mesh_m3d = m3d.Mesh64(
                        vert_properties=V_local,
                        tri_verts=F.astype(np.uint32),
                    )
                    solid_mf = m3d.Manifold(mesh_m3d)
                except Exception as e:
                    raise RuntimeError(
                        f"No se pudo construir el Manifold del sólido '{solid_name}'. "
                        f"Verificar que el mesh sea válido/watertight. Detalle: {e}"
                    )

                _op_inter = m3d.OpType.Intersect

                proportions = np.zeros(n_cand, dtype=np.float64)
                proportions[pure_interior] = 1.0

                # Matriz afín 3x4 para rotar el cubo al frame del modelo de bloques.
                # Si R es identidad, omitimos el transform para ahorrar overhead.
                if not R_is_identity:
                    R_affine = np.zeros((3, 4), dtype=np.float64)
                    R_affine[:, :3] = R
                    R_affine_list = R_affine.tolist()

                # Booleano exacto bloque-a-bloque solo sobre bloques frontera.
                boundary_idx_local = np.flatnonzero(boundary_mask)
                t_bool = time.perf_counter()
                report_step = max(1, len(boundary_idx_local) // 10)
                for i, bi in enumerate(boundary_idx_local):
                    cx, cy, cz = centers[bi]
                    dx, dy, dz = dims_cand[bi]
                    vol_bloque = float(vols_cand[bi])
                    if vol_bloque <= 0.0:
                        continue
                    cubo = m3d.Manifold.cube(
                        [float(dx), float(dy), float(dz)], True
                    )
                    if not R_is_identity:
                        cubo = cubo.transform(R_affine_list)
                    cubo = cubo.translate([float(cx - mesh_center[0]),
                                           float(cy - mesh_center[1]),
                                           float(cz - mesh_center[2])])
                    inter = m3d.Manifold.batch_boolean([solid_mf, cubo], _op_inter)
                    proportions[bi] = inter.volume() / vol_bloque

                    if (i + 1) % report_step == 0 and len(boundary_idx_local) >= 100:
                        print(f"    Booleano: {i + 1:,}/{len(boundary_idx_local):,} "
                              f"[{time.perf_counter() - t_bool:.2f}s]")

                # Fracciones < 0.1 % son ruido numérico del booleano (bloques que
                # no intersectan pero producen "slivers" espurios). Vulcan las ignora.
                proportions[proportions < 1e-3] = 0.0
                # Clamp por seguridad numérica del booleano.
                proportions = np.clip(proportions, 0.0, 1.0)
                inside_cand_mask = proportions > 0
                final_proportions = proportions[inside_cand_mask]
                tipo_calc = "Exacto (booleano)"

            else:
                # --- MODO CENTROIDE ---
                Q = np.ascontiguousarray(points[idx_cand], dtype=np.float64)
                winding = igl.fast_winding_number(V, F, Q)

                # Identificamos los que caen dentro (incluye el caso "sobre la pared":
                # para un punto exactamente en la superficie, FWN ≈ 0.5 por lo que
                # usamos >= para no perderlo por ruido numérico).
                raw_inside = winding >= winding_threshold

                # Rescate de centroides sobre/muy cerca de la pared del sólido.
                # FWN tiene ruido numérico en la banda ~[0.5-eps, 0.5+eps] alrededor
                # de la superficie. Si tolerancia_pared > 0, incluimos los centroides
                # que el winding marcó como "fuera" pero están dentro de esa banda
                # euclidiana respecto al mesh. Vulcan hace intersección exacta, así
                # que este parámetro replica ese comportamiento.
                if tolerancia_pared > 0:
                    outside_mask = ~raw_inside
                    if outside_mask.any():
                        sq_d, _, _ = igl.point_mesh_squared_distance(
                            Q[outside_mask], V, F
                        )
                        near_wall = sq_d <= tolerancia_pared ** 2
                        if near_wall.any():
                            outside_idx = np.flatnonzero(outside_mask)
                            raw_inside[outside_idx[near_wall]] = True
                            print(f"  ⓘ {int(near_wall.sum()):,} centroides rescatados por cercanía a la pared "
                                  f"(≤ {tolerancia_pared} m).")

                # Filtramos los que YA fueron asignados a otro sólido anteriormente
                global_inside_idx = idx_cand[raw_inside]
                unassigned = ~bloques_asignados[global_inside_idx]

                # Creamos la máscara final respetando el candado
                inside_cand_mask = np.zeros(len(idx_cand), dtype=bool)
                inside_cand_mask[raw_inside] = unassigned

                final_proportions = np.ones(inside_cand_mask.sum())

                # Bloqueamos estos índices para los siguientes sólidos
                idx_nuevos_asignados = idx_cand[inside_cand_mask]
                bloques_asignados[idx_nuevos_asignados] = True

                tipo_calc = "Centroide"

                # Avisar si hubo colisiones salvadas por el candado
                n_colisiones = raw_inside.sum() - inside_cand_mask.sum()
                if n_colisiones > 0:
                    print(
                        f"  ⚠ {n_colisiones:,} bloques ignorados por solapamiento (ya asignados a un sólido anterior).")

            # 3. Extraer y guardar
            inside_idx = idx_cand[inside_cand_mask]
            n_new = len(inside_idx)
            print(f"  Modo {tipo_calc}: {n_new:,} bloques intersectan [{time.perf_counter() - t_fwn:.2f}s]")

            if n_new > 0:
                df_interseccion = df[inside_idx].clone()
                df_interseccion = df_interseccion.with_columns([
                    pl.lit(solid_name).alias(solid_col),
                    pl.Series(name=prop_col, values=final_proportions)
                ])
                resultados_solidos.append(df_interseccion)

        print("=" * 70)

        if resultados_solidos:
            df_final = pl.concat(resultados_solidos)
        else:
            df_final = df.with_columns([
                pl.lit("").alias(solid_col),
                pl.lit(0.0).alias(prop_col)
            ]).filter(False)

        print(f"Clasificación total completada en: {time.perf_counter() - t_total:.2f}s")
        print(f"Resumen Final: {df_final.height:,} registros de intersección generados.")

        return df_final

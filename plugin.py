# -*- coding: utf-8 -*-
"""
Consulta SIRI
=============
Boton de activar/desactivar en la barra de herramientas de QGIS. Con la
herramienta activa, cada clic sobre el mapa consulta (GetFeatureInfo) las
capas WMS del SIRI (Sistema de Informacion del Registro Inmobiliario, Costa
Rica, siri.snitcr.go.cr) que esten cargadas en el proyecto, y guarda el
predio encontrado en una capa temporal de memoria.

Dos cuidados que motivaron este plugin:

1. Proyeccion: las capas WMS del SIRI (tipicamente cargadas como "Zona 1" /
   "Zona 2", catastro / catastro_aldia) negocian en EPSG:4326 aunque el
   proyecto este en otro CRS (p.ej. EPSG:8908, CRTM05). Hay que transformar
   el punto de clic HACIA 4326 antes de preguntarle al servidor, y volver a
   transformar la geometria de la respuesta DESDE 4326 hacia el CRS del
   proyecto antes de guardarla -si se omite este segundo paso, la geometria
   queda en grados dentro de una capa declarada en metros, y cada predio
   guardado aparece en un punto sin relacion con su ubicacion real.

2. Campos: el cuadro de dialogo en pantalla muestra nada mas un resumen
   (plano, finca, identifica, ubicacion, area), pero la capa guardada
   conserva TODOS los campos que devuelve el servidor, integros -mas cuatro
   campos propios de procedencia (capa de origen, fecha de consulta, y las
   coordenadas del clic).

Autor: Carlo Soto Castro
"""
from datetime import datetime

from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QMessageBox
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsRasterLayer, QgsFeature, QgsField,
    QgsRaster, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
)
from qgis.gui import QgsMapToolEmitPoint

import os

CRS_WMS = QgsCoordinateReferenceSystem("EPSG:4326")  # como negocian las capas del SIRI
NOMBRE_CAPA_RESULTADOS = "Consultas SIRI (temporal)"
FRAGMENTO_FUENTE_SIRI = "siri.snitcr.go.cr"  # para detectar las capas WMS del SIRI en el proyecto, sin depender de como el usuario las haya nombrado


def _campos_propios():
    """Los 4 campos de procedencia que este plugin agrega a cada predio
    guardado, ademas de todos los que trae el servidor. Definidos en un solo
    lugar y reutilizados tanto al crear la capa como al completar una capa
    reutilizada a la que le falten -para que nunca queden a medias."""
    return [
        QgsField("_capa_origen", QVariant.String),
        QgsField("_fecha_consulta", QVariant.String),
        QgsField("_x_clic", QVariant.Double),
        QgsField("_y_clic", QVariant.Double),
    ]


class ConsultaCatastroSiriTool(QgsMapToolEmitPoint):
    """Herramienta de mapa: clic -> GetFeatureInfo en las capas WMS del SIRI."""

    def __init__(self, canvas, obtener_capas_wms, obtener_capa_resultados):
        super().__init__(canvas)
        self.canvas = canvas
        self.obtener_capas_wms = obtener_capas_wms
        self.obtener_capa_resultados = obtener_capa_resultados

    def canvasReleaseEvent(self, event):
        capas_wms = self.obtener_capas_wms()
        if not capas_wms:
            QMessageBox.warning(
                None, "Consulta SIRI",
                "No hay ninguna capa WMS del SIRI cargada en el proyecto\n"
                f"(se buscan capas WMS cuyo origen contenga «{FRAGMENTO_FUENTE_SIRI}»).")
            return

        punto_proyecto = self.toMapCoordinates(event.pos())
        crs_proyecto = self.canvas.mapSettings().destinationCrs()
        a_wms = QgsCoordinateTransform(crs_proyecto, CRS_WMS, QgsProject.instance())
        de_wms = QgsCoordinateTransform(CRS_WMS, crs_proyecto, QgsProject.instance())

        punto_wms = a_wms.transform(punto_proyecto)
        extent_wms = a_wms.transformBoundingBox(self.canvas.extent())
        size = self.canvas.mapSettings().outputSize()

        for capa in capas_wms:
            try:
                resultado = capa.dataProvider().identify(
                    punto_wms, QgsRaster.IdentifyFormatFeature, extent_wms,
                    size.width(), size.height())
            except Exception as e:
                print(f"[Consulta SIRI] error al consultar «{capa.name()}»: {e}")
                continue

            if not resultado.isValid():
                continue

            for _, tiendas in resultado.results().items():
                if not isinstance(tiendas, list):
                    continue
                for tienda in tiendas:
                    for feat_origen in tienda.features():
                        if not feat_origen.hasGeometry():
                            continue
                        self._guardar(feat_origen, capa.name(), de_wms, punto_proyecto)
                        self._mostrar(feat_origen, capa.name())
                        return

        QMessageBox.information(
            None, "Consulta SIRI",
            "No hay predio catastrado en ese punto (o cae fuera de zona catastrada).")

    def _guardar(self, feat_origen, nombre_capa, de_wms, punto_proyecto):
        geom = feat_origen.geometry()
        geom.transform(de_wms)          # EPSG:4326 (como responde el WMS) -> CRS del proyecto
        geom.convertToMultiType()       # por si el servidor devuelve Polygon simple

        capa_resultados = self.obtener_capa_resultados(feat_origen.fields())

        # Si esta respuesta trae campos que la capa aun no tiene -p.ej. Zona 2
        # devuelve algo que Zona 1 no trae, o se esta reutilizando una capa
        # "Consultas SIRI (temporal)" de una sesion anterior con otro
        # esquema- se agregan sobre la marcha. Se revisan tanto los campos
        # del servidor como los 4 propios: nunca se descarta un campo del
        # servidor, y nunca falta un campo de procedencia por reusar una
        # capa vieja.
        existentes = {f.name() for f in capa_resultados.fields()}
        requeridos = list(feat_origen.fields()) + _campos_propios()
        faltantes = [QgsField(f) for f in requeridos if f.name() not in existentes]
        if faltantes:
            capa_resultados.dataProvider().addAttributes(faltantes)
            capa_resultados.updateFields()

        nueva = QgsFeature(capa_resultados.fields())
        nueva.setGeometry(geom)
        # Todos los campos que devolvio el servidor, integros -sin filtrar
        # ni resumir; lo que se resume es unicamente el cuadro de dialogo.
        for campo in feat_origen.fields():
            nueva.setAttribute(campo.name(), feat_origen.attribute(campo.name()))
        # Metadatos propios de la consulta (prefijo "_" para no chocar con
        # nombres de campo del SIRI).
        nueva.setAttribute("_capa_origen", nombre_capa)
        nueva.setAttribute("_fecha_consulta", datetime.now().isoformat(timespec="seconds"))
        nueva.setAttribute("_x_clic", punto_proyecto.x())
        nueva.setAttribute("_y_clic", punto_proyecto.y())

        capa_resultados.dataProvider().addFeature(nueva)
        capa_resultados.updateExtents()
        capa_resultados.triggerRepaint()

    def _mostrar(self, feat_origen, nombre_capa):
        def val(campo):
            idx = feat_origen.fieldNameIndex(campo)
            return feat_origen.attribute(idx) if idx >= 0 else "—"
        texto = (
            f"Capa: {nombre_capa}\n"
            f"Plano: {val('plano')}\n"
            f"Finca: {val('finca')}\n"
            f"Identifica: {val('identifica')}\n"
            f"Provincia/Cantón/Distrito: {val('provincia')}/{val('canton')}/{val('distrito')}\n"
            f"Área (shape_area): {val('shape_area')}\n\n"
            f"({len(feat_origen.fields())} campos guardados íntegros en "
            f"«{NOMBRE_CAPA_RESULTADOS}» — este cuadro es solo un resumen.)")
        print(texto)
        QMessageBox.information(None, "Predio encontrado (SIRI)", texto)


class ConsultaSiriPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self.accion = None
        self.herramienta = None
        self.herramienta_anterior = None

    # --- ciclo de vida del plugin ---------------------------------------
    def initGui(self):
        icono = os.path.join(os.path.dirname(__file__), "icon.png")
        self.accion = QAction(
            QIcon(icono) if os.path.exists(icono) else QIcon(),
            "Consulta SIRI", self.iface.mainWindow())
        self.accion.setCheckable(True)
        self.accion.setToolTip(
            "Consulta SIRI: clic en el mapa consulta el catastro (WMS) y guarda el "
            "predio -con todos sus campos- en una capa temporal.")
        self.accion.toggled.connect(self._alternar)

        self.iface.addToolBarIcon(self.accion)
        self.iface.addPluginToMenu("&Consulta SIRI", self.accion)

        self.herramienta = ConsultaCatastroSiriTool(
            self.canvas, self._capas_wms_del_proyecto, self._obtener_capa_resultados)

        self.canvas.mapToolSet.connect(self._al_cambiar_herramienta)

    def unload(self):
        try:
            self.canvas.mapToolSet.disconnect(self._al_cambiar_herramienta)
        except TypeError:
            pass
        if self.accion is not None:
            self.iface.removePluginMenu("&Consulta SIRI", self.accion)
            self.iface.removeToolBarIcon(self.accion)
        if self.herramienta is not None and self.canvas.mapTool() is self.herramienta:
            self.canvas.unsetMapTool(self.herramienta)
        self.accion = None
        self.herramienta = None

    # --- logica del boton ------------------------------------------------
    def _alternar(self, activar):
        if activar:
            self.herramienta_anterior = self.canvas.mapTool()
            self.canvas.setMapTool(self.herramienta)
        else:
            if self.canvas.mapTool() is self.herramienta:
                if self.herramienta_anterior:
                    self.canvas.setMapTool(self.herramienta_anterior)
                else:
                    self.canvas.unsetMapTool(self.herramienta)

    def _al_cambiar_herramienta(self, nueva_activa):
        if self.accion is None:
            return
        self.accion.blockSignals(True)
        self.accion.setChecked(nueva_activa is self.herramienta)
        self.accion.blockSignals(False)

    # --- capas -------------------------------------------------------------
    def _capas_wms_del_proyecto(self):
        return [
            l for l in QgsProject.instance().mapLayers().values()
            if isinstance(l, QgsRasterLayer)
            and l.providerType() == "wms"
            and FRAGMENTO_FUENTE_SIRI in l.source()
        ]

    def _obtener_capa_resultados(self, campos_modelo):
        existente = QgsProject.instance().mapLayersByName(NOMBRE_CAPA_RESULTADOS)
        if existente:
            return existente[0]

        crs_proyecto = QgsProject.instance().crs().authid()
        nueva = QgsVectorLayer(f"MultiPolygon?crs={crs_proyecto}", NOMBRE_CAPA_RESULTADOS, "memory")
        proveedor = nueva.dataProvider()
        proveedor.addAttributes([QgsField(f) for f in campos_modelo])
        proveedor.addAttributes(_campos_propios())
        nueva.updateFields()
        QgsProject.instance().addMapLayer(nueva)
        return nueva

# -*- coding: utf-8 -*-
"""
SIRI CR
=======
Barra de herramientas con dos cosas:

1. «Cargar capas»: agrega al proyecto las capas WMS del catastro del SIRI
   (Registro Inmobiliario del SNIT) de un solo clic, sin que el usuario tenga
   que crear la conexion a mano en el Administrador de fuentes de datos. Desde
   el mismo menu se puede agregar cualquier otro WMS pegando su URL.

2. «Consultar predio» (activar/desactivar): con la herramienta activa, cada
   clic sobre el mapa hace GetFeatureInfo contra las capas consultables y
   guarda el predio encontrado en una capa temporal de memoria.

Dos cuidados que motivaron este plugin:

1. Proyeccion: cada capa WMS negocia en el CRS con el que fue cargada -que no
   tiene por que ser el del proyecto (p.ej. proyecto en EPSG:8908 / CRTM05 y
   capa en EPSG:4326). Hay que transformar el punto de clic HACIA el CRS de la
   capa antes de preguntarle al servidor, y volver a transformar la geometria
   de la respuesta DESDE ese CRS hacia el del proyecto antes de guardarla -si
   se omite este segundo paso, la geometria queda en las unidades equivocadas
   dentro de la capa de resultados, y cada predio guardado aparece en un punto
   sin relacion con su ubicacion real.

2. Campos: el cuadro de dialogo en pantalla muestra nada mas un resumen
   (plano, finca, identifica, ubicacion, area), pero la capa guardada conserva
   TODOS los campos que devuelve el servidor, integros -mas cinco campos
   propios de procedencia (capa de origen, usuario de QGIS, fecha de consulta,
   y las coordenadas del clic).

Autor: Carlo Soto Castro
"""
import os
from datetime import datetime

from qgis.PyQt.QtCore import Qt, QVariant
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QMenu, QMessageBox, QToolButton
from qgis.core import (
    Qgis, QgsApplication, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsFeature, QgsField, QgsProject, QgsRaster, QgsRasterLayer, QgsVectorLayer,
)
from qgis.gui import QgsMapToolEmitPoint

from . import wms
from .dialogos import DialogoWmsPorUrl
from .servicios import SERVICIOS

NOMBRE_CORTO = "SIRI CR"
NOMBRE_CAPA_RESULTADOS = "Consultas SIRI (temporal)"
# Se sigue reconociendo el dominio del SIRI para que las capas que el usuario
# ya tuviera cargadas a mano -de antes de este plugin, o traidas de un
# proyecto de un companero- se puedan consultar igual.
FRAGMENTO_FUENTE_SIRI = "siri.snitcr.go.cr"


def _icono():
    carpeta = os.path.dirname(__file__)
    for archivo in ("icon.png", "icon.svg"):
        ruta = os.path.join(carpeta, archivo)
        if os.path.exists(ruta):
            return QIcon(ruta)
    return QIcon()


def _usuario_qgis():
    """Quien hizo la consulta -nombre completo del usuario configurado en
    QGIS (Configuracion > Opciones > General) y, si no esta configurado,
    el usuario de inicio de sesion del sistema operativo. No depende de un
    login propio del plugin -es el mismo dato que QGIS ya usa para @user_full_name."""
    return QgsApplication.userFullName() or QgsApplication.userLoginName()


def _campos_propios():
    """Los 5 campos de procedencia que este plugin agrega a cada predio
    guardado, ademas de todos los que trae el servidor. Definidos en un solo
    lugar y reutilizados tanto al crear la capa como al completar una capa
    reutilizada a la que le falten -para que nunca queden a medias."""
    return [
        QgsField("_capa_origen", QVariant.String),
        QgsField("_usuario_qgis", QVariant.String),
        QgsField("_fecha_consulta", QVariant.String),
        QgsField("_x_clic", QVariant.Double),
        QgsField("_y_clic", QVariant.Double),
    ]


class ConsultaCatastroSiriTool(QgsMapToolEmitPoint):
    """Herramienta de mapa: clic -> GetFeatureInfo en las capas consultables."""

    def __init__(self, canvas, obtener_capas_wms, obtener_capa_resultados):
        super().__init__(canvas)
        self.canvas = canvas
        self.obtener_capas_wms = obtener_capas_wms
        self.obtener_capa_resultados = obtener_capa_resultados

    def canvasReleaseEvent(self, event):
        capas_wms = self.obtener_capas_wms()
        if not capas_wms:
            QMessageBox.warning(
                None, NOMBRE_CORTO,
                "No hay ninguna capa WMS consultable en el proyecto.\n\n"
                "Usá «Cargar capas» en la barra de herramientas del plugin "
                "para agregar el catastro del SIRI.")
            return

        punto_proyecto = self.toMapCoordinates(event.pos())
        crs_proyecto = self.canvas.mapSettings().destinationCrs()
        size = self.canvas.mapSettings().outputSize()

        for capa in capas_wms:
            # Cada capa puede estar en un CRS distinto -el del servicio con el
            # que se cargo-, asi que la transformacion se calcula por capa y no
            # una sola vez para todas.
            crs_capa = capa.crs()
            if not crs_capa.isValid():
                crs_capa = QgsCoordinateReferenceSystem("EPSG:4326")
            a_capa = QgsCoordinateTransform(crs_proyecto, crs_capa, QgsProject.instance())
            de_capa = QgsCoordinateTransform(crs_capa, crs_proyecto, QgsProject.instance())

            try:
                punto_capa = a_capa.transform(punto_proyecto)
                extent_capa = a_capa.transformBoundingBox(self.canvas.extent())
                resultado = capa.dataProvider().identify(
                    punto_capa, QgsRaster.IdentifyFormatFeature, extent_capa,
                    size.width(), size.height())
            except Exception as e:
                print(f"[{NOMBRE_CORTO}] error al consultar «{capa.name()}»: {e}")
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
                        self._guardar(feat_origen, capa.name(), de_capa, punto_proyecto)
                        self._mostrar(feat_origen, capa.name())
                        return

        QMessageBox.information(
            None, NOMBRE_CORTO,
            "No hay predio catastrado en ese punto (o cae fuera de zona catastrada).")

    def _guardar(self, feat_origen, nombre_capa, de_capa, punto_proyecto):
        geom = feat_origen.geometry()
        geom.transform(de_capa)         # CRS de la capa WMS -> CRS del proyecto
        geom.convertToMultiType()       # por si el servidor devuelve Polygon simple

        capa_resultados = self.obtener_capa_resultados(feat_origen.fields())

        # Si esta respuesta trae campos que la capa aun no tiene -p.ej. Zona 2
        # devuelve algo que Zona 1 no trae, o se esta reutilizando una capa
        # "Consultas SIRI (temporal)" de una sesion anterior con otro
        # esquema- se agregan sobre la marcha. Se revisan tanto los campos
        # del servidor como los propios: nunca se descarta un campo del
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
        nueva.setAttribute("_usuario_qgis", _usuario_qgis())
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
        self.barra = None
        self.accion_consultar = None
        self.boton_capas = None
        self.acciones_menu = []
        self.herramienta = None
        self.herramienta_anterior = None

    # --- ciclo de vida del plugin ---------------------------------------
    def initGui(self):
        icono = _icono()
        self.barra = self.iface.addToolBar(NOMBRE_CORTO)
        self.barra.setObjectName("SiriCrToolBar")

        # 1. Boton con menu para cargar capas sin pasar por el Administrador
        #    de fuentes de datos.
        self.boton_capas = QToolButton(self.barra)
        self.boton_capas.setIcon(icono)
        self.boton_capas.setText("Cargar capas")
        self.boton_capas.setToolTip(
            f"{NOMBRE_CORTO}: cargar las capas WMS del catastro (SIRI) al "
            "proyecto, sin crear la conexión a mano.")
        self.boton_capas.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.boton_capas.setPopupMode(QToolButton.InstantPopup)
        self.boton_capas.setMenu(self._menu_capas())
        self.barra.addWidget(self.boton_capas)

        # 2. La herramienta de consulta propiamente dicha.
        self.accion_consultar = QAction(icono, "Consultar predio", self.iface.mainWindow())
        self.accion_consultar.setCheckable(True)
        self.accion_consultar.setToolTip(
            f"{NOMBRE_CORTO}: clic en el mapa consulta el catastro (WMS) y guarda el "
            "predio -con todos sus campos- en una capa temporal.")
        self.accion_consultar.toggled.connect(self._alternar)
        self.barra.addAction(self.accion_consultar)
        self.iface.addPluginToMenu(f"&{NOMBRE_CORTO}", self.accion_consultar)

        self.herramienta = ConsultaCatastroSiriTool(
            self.canvas, self._capas_consultables, self._obtener_capa_resultados)

        self.canvas.mapToolSet.connect(self._al_cambiar_herramienta)

    def _menu_capas(self):
        menu = QMenu(self.iface.mainWindow())
        for servicio in SERVICIOS:
            menu.addSection(servicio["nombre"])
            todas = QAction(f"Cargar todas ({len(servicio['capas'])})", menu)
            todas.triggered.connect(
                lambda _=False, s=servicio: self._cargar_servicio(s, s["capas"]))
            menu.addAction(todas)
            for capa in servicio["capas"]:
                accion = QAction(capa["titulo"], menu)
                accion.triggered.connect(
                    lambda _=False, s=servicio, c=capa: self._cargar_servicio(s, [c]))
                menu.addAction(accion)
        menu.addSeparator()

        otro = QAction("Agregar otro WMS (pegar URL)…", menu)
        otro.triggered.connect(self._agregar_wms_por_url)
        menu.addAction(otro)

        guardar = QAction("Guardar conexiones en el Explorador de QGIS", menu)
        guardar.triggered.connect(self._guardar_conexiones)
        menu.addAction(guardar)

        self.acciones_menu = menu.actions()
        return menu

    def unload(self):
        try:
            self.canvas.mapToolSet.disconnect(self._al_cambiar_herramienta)
        except TypeError:
            pass
        if self.accion_consultar is not None:
            self.iface.removePluginMenu(f"&{NOMBRE_CORTO}", self.accion_consultar)
        if self.herramienta is not None and self.canvas.mapTool() is self.herramienta:
            self.canvas.unsetMapTool(self.herramienta)
        if self.barra is not None:
            self.iface.mainWindow().removeToolBar(self.barra)
            self.barra.deleteLater()
        self.barra = None
        self.boton_capas = None
        self.acciones_menu = []
        self.accion_consultar = None
        self.herramienta = None

    # --- carga de capas ---------------------------------------------------
    def _cargar_servicio(self, servicio, capas):
        cargadas, reutilizadas, fallidas = [], [], []
        for capa in capas:
            uri = wms.uri_wms(
                servicio["url"], capa["layer"],
                capa.get("crs", servicio["crs"]),
                version=servicio.get("version", "1.1.1"),
                formato=servicio.get("formato", "image/png"))
            objeto, nueva = wms.cargar_capa_wms(
                uri, capa["titulo"], servicio["clave"], capa.get("consultable", True))
            if objeto is None:
                fallidas.append(capa["titulo"])
            elif nueva:
                cargadas.append(capa["titulo"])
            else:
                reutilizadas.append(capa["titulo"])

        partes = []
        if cargadas:
            partes.append("Cargadas: " + ", ".join(cargadas) + ".")
        if reutilizadas:
            partes.append("Ya estaban en el proyecto: " + ", ".join(reutilizadas) + ".")
        if fallidas:
            partes.append("No se pudieron cargar: " + ", ".join(fallidas) +
                          ". Revisá la conexión a internet o si el servicio está caído.")
        self.iface.messageBar().pushMessage(
            NOMBRE_CORTO, " ".join(partes),
            level=Qgis.Warning if fallidas else Qgis.Info, duration=8)

    def _agregar_wms_por_url(self):
        dialogo = DialogoWmsPorUrl(self.iface.mainWindow())
        if not dialogo.exec_():
            return
        cargadas, fallidas = [], []
        for url, version, capa, crs in dialogo.seleccion():
            uri = wms.uri_wms(url, capa["layer"], crs, version=version)
            objeto, _ = wms.cargar_capa_wms(
                uri, capa["titulo"], None, capa["consultable"])
            (cargadas if objeto is not None else fallidas).append(capa["titulo"])
        self.iface.messageBar().pushMessage(
            NOMBRE_CORTO,
            (f"{len(cargadas)} capa(s) agregadas. " if cargadas else "") +
            ("No se pudieron cargar: " + ", ".join(fallidas) if fallidas else ""),
            level=Qgis.Warning if fallidas else Qgis.Info, duration=8)

    def _guardar_conexiones(self):
        for servicio in SERVICIOS:
            wms.guardar_conexion_qgis(servicio["nombre"], servicio["url"])
        self.iface.messageBar().pushMessage(
            NOMBRE_CORTO,
            "Servicios guardados como conexiones WMS; aparecen en el panel "
            "Explorador y en el Administrador de fuentes de datos.",
            level=Qgis.Info, duration=8)

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
        if self.accion_consultar is None:
            return
        self.accion_consultar.blockSignals(True)
        self.accion_consultar.setChecked(nueva_activa is self.herramienta)
        self.accion_consultar.blockSignals(False)

    # --- capas -------------------------------------------------------------
    def _capas_consultables(self):
        """Las que el plugin cargo y marco como consultables, mas -por
        compatibilidad con proyectos armados antes de este plugin- cualquier
        capa WMS cuyo origen apunte al SIRI."""
        capas = []
        for l in QgsProject.instance().mapLayers().values():
            if not (isinstance(l, QgsRasterLayer) and l.providerType() == "wms"):
                continue
            marca = l.customProperty(wms.PROP_CONSULTABLE)
            if marca in (True, "true", "True"):
                capas.append(l)
            elif marca is None and FRAGMENTO_FUENTE_SIRI in l.source():
                capas.append(l)
        return capas

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

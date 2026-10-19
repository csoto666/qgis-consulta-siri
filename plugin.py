# -*- coding: utf-8 -*-
#
# SIRI CR - plugin de QGIS para el catastro del SIRI (Costa Rica)
# Copyright (C) 2026 Carlo Soto Castro
#
# Este programa es software libre: usted puede redistribuirlo y/o modificarlo
# bajo los terminos de la Licencia Publica General GNU publicada por la Free
# Software Foundation, ya sea la version 2 de la Licencia o (a su eleccion)
# cualquier version posterior.
#
# Este programa se distribuye con la esperanza de que sea util, pero SIN
# NINGUNA GARANTIA; ni siquiera la garantia implicita de COMERCIABILIDAD o
# APTITUD PARA UN PROPOSITO DETERMINADO. Vea la Licencia Publica General GNU
# para mas detalles.
#
# Usted deberia haber recibido una copia de la Licencia Publica General GNU
# junto con este programa (archivo LICENSE). Si no, vea
# <https://www.gnu.org/licenses/>.
#
"""
SIRI CR
=======
Barra de herramientas con tres cosas:

1. «Cargar capas»: agrega al proyecto las capas WMS del catastro del SIRI
   (Registro Inmobiliario del SNIT) de un solo clic, sin que el usuario tenga
   que crear la conexion a mano en el Administrador de fuentes de datos. Desde
   el mismo menu se puede agregar cualquier otro WMS pegando su URL.

2. «Consultar predio» (activar/desactivar): con la herramienta activa, cada
   clic sobre el mapa hace GetFeatureInfo contra las capas consultables y
   guarda el predio encontrado en una capa temporal de memoria.

3. «Buscar predio»: lo mismo pero por numero de finca o de plano, con el
   buscador del visor publico del SNIT (ver busqueda.py).

Funciona en QGIS 3 y en QGIS 4: donde identify() no se puede leer desde
Python (algunos builds de QGIS 4), la consulta por clic pide el
GetFeatureInfo directo en GeoJSON (ver wms.py).

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
from qgis.PyQt.QtWidgets import (
    QAction, QApplication, QMenu, QMessageBox, QToolButton,
)
from qgis.core import (
    Qgis, QgsApplication, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsFeature, QgsField, QgsProject, QgsRaster, QgsRasterLayer, QgsVectorLayer,
)
from qgis.gui import QgsMapToolEmitPoint

from . import wms
from .busqueda import DialogoBuscarPredio
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

    # True cuando identify() responde pero sus resultados no se pueden leer
    # desde Python (QGIS 4): de ahi en adelante se va directo al GeoJSON.
    identify_no_convierte = False

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
        # Si TODAS las capas fallaron por red, el mensaje final no puede ser
        # "no hay predio aqui" -seria mentira, y manda al usuario a buscar el
        # problema donde no esta.
        hubo_error_de_servicio = False

        for capa in capas_wms:
            # Cada capa puede estar en un CRS distinto -el del servicio con el
            # que se cargo-, asi que la transformacion se calcula por capa y no
            # una sola vez para todas.
            crs_capa = capa.crs()
            if not crs_capa.isValid():
                crs_capa = QgsCoordinateReferenceSystem("EPSG:4326")
            a_capa = QgsCoordinateTransform(crs_proyecto, crs_capa, QgsProject.instance())
            punto_capa = a_capa.transform(punto_proyecto)
            extent_capa = a_capa.transformBoundingBox(self.canvas.extent())

            entidades, crs_respuesta = self._consultar_capa(
                capa, punto_capa, extent_capa, size.width(), size.height())
            if entidades is None:
                hubo_error_de_servicio = True
                continue

            de_capa = QgsCoordinateTransform(
                crs_respuesta or crs_capa, crs_proyecto, QgsProject.instance())
            for feat_origen in entidades:
                if not feat_origen.hasGeometry():
                    continue
                self._guardar(feat_origen, capa.name(), de_capa, punto_proyecto)
                self._mostrar(feat_origen, capa.name())
                return

        if hubo_error_de_servicio:
            QMessageBox.warning(
                None, NOMBRE_CORTO,
                "El servicio del SIRI no respondió a la consulta.\n\n"
                "Ese servidor responde de forma intermitente: probá haciendo "
                "clic otra vez sobre el mismo punto.")
            return

        QMessageBox.information(
            None, NOMBRE_CORTO,
            "No hay predio catastrado en ese punto (o cae fuera de zona catastrada).")

    def _consultar_capa(self, capa, punto, extent, ancho, alto):
        """(entidades, crs_de_las_geometrias) del predio en el punto, con [] si
        el servidor contesto que ahi no hay nada; (None, None) si no contesto
        en ninguno de los reintentos. crs None = el de la capa."""
        for intento in range(wms.INTENTOS):
            if intento:
                wms.dormir(wms._espera(intento))

            if not ConsultaCatastroSiriTool.identify_no_convierte:
                try:
                    resultado = capa.dataProvider().identify(
                        punto, QgsRaster.IdentifyFormat.IdentifyFormatFeature, extent,
                        ancho, alto)
                except Exception as e:
                    print(f"[{NOMBRE_CORTO}] error al consultar «{capa.name()}»: {e}")
                    continue
                if not resultado.isValid():
                    continue
                try:
                    resultados = resultado.results()
                except Exception as e:
                    # QGIS 4: no convierte la QgsFeatureStoreList (ver wms.py,
                    # seccion GeoJSON). No va a mejorar en esta sesion, asi que
                    # se recuerda y no se vuelve a intentar.
                    print(f"[{NOMBRE_CORTO}] identify() no se puede leer en esta "
                          f"version de QGIS ({e}); se usa GetFeatureInfo GeoJSON.")
                    ConsultaCatastroSiriTool.identify_no_convierte = True
                else:
                    # Un fallo intermitente puede venir disfrazado de respuesta
                    # valida pero con el diccionario de resultados VACIO. Cuando
                    # de verdad no hay predio en el punto, el servidor si contesta
                    # -con una entrada por capa y cero entidades adentro. Por eso
                    # la condicion mira results() y no solo isValid().
                    if not resultados:
                        continue
                    entidades = []
                    for tiendas in resultados.values():
                        if isinstance(tiendas, list):
                            for tienda in tiendas:
                                entidades.extend(tienda.features())
                    return entidades, None

            entidades, crs, error = wms.getfeatureinfo_geojson(capa, punto, extent, ancho, alto)
            if entidades is not None:
                return entidades, crs
            print(f"[{NOMBRE_CORTO}] error al consultar «{capa.name()}»: {error}")
        return None, None

    def _guardar(self, feat_origen, nombre_capa, de_capa, punto_proyecto):
        geom = feat_origen.geometry()
        geom.transform(de_capa)         # CRS de la respuesta -> CRS del proyecto
        geom.convertToMultiType()       # por si el servidor devuelve Polygon simple
        capa_resultados = self.obtener_capa_resultados(feat_origen.fields())
        guardar_predio(capa_resultados, feat_origen, geom, nombre_capa,
                       punto_proyecto.x(), punto_proyecto.y())

    def _mostrar(self, feat_origen, nombre_capa):
        mostrar_predio(feat_origen, nombre_capa)


def guardar_predio(capa_resultados, feat_origen, geom, nombre_capa, x_clic, y_clic):
    """Agrega el predio a la capa de resultados. geom ya viene en el CRS del
    proyecto. Lo usan tanto el clic en el mapa como la busqueda por numero
    (que no tiene clic: ahi x_clic/y_clic van en NULL)."""
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
    nueva.setAttribute("_x_clic", x_clic)
    nueva.setAttribute("_y_clic", y_clic)

    capa_resultados.dataProvider().addFeature(nueva)
    capa_resultados.updateExtents()
    capa_resultados.triggerRepaint()


def mostrar_predio(feat_origen, nombre_capa):
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
        self.accion_buscar = None
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
        self.boton_capas.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.boton_capas.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
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

        # 3. Busqueda por numero de finca o de plano, sin tener que ubicar el
        #    predio en el mapa.
        self.accion_buscar = QAction(icono, "Buscar predio", self.iface.mainWindow())
        self.accion_buscar.setToolTip(
            f"{NOMBRE_CORTO}: buscar un predio por número de finca o de plano y "
            "agregarlo a la capa temporal.")
        self.accion_buscar.triggered.connect(self._abrir_busqueda)
        self.barra.addAction(self.accion_buscar)
        self.iface.addPluginToMenu(f"&{NOMBRE_CORTO}", self.accion_buscar)

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
        if self.accion_buscar is not None:
            self.iface.removePluginMenu(f"&{NOMBRE_CORTO}", self.accion_buscar)
        if self.herramienta is not None and self.canvas.mapTool() is self.herramienta:
            self.canvas.unsetMapTool(self.herramienta)
        if self.barra is not None:
            self.iface.mainWindow().removeToolBar(self.barra)
            self.barra.deleteLater()
        self.barra = None
        self.boton_capas = None
        self.acciones_menu = []
        self.accion_consultar = None
        self.accion_buscar = None
        self.herramienta = None

    # --- busqueda por finca o plano --------------------------------------
    def _abrir_busqueda(self):
        DialogoBuscarPredio(self._guardar_busqueda, self.iface.mainWindow()).exec()

    def _guardar_busqueda(self, zona, feat, crs_origen):
        proyecto = QgsProject.instance()
        geom = feat.geometry()
        geom.transform(QgsCoordinateTransform(crs_origen, proyecto.crs(), proyecto))
        geom.convertToMultiType()
        capa_resultados = self._obtener_capa_resultados(feat.fields())
        # Sin clic de por medio: las coordenadas del clic quedan en NULL en
        # vez de inventarlas con el centroide.
        guardar_predio(capa_resultados, feat, geom, f"Búsqueda SNIT ({zona})", None, None)
        self.iface.setActiveLayer(capa_resultados)
        caja = geom.boundingBox()
        self.canvas.setExtent(caja.buffered(max(caja.width(), caja.height()) * 0.2))
        self.canvas.refresh()

    # --- carga de capas ---------------------------------------------------
    def _cargar_servicio(self, servicio, capas):
        cargadas, reutilizadas, fallidas = [], [], []
        # Los reintentos contra un servidor intermitente pueden tardar unos
        # segundos; sin el cursor de espera parece que el boton no hizo nada.
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self._cargar_capas(servicio, capas, cargadas, reutilizadas, fallidas)
        finally:
            QApplication.restoreOverrideCursor()

        partes = []
        if cargadas:
            partes.append("Cargadas: " + ", ".join(cargadas) + ".")
        if reutilizadas:
            partes.append("Ya estaban en el proyecto: " + ", ".join(reutilizadas) + ".")
        if fallidas:
            partes.append(
                "No respondió el servicio para: " + ", ".join(fallidas) +
                ". El servidor del SIRI responde de forma intermitente — "
                "volvé a intentarlo en unos segundos.")
        self.iface.messageBar().pushMessage(
            NOMBRE_CORTO, " ".join(partes),
            level=Qgis.MessageLevel.Warning if fallidas else Qgis.MessageLevel.Info, duration=8)

    def _cargar_capas(self, servicio, capas, cargadas, reutilizadas, fallidas):
        """Dos pasadas. La segunda no es terquedad: QGIS guarda en su cache de
        red el GetCapabilities que si llego, y todas las capas de un servicio
        comparten ese documento. O sea que si una capa entro, las que habian
        fallado antes entran despues al instante, sin volver a salir a la red
        -medido: 0.0s con el servicio del SIRI al 10% de disponibilidad."""
        self._intentar_capas(servicio, capas, cargadas, reutilizadas, fallidas)
        if fallidas and cargadas:
            reintentar = [c for c in capas if c["titulo"] in fallidas]
            fallidas.clear()
            self._intentar_capas(servicio, reintentar, cargadas, reutilizadas, fallidas)

    def _intentar_capas(self, servicio, capas, cargadas, reutilizadas, fallidas):
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

    def _agregar_wms_por_url(self):
        dialogo = DialogoWmsPorUrl(self.iface.mainWindow())
        if not dialogo.exec():
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
            level=Qgis.MessageLevel.Warning if fallidas else Qgis.MessageLevel.Info, duration=8)

    def _guardar_conexiones(self):
        for servicio in SERVICIOS:
            wms.guardar_conexion_qgis(servicio["nombre"], servicio["url"])
        self.iface.messageBar().pushMessage(
            NOMBRE_CORTO,
            "Servicios guardados como conexiones WMS; aparecen en el panel "
            "Explorador y en el Administrador de fuentes de datos.",
            level=Qgis.MessageLevel.Info, duration=8)

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
        for capa in QgsProject.instance().mapLayers().values():
            if not (isinstance(capa, QgsRasterLayer) and capa.providerType() == "wms"):
                continue
            marca = capa.customProperty(wms.PROP_CONSULTABLE)
            if marca in (True, "true", "True"):
                capas.append(capa)
            elif marca is None and FRAGMENTO_FUENTE_SIRI in capa.source():
                capas.append(capa)
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

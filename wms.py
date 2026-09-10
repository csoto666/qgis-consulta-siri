# -*- coding: utf-8 -*-
"""
Todo lo que tiene que ver con hablar WMS: armar el URI que entiende el
proveedor "wms" de QGIS, cargar la capa al proyecto, y leer un
GetCapabilities para poder ofrecerle al usuario la lista de capas de un
servicio que el mismo pegue.

La idea de fondo: que nadie tenga que pasar por «Administrador de fuentes de
datos -> WMS/WMTS -> Nuevo -> pegar URL -> Conectar -> escoger capa». Eso es
lo que hace que la gente de SINAC termine trabajando sin el catastro a la
vista.
"""
import time
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from qgis.PyQt.QtCore import QEventLoop, QUrl
from qgis.PyQt.QtWidgets import QApplication
from qgis.PyQt.QtNetwork import QNetworkRequest
from qgis.core import (
    QgsBlockingNetworkRequest, QgsDataSourceUri, QgsProject, QgsRasterLayer,
    QgsSettings,
)

# El servidor del SIRI responde de forma intermitente: una de cada dos
# peticiones (medido) se va en un 302 hacia /Geoservicios/error en vez de
# devolver el GetCapabilities. No es la red del usuario ni el URI -el mismo
# pedido, repetido, funciona. Por eso todo lo que sale a ese servicio
# reintenta antes de darse por vencido; sin esto la capa se crea invalida y
# el plugin parece roto cuando lo que fallo fue un intento suelto.
INTENTOS = 6
ESPERA_ENTRE_INTENTOS = 1.0   # segundos; crece en cada reintento (ver _espera)
ESPERA_MAXIMA = 4.0


def _espera(intento):
    """Espera creciente: no tiene sentido reintentar cinco veces seguidas a
    un servidor que esta ahogado -conviene darle aire."""
    return min(ESPERA_ENTRE_INTENTOS * (1.5 ** intento), ESPERA_MAXIMA)


def dormir(segundos):
    """Esperar sin dejar QGIS congelado. Con el servicio del SIRI en mal dia
    la espera acumulada llega a la media docena de segundos por capa; un
    time.sleep() pelado deja la ventana sin repintar y el sistema la marca
    como «no responde», que es peor que la demora en si.

    Se excluyen los eventos de entrada a proposito: se quiere que la ventana
    se repinte, no que un segundo clic impaciente en el menu dispare otra
    carga encima de la que ya esta corriendo."""
    fin = time.monotonic() + segundos
    while True:
        restante = fin - time.monotonic()
        if restante <= 0:
            return
        time.sleep(min(restante, 0.05))
        QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)

# Marca que el plugin le pone a las capas que el mismo carga, para despues
# saber cuales puede consultar sin adivinar por el nombre ni por el dominio.
PROP_CONSULTABLE = "consulta_siri/consultable"
PROP_SERVICIO = "consulta_siri/servicio"


def uri_wms(url, layer, crs, version="1.1.1", formato="image/png", estilo=""):
    """URI del proveedor «wms» de QGIS. Se arma con QgsDataSourceUri para que
    el escapado de la URL (que lleva ':' y '/') quede como QGIS lo espera."""
    u = QgsDataSourceUri()
    u.setParam("url", url)
    u.setParam("layers", layer)
    u.setParam("styles", estilo)
    u.setParam("format", formato)
    u.setParam("crs", crs)
    u.setParam("version", version)
    u.setParam("dpiMode", "7")
    return bytes(u.encodedUri()).decode()


def cargar_capa_wms(uri, titulo, clave_servicio=None, consultable=True,
                    intentos=INTENTOS):
    """Agrega la capa al proyecto (o devuelve la que ya estuviera cargada con
    ese mismo origen, para no llenar la tabla de contenidos de duplicados).
    Reintenta: crear la capa implica pedir el GetCapabilities, y ese pedido
    falla seguido contra el SIRI."""
    for capa in QgsProject.instance().mapLayers().values():
        if isinstance(capa, QgsRasterLayer) and capa.source() == uri:
            return capa, False

    capa = None
    for intento in range(intentos):
        if intento:
            dormir(_espera(intento))
        capa = QgsRasterLayer(uri, titulo, "wms")
        if capa.isValid():
            break
    if capa is None or not capa.isValid():
        return None, False

    capa.setCustomProperty(PROP_CONSULTABLE, bool(consultable))
    if clave_servicio:
        capa.setCustomProperty(PROP_SERVICIO, clave_servicio)
    QgsProject.instance().addMapLayer(capa)
    return capa, True


def guardar_conexion_qgis(nombre, url):
    """Deja el servicio guardado como conexion WMS de QGIS, para que ademas
    aparezca en el panel Explorador y en el Administrador de fuentes de datos
    -util cuando el usuario quiera armar sus propias capas a mano."""
    s = QgsSettings()
    base = f"qgis/connections-wms/{nombre}"
    s.setValue(f"{base}/url", url)
    s.setValue(f"{base}/ignoreGetMapURI", False)
    s.setValue(f"{base}/ignoreGetFeatureInfoURI", False)
    s.setValue(f"{base}/smoothPixmapTransform", False)


# --- GetCapabilities ----------------------------------------------------

def url_capabilities(url, version="1.1.1"):
    """Le pega service/request/version a la URL respetando lo que ya traiga
    (varios nodos del SNIT publican la URL con parametros incluidos)."""
    partes = urlsplit(url)
    q = {k.lower(): v for k, v in parse_qsl(partes.query)}
    q.update({"service": "WMS", "request": "GetCapabilities", "version": version})
    return urlunsplit((partes.scheme, partes.netloc, partes.path, urlencode(q), ""))


def _sin_ns(tag):
    return tag.split("}", 1)[-1]


def _recorrer_capas(elemento, srs_heredados, acumulado):
    """Un WMS anida <Layer> dentro de <Layer>; los SRS y el bbox se heredan
    del padre. Solo interesan las hojas que tienen <Name> (las que se pueden
    pedir); las intermedias son agrupadores."""
    srs = set(srs_heredados)
    nombre = titulo = None
    consultable = elemento.get("queryable") == "1"
    hijos = []

    for hijo in elemento:
        t = _sin_ns(hijo.tag)
        if t == "Name" and nombre is None:
            nombre = (hijo.text or "").strip()
        elif t == "Title" and titulo is None:
            titulo = (hijo.text or "").strip()
        elif t in ("SRS", "CRS") and hijo.text:
            srs.update(x.strip() for x in hijo.text.split() if x.strip())
        elif t == "Layer":
            hijos.append(hijo)

    if nombre:
        acumulado.append({
            "layer": nombre,
            "titulo": titulo or nombre,
            "consultable": consultable,
            "srs": sorted(srs),
        })
    for hijo in hijos:
        _recorrer_capas(hijo, srs, acumulado)


def _pedir_capabilities(url, version, tiempo_espera):
    """Un intento. Devuelve (xml_crudo, error)."""
    peticion = QgsBlockingNetworkRequest()
    if hasattr(peticion, "setTimeout"):   # no existe en QGIS 3.x tempranos
        peticion.setTimeout(tiempo_espera)
    codigo = peticion.get(QNetworkRequest(QUrl(url_capabilities(url, version))), True)
    if codigo != QgsBlockingNetworkRequest.NoError:
        return None, peticion.errorMessage() or "No se pudo contactar el servicio."
    return bytes(peticion.reply().content()), None


def leer_capabilities(url, version="1.1.1", tiempo_espera=30000, intentos=INTENTOS):
    """Devuelve (lista_de_capas, error). Usa la pila de red de QGIS para que
    respete el proxy y los certificados configurados en el perfil, y reintenta
    -ver el comentario de INTENTOS."""
    error = None
    for intento in range(intentos):
        if intento:
            dormir(_espera(intento))

        crudo, error = _pedir_capabilities(url, version, tiempo_espera)
        if crudo is None:
            continue

        try:
            raiz = ET.fromstring(crudo)
        except ET.ParseError as e:
            error = f"La respuesta no es un GetCapabilities válido ({e})."
            continue

        # Un ServiceException es justamente la respuesta intermitente del
        # SIRI: hay que volver a intentar, no rendirse.
        if _sin_ns(raiz.tag) == "ServiceExceptionReport":
            error = "El servicio devolvió un error (respuesta intermitente del servidor)."
            continue

        capas = []
        for elemento in raiz.iter():
            if _sin_ns(elemento.tag) == "Capability":
                for hijo in elemento:
                    if _sin_ns(hijo.tag) == "Layer":
                        _recorrer_capas(hijo, set(), capas)
                break
        if capas:
            return capas, None

        error = ("El servicio respondió pero no declaró ninguna capa. "
                 "Algunos servidores solo listan sus capas con la versión "
                 "1.1.1 del protocolo (probá con esa).")

    return [], error


def escoger_crs(srs_disponibles, crs_proyecto):
    """Que CRS pedirle al servidor: el del proyecto si el servicio lo ofrece
    -asi no hay reproyeccion de por medio- y si no, algo que casi todo
    servidor publica."""
    if crs_proyecto in srs_disponibles:
        return crs_proyecto
    for preferido in ("EPSG:8908", "EPSG:5367", "EPSG:4326", "EPSG:3857"):
        if preferido in srs_disponibles:
            return preferido
    return srs_disponibles[0] if srs_disponibles else "EPSG:4326"

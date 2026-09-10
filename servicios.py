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
Catalogo de servicios WMS que el plugin sabe cargar solo -sin que el usuario
tenga que crear la conexion a mano en el Administrador de fuentes de datos.

Por ahora aqui vive UNICAMENTE el nodo "Registro Inmobiliario" del SNIT
(https://www.snitcr.go.cr -> Servicios OGC), que es el que da el catastro.
Cualquier otro servicio -incluido el de FONAFIFO/PSA cuando su servidor
vuelva a estar en linea- se puede agregar sin tocar este archivo, con
«Agregar otro WMS (pegar URL)...», que lee el GetCapabilities del servicio y
deja escoger las capas.

Nota sobre la version del protocolo: el servidor del SIRI devuelve un
GetCapabilities VACIO si se le pide version=1.3.0 explicitamente; con 1.1.1
si lista las capas. Por eso `version` es parte de la definicion del servicio
y no una constante del plugin.
"""

# Los datos de abajo fueron verificados el 2026-09-10 contra
# https://www.snitcr.go.cr/Visor/detalle_nodo (nodo "Registro Inmobiliario")
# y contra el propio GetCapabilities del servicio.
SERVICIOS = [
    {
        "clave": "siri_ri",
        "nombre": "Catastro — Registro Inmobiliario (SIRI)",
        "url": "https://siri.snitcr.go.cr/Geoservicios/wms",
        "version": "1.1.1",
        "crs": "EPSG:8908",       # CRTM05; el servicio tambien ofrece 4326, 5367 y 3857
        "formato": "image/png",
        "institucion": "Registro Inmobiliario (SNIT)",
        "capas": [
            {"layer": "catastro",       "titulo": "Catastro Zona 1 (SIRI)",     "consultable": True},
            {"layer": "catastro_aldia", "titulo": "Catastro Zona 2 (SIRI)",     "consultable": True},
            {"layer": "vias_publicas",  "titulo": "Vías públicas (SIRI)",       "consultable": True,
             "crs": "EPSG:5367"},     # esta capa se publica en CRTM05 viejo
        ],
    },
]


def servicio(clave):
    for s in SERVICIOS:
        if s["clave"] == clave:
            return s
    return None

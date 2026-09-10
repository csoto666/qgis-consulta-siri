# SIRI CR (plugin de QGIS)

Plugin de QGIS que pone el **catastro del SIRI** (Registro Inmobiliario de
Costa Rica) a un clic de distancia, dentro de QGIS:

1. **Cargar capas** — agrega al proyecto las capas WMS del catastro
   (`Zona 1`, `Zona 2`, `Vías públicas`) **sin pasar por el Administrador de
   fuentes de datos**: nada de *WMS/WMTS → Nuevo → pegar URL → Conectar →
   escoger capa*. Desde el mismo menú se puede agregar **cualquier otro WMS
   pegando su URL** (el plugin lee el `GetCapabilities` y deja escoger las
   capas), y guardar los servicios como conexiones del Explorador de QGIS.
2. **Consultar predio** — con la herramienta activa, cada clic sobre el mapa
   consulta (`GetFeatureInfo`) las capas cargadas, muestra un resumen del
   predio y lo guarda —geometría y **todos** los campos que devuelve el
   servidor, íntegros— en una capa temporal de memoria.

Nació como apoyo para el registro de proyectos forestales (RPF) del SINAC,
para poder confirmar y visualizar predios catastrados sin salir de QGIS.

## Por qué existe

- **Sin procedimiento de conexión**: la parte que más traba a la gente no es
  consultar, es dejar el catastro cargado. El plugin trae la URL del servicio
  ya adentro y arma la capa solo.
- **Proyección**: cada capa WMS negocia en el CRS con el que fue cargada, que
  no tiene por qué ser el del proyecto. El plugin transforma el punto de clic
  hacia el CRS de la capa antes de consultar, y transforma la geometría de la
  respuesta de vuelta al CRS del proyecto antes de guardarla. Sin este segundo
  paso, cada predio guardado queda con coordenadas de otra unidad metidas en
  la capa de resultados —mal ubicado, aunque la consulta en sí haya
  funcionado.
- **Campos íntegros**: el cuadro de diálogo en pantalla muestra solo un
  resumen (plano, finca, identifica, ubicación, área), pero la capa guardada
  conserva absolutamente todos los campos que trae la respuesta del servidor
  —sin filtrar ni resumir nada—, más 5 campos propios de procedencia
  (`_capa_origen`, `_usuario_qgis`, `_fecha_consulta`, `_x_clic`, `_y_clic`).

## Requisitos

- QGIS 3.x
- Conexión a internet (el catastro se consulta en línea contra el SIRI).

## Instalación

**Opción A — desde ZIP (recomendada):**

1. Descargá este repositorio como ZIP (botón *Code → Download ZIP* en
   GitHub).
2. En QGIS: *Complementos → Administrar e instalar complementos → Instalar
   desde ZIP* → seleccioná el archivo descargado.

**Opción B — copiando la carpeta a mano:**

Copiá esta carpeta completa dentro de la carpeta de complementos de tu
perfil de QGIS, respetando el nombre `consulta_siri`:

- macOS: `~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/consulta_siri/`
- Windows: `%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\consulta_siri\`
- Linux: `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/consulta_siri/`

Después activalo en *Complementos → Administrar e instalar complementos →
Instalados*.

## Uso

1. En la barra **SIRI CR**, abrí **«Cargar capas»** y elegí *Cargar todas* (o
   solo Zona 1 / Zona 2).
2. Activá el botón **«Consultar predio»**.
3. Hacé clic sobre un predio catastrado: aparece un resumen y el predio queda
   guardado en la capa `"Consultas SIRI (temporal)"`.
4. Clic de nuevo en el botón (o cambiá de herramienta) para desactivar —
   vuelve sola a la herramienta que tenías antes.

La capa de resultados es de memoria: para conservarla, exportala
(*clic derecho → Exportar → Guardar entidades como…*) antes de cerrar QGIS.

## Servicio que trae configurado

| Nodo | URL | Capas |
|---|---|---|
| Registro Inmobiliario (SNIT) | `https://siri.snitcr.go.cr/Geoservicios/wms` | `catastro` (Zona 1), `catastro_aldia` (Zona 2), `vias_publicas` |

Se pide con **WMS 1.1.1** a propósito: ese servidor devuelve un
`GetCapabilities` vacío si se le exige `version=1.3.0`. Publica sus capas en
`EPSG:8908` (CRTM05) y también acepta `4326`, `5367` y `3857`.

## Si falla al cargar («no respondió el servicio»)

El servidor del SIRI responde **de forma intermitente**: medido el
2026-09-10, cerca de la mitad de las peticiones se van en un `302` hacia
`/Geoservicios/error` en vez de contestar. No es la red de uno ni la URL —el
mismo pedido, repetido, funciona. Por eso el plugin **reintenta** (4 veces,
con 1,5 s entre intentos) antes de darse por vencido, tanto al cargar las
capas como al consultar un predio. Si aun así falla, esperá unos segundos y
volvé a hacer clic.

Ojo con una consecuencia de lo mismo: cuando de verdad no hay predio en el
punto, el servidor **sí** contesta (con cero entidades). Un diccionario de
resultados vacío significa que falló la petición, no que el terreno no esté
catastrado —el plugin distingue los dos casos y te lo dice distinto.

## Otros servicios que se pueden agregar con «Agregar otro WMS»

El SNIT publica el listado completo de nodos en
<https://www.snitcr.go.cr/ico_servicios_ogc>. Estos son algunos de los que
más sirven para el trabajo forestal; se agregan pegando la URL en el cuadro
del plugin (todos verificados el 2026-09-10):

| Nodo | URL |
|---|---|
| SINAC — ASP, patrimonio natural del Estado, cobertura forestal 2021/2023, corredores biológicos | `https://geos1pne.sirefor.go.cr/wms` |
| IGN Cartografía 1:5 mil | `https://geos.snitcr.go.cr/be/IGN_5/wms` |
| IGN Cartografía 1:25 mil | `https://geos.snitcr.go.cr/be/IGN_25/wms` |
| Ortofoto 2014-2017 (5k) | `https://geos1.snitcr.go.cr/Ortofoto2017/wms` |
| Ortofoto 2015-2018 (1k) | `https://geos1.snitcr.go.cr/Ortofoto1k/wms` |
| SETENA | `https://tramites.setena.go.cr/Geoservicios/wms` |
| SENARA — vulnerabilidad de acuíferos | `https://mapas.senara.go.cr/wms` |
| INEC — marco geoestadístico | `https://gestorgeo.inec.go.cr/geoserver/SNIT_INEC_MGN/wms` |
| Zonas homogéneas (ONT / Hacienda) | `https://sig.hacienda.go.cr/server/services/Zonas_Homogeneas_ONT/MapServer/WMSServer` |

**PSA / FONAFIFO:** el nodo existe en el SNIT
(`https://geodatos.sinia.go.cr/geoserver/FONAFIFO/wms`) pero hoy está
publicando **0 capas** —el servidor de SINIA está caído y todavía no lo
levantan. Por eso el PSA **no** viene precargado en el plugin: en cuanto el
servicio vuelva, se agrega pegando esa URL, sin necesidad de actualizar el
plugin.

## Licencia

Todos los derechos reservados. Este código no está publicado bajo una
licencia de código abierto; para reutilizarlo fuera de este proyecto,
contactar al autor.

## Descargo de responsabilidad

Este plugin consulta servicios públicos de terceros (SIRI /
`siri.snitcr.go.cr`, Registro Nacional de Costa Rica, y los demás nodos del
SNIT) que no están afiliados a este proyecto. Su disponibilidad, exactitud y
condiciones de uso son responsabilidad de esas entidades.

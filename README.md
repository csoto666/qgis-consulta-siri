# Consulta SIRI (plugin de QGIS)

Plugin de QGIS que agrega un botón de activar/desactivar en la barra de
herramientas. Con la herramienta activa, cada clic sobre el mapa consulta
(`GetFeatureInfo`) las capas WMS del **SIRI** (Sistema de Información del
Registro Inmobiliario de Costa Rica, `siri.snitcr.go.cr`) que estén
cargadas en el proyecto, muestra un resumen del predio encontrado, y lo
guarda —geometría y **todos** los campos que devuelve el servidor,
íntegros— en una capa temporal de memoria.

Nació como apoyo para el registro de proyectos forestales (RPF) del SINAC,
para poder confirmar y visualizar predios catastrados sin salir de QGIS.

## Por qué existe

- **Proyección**: las capas WMS del SIRI negocian en `EPSG:4326` aunque el
  proyecto esté en otro CRS (p. ej. `EPSG:8908`, CRTM05). El plugin
  transforma el punto de clic hacia 4326 antes de consultar, y transforma la
  geometría de la respuesta de vuelta al CRS del proyecto antes de
  guardarla. Sin este segundo paso, cada predio guardado queda con
  coordenadas de grados metidas en una capa de metros —mal ubicado, aunque
  la consulta en sí haya funcionado.
- **Campos íntegros**: el cuadro de diálogo en pantalla muestra solo un
  resumen (plano, finca, identifica, ubicación, área), pero la capa
  guardada conserva absolutamente todos los campos que trae la respuesta
  del servidor —sin filtrar ni resumir nada—, más 4 campos propios de
  procedencia (`_capa_origen`, `_fecha_consulta`, `_x_clic`, `_y_clic`).

## Requisitos

- QGIS 3.x
- Tener cargada en el proyecto al menos una capa WMS cuyo origen apunte a
  `siri.snitcr.go.cr` (por ejemplo las capas públicas `catastro` /
  `catastro_aldia` del visor SIRI). El plugin las detecta automáticamente
  por esa coincidencia en el origen de la capa, sin importar cómo las hayas
  nombrado en tu proyecto.

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

1. Cargá en tu proyecto de QGIS alguna capa WMS del SIRI.
2. Activá el botón **"Consulta SIRI"** de la barra de herramientas.
3. Hacé clic sobre un predio catastrado en el mapa: aparece un resumen y el
   predio queda guardado en la capa `"Consultas SIRI (temporal)"`.
4. Clic de nuevo en el botón (o cambiá de herramienta) para desactivar —
   vuelve sola a la herramienta que tenías antes.

## Licencia

Todos los derechos reservados. Este código no está publicado bajo una
licencia de código abierto; para reutilizarlo fuera de este proyecto,
contactar al autor.

## Descargo de responsabilidad

Este plugin consulta un servicio público de terceros (SIRI /
`siri.snitcr.go.cr`, Registro Nacional de Costa Rica) que no está afiliado
a este proyecto. Su disponibilidad, exactitud y condiciones de uso son
responsabilidad de esa entidad.

# Relay de alertas a issues privados

`relay.py` recibe webhooks v4 de Alertmanager en `POST /alerts`, abre un issue por fingerprint activo y lo cierra al recibir `resolved`. El destino predeterminado es `SergioMultimedios/medisur-cicd`. Antes de procesar cada notificacion, consulta los metadatos del repositorio y exige `private: true`. No crea etiquetas, comentarios, asignaciones ni repositorios.

## Archivos y ejecucion

- `relay.py`: servidor HTTP, cliente REST con `urllib` y estado SQLite.
- `Dockerfile`: imagen `python:3.14.10-slim`, UID/GID `10001:10001`, datos en `/data`.
- `test_relay.py`: pruebas locales con GitHub simulado; no necesitan credenciales.
- `compose.example.yaml`: fragmento para integrar en `infra/tools/compose.yaml`.
- `alertmanager.example.yaml`: receptor y ruta de ejemplo para Alertmanager.

El contenedor ejecuta `python /app/relay.py`. `GET /healthz` solo confirma que responde el proceso; no consulta GitHub ni confirma su disponibilidad. No existe una ruta HTTP para administrar el estado.

## Configuracion y permisos

| Parametro | Comportamiento |
| --- | --- |
| `GITHUB_REPOSITORY` | Destino fijo `owner/repo`; predeterminado `SergioMultimedios/medisur-cicd`. No acepta URL ni cambios desde el webhook. |
| `/run/secrets/github-token` | Archivo obligatorio para operaciones GitHub; se vuelve a leer por peticion REST para admitir rotacion. |
| `ALERT_RELAY_SECRET` | Bearer compartido; si tiene contenido, exige `Authorization: Bearer <secreto>`. |
| `ALERT_RELAY_SECRET_FILE` | Alternativa de archivo cuando la variable anterior esta vacia. Un archivo ausente o vacio impide iniciar. El ejemplo usa `/run/secrets/alert-relay-secret`. |
| `/data/alerts.sqlite3` | Persistencia de fingerprint, inicio, estado, numero de issue y marcador de reconciliacion. Se asocia a un solo repositorio. |

Sin las dos opciones de secreto, la autenticacion queda deshabilitada y aparece una advertencia. El ejemplo de Compose exige autenticacion mediante archivo. El secreto compartido admite caracteres ASCII sin espacios; conviene generar al menos 32 bytes aleatorios y conservar su representacion hexadecimal. No colocar valores reales en YAML, Git, capturas, URLs o comandos visibles en historiales.

El PAT de grano fino debe limitarse al repositorio privado indicado, con `Issues: read and write` y lectura de metadatos. Un PAT clasico para un repositorio privado normalmente requiere `repo`, un alcance mas amplio. La sesion CLI existente no se consulta ni se reutiliza automaticamente; sus permisos actuales no quedaron verificados. El servicio no necesita permisos de administracion ni de Actions. El cliente de produccion usa la API REST; las pruebas no ejecutan `gh` ni llaman a GitHub.

Los secretos de Compose son archivos montados, no un almacen cifrado. El token GitHub debe ser legible por UID 10001 y no por otros servicios. El secreto compartido debe ser legible por UID 10001 y por el UID real de Alertmanager: preparar grupo/ACL del host o dos archivos con el mismo valor y permisos especificos. No confiar en que `uid/gid/mode` de secretos Compose remapeen archivos del host. Un volumen nombrado nuevo hereda `/data` con propietario 10001; un bind mount o volumen preexistente requiere propietario 10001 y modo 0700.

## Integracion con el stack

El fragmento Compose esta escrito para incorporarse al archivo de `infra/tools`, no para ejecutarlo aisladamente desde este directorio. No publica puertos. `alert_internal` es una red `internal: true` para recibir alertas. `relay_egress`, exclusiva del relay, permite salida a GitHub: una unica red `internal: true` impediria esta salida. El puerto HTTP sigue sin publicarse en el host; los administradores del host conservan acceso. No conectar servicios ajenos a estas redes ni publicar 8080 mediante proxy, balanceador o reglas de host.

Agregar al servicio Alertmanager existente la red `alert_internal` y el secreto, conservando sus redes y volumenes actuales:

```yaml
services:
  alertmanager:
    networks: [default, alert_internal]
    secrets:
      - source: alert_relay_secret
        target: alert-relay-secret
```

Montar la configuracion integrada de Alertmanager en la ruta que use su argumento `--config.file`. Incorporar el receptor `private-github` y la ruta de `alertmanager.example.yaml` sin sustituir otros receptores. `http_config.authorization.credentials_file` lee el valor del secreto y construye el encabezado Bearer; el archivo contiene solo el valor, no el prefijo `Bearer `. Prometheus debe apuntar a ese Alertmanager mediante su configuracion `alerting.alertmanagers`. Este modulo no modifica dicha configuracion ni crea un segundo Alertmanager.

`send_resolved: true` es necesario para cerrar issues. `max_alerts: 0` evita truncar silenciosamente el lote; el receptor rechaza `truncatedAlerts` distinto de cero. Los grupos deben mantenerse por debajo de 100 alertas y 256 KiB mediante `group_by`; grupos mas grandes reciben 400/413 y necesitan reparticion, no reintentos. El timeout de webhook de ejemplo es 60 segundos. Un lote grande puede excederlo; los efectos confirmados permanecen en SQLite y el reintento continua sin repetirlos.

La red de salida no implementa una lista de destinos permitidos a nivel firewall. El codigo fija `https://api.github.com`, deshabilita proxies de entorno y rechaza redirecciones; no sigue `Link`, `generatorURL` ni `externalURL` del webhook. El trafico interno usa HTTP y presupone una VM/red Docker de confianza, no una red multiusuario hostil.

## Estado, reintentos y datos enviados

Un lock serializa las notificaciones y SQLite confirma cada transicion con WAL y `synchronous=FULL`. Un fingerprint activo conserva su issue aunque cambie `startsAt`; una resolucion mas antigua no cierra un episodio mas reciente. Tras resolver, el mismo inicio no vuelve a abrirse; un inicio posterior crea otro issue. Una resolucion sin apertura previa guarda una marca de resuelto, sin crear un issue.

Antes de crear se persiste `pending` con un marcador aleatorio incluido en el cuerpo. Si GitHub confirma el numero, pasa a `active`. Un rechazo explicito de creacion revierte el estado pendiente para permitir otro intento. Un timeout, una respuesta 5xx o una respuesta de exito sin numero conservan `pending`: el siguiente intento busca el marcador en issues abiertos y cerrados, sin repetir ciegamente el POST. La reconciliacion consulta hasta diez paginas de cien issues y excluye pull requests.

GitHub y SQLite no comparten transaccion: no existe garantia distribuida de exactamente una entrega. Si un POST no llego a GitHub pero su resultado quedo incierto, el estado permanece pendiente y devuelve 503 hasta intervencion. Antes de modificar una fila pendiente, detener el unico relay, respaldar SQLite y verificar manualmente en el repositorio si existe el marcador. Si existe, conservar el numero; si se confirma que no existe, retirar solo esa fila pendiente permite que el reintento cree el issue. No borrar el volumen completo: se perderian deduplicacion y resoluciones historicas. Un issue fuera de las primeras 1000 entradas tambien requiere reconciliacion manual.

Las operaciones GET y PATCH tienen hasta tres intentos con timeout de socket de diez segundos y pausas de 0.25 y 0.5 segundos. POST tiene un solo intento para evitar duplicados. Los fallos REST, SQLite o de contencion responden 503 con `Retry-After: 15`; Alertmanager aplica su politica de reintento. El relay no duerme durante periodos largos de rate limit. El servidor limita a 16 conexiones atendidas concurrentemente, cuerpo de 262144 bytes y 100 alertas por notificacion. Rechaza cuerpos chunked/comprimidos y valida todo el lote antes de generar efectos.

El issue incluye `startsAt`, etiquetas y marcador. Los valores se acotan, se escapan como Markdown/HTML y se eliminan controles, menciones activas, URLs HTTP(S) y formatos reconocibles de tokens GitHub. Las etiquetas con nombres asociados a credenciales se redactan; annotations, generatorURL y externalURL no se envian. Esto no es un detector universal de secretos: las etiquetas de Prometheus deben excluir contrasenas, datos clinicos e identificadores sensibles. Los logs solo contienen mensajes fijos; no registran cuerpo, ruta, cabeceras, tokens ni excepciones remotas.

## Pruebas y limites pendientes

Desde PowerShell, las pruebas se ejecutan sin crear bytecode:

```powershell
python -B -m unittest discover -s "C:\Users\serch\OneDrive\Escritorio\Computo en la nube\medisur-cicd\infra\alert-relay" -p "test_*.py" -v
```

La suite usa sockets loopback temporales para probar HTTP y mocks para GitHub. Cubre duplicados, reinicio, resolucion, episodios antiguos, concurrencia, creaciones fallidas/inciertas, autenticacion, sanitizacion y limites. No verifica permisos reales del PAT, privacidad real del repositorio, entrega desde el stack, disponibilidad del tag Docker ni conectividad de produccion.

El despliegue admite un solo proceso/replica con un volumen persistente local, no varias replicas ni SQLite sobre almacenamiento de red. Las marcas de resuelto no se purgan automaticamente. Un cierre manual del issue mientras la alerta sigue activa no provoca reapertura. La perdida de una notificacion de resolucion no se compensa mediante consultas a Prometheus. Un cambio de visibilidad entre la comprobacion de privacidad y la escritura sigue siendo una carrera externa; la administracion del repositorio debe mantenerlo privado.
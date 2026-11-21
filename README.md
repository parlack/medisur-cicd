# MediSur CI/CD

API Flask con SQLite, pruebas automatizadas, imagen Docker publicada en GHCR, pipeline Jenkins, despliegue en MicroK8s y monitoreo con Prometheus y Grafana sobre dos máquinas virtuales de Azure.

Este archivo es el documento de operación: explica cómo ejecutar la aplicación y las pruebas, cómo encender y apagar el laboratorio, cómo acceder a las interfaces administrativas y qué se ha verificado del comportamiento del sistema. La descripción completa de arquitectura, inventario, CMDB, riesgos y declaración de uso de IA está en el [reporte](docs/reporte.md), con copia en [reporte.docx](docs/reporte.docx).

El repositorio https://github.com/SergioMultimedios/medisur-cicd es público. El paquete de imágenes en GHCR permanece privado, por lo que el clúster necesita el secreto de pull `ghcr-pull` para descargar la imagen.

## Componentes

| Componente | Ubicación | Función |
| --- | --- | --- |
| Aplicación Flask | `app/` | API HTTP con persistencia SQLite sobre volumen |
| Pruebas | `tests/`, `infra/*/test_*.py`, `k8s/ci/test-deploy.sh` | Pruebas de aplicación, configuración y scripts |
| Imagen | `Dockerfile` | Construcción en dos etapas, ejecución como UID/GID 10001 |
| Pipeline | `Jenkinsfile`, `ci/` | Siete etapas: checkout, pruebas, Sonar, gate, imagen, publicación y despliegue |
| Manifiestos | `k8s/` | Deployment, Service, ConfigMap, RBAC y monitoreo |
| Scripts de despliegue | `scripts/deploy.sh`, `scripts/verify.sh` | Sustitución de placeholders, rollout y verificación de versión |
| Herramientas | `infra/tools/` | Compose con Jenkins, SonarQube, PostgreSQL, Prometheus y Grafana |
| Operación del laboratorio | `scripts/*.ps1`, `infra/azure/` | Encendido, apagado, túneles e inventario de Azure |

La aplicación expone `/health`, `/ready`, `/version`, `/metrics` y `GET`/`POST /api/items`. El POST acepta únicamente la forma `{"name":"insumo de prueba"}`. La API no implementa autenticación, así que solo deben usarse datos sintéticos.

## Ejecución local en Windows

Requiere Python 3.14 disponible en la ruta. Desde PowerShell, en la raíz del proyecto:

```powershell
$Project = 'C:\Users\serch\OneDrive\Escritorio\Computo en la nube\medisur-cicd'
Push-Location $Project
try {
    python -m venv tests/.venv
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo preparar el entorno virtual' }
    & ./tests/.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
    if ($LASTEXITCODE -ne 0) { throw 'Fallo la instalacion de dependencias' }
    & ./tests/.venv/Scripts/python.exe -m pytest
    if ($LASTEXITCODE -ne 0) { throw 'Las pruebas no terminaron correctamente' }
} finally {
    Pop-Location
}
```

La suite local reúne 99 pruebas de Python —aplicación, configuración de herramientas, relay de alertas, webhook y renovación de credenciales— y 10 escenarios con mocks para los scripts de despliegue, ejecutados mediante `k8s/ci/test-deploy.sh`.

Para levantar solo la API de desarrollo después de instalar las dependencias:

```powershell
$Project = 'C:\Users\serch\OneDrive\Escritorio\Computo en la nube\medisur-cicd'
$env:DATA_DIR = Join-Path $Project 'tests/local-data'
Push-Location $Project
try {
    & ./tests/.venv/Scripts/python.exe -m flask --app 'app:create_app()' run --host 127.0.0.1 --port 8000
} finally {
    Pop-Location
}
```

Se detiene con Ctrl+C. Desde otra terminal se consulta con `Invoke-RestMethod http://127.0.0.1:8000/health`. Gunicorn se usa dentro del contenedor Linux y no como servidor en Windows.

## Imagen Docker

Con Docker configurado para contenedores Linux, desde la raíz del proyecto:

```text
docker build --target runtime -t medisur-cicd:local .
docker run --rm --name medisur-local -p 127.0.0.1:8000:8000 -v medisur-local-data:/data medisur-cicd:local
```

El segundo comando crea o reutiliza un volumen nombrado, que sobrevive al borrado del contenedor. Sin argumentos de build la imagen queda con valores de versión de desarrollo, útiles para pruebas locales y no para acreditar una publicación.

## Pipeline

`Jenkinsfile` ejecuta pruebas, análisis Sonar, quality gate, construcción de imagen y publicación en GHCR, y despliega únicamente desde `main`. Requiere un ejecutor Linux con la etiqueta `medisur-ci` y Docker, Bash, kubectl, curl y jq disponibles. Usa las credenciales `ghcr`, `sonar-token`, `sonarqube-webhook-secret` y `medisur-kubeconfig`, además de la credencial de checkout. La instalación de SonarQube se llama `SonarQube` tanto en el `Jenkinsfile` como en la configuración JCasC. El gate exige cobertura mínima de 60 %.

El job `medisur-branches` cubre las ramas distintas de `main` con el regex `origin/(?!main$).*`, un proyecto Sonar separado y sin publicación ni despliegue. Está configurado mediante `scripts/configure-feature-ci.py`. Se verificó contra la rama `prueba-ramas`: el webhook disparó la ejecución con el push y las cuatro etapas —checkout, pruebas con cobertura, análisis de Sonar y puerta de calidad— terminaron en verde en 33 segundos.

Jenkins corre la versión 2.580.2 con 85 plugins fijados en `infra/tools/jenkins/plugins.txt`; el cotejo del controlador en ejecución los reporta activos y con las versiones exactas del manifiesto.

Para reproducir el despliegue en otro entorno, un administrador debe preparar el namespace, el PVC `medisur-data`, el secreto de pull `ghcr-pull`, el RBAC y la etiqueta `medisur.io/sqlite-node=true` en el nodo de datos. Revisar `k8s/bootstrap/` y `k8s/templates/` antes de aplicar. Los placeholders de `k8s/app/` no deben aplicarse de forma literal: `scripts/deploy.sh` los sustituye en tiempo de despliegue. Jenkins no debe recibir credenciales de administrador del clúster.

## Estado verificado

El último despliegue verificado corresponde al commit `ddb22beb209e6132e5b72b03b7e206e15265d6c2`, versión `0.1.0-ddb22beb`, con `build_date` `2026-11-19T20:12:03Z`. Lo produjo el build 5 de `medisur-main`, en estado `SUCCESS` tras 77 s, y dejó 2/2 réplicas listas con `/version` coincidente.

### Rollback automático

El rollback automático ante un rollout fallido está comprobado en vivo y registrado en [`docs/evidence/automatic-rollback.json`](docs/evidence/automatic-rollback.json). La prueba usó una etiqueta de imagen inexistente, de modo que el pod candidato quedó en `ImagePullBackOff`. `scripts/deploy.sh` salió con código 1 a los 182 s por agotamiento del rollout y su propio trap ejecutó el `kubectl rollout undo`, devolviendo el deployment al SHA que estaba sirviendo. El monitor tomó 36 comprobaciones HTTP, todas exitosas. Son muestras periódicas y no una prueba de disponibilidad ininterrumpida; el ensayo no incluyó escrituras ni rollback de datos en SQLite.

### Autorecuperación y rollback manual

[`docs/evidence/recovery-verified.json`](docs/evidence/recovery-verified.json) registra la recuperación de pods: tras borrar un pod, el deployment volvió a dos réplicas listas en 21.623 s, con 51 comprobaciones HTTP exitosas. El mismo archivo documenta un rollback manual completado en 46.206 s. La recuperación de datos permanece fuera de alcance: dos réplicas comparten un SQLite en un solo nodo, sin tolerancia a la caída de esa máquina virtual.

### Bloqueo por quality gate

El bloqueo del pipeline por quality gate se comprobó en el proyecto Sonar separado `medisur-quality-demo` y quedó en [`docs/quality-gate.json`](docs/quality-gate.json): con cobertura reducida a 37.7 % y las 35 pruebas aprobadas, el gate devolvió `ERROR`, el pipeline se abortó y no se publicó ni desplegó nada.

### Monitoreo

[`docs/live-validation.json`](docs/live-validation.json) recoge la validación del monitoreo: 7 targets de Prometheus saludables, 2 dashboards de Grafana registrados, 35 pruebas y 93.94 % de cobertura combinada en el proyecto principal. Los targets son Prometheus, dos exportadores de nodo, kube-state-metrics, cAdvisor y los dos pods de aplicación descubiertos individualmente. Los dashboards están en [`infra/monitoring/grafana/dashboards/`](infra/monitoring/grafana/dashboards/).

Alertmanager y el relay de alertas corren en el Compose separado `infra/alerting/compose.yaml`, sin puertos publicados, y Prometheus apunta a `alertmanager:9093`. Hay tres alertas operativas configuradas —disco, reinicios y réplicas— más dos reglas de salud del propio monitoreo. Los límites del relay están en su [runbook](infra/alert-relay/README.md).

## Runbook del laboratorio

### Encender

Con Azure CLI autenticado en la suscripción autorizada, OpenSSH disponible y las claves de host ya verificadas en `known_hosts`:

```powershell
$Project = 'C:\Users\serch\OneDrive\Escritorio\Computo en la nube\medisur-cicd'
& "$Project/scripts/start-lab.ps1" -Wait
& "$Project/scripts/open-tunnels.ps1"
```

`start-lab.ps1` enciende primero `LAB-03` y después `medisur-tools`. Con `-Wait` consulta SSH y los endpoints de salud sin autenticación; no recupera credenciales, no inicia contenedores y no acredita que el pipeline esté listo. Si falla, las máquinas virtuales quedan asignadas y siguen generando costo.

`open-tunnels.ps1` mantiene cinco túneles de loopback en primer plano. Rechaza la ejecución si algún puerto local está ocupado y nunca termina procesos existentes. Ctrl+C cierra solo esa sesión SSH, sin apagar las máquinas virtuales.

| Servicio | URL local | Destino |
| --- | --- | --- |
| Jenkins | http://localhost:18080 | 127.0.0.1:8080 |
| SonarQube | http://localhost:19000 | 127.0.0.1:9000 |
| Grafana | http://localhost:13000 | 127.0.0.1:3000 |
| Prometheus | http://localhost:19090 | 127.0.0.1:9090 |
| Aplicación privada | http://localhost:18000 | 172.16.0.4:30081 |

Jenkins y Grafana usan el usuario `medisur-admin`; SonarQube usa `admin`. Las contraseñas viven en `/etc/medisur/secrets/` dentro de la máquina virtual de herramientas y se consultan por SSH desde una terminal privada. Esa consulta no debe hacerse durante grabaciones, ni redirigirse a archivos del proyecto, ni pegarse en Git, chat o capturas. El repositorio no contiene contraseñas, tokens ni claves.

### Apagar

Al terminar el trabajo y sin builds en ejecución:

```powershell
& "$Project/scripts/stop-lab.ps1" -Confirm
```

Desasigna primero herramientas y después el clúster, interrumpe los servicios en curso y comprueba el estado `PowerState/deallocated`. No elimina discos, IP ni recursos; los discos y las IP reservadas siguen generando cargos mientras existan. Ambos scripts admiten `-WhatIf`, que de todos modos consulta la cuenta de Azure.

### Comprobar el clúster

Desde una sesión Linux autorizada, con conectividad privada y el kubeconfig correspondiente:

```bash
kubectl -n medisur get deployment,pods,pvc,services -o wide
kubectl -n medisur rollout status deployment/medisur --timeout=240s
curl --fail --silent --show-error http://172.16.0.4:30081/health
curl --fail --silent --show-error http://172.16.0.4:30081/ready
curl --fail --silent --show-error http://172.16.0.4:30081/version
```

La identidad del pipeline carece de permisos sobre el PVC; su consulta requiere una identidad operativa autorizada. Para cotejar una publicación, desde la raíz del checkout Linux: `bash scripts/verify.sh "$EXPECTED_SHA" "$EXPECTED_VERSION"`, con el SHA completo y la versión reales. `verify.sh` solo lee. `scripts/deploy.sh` modifica el clúster y debe ejecutarse mediante el flujo de publicación aprobado.

### Stack de herramientas

El checkout de la máquina virtual de herramientas vive en `/opt/medisur-cicd`. Para consultar los servicios:

```bash
cd /opt/medisur-cicd
sudo docker compose --env-file infra/tools/.env -f infra/tools/compose.yaml ps
```

La preparación inicial usa `infra/tools/setup-ubuntu.sh`, `.env.example`, `initialize-secrets.sh` y `start.sh`. Esos scripts instalan e inician componentes. Los secretos se guardan fuera del repositorio y no deben escribirse en `.env` versionado ni imprimirse en registros compartidos.

Jenkins usa el socket de Docker, lo que equivale a privilegios de root en esa máquina virtual; no debe ejecutar código no confiable. El monitoreo mediante API proxy tiene permisos más amplios que la simple lectura de métricas. Ambos riesgos están detallados en el [reporte](docs/reporte.md#10-decisiones-desviaciones-y-riesgos) y en [`docs/jenkins-security.md`](docs/jenkins-security.md).

### Renovación de credenciales

La renovación del kubeconfig está instalada como timer de systemd en `medisur-tools`, con ejecución horaria y recuperación al arranque. Emite tokens de 24 horas, actualiza la credencial Jenkins `medisur-kubeconfig` sin modificar jobs y conserva los archivos que monta Prometheus. La conexión SSH al clúster usa un comando forzado. Para diagnóstico:

```bash
sudo systemctl status medisur-credentials.timer --no-pager
sudo systemctl show medisur-credentials.service -p Result -p ExecMainStatus
sudo systemctl list-timers medisur-credentials.timer --no-pager
```

Las unidades y utilidades están en [`infra/credentials/`](infra/credentials/). El servicio es `oneshot`: queda inactivo después de `Result=success`, lo cual es el comportamiento esperado. `provision.py` instala componentes y `verify-live.py` fuerza una renovación; ninguno es de solo lectura.

### Diagnóstico frecuente

Ante pods que no alcanzan readiness, revisar eventos, referencia de imagen, sonda de readiness, permisos del volumen y espacio en disco. Ante fallos de scrape, revisar token, CA, RBAC y conectividad. El PVC y los volúmenes de Compose no deben borrarse como intento de reparación.

## Exposición y protección de ramas

El único endpoint público es **https://medisur-sergio-cicd.eastus.cloudapp.azure.com/github-webhook/**, reservado para el webhook de GitHub. nginx 1.18 de Ubuntu sirve HTTPS con un certificado vigente hasta el 17/02/2027. Una firma HMAC incorrecta devuelve 401 y las rutas `/admin` y `/login` devuelven 404. Las interfaces administrativas se ligan a localhost en la máquina virtual y solo se alcanzan por los túneles SSH descritos arriba.

La rama `main` está protegida con la configuración de [`infra/github/branch-protection.json`](infra/github/branch-protection.json):

- Pull Request obligatorio con 1 revisión aprobatoria.
- Aprobaciones obsoletas descartadas al llegar nuevos commits.
- Force push y borrado de rama bloqueados.
- Historial lineal obligatorio.
- Conversaciones resueltas antes de fusionar.

La regla no aplica a administradores en su configuración actual (`enforce_admins: false`).

## Costos y apagado diario

El grupo de recursos `LAB-03` tiene un presupuesto aprobado de 50 USD mensuales, con avisos por correo al 80 % y al 100 % del monto. Ambas máquinas virtuales se apagan automáticamente a las 05:00 UTC, equivalente a las 23:00 del día anterior en el centro de México. El presupuesto emite avisos y no corta el gasto; detener la aplicación tampoco desasigna una máquina virtual.

`infra/azure/provision.ps1` realiza el inventario de Azure en modo de solo lectura con una sesión `az login` existente, y solo crea recursos faltantes del laboratorio cuando se le pasa `-Create`. No reemplaza recursos existentes, no cambia el sistema operativo ni los discos de `LAB-03` y detiene la ejecución ante configuración conflictiva para revisión manual. Los datos de Cost Management que devuelve son cargos reportados con latencia de facturación. El grupo `LAB-03` no debe eliminarse: contiene infraestructura preexistente.

## Estructura del repositorio

```text
app/                      Aplicación Flask y configuración de Gunicorn
tests/                    Pruebas de la aplicación y conftest
ci/                       Jenkinsfiles auxiliares para demostraciones aisladas
k8s/app/                  Deployment, Service, ConfigMap y kustomization
k8s/bootstrap/            Namespace y RBAC del desplegador
k8s/ci/                   Imagen del ejecutor y pruebas del script de despliegue
k8s/monitoring/           kube-state-metrics, RBAC y renovación de token
k8s/templates/            Plantilla del secreto de pull de GHCR
infra/tools/              Compose de Jenkins, Sonar, PostgreSQL, Prometheus y Grafana
infra/monitoring/         Reglas de Prometheus y dashboards de Grafana
infra/alerting/           Alertmanager y utilidades de validación
infra/alert-relay/        Relay de alertas hacia issues de GitHub
infra/credentials/        Unidades systemd y utilidades de renovación
infra/webhook/            Servidor del webhook, nginx y unidad systemd
infra/azure/              Inventario, presupuesto y consulta de costos
infra/github/             Configuración de protección de rama
scripts/                  Despliegue, verificación y operación del laboratorio
docs/                     Reporte, diagramas y evidencias
```

## Documentación y evidencias

- [`docs/reporte.md`](docs/reporte.md) y [`docs/reporte.docx`](docs/reporte.docx): reporte de trece secciones.
- [`docs/architecture.png`](docs/architecture.png) y [`docs/architecture.drawio`](docs/architecture.drawio): diagrama de arquitectura y su fuente editable.
- [`docs/evidence/automatic-rollback.json`](docs/evidence/automatic-rollback.json): rollback automático ante rollout fallido.
- [`docs/evidence/recovery-verified.json`](docs/evidence/recovery-verified.json): recuperación de pod y rollback manual.
- [`docs/quality-gate.json`](docs/quality-gate.json): bloqueo del pipeline por quality gate.
- [`docs/live-validation.json`](docs/live-validation.json): validación de pruebas, Sonar y monitoreo.
- [`docs/evidence/`](docs/evidence/): capturas de Jenkins, SonarQube y Grafana.

La documentación y la preparación se asistieron con Cortex Code, modelo `claude-opus-5`. La [declaración de uso de IA](docs/reporte.md#8-declaración-de-uso-de-inteligencia-artificial) delimita esa asistencia frente a la revisión humana.

## Pendientes

- Video de respaldo de 10 a 15 minutos.
- Confirmación docente del disco de 30 GB del clúster y de Ubuntu 24.04.
- Conciliación final de costos, sujeta a la latencia de facturación de Azure.

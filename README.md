# MediSur CI/CD

Piloto escolar de API Flask con SQLite, pruebas, imagen Docker, pipeline Jenkins, MicroK8s y monitoreo en Azure. El despliegue, la validación automatizada y el rollback automático ante un rollout fallido están demostrados; la entrega escolar sigue abierta. El [reporte de trece secciones](docs/reporte.md) contiene arquitectura, inventario, CMDB, riesgos, uso de IA y pendientes.

## Estado comprobable

Al corte del 19/11/2026 el despliegue actual es el **build 4 de `medisur-main`, `SUCCESS` en 76 s**, disparado por el webhook tras empujar el commit `7c42b65a94e87f2e8c115c91d702226bd1ee1da7` a `main` privado. El `/version` desplegado devuelve ese `git_sha`, versión `0.1.0-7c42b65a94e8` —la convención es `0.1.0-` más los doce primeros caracteres del SHA— y `build_date` `2026-11-19T20:01:18Z`, con **2/2 réplicas listas**. De ese build conservo resultado y duración; el desglose por etapas, pruebas y Sonar que sigue corresponde al build 3.

Los builds 3 y 2 pasan a ser historia. El 3 corrió de `18:17:25.309` a `18:19:17.046` UTC con las siete etapas y las acciones posteriores en `SUCCESS`, desplegó el SHA `f04534bb54d0a16472ea97009fcebc409e43fb8c` desde la imagen privada `ghcr.io/sergiomultimedios/medisur-cicd:0.1.0-f04534bb54d0` y dejó dos réplicas listas con `/version` coincidente. El 2, de 18:13:40 a 18:15:05 UTC, fue el primer éxito disparado por un push real de GitHub.

La evidencia del build 3 registra 35 pruebas aprobadas, sin fallos ni omisiones. Sonar muestra cobertura 93.9 %, bugs 0, vulnerabilidades 0 y quality gate `OK`; conserva un code smell, por lo que no equivale a cero hallazgos. Los XML locales distinguen 95.92 % de líneas, 77.78 % de ramas y 93.94 % combinado: son artefactos separados.

La verificación local comunicada por el flujo principal suma **99 pruebas Python aprobadas**: 35 de aplicación, 8 de configuración de herramientas, 39 del relay, 9 del webhook y 8 de credenciales; además, **10 pruebas shell con mocks aprobadas**. Son 109 casos locales, no 109 pruebas del build Jenkins ni ensayos en vivo. No se volvieron a ejecutar durante esta edición.

La [recuperación verificada](docs/evidence/recovery-verified.json) terminó `passed`: borrar un pod mediante kubectl local recuperó dos réplicas listas en **21.623 s**; el monitor obtuvo **51 solicitudes HTTP exitosas y 0 fallidas**. El rollback manual tardó **46.206 s**, volvió al SHA `6fc0a30fbeba3e19127b868abbcf28d3a3854fff` como revisión 3 y después restituyó `f04534bb54d0a16472ea97009fcebc409e43fb8c` como revisión 4, con dos réplicas listas. No demuestra rollback de datos. La activación automática se demostró por separado.

El [rollback automático](docs/evidence/automatic-rollback.json) ya tiene prueba en vivo. El job aislado `medisur-rollout-demo`, build 1, terminó `SUCCESS` a las `19:57:46` UTC; ese éxito significa que la prueba negativa pasó, es decir que `scripts/deploy.sh` salió con código distinto de cero. Se ejecutó el `deploy.sh` comprometido sin modificarlo, desde Jenkins y con el kubeconfig limitado al namespace, y el propio job verificó dentro los sha256 de `deploy.sh`, `verify.sh` y los cuatro manifiestos de `k8s/app/`. El hash de la configuración del job real `medisur-main` fue `d620e39b55ee81e7773deace81a3d2247c16ab83fb9bf1178485851009ceed42` antes y después: el pipeline de producción no se tocó.

La imagen candidata `ghcr.io/sergiomultimedios/medisur-cicd:0.1.0-deadbeefdead` no existe, así que el pod nuevo quedó en `ImagePullBackOff`. `deploy.sh` salió 1 a los **182 s** por rollout agotado y su trap ejecutó `kubectl rollout undo`: la revisión pasó de **4 a 6**, el SHA final volvió a ser el inicial `f04534bb54d0a16472ea97009fcebc409e43fb8c` y `/version` coincidió. El monitor tomó **36 muestras HTTP, 0 fallidas**, y en ninguna bajó de dos pods viejos listos. Es muestreo, no prueba de disponibilidad ininterrumpida, y no hubo escrituras ni rollback de SQLite. Queda una discrepancia que no reconcilio: el JSON marca `original_pods_preserved: false` mientras que los dos pods originales, de unos 90 minutos de edad, eran los que seguían sirviendo al final. Ese indicador es más estricto que la identidad de pods observada, porque el pod candidato aún figuraba en la lista final en estado terminating. Reporto ambos campos tal como están. Este ensayo ocurrió antes del build 4: su SHA final ya no es el desplegado hoy.

El primer intento de esta prueba falló y descubrió un defecto real del repositorio. El SHA candidato de entonces era todo dígitos; al sustituirse sin comillas, el valor se interpretó como número YAML y el API server rechazó el apply con `unrecognized type: string`. El dry-run de servidor de `deploy.sh` lo atrapó, el script salió 1 y no cambió nada: ambas réplicas siguieron en pie. La corrección va en la sustitución, no en el manifiesto fuente, porque kustomize quita las comillas de origen. Quedó verificada parseando el YAML renderizado: `git-sha`, la anotación de versión y la etiqueta de versión salen como `str`.

[`docs/live-validation.json`](docs/live-validation.json), recolectado de `18:20:10.624927` a `18:20:11.145892` UTC, registra `success: true`, sin errores ni comprobaciones fallidas: pruebas, etapas, Sonar, siete targets saludables, CPU y memoria de ambos pods de ese momento y dos dashboards. El defecto previo del parser JUnit y la falta inicial de muestras CPU quedaron superados en esta captura. Es anterior al ensayo de recuperación, a la prueba de rollback automático y al build 4: no describe los pods ni la versión actuales. Sonar y Prometheus siguen siendo una instantánea, no una correlación automática con el build.

La [demostración negativa del gate](docs/quality-gate.json), recogida a las `18:18:30.797922` UTC, registra el build 2 de `medisur-quality-demo` en `FAILURE`: las 35 pruebas y el análisis Sonar pasaron, pero la cobertura deliberadamente reducida a 37.7 % incumplió el mínimo de 60 % y el gate devolvió `ERROR`. `Would publish` quedó omitida según la verificación comunicada; la API de etapas la representa como `FAILED` por propagación del aborto, no con el literal `SKIPPED`. El ensayo aislado no publicó ni desplegó.

Jenkins está actualizado a **2.580.2**. El cotejo del controlador en ejecución comunicado a las 18:15 UTC confirmó los **85/85 plugins**, activos, habilitados y con versiones exactas al manifiesto, sin discrepancias. La versión 2.528.3 es histórica. El repositorio https://github.com/SergioMultimedios/medisur-cicd y GHCR son privados; el push ya ocurrió. La protección de `main` sigue bloqueada por HTTP 403 —GitHub pide Pro o repositorio público para esa configuración—, sin protección efectiva ni dispensa acreditada.

## Ejecución local en Windows

Requisito: Python 3.14 disponible. Desde PowerShell, estos comandos preparan el entorno y ejecutan las pruebas; instalan dependencias y generan artefactos locales. Son instrucciones para reproducir, no comandos ejecutados durante esta redacción.

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

Para iniciar solo la API de desarrollo, después de una instalación satisfactoria:

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

Detener con Ctrl+C. Desde otra terminal: `Invoke-RestMethod http://127.0.0.1:8000/health`. También existen `/ready`, `/version`, `/metrics` y `GET/POST /api/items`; el POST admite únicamente `{"name":"insumo de prueba"}`. Usar datos sintéticos: la API no implementa autenticación. Gunicorn se utiliza en el contenedor Linux, no como servidor Windows.

## Imagen y despliegue

El `Dockerfile` tiene dos etapas y ejecuta como UID/GID 10001. Con Docker para contenedores Linux disponible, desde la raíz local:

```text
docker build --target runtime -t medisur-cicd:local .
docker run --rm --name medisur-local -p 127.0.0.1:8000:8000 -v medisur-local-data:/data medisur-cicd:local
```

El segundo comando crea o reutiliza un volumen nombrado; no lo borra al detener el contenedor. La versión local queda en valores de desarrollo si no se pasan argumentos de build. No usarlo como evidencia de versión publicada.

`Jenkinsfile` ejecuta pruebas, análisis Sonar, quality gate, imagen GHCR y despliegue solo para `main`. Requiere ejecutor Linux `medisur-ci`, Docker, Bash, kubectl, curl y jq; credenciales de checkout privado, GHCR, Sonar, webhook Sonar y kubeconfig restringido. Los IDs incluyen `ghcr`, `sonar-token`, `sonarqube-webhook-secret` y `medisur-kubeconfig`. El nombre de instalación `SonarQube` ya coincide en Jenkinsfile y JCasC; el gate observado exige cobertura de al menos 60 %.

Existe además el job `medisur-branches` para ramas distintas de `main`, con el regex `origin/(?!main$).*`, proyecto Sonar separado `medisur-feature-ci` y sin publicación ni despliegue. Está configurado y ningún push real de rama lo ha ejercitado todavía.

Para reproducir el despliegue en otro entorno, un administrador debe preparar namespace, PVC `medisur-data`, secreto de pull `ghcr-pull`, RBAC y la etiqueta `medisur.io/sqlite-node=true` en el único nodo de datos. Revisar `k8s/bootstrap/` y `k8s/templates/` antes de aplicar. No aplicar literalmente los placeholders de `k8s/app/`: `scripts/deploy.sh` los sustituye. Jenkins no debe recibir credenciales de administrador del clúster.

## Runbook breve

Para encender el laboratorio desde PowerShell, con Azure CLI autenticado en la suscripción autorizada, OpenSSH y las claves de host verificadas en `known_hosts`:

```powershell
$Project = 'C:\Users\serch\OneDrive\Escritorio\Computo en la nube\medisur-cicd'
& "$Project/scripts/start-lab.ps1" -Wait
& "$Project/scripts/open-tunnels.ps1"
```

`start-lab.ps1` enciende primero `LAB-03` y después `medisur-tools`; `-Wait` consulta SSH y salud HTTP, no valida builds, métricas ni credenciales y no inicia contenedores por su cuenta. Si falla, las VM quedan asignadas. `open-tunnels.ps1` mantiene cinco túneles en primer plano: Ctrl+C cierra solo esa sesión, sin apagar las VM. Rechaza puertos ocupados sin cerrar procesos existentes. Las direcciones de acceso y usuarios se detallan más abajo; abrir un túnel no acredita que el servicio esté listo.

Al terminar el trabajo y sin builds en ejecución, desasignar ambas VM con `& "$Project/scripts/stop-lab.ps1" -Confirm`. Detiene primero herramientas y luego clúster, interrumpe servicios y comprueba `PowerState/deallocated`; no elimina discos, IP ni recursos. Los discos y las IP pueden seguir generando cargos. Inicio y parada admiten `-WhatIf`, aunque todavía consultan la cuenta Azure. Estas son instrucciones, no operaciones ejecutadas en esta edición.

Ejecutar las comprobaciones desde una sesión Linux autorizada con conectividad privada y el kubeconfig correspondiente:

```bash
kubectl -n medisur get deployment,pods,pvc,services -o wide
kubectl -n medisur rollout status deployment/medisur --timeout=240s
curl --fail --silent --show-error http://172.16.0.4:30081/health
curl --fail --silent --show-error http://172.16.0.4:30081/ready
curl --fail --silent --show-error http://172.16.0.4:30081/version
```

La identidad de pipeline no tiene permisos sobre PVC; su consulta requiere una identidad operativa autorizada. Para cotejar una publicación, desde la raíz del checkout Linux usar `bash scripts/verify.sh "$EXPECTED_SHA" "$EXPECTED_VERSION"`, con SHA completo y versión reales. Este script no crea datos. `scripts/deploy.sh` sí modifica el clúster y debe ejecutarse mediante el flujo de publicación aprobado; su rollback no restaura SQLite.

En la VM de herramientas, desde la raíz del checkout, `sudo docker compose --env-file infra/tools/.env -f infra/tools/compose.yaml ps` consulta los servicios. Para preparación revisar `infra/tools/setup-ubuntu.sh`, `.env.example`, `initialize-secrets.sh` y `start.sh`. No son comandos de solo diagnóstico: instalan o inician componentes. Los secretos se guardan fuera del repositorio; no imprimirlos en registros compartidos ni introducirlos en `.env`.

La renovación Kubernetes está instalada en `medisur-tools`: `medisur-credentials.timer` está habilitado y activo, con ejecución horaria y recuperación al arranque. La última renovación comunicada fue `2026-11-19T18:17:09.198913Z`, con tokens de 24 horas y ocho pruebas focalizadas aprobadas. Actualiza la credencial Jenkins `medisur-kubeconfig` sin cambiar jobs y conserva los archivos montados por Prometheus. La conexión SSH al clúster usa un comando forzado, no una sesión arbitraria. Para diagnóstico, desde la VM de herramientas:

```bash
sudo systemctl status medisur-credentials.timer --no-pager
sudo systemctl show medisur-credentials.service -p Result -p ExecMainStatus
sudo systemctl list-timers medisur-credentials.timer --no-pager
```

Revisar las unidades y utilidades en [`infra/credentials/`](infra/credentials/). El servicio es `oneshot`: quedar inactivo después de `Result=success` es normal. `provision.py` instala componentes y `verify-live.py` fuerza una renovación; no son consultas de solo lectura.

El endpoint público es **https://medisur-sergio-cicd.eastus.cloudapp.azure.com/github-webhook/**, exclusivamente para el webhook; no es la aplicación ni una interfaz administrativa. nginx 1.18 de Ubuntu sirve HTTPS con certificado que vence el 17/02/2027. La prueba de HMAC incorrecto devolvió 401; las rutas administrativas `/admin` y `/login`, 404. GitHub entregó ping y push reales con HTTP 200 mediante el hook `714127126`.

Las interfaces administrativas se ligan a localhost en la VM. Los túneles SSH están activos al corte: Jenkins http://localhost:18080, Sonar http://localhost:19000, Grafana http://localhost:13000, Prometheus http://localhost:19090 y aplicación privada http://localhost:18000. Para volver a abrirlos cuando no estén activos, ejecutar en PowerShell y mantener la terminal abierta:

```powershell
ssh -N -o ExitOnForwardFailure=yes -i 'C:\Users\serch\OneDrive\Escritorio\Computo en la nube\LAB-03_key.pem' -L 127.0.0.1:18080:127.0.0.1:8080 -L 127.0.0.1:19000:127.0.0.1:9000 -L 127.0.0.1:13000:127.0.0.1:3000 -L 127.0.0.1:19090:127.0.0.1:9090 -L 127.0.0.1:18000:172.16.0.4:30081 username@medisur-sergio-cicd.eastus.cloudapp.azure.com
```

El usuario SSH es `username`; la clave corresponde a `../LAB-03_key.pem` respecto del proyecto y queda fuera del repositorio. Jenkins y Grafana usan `medisur-admin`; Sonar usa `admin`. El propietario puede consultar su contraseña en una terminal local privada por SSH: `ssh -i 'C:\Users\serch\OneDrive\Escritorio\Computo en la nube\LAB-03_key.pem' username@medisur-sergio-cicd.eastus.cloudapp.azure.com 'sudo cat /etc/medisur/secrets/jenkins_admin_password'`. Para Sonar o Grafana, sustituir solo el nombre por `sonar_admin_password` o `grafana_admin_password`. No ejecutar esa consulta durante grabaciones, no redirigirla a archivos del proyecto ni pegar su salida en Git, chat o capturas. Aquí no se incluyen contraseñas reales.

Hay cinco servicios centrales de herramientas —Jenkins, SonarQube, PostgreSQL, Prometheus y Grafana— más node-exporter. Prometheus registró siete targets `up`: él mismo, dos exportadores de nodo, kube-state-metrics, cAdvisor y dos pods de aplicación descubiertos individualmente. El nodo Kubernetes usa el paquete Ubuntu `prometheus-node-exporter` **1.7.0-1ubuntu0.3**; herramientas usa **1.10.2**. Los dos [dashboards](infra/monitoring/grafana/dashboards/) están registrados y la captura contiene tasas, CPU y memoria de ambos pods activos a las 18:20 UTC. También conserva series de la revisión anterior; no indican cuatro réplicas activas ni constituyen una prueba de carga.

Alertmanager y relay están saludables en el Compose separado `infra/alerting/compose.yaml`, sin puertos publicados; Prometheus ya apunta a `alertmanager:9093`. La prueba sintética abrió y cerró automáticamente el [issue privado #1](https://github.com/SergioMultimedios/medisur-cicd/issues/1), identificado como `TEST`. Las tres alertas operativas de disco, reinicios y réplicas están configuradas, además de dos reglas de salud del monitoreo; no son incidentes reales observados. Consultar el [runbook del relay](infra/alert-relay/README.md) para sus límites. Ni estas interfaces, ni los avisos configurados, ni el presupuesto y los apagados requieren entrar al portal de Azure para su uso habitual.

Ante pods no listos, revisar eventos, imagen, readiness, permisos del volumen y espacio. Ante fallo del scrape, revisar token, CA, RBAC y conectividad. No borrar el PVC ni volúmenes Compose para intentar reparar el servicio. Dos réplicas comparten SQLite en un nodo: no hay tolerancia a la caída de esa VM ni backup demostrado.

Jenkins usa el socket Docker, con privilegios equivalentes a root en la VM; no ejecutar código no confiable. El monitoreo mediante API proxy tiene permisos más amplios que leer métricas. Estos riesgos y el cierre de evidencias están detallados en el [reporte](docs/reporte.md#10-decisiones-desviaciones-y-riesgos).

## Costos, cierre y autoría

Se creó el presupuesto aprobado de 50 USD mensuales para `LAB-03`, con avisos por correo al 80/100 % y apagado de ambas VM a las 05:00 UTC (23:00 del día anterior en el centro de México). La consulta Azure del 19/11/2026 reporta gasto acumulado del mes: discos **3.2632853475 USD** + IP **2.22 USD** = **5.4832853475 USD**. Es gasto reportado, no saldo de créditos ni costo final: aún no incluye cargos recientes de herramientas por latencia de facturación. La tarifa de 0.192 USD/h de D4s_v4 es solo referencia. El presupuesto no es un límite duro; detener la aplicación no desasigna una VM. No eliminar `LAB-03`, que contiene infraestructura preexistente.

Documentación y preparación asistidas con Cortex Code, modelo `openai-gpt-6-astra`; no se atribuye al estudiante ejecución manual del trabajo del asistente. La [declaración de IA](docs/reporte.md#8-declaración-de-uso-de-inteligencia-artificial) separa esa asistencia de la revisión humana pendiente.

Ya existen [reporte.docx](docs/reporte.docx), [architecture.png](docs/architecture.png) y el [diagrama editable](docs/architecture.drawio). El Mermaid del reporte refleja las integraciones configuradas. La actualización de PNG/drawio y la regeneración del DOCX corresponden al flujo principal; sus copias existentes no certifican esta revisión. Esta edición solo cambia los dos Markdown.

En `docs/evidence/` hay siete capturas reales de Jenkins, Sonar y Grafana; su existencia no acredita revisión visual, saneamiento ni cierre de la captura de evidencias. Word sigue pendiente de regenerar con este contenido.

El [primer ensayo](docs/evidence/recovery.json) conserva el estado histórico `blocked`: agotó el acceso local a la API y no hizo mutaciones. Según la verificación comunicada por el flujo principal, se actualizó la regla NSG para permitir la IP del operador y el acceso local funcionó. El [ensayo posterior aprobado](docs/evidence/recovery-verified.json), de `18:23:11.242544` a `18:27:03.766555` UTC, realizó las mutaciones mediante **kubectl local, no por SSH**; cierra recuperación de pod y rollback manual. El monitor tenía un máximo de 200 solicitudes y 120 s: por duración y latencia de red obtuvo 51 muestras, no 200. Sus cero fallos describen solo ese muestreo por túnel, no disponibilidad universal ni continuidad HTTP durante todo el rollback. El rollback automático dejó de ser pendiente: la [prueba negativa aislada](docs/evidence/automatic-rollback.json) lo activó ante un rollout fallido real y quedó registrada arriba. Persistencia y restauración de datos siguen pendientes de demostración.

Para la entrega faltan video de 10 a 15 minutos, nombres y roles, revisión humana y aprobación docente de las desviaciones: disco de 30 GB (no 32) frente a 64 GB, Ubuntu 24.04 frente a 22.04 y tamaños D2as_v4/D4s_v4 en lugar de serie B. La protección de `main` permanece sin resolver por el HTTP 403, que exige GitHub Pro o repositorio público. Tampoco está ejercitado el primer run real del job de ramas ni conciliado el costo final por latencia de facturación. El [reporte](docs/reporte.md#b-todo-documental-para-el-cierre-del-despliegue) conserva el listado de cierre; el éxito técnico no equivale a entrega escolar completa.
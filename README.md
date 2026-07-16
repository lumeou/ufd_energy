# pvpc_energy
![UFD logo](https://github.com/lumeou/ufd_energy/raw/main/assets/logo-ufd.png) ![ESIOS logo](https://github.com/lumeou/ufd_energy/raw/main/assets/logo-esios.png) ![CNMC logo](https://github.com/lumeou/ufd_energy/raw/main/assets/logo-cnmc.png)  
  
Importa en el panel de energía los consumos y costes de ufd.es y ree.es. Calcula la factura en curso y las anterioes de cnmc.gob.es  

> **Nota de migración (v0.5.0):** el dominio interno de la integración ha pasado de `pvpc_energy` a `ufd_energy`, porque los consumos/costes son agnósticos de la tarifa (PVPC o mercado libre). **Esto es un cambio incompatible**: hay que eliminar y volver a añadir la integración (nuevas credenciales, CUPS, etc.), y las estadísticas antiguas (`pvpc_energy:*`) no se migran automáticamente — quedan congeladas y las nuevas (`ufd_energy:*`) empiezan de cero. Los CSV de `user_files` sí se pueden reutilizar copiándolos a la nueva ruta (ver más abajo).
  
![Daily consumption](https://github.com/lumeou/ufd_energy/raw/main/assets/energy-daily.png)  
![Monthly consumption](https://github.com/lumeou/ufd_energy/raw/main/assets/energy-monthly.png)  
![Bills](https://github.com/lumeou/ufd_energy/raw/main/assets/bills.png)  
  
## Instalación y configuración
Necesitas estar registrado en https://www.ufd.es y tener desactivado el inicio de sesión en dos pasos.
Después de reiniciar y añadir la integración, deberás indicar las credenciales de UFD y el número de facturas a mostrar en la Markdown Card.  
  
![Config credentials](https://github.com/lumeou/ufd_energy/raw/main/assets/config-credentials.png)  
![Config bills](https://github.com/lumeou/ufd_energy/raw/main/assets/config-bills-number.png)  
  
Los datos de energía y coste (energy_data.csv) y los de las facturas (billing_periods.csv) se almacenan en el directorio "user_files" (`/config/custom_components/ufd_energy/user_files`). Descárgalos antes de desinstalar la integración si la vas a reinstalar más adelante.
En el caso de tener datos de instalaciones previas (o si vienes de la antigua `pvpc_energy`), crea el directorio y copia ahí los ficheros (`energy_data.csv`, `billing_periods.csv`, `selfconsumption_data.csv`) antes de añadir la integración — se detectarán y usarán automáticamente en vez de descargar los ~2 años desde cero.

Si tienes una instalación solar de autoconsumo, marca la opción "Tengo instalación de autoconsumo solar" al configurar el CUPS. Esto descargará además el excedente horario vertido a la red, guardado en `selfconsumption_data.csv`.

### Coste con tarifa de mercado libre (opcional)

Si tienes una tarifa de mercado libre por periodos (P1/P2/P3), en el último paso de la configuración (o después, desde "Configurar" en la integración) puedes indicar, uno a uno, el `entity_id` de un sensor o helper que contenga cada valor de tu tarifa: precio de la energía (€/kWh sin impuestos), precio de potencia P1/P2 (€/kW·día), potencia contratada (kW), alquiler del contador (€/día), financiación del bono social (€/día), impuesto eléctrico (%), IVA (%) y compensación de excedentes (€/kWh). Todos los campos son opcionales: el que dejes en blanco simplemente no se incluye en el cálculo.

Con esto se genera la estadística **"UFD: Coste (mercado libre)"**, calculada hora a hora con tu consumo real. Es una estimación aproximada de seguimiento, no una reconstrucción exacta de la factura.

Puntos importantes sobre cómo funciona:
- El cálculo diario normal es **incremental y no retroactivo**: cada hora se calcula una única vez, con el valor que tuvieran tus sensores/helpers en ese momento, y no se recalcula después aunque cambies esos valores más adelante. No se rellena el histórico pasado (no había tarifa de mercado libre configurada antes de activarlo).
- El servicio `reprocess_market_cost` sí reconstruye todo el histórico de consumo desde cero, pero usando el **valor histórico real** de cada sensor/helper en cada hora (a través de las estadísticas de larga duración de Home Assistant), nunca el valor actual aplicado al pasado. Las horas sin dato histórico disponible en un sensor concreto se calculan sin ese término.
- Para que un sensor o helper conserve histórico horario reconstruible por `reprocess_market_cost`, necesita tener `state_class: measurement` (y una `unit_of_measurement`). Un `input_number` normal no lo tiene por sí solo: hay que envolverlo en un sensor de plantilla (helper "Plantilla" en Ajustes → Ayudantes) con esos atributos.

Si tienes una instalación solar de autoconsumo, marca la opción "Tengo instalación de autoconsumo solar" al configurar el CUPS. Esto descargará además el excedente horario vertido a la red, guardado en `selfconsumption_data.csv`.

## Salida
Después de añadir y configuración la integración, se descargará automáticamente los datos desde:
* https://www.ufd.es: Consumos horarios de electricidad y periodos de facturación
* https://api.esios.ree.es: Precios horarios de la electricidad
* https://comparador.cnmc.gob.es/facturaluz/inicio: Simulaciones de las facturas

Tardará unos cuantos minutos en obtener todos los datos disponibles (alrededor de unos 2 años). Después de esto tendrás:
* Estadísticas
    * ufd_energy:consumption (UFD: Consumo): Consumos horarios de electricidad
    * ufd_energy:cost (UFD: Coste (PVPC)): Costes horarios de electricidad simulados con la tarifa PVPC
    * ufd_energy:export (UFD: Excedente vertido a red): solo si tienes autoconsumo activado
    * ufd_energy:market_cost (UFD: Coste (mercado libre)): solo si has configurado una tarifa de mercado libre
* Estados
    * ufd_energy.current_bill: Coste de la factura en curso. En el atributo "detail" se almacena un texto formateado con markdown con los datos de las últimas facturas para mostrar con Markdown Card.

Tras la descarga inicial se realiza una actualización diaria a las 7:05 para descargar los nuevos datos. Los datos de consumo en UFD suelen tener un par de días de retraso.

## Paneles
Añade el consumo de la red al panel de energía:
* Elige un sensor que mida el consumo de la red en GJ, kWh, MJ, MWh, Wh.: "UFD: Consumo"  
* Usar una entidad que realiza un seguimiento de los costes totales: "UFD: Coste (PVPC)"  
![Grid consumption configuration](https://github.com/lumeou/ufd_energy/raw/main/assets/grid-consumption-configuration.png)

Añade a Lovelace un Markdown card para mostrar los datos de las últimas facturas:
```yml
{{ state_attr('ufd_energy.current_bill', 'detail')}}
```
![Markdown card configuration](https://github.com/lumeou/ufd_energy/raw/main/assets/markdown-card-config.png)

## Servicios
* import_energy_data - _Obtener nuevos datos de energía_: Obtiene nuevos datos de energía desde el último día hasta dos menos del actual. Se ejecuta automáticamnete todos los días a las 7:05.
* force_import_energy_data - _Forzar obtención de datos de energía_: Obtiene los datos de consumo y coste de los últimos 2 años y los sobreescribe (los datos existentes de más de 2 años se mantienen). Regenera todas las estadísticas y facturas.
* reprocess_energy_data - _Reprocesar datos de energía_: Regenera las estadísticas del panel de energía y las facturas.
* reprocess_market_cost - _Reprocesar coste de tarifa de mercado libre_: Regenera desde cero "UFD: Coste (mercado libre)" usando el valor histórico real de cada sensor/helper de tarifa configurado, hora a hora.

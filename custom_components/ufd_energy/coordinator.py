from .const import DOMAIN, CURRENT_BILL_STATE, CONSUMPTION_STATISTIC_ID, CONSUMPTION_STATISTIC_NAME, COST_STATISTIC_ID, COST_STATISTIC_NAME, EXPORT_STATISTIC_ID, EXPORT_STATISTIC_NAME, MARKET_COST_STATISTIC_ID, MARKET_COST_STATISTIC_NAME, USER_FILES_PATH, ENERGY_FILE, BILLING_PERIODS_FILE, SELFCONSUMPTION_FILE
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (StatisticData, StatisticMeanType, StatisticMetaData,)
from homeassistant.components.recorder.statistics import (async_add_external_statistics, get_last_statistics, statistics_during_period,)
from homeassistant.util.unit_conversion import EnergyConverter
from homeassistant.const import (UnitOfEnergy, CURRENCY_EURO,)
from .ufd import UFD
from .ree import REE
from .cnmc import CNMC
import datetime
import time
from os.path import exists
from os import makedirs
import logging

_LOGGER = logging.getLogger(__name__)


class UfdEnergyCoordinator:
    ufd_login = None
    ufd_password = None
    cups = None
    zip_code = None
    contract_start_date = None
    bills_number = None
    has_self_consumption = False

    market_price_entity = None
    power_price_entity = None
    meter_rent_entity = None
    social_bonus_entity = None
    electricity_tax_entity = None
    iva_entity = None
    export_compensation_entity = None

    consumption_metadata = StatisticMetaData(
        name=CONSUMPTION_STATISTIC_NAME,
        mean_type=StatisticMeanType.NONE,
        unit_class=EnergyConverter.UNIT_CLASS,
        has_sum=True,
        source=DOMAIN,
        statistic_id=CONSUMPTION_STATISTIC_ID,
        unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR
    )
    cost_metadata = StatisticMetaData(
        name=COST_STATISTIC_NAME,
        mean_type=StatisticMeanType.NONE,
        unit_class=None,
        has_sum=True,
        source=DOMAIN,
        statistic_id=COST_STATISTIC_ID,
        unit_of_measurement=CURRENCY_EURO,
    )
    export_metadata = StatisticMetaData(
        name=EXPORT_STATISTIC_NAME,
        mean_type=StatisticMeanType.NONE,
        unit_class=EnergyConverter.UNIT_CLASS,
        has_sum=True,
        source=DOMAIN,
        statistic_id=EXPORT_STATISTIC_ID,
        unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR
    )
    market_cost_metadata = StatisticMetaData(
        name=MARKET_COST_STATISTIC_NAME,
        mean_type=StatisticMeanType.NONE,
        unit_class=None,
        has_sum=True,
        source=DOMAIN,
        statistic_id=MARKET_COST_STATISTIC_ID,
        unit_of_measurement=CURRENCY_EURO,
    )


    def set_config(config):
        _LOGGER.debug(f"START - set_config(config={config})")
        UfdEnergyCoordinator.ufd_login = config['ufd_login']
        UfdEnergyCoordinator.ufd_password = config['ufd_password']
        UfdEnergyCoordinator.cups = config['cups']
        UfdEnergyCoordinator.zip_code = config['zip_code']
        if 'contract_start_date' in config:
            UfdEnergyCoordinator.contract_start_date = datetime.datetime.strptime(config['contract_start_date'], '%d/%m/%Y').date()
        UfdEnergyCoordinator.bills_number = config['bills_number']
        UfdEnergyCoordinator.has_self_consumption = config.get('has_self_consumption', False)

        UfdEnergyCoordinator.market_price_entity = config.get('market_price_entity')
        UfdEnergyCoordinator.power_price_entity = config.get('power_price_entity')
        UfdEnergyCoordinator.meter_rent_entity = config.get('meter_rent_entity')
        UfdEnergyCoordinator.social_bonus_entity = config.get('social_bonus_entity')
        UfdEnergyCoordinator.electricity_tax_entity = config.get('electricity_tax_entity')
        UfdEnergyCoordinator.iva_entity = config.get('iva_entity')
        UfdEnergyCoordinator.export_compensation_entity = config.get('export_compensation_entity')

        UFD.User = config['ufd_login']
        UFD.Password = config['ufd_password']
        UFD.cups = config['cups']        
        _LOGGER.debug(f"END - set_config")

    async def reprocess_energy_data(hass, reprocess_market_cost=False):
        _LOGGER.debug(f"START - reprocess_statistics(reprocess_market_cost={reprocess_market_cost})")
        if reprocess_market_cost:
            await UfdEnergyCoordinator.reprocess_market_cost(hass)

        consumptions, prices = await UfdEnergyCoordinator.load_energy_data(hass, ENERGY_FILE)

        _LOGGER.info(f"len(total_consumptions)={len(consumptions)}")
        if len(consumptions) > 0:
            consumption_statistics, cost_statistics = UfdEnergyCoordinator.create_statistics(0, consumptions, prices, 0, 0)
            _LOGGER.info(f"len(consumption_statistics)={len(consumption_statistics)}, len(cost_statistics)={len(cost_statistics)}")
            get_instance(hass).async_add_executor_job(async_add_external_statistics, hass, UfdEnergyCoordinator.consumption_metadata, consumption_statistics)
            get_instance(hass).async_add_executor_job(async_add_external_statistics, hass, UfdEnergyCoordinator.cost_metadata, cost_statistics)
            billing_periods = await UfdEnergyCoordinator.load_billing_periods(hass, BILLING_PERIODS_FILE)
            await UfdEnergyCoordinator.calculate_bills(hass, billing_periods, consumptions, prices, True)
        _LOGGER.debug(f"END - reprocess_statistics()")

    async def import_energy_data(hass, force_update=False):
        _LOGGER.debug(f"START - import_energy_data(), force_update={force_update}")

        start_date = datetime.datetime(year=datetime.date.today().year - 2, month=datetime.date.today().month, day=1).date()
        if UfdEnergyCoordinator.contract_start_date and UfdEnergyCoordinator.contract_start_date > start_date:
            start_date = UfdEnergyCoordinator.contract_start_date
        end_date = datetime.date.today() - datetime.timedelta(days=2)

        consumptions, prices = await UfdEnergyCoordinator.load_energy_data(hass, ENERGY_FILE, start_date)
        consumptions_len = len(consumptions)
        prices_len = len(prices)

        first_consumption_date = None
        last_consumption_date = None
        start_billing_date = start_date
        if consumptions_len > 0:
            first_consumption_date = datetime.datetime.fromtimestamp(min(list(consumptions.keys()))).date()
            last_consumption_date = datetime.datetime.fromtimestamp(max(list(consumptions.keys()))).date()
            start_billing_date = min(start_date, first_consumption_date)

        get_new_periods = last_consumption_date is None or end_date > last_consumption_date
        billing_periods = await UfdEnergyCoordinator.get_billing_periods(hass, start_billing_date, get_new_periods)

        first_billing_date = None
        for billing_period in billing_periods:
            if not first_billing_date or billing_period['start_date'] < first_billing_date:
                first_billing_date = billing_period['start_date']
        if first_billing_date and first_billing_date > start_date:
            start_date = first_billing_date

        if force_update or first_consumption_date is None or first_consumption_date > start_date:
            await UfdEnergyCoordinator.get_data(UFD.consumptions, start_date, end_date, consumptions, 14, force_update)
        elif end_date > last_consumption_date:
            await UfdEnergyCoordinator.get_data(UFD.consumptions, last_consumption_date + datetime.timedelta(days=1), end_date, consumptions, 14)
        
        first_price_date = None
        last_price_date = None
        if prices_len > 0:
            first_price_date = datetime.datetime.fromtimestamp(min(list(prices.keys()))).date()
            last_price_date = datetime.datetime.fromtimestamp(max(list(prices.keys()))).date()

        if force_update or first_price_date is None or first_price_date > start_date:
            await UfdEnergyCoordinator.get_data(REE.pvpc, start_date, end_date, prices, 28, force_update)
        elif end_date > last_price_date:
            await UfdEnergyCoordinator.get_data(REE.pvpc, last_price_date + datetime.timedelta(days=1), end_date, prices, 28)
        
        data_updated = force_update or len(consumptions) > consumptions_len or len(prices) > prices_len
        if data_updated:
            await UfdEnergyCoordinator.save_energy_data(hass, ENERGY_FILE, consumptions, prices)

        # Autoconsumo y coste de mercado libre antes de calcular las facturas, que leen la
        # estadística de mercado libre y deben ver ya las horas recién importadas.
        if UfdEnergyCoordinator.has_self_consumption:
            try:
                await UfdEnergyCoordinator.import_selfconsumption_data(hass, start_date, end_date, force_update)
            except Exception:
                _LOGGER.exception("Error importando datos de autoconsumo")

        if UfdEnergyCoordinator.market_price_entity:
            try:
                await UfdEnergyCoordinator.import_market_cost_data(hass, consumptions)
            except Exception:
                _LOGGER.exception("Error calculando el coste con la tarifa de mercado libre")

        if data_updated:
            if len(consumptions) > 0:
                last_stat = await get_instance(hass).async_add_executor_job(get_last_statistics, hass, 1, CONSUMPTION_STATISTIC_ID, True, set())
                if force_update or last_stat is None or first_consumption_date is None \
                    or first_consumption_date != datetime.datetime.fromtimestamp(min(list(consumptions.keys()))).date() \
                    or last_consumption_date != datetime.datetime.fromtimestamp(last_stat[CONSUMPTION_STATISTIC_ID][0]['start'], datetime.UTC).date():
                    consumption_statistics, cost_statistics = UfdEnergyCoordinator.create_statistics(0, consumptions, prices, 0, 0)
                else:
                    start = datetime.datetime.fromtimestamp(last_stat[CONSUMPTION_STATISTIC_ID][0]['start'], datetime.UTC)
                    stats = await get_instance(hass).async_add_executor_job(statistics_during_period, hass, start, None, {CONSUMPTION_STATISTIC_ID, COST_STATISTIC_ID}, 'hour', None, {'sum'})
                    total_energy_consumption = stats[CONSUMPTION_STATISTIC_ID][0]["sum"]
                    total_energy_cost = stats[COST_STATISTIC_ID][0]["sum"]
                    last_statistic_timestamp = stats[COST_STATISTIC_ID][0]["start"]
                    _LOGGER.info(f"last_stat={last_stat}, stats={stats}")
                    consumption_statistics, cost_statistics = UfdEnergyCoordinator.create_statistics(last_statistic_timestamp, consumptions, prices, total_energy_consumption, total_energy_cost)

                _LOGGER.info(f"len(consumption_statistics)={len(consumption_statistics)}, len(cost_statistics)={len(cost_statistics)}")
                get_instance(hass).async_add_executor_job(async_add_external_statistics, hass, UfdEnergyCoordinator.consumption_metadata, consumption_statistics)
                get_instance(hass).async_add_executor_job(async_add_external_statistics, hass, UfdEnergyCoordinator.cost_metadata, cost_statistics)
                await UfdEnergyCoordinator.calculate_bills(hass, billing_periods, consumptions, prices, force_update)
        elif not hass.states.get(CURRENT_BILL_STATE) and len(consumptions) > 0:
            await UfdEnergyCoordinator.calculate_bills(hass, billing_periods, consumptions, prices, force_update)

        _LOGGER.debug(f"END - import_energy_data")

    def get_entity_value(hass, entity_id):
        if not entity_id:
            return None
        state = hass.states.get(entity_id)
        if state is None or state.state in (None, '', 'unknown', 'unavailable'):
            return None
        try:
            return float(state.state)
        except (ValueError, TypeError):
            return None

    def get_price_entities():
        """Entidades de precio/tarifa configuradas, indexadas por nombre lógico."""
        entities = {
            'market_price': UfdEnergyCoordinator.market_price_entity,
            'power_price': UfdEnergyCoordinator.power_price_entity,
            'meter_rent': UfdEnergyCoordinator.meter_rent_entity,
            'social_bonus': UfdEnergyCoordinator.social_bonus_entity,
            'electricity_tax': UfdEnergyCoordinator.electricity_tax_entity,
            'iva': UfdEnergyCoordinator.iva_entity,
            'export_compensation': UfdEnergyCoordinator.export_compensation_entity,
        }
        return {name: entity_id for name, entity_id in entities.items() if entity_id}

    # Entidades cuyo coste SÍ debe calcularse con el histórico horario real
    # (varían por hora: el precio de mercado). Todas las demás son ajustes de
    # contrato casi constantes (potencia, alquiler, bono social, impuestos,
    # compensación de excedentes) que normalmente se configuran con helpers
    # sin `state_class`, así que no tienen estadísticas históricas: para esas,
    # si no hay valor histórico para la hora, se usa el valor actual en vivo
    # como aproximación en lugar de tratarlas silenciosamente como 0€.
    HOURLY_VARYING_ENTITIES = {'market_price'}

    async def get_hourly_price_values(hass, timestamps, configured_entities):
        """Valor medio horario histórico (long-term statistics) de cada entidad
        de precio/tarifa, para el rango de `timestamps` dado.

        Solo tiene sentido para entidades con `state_class` y estadísticas
        generadas por el Recorder. Las que no las tengan devolverán un diccionario
        vacío aquí, y `compute_hour_cost` decidirá si recurre al valor en vivo
        como aproximación (ver HOURLY_VARYING_ENTITIES).
        """
        if not configured_entities or not timestamps:
            return {}

        start = datetime.datetime.fromtimestamp(min(timestamps), datetime.UTC)
        end = datetime.datetime.fromtimestamp(max(timestamps), datetime.UTC) + datetime.timedelta(hours=1)
        stats = await get_instance(hass).async_add_executor_job(
            statistics_during_period, hass, start, end, set(configured_entities.values()), 'hour', None, {'mean'}
        )
        values_by_entity = {
            entity_id: {int(row['start']): row['mean'] for row in rows if row['mean'] is not None}
            for entity_id, rows in stats.items()
        }

        missing_history = [
            name for name, entity_id in configured_entities.items()
            if not values_by_entity.get(entity_id)
        ]
        if missing_history:
            _LOGGER.warning(
                "get_hourly_price_values: sin estadísticas históricas para %s. "
                "Se usará el valor actual en vivo como aproximación para esas horas "
                "(salvo para %s, que se tratará como 0€ si falta). "
                "Si alguna de estas entidades cambia de valor con el tiempo, "
                "considera añadirle `state_class: measurement` para que el Recorder "
                "guarde su historial y el cálculo sea exacto.",
                missing_history, sorted(UfdEnergyCoordinator.HOURLY_VARYING_ENTITIES),
            )

        return values_by_entity

    def compute_hour_cost(hass, configured_entities, values_by_entity, timestamp, grid_kwh, export_kwh):
        """Coste de una hora concreta a partir de los valores horarios de tarifa."""
        def get_value(name):
            entity_id = configured_entities.get(name)
            if not entity_id:
                return None
            historical = values_by_entity.get(entity_id, {}).get(timestamp)
            if historical is not None:
                return historical
            if name in UfdEnergyCoordinator.HOURLY_VARYING_ENTITIES:
                return None
            return UfdEnergyCoordinator.get_entity_value(hass, entity_id)

        # consumo de energía de la red (kWh) * precio de mercado (€/kWh)
        market_price = get_value('market_price')
        energia = grid_kwh * market_price if market_price is not None else 0.0

        # valoración de la energía exportada (kWh) * compensación por excedentes (€/kWh)
        export_compensation = get_value('export_compensation')
        excedente = export_kwh * export_compensation if export_compensation is not None else 0.0

        # coste fijo diario (€/día) = potencia contratada + bono social
        fixed_daily = 0.0
        power_price = get_value('power_price')
        if power_price is not None:
            fixed_daily += power_price
        social_bonus = get_value('social_bonus')
        if social_bonus is not None:
            fixed_daily += social_bonus
        fixed_hourly = fixed_daily / 24

        # coste de la electricidad (€/hora) = consumo - excedente + coste fijo horario
        electricity_subtotal = energia - excedente + fixed_hourly

        # impuesto de electricidad (%) aplicado al subtotal de electricidad
        electricity_tax = get_value('electricity_tax')
        impuesto_electricidad = electricity_subtotal * (electricity_tax / 100) if electricity_tax is not None else 0.0

        # otros conceptos diarios (€/día) = alquiler del contador
        otros_conceptos_diarios = 0.0
        meter_rent = get_value('meter_rent')
        if meter_rent is not None:
            otros_conceptos_diarios += meter_rent
        otros_conceptos_horarios = otros_conceptos_diarios / 24

        # subtotal de todos los conceptos antes del IVA (€/hora) = subtotal electricidad + impuesto electricidad + otros conceptos horarios
        subtotal = electricity_subtotal + impuesto_electricidad + otros_conceptos_horarios

        # IVA (%) aplicado al subtotal de todos los conceptos antes del IVA
        iva = get_value('iva')
        iva_amount = subtotal * (iva / 100) if iva is not None else 0.0

        # coste total de la hora (€/hora) = subtotal + IVA
        return subtotal + iva_amount

    async def import_market_cost_data(hass, consumptions):
        _LOGGER.debug(f"START - import_market_cost_data(len(consumptions)={len(consumptions)})")

        if len(consumptions) == 0:
            _LOGGER.debug(f"END - import_market_cost_data: sin consumos")
            return

        selfconsumptions = await UfdEnergyCoordinator.load_selfconsumption_data(hass, SELFCONSUMPTION_FILE)

        last_stat = await get_instance(hass).async_add_executor_job(get_last_statistics, hass, 1, MARKET_COST_STATISTIC_ID, True, {'sum'})
        if not last_stat:
            # Los consumos de UFD llegan con ~2 días de retraso, así que empezar "desde hoy" no
            # generaría nunca ninguna hora. Si la estadística no existe se construye con el
            # histórico disponible de los sensores de tarifa.
            _LOGGER.info("import_market_cost_data: no existe la estadística de mercado libre, se reconstruye")
            await UfdEnergyCoordinator.reprocess_market_cost(hass)
            return
        start_timestamp = last_stat[MARKET_COST_STATISTIC_ID][0]['start'] + 3600
        total_cost = last_stat[MARKET_COST_STATISTIC_ID][0]['sum']

        timestamps = sorted(t for t in consumptions.keys() if t >= start_timestamp)
        if len(timestamps) == 0:
            _LOGGER.debug(f"END - import_market_cost_data: no hay horas nuevas que calcular")
            return

        configured_entities = UfdEnergyCoordinator.get_price_entities()
        values_by_entity = await UfdEnergyCoordinator.get_hourly_price_values(hass, timestamps, configured_entities)

        market_cost_statistics = []
        day_cost = 0.0
        for timestamp in timestamps:
            grid_kwh = consumptions[timestamp]['value']
            export_kwh = selfconsumptions.get(timestamp, {}).get('export', 0.0)

            hour_cost = UfdEnergyCoordinator.compute_hour_cost(
                hass, configured_entities, values_by_entity, timestamp, grid_kwh, export_kwh
            )

            total_cost += hour_cost
            day_cost += hour_cost
            if datetime.datetime.fromtimestamp(timestamp).hour == 0:
                day_cost = hour_cost

            start = datetime.datetime.fromtimestamp(timestamp, datetime.UTC)
            market_cost_statistics.append(
                StatisticData(start=start, state=day_cost, sum=total_cost)
            )

        _LOGGER.info(f"len(market_cost_statistics)={len(market_cost_statistics)}")
        await UfdEnergyCoordinator.write_market_cost_statistics(hass, market_cost_statistics)
        _LOGGER.debug(f"END - import_market_cost_data")

    async def write_market_cost_statistics(hass, market_cost_statistics, clear=False):
        """Encola la escritura de la estadística de mercado libre y espera a que el Recorder la
        persista, para que las lecturas posteriores (facturas) vean ya los datos nuevos."""
        recorder = get_instance(hass)
        if clear:
            recorder.async_clear_statistics([MARKET_COST_STATISTIC_ID])
        if market_cost_statistics:
            async_add_external_statistics(hass, UfdEnergyCoordinator.market_cost_metadata, market_cost_statistics)
        await recorder.async_block_till_done()

    async def reprocess_market_cost(hass):
        _LOGGER.debug(f"START - reprocess_market_cost()")

        if not UfdEnergyCoordinator.market_price_entity:
            _LOGGER.warning("reprocess_market_cost: no hay 'market_price_entity' configurado, nada que reprocesar")
            return

        consumptions, _ = await UfdEnergyCoordinator.load_energy_data(hass, ENERGY_FILE)
        if len(consumptions) == 0:
            _LOGGER.debug(f"END - reprocess_market_cost: sin consumos")
            return
        selfconsumptions = await UfdEnergyCoordinator.load_selfconsumption_data(hass, SELFCONSUMPTION_FILE)

        configured_entities = UfdEnergyCoordinator.get_price_entities()
        timestamps = sorted(consumptions.keys())
        values_by_entity = await UfdEnergyCoordinator.get_hourly_price_values(hass, timestamps, configured_entities)

        # Sin precio de mercado la hora saldría solo con los costes fijos (muy por debajo del
        # real), así que se empieza en la primera hora con histórico del precio de mercado.
        # get_market_cost_for_period ya marca los periodos que solo quedan cubiertos en parte.
        market_price_history = values_by_entity.get(UfdEnergyCoordinator.market_price_entity, {})
        if not market_price_history:
            _LOGGER.warning(
                "reprocess_market_cost: '%s' no tiene estadísticas históricas (¿falta `state_class: measurement`?), "
                "no se puede reconstruir el coste de mercado libre", UfdEnergyCoordinator.market_price_entity
            )
            return
        first_price_timestamp = min(market_price_history.keys())
        _LOGGER.info(
            "reprocess_market_cost: histórico de '%s' disponible desde %s; las horas anteriores no se calculan",
            UfdEnergyCoordinator.market_price_entity, datetime.datetime.fromtimestamp(first_price_timestamp).isoformat()
        )
        timestamps = [t for t in timestamps if t >= first_price_timestamp]

        total_cost = 0.0
        day_cost = 0.0
        market_cost_statistics = []
        for timestamp in timestamps:
            grid_kwh = consumptions[timestamp]['value']
            export_kwh = selfconsumptions.get(timestamp, {}).get('export', 0.0)

            hour_cost = UfdEnergyCoordinator.compute_hour_cost(
                hass, configured_entities, values_by_entity, timestamp, grid_kwh, export_kwh
            )

            total_cost += hour_cost
            day_cost += hour_cost
            if datetime.datetime.fromtimestamp(timestamp).hour == 0:
                day_cost = hour_cost

            start_dt = datetime.datetime.fromtimestamp(timestamp, datetime.UTC)
            market_cost_statistics.append(
                StatisticData(start=start_dt, state=day_cost, sum=total_cost)
            )

        _LOGGER.info(f"len(market_cost_statistics)={len(market_cost_statistics)}")
        # Se borra antes la estadística: si no, las filas previas a la nueva primera hora
        # conservarían acumulados antiguos incoherentes con los nuevos.
        await UfdEnergyCoordinator.write_market_cost_statistics(hass, market_cost_statistics, clear=True)
        _LOGGER.debug(f"END - reprocess_market_cost")

    async def import_selfconsumption_data(hass, start_date, end_date, force_update=False):
        _LOGGER.debug(f"START - import_selfconsumption_data(start_date={start_date.isoformat()}, end_date={end_date.isoformat()}, force_update={force_update})")

        selfconsumptions = await UfdEnergyCoordinator.load_selfconsumption_data(hass, SELFCONSUMPTION_FILE)
        selfconsumptions_len = len(selfconsumptions)

        last_selfconsumption_date = None
        if selfconsumptions_len > 0:
            last_selfconsumption_date = datetime.datetime.fromtimestamp(max(selfconsumptions.keys())).date()

        if force_update or last_selfconsumption_date is None:
            await UfdEnergyCoordinator.get_data(UFD.selfConsumptions, start_date, end_date, selfconsumptions, 14, force_update)
        elif end_date > last_selfconsumption_date:
            await UfdEnergyCoordinator.get_data(UFD.selfConsumptions, last_selfconsumption_date + datetime.timedelta(days=1), end_date, selfconsumptions, 14)

        if force_update or len(selfconsumptions) > selfconsumptions_len:
            await UfdEnergyCoordinator.save_selfconsumption_data(hass, SELFCONSUMPTION_FILE, selfconsumptions)

        if len(selfconsumptions) > 0:
            last_stat = await get_instance(hass).async_add_executor_job(get_last_statistics, hass, 1, EXPORT_STATISTIC_ID, True, {'sum'})
            # Aunque no haya llegado ninguna fila nueva de UFD en esta ejecución (su dato horario
            # suele llevar un par de días de retraso), si la estadística todavía no existe en el
            # recorder hay que crearla igualmente a partir de lo que ya tengamos en el CSV/memoria
            # (por ejemplo, ficheros migrados a mano). No depender solo de "hay filas nuevas".
            if force_update or not last_stat:
                export_statistics = UfdEnergyCoordinator.create_export_statistics(0, selfconsumptions, 0)
            elif len(selfconsumptions) > selfconsumptions_len:
                start = datetime.datetime.fromtimestamp(last_stat[EXPORT_STATISTIC_ID][0]['start'], datetime.UTC)
                stats = await get_instance(hass).async_add_executor_job(statistics_during_period, hass, start, None, {EXPORT_STATISTIC_ID}, 'hour', None, {'sum'})
                total_export = stats[EXPORT_STATISTIC_ID][0]["sum"]
                last_statistic_timestamp = stats[EXPORT_STATISTIC_ID][0]["start"]
                export_statistics = UfdEnergyCoordinator.create_export_statistics(last_statistic_timestamp, selfconsumptions, total_export)
            else:
                export_statistics = None

            if export_statistics:
                _LOGGER.info(f"len(export_statistics)={len(export_statistics)}")
                get_instance(hass).async_add_executor_job(async_add_external_statistics, hass, UfdEnergyCoordinator.export_metadata, export_statistics)


        _LOGGER.debug(f"END - import_selfconsumption_data")

    async def load_selfconsumption_data(hass, file_path):
        _LOGGER.debug(f"START - load_selfconsumption_data(file_path={file_path})")
        selfconsumptions = {}
        if exists(file_path):
            with await hass.async_add_executor_job(open, file_path, 'r') as file:
                file.readline()  # cabecera
                for line in file:
                    date, timestamp, grid, self_consumed, export = line[0:-1].split(',')
                    selfconsumptions[int(timestamp)] = {
                        'grid': float(grid) if grid != '' else 0.0,
                        'self_consumed': float(self_consumed) if self_consumed != '' else 0.0,
                        'export': float(export) if export != '' else 0.0,
                    }
        _LOGGER.debug(f"END - load_selfconsumption_data: len(selfconsumptions): {len(selfconsumptions)}")
        return selfconsumptions

    async def save_selfconsumption_data(hass, file_path, selfconsumptions):
        _LOGGER.debug(f"START - save_selfconsumption_data(file_path={file_path}, len(selfconsumptions)={len(selfconsumptions)})")
        timestamps = list(selfconsumptions.keys())
        timestamps.sort()
        with await hass.async_add_executor_job(open, file_path, 'w') as file:
            file.write("date,timestamp,grid,self_consumed,export\n")
            for timestamp in timestamps:
                date = datetime.datetime.fromtimestamp(timestamp).strftime('%d/%m/%Y %H')
                value = selfconsumptions[timestamp]
                file.write(f"{date},{timestamp},{value['grid']},{value['self_consumed']},{value['export']}\n")
        _LOGGER.debug(f"END - save_selfconsumption_data")

    def create_export_statistics(last_statistic_timestamp, selfconsumptions, total_export):
        _LOGGER.debug(f"START - create_export_statistics(last_statistic_timestamp={last_statistic_timestamp}, len(selfconsumptions)={len(selfconsumptions)}, total_export={total_export})")
        export_statistics = []
        timestamp = max(min(selfconsumptions.keys()), last_statistic_timestamp + 3600)
        last_timestamp = max(selfconsumptions.keys())
        day_export = 0
        while timestamp <= last_timestamp:
            export = 0
            if timestamp in selfconsumptions:
                export = selfconsumptions[timestamp]['export']
            total_export += export
            day_export += export
            if datetime.datetime.fromtimestamp(timestamp).hour == 0:
                day_export = export
            start = datetime.datetime.fromtimestamp(timestamp, datetime.UTC)
            export_statistics.append(
                StatisticData(
                    start=start, state=day_export, sum=total_export
                )
            )
            timestamp += 3600
        _LOGGER.debug(f"END - create_export_statistics: len(export_statistics)={len(export_statistics)}")
        return export_statistics

    async def get_data(getter, start_date, end_date, data, days, force_update=False):
        _LOGGER.debug(f"START - get_data(getter={getter}, start_date={start_date.isoformat()}, end_date={end_date.isoformat()}, len(data)={len(data)}, days={days}, force_update={force_update})")    
        data_len = len(data)
        
        request_start_date = end_date + datetime.timedelta(days=1)
        while request_start_date > start_date:
            request_end_date = request_start_date - datetime.timedelta(days=1)
            while not force_update and int(time.mktime(request_end_date.timetuple())) in data.keys() and request_end_date >= start_date:
                request_end_date -= datetime.timedelta(days=1)
            if request_end_date < start_date: break
            request_start_date = request_end_date - datetime.timedelta(days=1)
            while (force_update or int(time.mktime(request_start_date.timetuple())) not in data.keys()) and request_start_date >= start_date and (request_end_date - request_start_date).days < days:
                request_start_date -= datetime.timedelta(days=1)
            request_start_date += datetime.timedelta(days=1)
            new_data = await getter(request_start_date, request_end_date)

            if new_data is None:
                break
            elif len(new_data) > 0:
                data.update(new_data)

        _LOGGER.debug(f"END - get_data: new_data=={len(data)-data_len}")

    async def get_billing_periods(hass, start_billing_date, get_new_periods):
        _LOGGER.debug(f"START - get_billing_periods(start_billing_date={start_billing_date}, get_new_periods={get_new_periods})")
        billing_periods = await UfdEnergyCoordinator.load_billing_periods(hass, BILLING_PERIODS_FILE)

        if start_billing_date:
            remove_periods = []
            for billing_period in billing_periods:
                if billing_period['start_date'] < start_billing_date:
                    remove_periods.append(billing_period)
            if len(remove_periods) > 0:
                for billing_period in remove_periods:
                    billing_periods.remove(billing_period)
                await UfdEnergyCoordinator.save_billing_periods(hass, BILLING_PERIODS_FILE, billing_periods)

        if len(billing_periods) == 0:
            start_date = datetime.date(2000, 1, 1)
        else:
            start_date = billing_periods[-1]['end_date']
            previous_end_date = None
            for billing_period in billing_periods:
                if previous_end_date and billing_period['start_date'] != (previous_end_date + datetime.timedelta(days=1)):
                    billing_period['start_date'] = previous_end_date + datetime.timedelta(days=1)
                    if 'total_cost' in billing_period:
                        del billing_period['total_cost']
                previous_end_date = billing_period['end_date']

        end_date = datetime.date.today()
        _LOGGER.debug(f"start_date={start_date.isoformat()}, end_date={end_date.isoformat()}, (end_date - start_date).days={(end_date - start_date).days}")
        if get_new_periods and (end_date - start_date).days > 2:
            new_billing_periods = await UFD.billingPeriods(start_date, end_date)
            if len(new_billing_periods) > 0:
                billing_periods += new_billing_periods
                await UfdEnergyCoordinator.save_billing_periods(hass, BILLING_PERIODS_FILE, billing_periods)
        _LOGGER.debug(f"END - get_billing_periods: len(billing_periods)={len(billing_periods)}")
        return billing_periods

    async def get_market_cost_for_period(hass, start_date, end_date):
        if not UfdEnergyCoordinator.market_price_entity:
            return None, None
        start = datetime.datetime.combine(start_date, datetime.time.min, tzinfo=datetime.UTC)
        end = datetime.datetime.combine(end_date + datetime.timedelta(days=1), datetime.time.min, tzinfo=datetime.UTC)
        stats = await get_instance(hass).async_add_executor_job(
            statistics_during_period, hass, start, end, {MARKET_COST_STATISTIC_ID}, 'hour', None, {'sum', 'state'}
        )
        rows = stats.get(MARKET_COST_STATISTIC_ID)
        if not rows:
            return None, None
        # sum() es acumulado desde siempre; el coste de este rango es la diferencia entre el
        # acumulado al final y el acumulado justo antes de la primera hora devuelta.
        cost = rows[-1]['sum'] - (rows[0]['sum'] - rows[0]['state'])
        # market_cost no rellena histórico: covered_from es desde cuándo hay datos reales en
        # este rango (puede ser posterior a start_date si la tarifa se activó a mitad del periodo).
        covered_from = max(start_date, datetime.datetime.fromtimestamp(rows[0]['start'], datetime.UTC).date())
        return cost, covered_from

    async def calculate_bills(hass, billing_periods, consumptions, prices, force_update=False):
        _LOGGER.debug(f"START - calculate_bills(len(billing_periods)={len(billing_periods)}, len(consumptions)={len(consumptions)}, len(prices)={len(prices)}, force_update={force_update})")

        if len(consumptions) > 0 and len(billing_periods) > 0:
            update = False
            first_consumption_date = datetime.datetime.fromtimestamp(min(consumptions.keys())).date()
            last_consumption_date = datetime.datetime.fromtimestamp(max(consumptions.keys())).date()
            for billing_period in reversed(billing_periods):
                if billing_period['start_date'] >= first_consumption_date and (force_update or 'total_cost' not in billing_period):
                    billing_period = await UfdEnergyCoordinator.get_bill(hass, billing_period, consumptions)
                    if 'total_cost' in billing_period:
                        update = True
            if update:
                await UfdEnergyCoordinator.save_billing_periods(hass, BILLING_PERIODS_FILE, billing_periods)

            current_billing_period = None
            if (last_consumption_date - billing_periods[-1]['end_date']).days >= 2:
                current_billing_period = {'start_date': billing_periods[-1]['end_date'] + datetime.timedelta(days=1), 'end_date':last_consumption_date}
                current_billing_period['power_high'], current_billing_period['power_low'] = await UFD.supplyPointPowers(UfdEnergyCoordinator.cups)
                current_billing_period = await UfdEnergyCoordinator.get_bill(hass, current_billing_period, consumptions)

            current_bill_value = ''
            bills_description = ''
            if current_billing_period and 'total_cost' in current_billing_period and current_billing_period['total_cost'] != '-':
                billing_periods.append(current_billing_period)
                current_bill_value = '%.2f' % current_billing_period['total_cost']
                days = (current_billing_period['end_date'] - current_billing_period['start_date']).days + 1

                estimation_days = 30
                if estimation_days > days:
                    estimation_end_date = current_billing_period['start_date'] + datetime.timedelta(days=estimation_days-1)
                    cost = round(current_billing_period['total_cost'] / days * estimation_days, 2)
                    bills_description += f"Estimación PVPC {current_billing_period['start_date'].strftime('%d/%m')} - {estimation_end_date.strftime('%d/%m')} ({estimation_days} días): **{cost} €**\n\n"

                    market_cost, market_cost_covered_from = await UfdEnergyCoordinator.get_market_cost_for_period(
                        hass, current_billing_period['start_date'], current_billing_period['end_date']
                    )
                    if market_cost is not None:
                        covered_days = (current_billing_period['end_date'] - market_cost_covered_from).days + 1
                        market_estimate = round(market_cost / covered_days * estimation_days, 2)
                        note = '' if market_cost_covered_from <= current_billing_period['start_date'] \
                            else f" _(datos desde {market_cost_covered_from.strftime('%d/%m')}, no cubre todo el periodo)_"
                        bills_description += f"Estimación mercado libre {current_billing_period['start_date'].strftime('%d/%m')} - {estimation_end_date.strftime('%d/%m')} ({estimation_days} días): **{market_estimate} €**{note}\n\n"
                        
            # Cabecera de la tabla
            bills_description += '| Fecha | Días | kWh | kWh/día |'
            bills_description += ' Tarifa PVPC | | |'
            bills_description += ' Tarifa mercado libre | | |\n'
            bills_description += '| :---: | :---: | :---: | :---: |'
            bills_description += ' :---: | :---: | :---: |'
            bills_description += ' :---: | :---: | :---: |\n'
            bills_description += '| | | | |'
            bills_description += ' Importe | cent/kWh | €/día |'
            bills_description += ' Importe | cent/kWh | €/día |'
            
            has_partial_market_cost = False
            for billing_period in reversed(billing_periods[-UfdEnergyCoordinator.bills_number:]):
                if 'total_cost' in billing_period and billing_period['total_cost'] != '-':
                    date = billing_period['end_date'].strftime('%d/%m')
                    days = (billing_period['end_date'] - billing_period['start_date']).days + 1
                    consumption = round(billing_period['total_consumption'], 0)
                    day_consumption = round(billing_period['total_consumption'] / days, 1)
                    
                    # Variables PVPC
                    cost = ''
                    cost_kwh = ''
                    day_cost = ''
                    if 'total_cost' in billing_period and billing_period['total_cost'] != '-':
                        cost = round(billing_period['total_cost'], 2)
                        if billing_period['total_consumption'] != 0:
                            cost_kwh = round(billing_period['energy_cost'] / billing_period['total_consumption'] * 100, 1)
                        day_cost = round(billing_period['total_cost'] / days, 2)
                    
                    # Variables mercado libre
                    market_cost = ''
                    market_cost_kwh = ''
                    day_market_cost = ''
                    period_market_cost, market_cost_covered_from = await UfdEnergyCoordinator.get_market_cost_for_period(
                        hass, billing_period['start_date'], billing_period['end_date']
                    )
                    if period_market_cost is not None:
                        market_consumption = billing_period['total_consumption']
                        market_days = days
                        partial_mark = ''
                        if market_cost_covered_from > billing_period['start_date']:
                            # Datos parciales: cent/kWh y €/día se calculan solo sobre los días
                            # cubiertos, y se marcan con * para no confundirlos con el periodo completo.
                            covered_start = int(time.mktime(market_cost_covered_from.timetuple()))
                            covered_end = int(time.mktime((billing_period['end_date'] + datetime.timedelta(days=1)).timetuple()))
                            market_consumption = sum(c['value'] for t, c in consumptions.items() if covered_start <= t < covered_end)
                            market_days = (billing_period['end_date'] - market_cost_covered_from).days + 1
                            partial_mark = '*'
                            has_partial_market_cost = True
                        market_cost = f"{round(period_market_cost, 2)}{partial_mark}"
                        if market_consumption != 0:
                            market_cost_kwh = f"{round(period_market_cost / market_consumption * 100, 1)}{partial_mark}"
                        day_market_cost = f"{round(period_market_cost / market_days, 2)}{partial_mark}"

                    if billing_period == current_billing_period:
                        bills_description += f"\n|\n| {date} | {days} | {consumption} | {day_consumption} |"
                        bills_description += f" **{cost}** | **{cost_kwh}** | **{day_cost}** |"
                        bills_description += f" **{market_cost}** | **{market_cost_kwh}** | **{day_market_cost}** |\n|"
                    else:
                        bills_description += f"\n| {date} | {days} | {consumption} | {day_consumption} |"
                        bills_description += f" {cost} | {cost_kwh} | {day_cost} |"
                        bills_description += f" {market_cost} | {market_cost_kwh} | {day_market_cost} |"

            if has_partial_market_cost:
                bills_description += "\n\n_* Mercado libre: periodo cubierto solo en parte; importe, cent/kWh y €/día corresponden únicamente a los días con datos._"

            _LOGGER.debug(f"current_bill_value={current_bill_value}, bills_description={bills_description}")
            hass.states.async_set(CURRENT_BILL_STATE, current_bill_value, {'detail': bills_description})
        _LOGGER.debug(f"END - calculate_bills")

    async def get_bill(hass, billing_period, consumptions):
        _LOGGER.debug(f"START - get_bill(billing_period={billing_period}, len(consumptions)={len(consumptions)})")
        start_timestamp = int(time.mktime(billing_period['start_date'].timetuple()))
        end_timestamp = int(time.mktime((billing_period['end_date'] + datetime.timedelta(days=1)).timetuple())) - 3600
        bill_consumptions = {}
        for timestamp, consumption in consumptions.items():
            if start_timestamp <= timestamp <= end_timestamp:
                bill_consumptions[timestamp] = consumption['value']
        if bill_consumptions:
            billing_period, consumptions_file = await CNMC.calculate_bill(billing_period, UfdEnergyCoordinator.cups, bill_consumptions, UfdEnergyCoordinator.zip_code)
            if consumptions_file is not None:
                path = f"{USER_FILES_PATH}/consumption_files/{billing_period['start_date'].year}"
                if not exists(path):
                    makedirs(path)
                with await hass.async_add_executor_job(open, f"{path}/consumptions_{billing_period['start_date'].strftime('%Y-%m-%d')}.csv", 'w') as file:
                    file.write(consumptions_file)
        _LOGGER.debug(f"END - get_bill: {billing_period}")
        return billing_period

    async def load_energy_data(hass, file_path, start_date=None):
        _LOGGER.debug(f"START - load_energy_data(file_path={file_path})")
        consumptions = {}
        prices = {}
        if exists(file_path):
            with await hass.async_add_executor_job(open, file_path, 'r') as file:
                has_reading_type = 'reading_type' in file.readline()
                for line in file:
                    if has_reading_type:
                        timestamp, consumption, price, reading_type = line[0:-1].split(',')[-4:]
                    else:
                        timestamp, consumption, price = line[0:-1].split(',')[-3:]
                        reading_type = ''
                    timestamp = int(timestamp)
                    if consumption != '-' and consumption != '':
                        consumptions[timestamp] = {'value': float(consumption), 'reading_type': reading_type}
                    if price != '-' and price != '':
                        prices[timestamp] = float(price)
            
            # Comprobar que todos los días tengan consumos correctos
            if (start_date):
                timestamps = list(consumptions.keys())
                timestamps.sort()
                if len(timestamps) > 0:
                    previous_day = datetime.datetime.fromtimestamp(timestamps[0]).date()
                    hours = 0
                    for timestamp in timestamps:
                        day = datetime.datetime.fromtimestamp(timestamp).date()
                        if day == previous_day:
                            hours += 1
                        elif hours in (23, 24, 25) or day < start_date:
                            previous_day = day
                            hours = 1
                        else:
                            # Eliminar los consumos desde previous_day
                            new_consumptions = {}
                            for timestamp in timestamps:
                                if datetime.datetime.fromtimestamp(timestamp).date() < previous_day:
                                    new_consumptions[timestamp] = consumptions[timestamp]
                            consumptions = new_consumptions
                            break
                else:
                    _LOGGER.debug("load_energy_data: no hay consumos locales validos; se intentaran descargar de UFD")

        _LOGGER.debug(f"END - load_energy_data: len(consumptions): {len(consumptions)}, len(prices): {len(prices)}")
        return consumptions, prices

    async def save_energy_data(hass, file_path, consumptions, prices):
        _LOGGER.debug(f"START - save_energy_data(file_path={file_path}, len(consumptions)={len(consumptions)}, len(prices)={len(prices)})")
        timestamps = list(consumptions.keys())
        timestamps = timestamps + list(set(list(prices.keys())) - set(timestamps))
        timestamps.sort()
        with await hass.async_add_executor_job(open, file_path, 'w') as file:
            file.write("date,timestamp,consumption,price,reading_type\n")
            for timestamp in timestamps:
                date = datetime.datetime.fromtimestamp(timestamp).strftime('%d/%m/%Y %H')
                consumption = '' if timestamp not in consumptions else consumptions[timestamp]['value']
                reading_type = '' if timestamp not in consumptions else consumptions[timestamp]['reading_type']
                price = '' if timestamp not in prices else prices[timestamp]
                file.write(f"{date},{timestamp},{consumption},{price},{reading_type}\n")
        _LOGGER.debug(f"END - save_energy_data")

    async def load_billing_periods(hass, file_path):
        _LOGGER.debug(f"START - load_billing_periods(file_path={file_path})")
        billing_periods = []
        update = False
        if exists(file_path):
            with await hass.async_add_executor_job(open, file_path, 'r') as file:
                if file.readline().count(',') == 7:
                    update = True
                    power_high, power_low = await UFD.supplyPointPowers(UfdEnergyCoordinator.cups)
                for line in file:
                    if line.count(',') == 7:
                        start_date, end_date, total_cost, power_cost, energy_cost, rent_cost, tax_cost, total_consumption = line[0:-1].split(',')
                    else:
                        start_date, end_date, power_high, power_low, total_cost, power_cost, energy_cost, rent_cost, tax_cost, total_consumption = line[0:-1].split(',')

                    billing_period = {'start_date': datetime.date.fromisoformat(start_date), 'end_date': datetime.date.fromisoformat(end_date)}
                    billing_period['power_high'] = float(power_high)
                    billing_period['power_low'] = float(power_low)
                    if total_cost != '':
                        if total_cost == '-':
                            billing_period['total_cost'] = total_cost
                        else:
                            billing_period['total_cost'] = float(total_cost)
                    if power_cost != '': billing_period['power_cost'] = float(power_cost)
                    if energy_cost != '': billing_period['energy_cost'] = float(energy_cost)
                    if rent_cost != '': billing_period['rent_cost'] = float(rent_cost)
                    if tax_cost != '': billing_period['tax_cost'] = float(tax_cost)
                    if total_consumption != '': billing_period['total_consumption'] = float(total_consumption)
                    billing_periods.append(billing_period)
        if update:
            await UfdEnergyCoordinator.save_billing_periods(hass, file_path, billing_periods)
        _LOGGER.debug(f"END - load_billing_periods: len(billing_periods): {len(billing_periods)}")
        return billing_periods

    async def save_billing_periods(hass, file_path, billing_periods):
        _LOGGER.debug(f"START - save_billing_periods(file_path={file_path}, len(billing_periods)={len(billing_periods)})")
        with await hass.async_add_executor_job(open, file_path, 'w') as file:
            file.write("start_date,end_date,power_high,power_low,total_cost,power_cost,energy_cost,rent_cost,tax_cost,total_consumption\n")
            for billing_period in billing_periods:
                total_cost = billing_period['total_cost'] if 'total_cost' in billing_period else ''
                power_cost = billing_period['power_cost'] if 'power_cost' in billing_period else ''
                energy_cost = billing_period['energy_cost'] if 'energy_cost' in billing_period else ''
                rent_cost = billing_period['rent_cost'] if 'rent_cost' in billing_period else ''
                tax_cost = billing_period['tax_cost'] if 'tax_cost' in billing_period else ''
                total_consumption = billing_period['total_consumption'] if 'total_consumption' in billing_period else ''

                file.write(f"{billing_period['start_date'].isoformat()},{billing_period['end_date'].isoformat()},{billing_period['power_high']},{billing_period['power_low']},{total_cost},{power_cost},{energy_cost},{rent_cost},{tax_cost},{total_consumption}\n")
        _LOGGER.debug(f"END - save_billing_periods")

    def create_statistics(last_statistic_timestamp, consumptions, prices, total_energy_consumption, total_energy_cost):
        _LOGGER.debug(f"START - create_statistics(last_statistic_timestamp={last_statistic_timestamp}, len(consumptions)={len(consumptions)}, len(prices)={len(prices)}, total_energy_consumption={total_energy_consumption}, total_energy_cost={total_energy_cost})")
        day_energy_consumption = 0
        day_energy_cost = 0
        consumption_statistics = []
        cost_statistics = []
        timestamp = max(min(consumptions.keys()), last_statistic_timestamp + 3600)
        last_timestamp = max(consumptions.keys())
        while timestamp <= last_timestamp:
            consumption = 0
            if timestamp in consumptions:
                consumption = consumptions[timestamp]['value']
            total_energy_consumption += consumption
            day_energy_consumption += consumption
            if datetime.datetime.fromtimestamp(timestamp).hour == 0:
                day_energy_consumption = consumption
            start = datetime.datetime.fromtimestamp(timestamp, datetime.UTC)
            consumption_statistics.append(
                StatisticData(
                    start=start, state=day_energy_consumption, sum=total_energy_consumption
                )
            )

            cost = 0
            if timestamp in prices:
                cost = prices[timestamp]
            hour_cost = consumption * cost
            total_energy_cost += hour_cost
            day_energy_cost += hour_cost
            if datetime.datetime.fromtimestamp(timestamp).hour == 0:
                day_energy_cost = hour_cost
            cost_statistics.append(
                StatisticData(
                    start=start, state=day_energy_cost, sum=total_energy_cost
                )
            )
            timestamp += 3600
        _LOGGER.debug(f"END - create_statistics: len(consumption_statistics)={len(consumption_statistics)}, len(cost_statistics)={len(cost_statistics)}")
        return consumption_statistics, cost_statistics
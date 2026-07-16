"""Constants for the UFD Energy integration."""

DOMAIN = "ufd_energy"
CURRENT_BILL_STATE = f"{DOMAIN}.current_bill"
CONSUMPTION_STATISTIC_ID = f"{DOMAIN}:consumption"
CONSUMPTION_STATISTIC_NAME = 'UFD: Consumo'
COST_STATISTIC_ID = f"{DOMAIN}:cost"
COST_STATISTIC_NAME = 'UFD: Coste (PVPC)'
EXPORT_STATISTIC_ID = f"{DOMAIN}:export"
EXPORT_STATISTIC_NAME = 'UFD: Excedente vertido a red'
MARKET_COST_STATISTIC_ID = f"{DOMAIN}:market_cost"
MARKET_COST_STATISTIC_NAME = 'UFD: Coste (mercado libre)'
USER_FILES_PATH = f"/config/custom_components/{DOMAIN}/user_files"
ENERGY_FILE = f"{USER_FILES_PATH}/energy_data.csv"
BILLING_PERIODS_FILE = f"{USER_FILES_PATH}/billing_periods.csv"
SELFCONSUMPTION_FILE = f"{USER_FILES_PATH}/selfconsumption_data.csv"